"""Voiceover generation — Kokoro (free, local, default) with an ElevenLabs
paid toggle, matching the pattern spec'd for stock footage / other swappable
services. Takes a generated story's full_text and produces one continuous
narrated audio file.
"""

import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pipeline.config import ROOT, env, load_channel

TTS_ENGINE = env("TTS_ENGINE", "kokoro")
# batch.py overrides TTS_WORKERS per-process when multiple videos run
# concurrently, so they split cores instead of each grabbing up to 6.
TTS_WORKERS = int(env("TTS_WORKERS", "0")) or min(6, os.cpu_count() or 4)
# Chunk count is independent of worker count: more, smaller chunks queue up
# behind whatever TTS_WORKERS actually is, which keeps every worker busy
# until the very end instead of the whole batch waiting on one large, slow
# chunk (see build log — this decoupling is what makes a big pod with many
# cores actually pay off, vs. the old 1-chunk-per-worker scheme where chunk
# count silently tracked the local machine's RAM-limited worker count).
TTS_CHUNKS = int(env("TTS_CHUNKS", "0")) or TTS_WORKERS
# Bug fixed after a real batch-concurrency test crashed from oversubscription:
# each worker process also multiplies by its own internal thread count, so
# TTS_WORKERS x KOKORO_WORKER_THREADS was silently 2x the intended core
# budget once multiple videos ran at once. Solo runs keep 2 (unchanged,
# already proven fine); batch.py sets this to 1 for concurrent runs.
KOKORO_WORKER_THREADS = int(env("KOKORO_WORKER_THREADS", "2"))

# Set by _init_kokoro_worker, once per worker process — loading KPipeline is
# expensive, so each worker loads it exactly once and reuses it across every
# chunk it's assigned, rather than reloading per chunk (see build log: with
# TTS_CHUNKS raised well above TTS_WORKERS, a worker handles many chunks
# over its lifetime, and a per-chunk reload would have eaten the speedup
# the extra chunking was meant to buy).
_worker_pipeline = None


def _init_kokoro_worker(worker_threads: int) -> None:
    global _worker_pipeline
    import torch
    from kokoro import KPipeline

    # Capping intra-op threads here keeps TTS_WORKERS processes from
    # oversubscribing the CPU against each other (see build log: this is
    # what made the parallel split a real speedup instead of the same
    # total work spread thinner).
    torch.set_num_threads(worker_threads)
    _worker_pipeline = KPipeline(lang_code="a")


def _split_text_for_parallel(text: str, n_chunks: int) -> list[str]:
    """Split into n_chunks roughly-equal-length pieces on sentence
    boundaries — each piece is synthesized independently and queued across
    the worker pool, then the resulting audio is concatenated back in
    order."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    total_words = sum(len(s.split()) for s in sentences)
    target = max(1, total_words / n_chunks)

    chunks, current, current_words = [], [], 0
    for sentence in sentences:
        current.append(sentence)
        current_words += len(sentence.split())
        if current_words >= target and len(chunks) < n_chunks - 1:
            chunks.append(" ".join(current))
            current, current_words = [], 0
    if current:
        chunks.append(" ".join(current))
    return chunks


def _synthesize_chunk_worker(args: tuple) -> tuple:
    idx, text, voice = args
    import numpy as np

    audio_chunks = [audio for _, _, audio in _worker_pipeline(text, voice=voice)]
    audio = np.concatenate(audio_chunks) if audio_chunks else np.zeros(0, dtype="float32")
    return idx, audio


def _synthesize_kokoro(text: str, voice: str, out_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    text_chunks = _split_text_for_parallel(text, TTS_CHUNKS)
    print(f"    ...splitting into {len(text_chunks)} chunks across {TTS_WORKERS} workers")

    results: dict[int, "np.ndarray"] = {}
    with ProcessPoolExecutor(
        max_workers=TTS_WORKERS,
        initializer=_init_kokoro_worker,
        initargs=(KOKORO_WORKER_THREADS,),
    ) as executor:
        futures = {
            executor.submit(_synthesize_chunk_worker, (i, chunk, voice)): i
            for i, chunk in enumerate(text_chunks)
        }
        done = 0
        for future in as_completed(futures):
            idx, audio = future.result()
            results[idx] = audio
            done += 1
            print(f"    ...chunk {done}/{len(text_chunks)} synthesized")

    full_audio = np.concatenate([results[i] for i in range(len(text_chunks))])
    sf.write(str(out_path), full_audio, 24000)


def _synthesize_ai33(text: str, voice_id: str, out_path: Path) -> None:
    """Cloud TTS via ai33.pro (OpenSpeaker) — proxies Edge/ElevenLabs/Minimax/
    etc. voices behind one API. Replaces local Kokoro as the default: Kokoro's
    6-parallel-worker approach kept crashing with BrokenProcessPool whenever
    anything else touched the machine's RAM at the same time (see build log)
    — a cloud call sidesteps that entirely since nothing heavy runs locally."""
    import time

    import requests

    api_key = env("AI33_API_KEY", "")
    if not api_key:
        raise RuntimeError("AI33_API_KEY not set in .env")

    headers = {"xi-api-key": api_key}
    # The task-creation request itself can hit the same transient
    # timeouts/5xx as polling (see build log: a real production run died
    # here with an unhandled ReadTimeout during an ai33.pro slow spell) —
    # retry it the same way the polling loop already does, instead of only
    # protecting the poll step.
    max_create_retries = 5
    create_retry_wait = 10
    resp = None
    for attempt in range(1, max_create_retries + 1):
        try:
            resp = requests.post(
                "https://api.ai33.pro/v3/text-to-speech",
                headers=headers,
                # 0.95 rather than full speed (1) — slightly slower reads more
                # natural for long-form narration and avoids TTS artifacts at
                # full speed (see build log).
                data={"text": text, "voice_id": voice_id, "speed": "0.95", "with_transcript": "false"},
                timeout=120,
            )
            resp.raise_for_status()
            break
        except (requests.exceptions.RequestException,) as e:
            if attempt == max_create_retries:
                raise
            print(f"    ...transient error starting ai33.pro task ({e}), retrying in {create_retry_wait}s ({attempt}/{max_create_retries})")
            time.sleep(create_retry_wait)
    task_id = resp.json()["task_id"]

    poll_interval = 5
    max_wait = 3600
    waited = 0
    poll_retries = 0
    max_poll_retries = 5
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        try:
            status_resp = requests.get(f"https://api.ai33.pro/v3/task/{task_id}", headers=headers, timeout=30)
            status_resp.raise_for_status()
            poll_retries = 0
        except requests.exceptions.RequestException as e:
            # transient server hiccups (503 etc.) shouldn't kill a task that
            # was otherwise progressing fine (see build log) — back off and
            # keep polling instead of failing outright
            poll_retries += 1
            if poll_retries > max_poll_retries:
                raise
            print(f"    ...transient error polling ai33.pro ({e}), retrying ({poll_retries}/{max_poll_retries})")
            continue
        data = status_resp.json()["data"]
        if data["status"] == "done":
            audio_url = data["metadata"]["audio_url"]
            audio_resp = requests.get(audio_url, timeout=120)
            audio_resp.raise_for_status()
            out_path.write_bytes(audio_resp.content)
            return
        if data["status"] == "failed":
            raise RuntimeError(f"ai33.pro TTS task failed: {data}")
        print(f"    ...ai33.pro synthesizing ({data.get('progress', 0)}%, {waited}s elapsed)")

    raise TimeoutError(f"ai33.pro TTS task {task_id} did not finish within {max_wait}s")


def _synthesize_elevenlabs(text: str, voice_id: str, out_path: Path) -> None:
    from elevenlabs.client import ElevenLabs

    api_key = env("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set in .env")
    client = ElevenLabs(api_key=api_key)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        model_id="eleven_multilingual_v2",
        text=text,
    )
    with open(out_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)


def generate_voiceover(story: dict, channel_id: str) -> Path:
    channel = load_channel(channel_id)
    out_dir = ROOT / "out" / channel_id
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", story["title"].lower()).strip("-")[:60]
    text = story["full_text"]

    if TTS_ENGINE == "ai33":
        voice_id = channel["tts_voice"].get("ai33_voice_id", "edge_en-US-AriaNeural")
        out_path = out_dir / f"{slug}_voiceover.mp3"
        print(f"[TTS] Synthesizing with ai33.pro (voice={voice_id})...")
        _synthesize_ai33(text, voice_id, out_path)
    elif TTS_ENGINE == "kokoro":
        voice = channel["tts_voice"]["kokoro"]
        out_path = out_dir / f"{slug}_voiceover.wav"
        print(f"[TTS] Synthesizing with Kokoro (voice={voice})...")
        _synthesize_kokoro(text, voice, out_path)
    elif TTS_ENGINE == "elevenlabs":
        voice_id = channel["tts_voice"]["elevenlabs_voice_id"]
        out_path = out_dir / f"{slug}_voiceover.mp3"
        print(f"[TTS] Synthesizing with ElevenLabs (voice_id={voice_id})...")
        _synthesize_elevenlabs(text, voice_id, out_path)
    else:
        raise ValueError(f"Unknown TTS_ENGINE: {TTS_ENGINE!r}")

    print(f"[TTS] Saved: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.tts <path-to-story.json>")
        sys.exit(1)
    story_path = Path(sys.argv[1])
    story = json.loads(story_path.read_text(encoding="utf-8"))
    generate_voiceover(story, story["channel_id"])
