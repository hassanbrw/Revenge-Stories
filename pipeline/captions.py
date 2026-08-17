"""Captioning — Whisper word-level timestamps grouped into phrase-chunk
captions matching the Remotion template's Captions.tsx shape:
{"text": str, "startFrame": int, "endFrame": int}.
"""

import json
import sys
from pathlib import Path

from pipeline.config import env

FPS = 30
# "base" + beam_size=1 + VAD transcribes this clean single-speaker TTS audio
# at near-identical accuracy to "small" defaults but ~14x faster (52.5min ->
# 3.8min on a 60min video — see build log); TTS audio has no background
# noise, so the larger model's extra robustness isn't buying anything here.
WHISPER_MODEL_SIZE = env("WHISPER_MODEL_SIZE", "base")
# 0 = let faster-whisper auto-detect; batch.py overrides this per-process
# when multiple videos render concurrently, so they split cores instead of
# each grabbing all of them.
WHISPER_CPU_THREADS = int(env("WHISPER_CPU_THREADS", "0"))
MAX_WORDS_PER_CAPTION = 10
MAX_CAPTION_SECONDS = 4.0
PAUSE_BREAK_SECONDS = 0.6

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            cpu_threads=WHISPER_CPU_THREADS,
        )
    return _model


def transcribe_words(audio_path: Path) -> list[dict]:
    """Return a flat list of {"word": str, "start": float, "end": float}."""
    model = _get_model()
    segments, _ = model.transcribe(
        str(audio_path), word_timestamps=True, beam_size=1, vad_filter=True
    )
    words = []
    for segment in segments:
        for w in segment.words:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    return words


def group_into_captions(words: list[dict]) -> list[dict]:
    """Group word-level timestamps into phrase-chunk captions (frame-based)."""
    captions = []
    current: list[dict] = []

    def flush():
        if not current:
            return
        text = " ".join(w["word"] for w in current)
        start_frame = round(current[0]["start"] * FPS)
        end_frame = round(current[-1]["end"] * FPS)
        captions.append({"text": text, "startFrame": start_frame, "endFrame": end_frame})

    prev_end = None
    for w in words:
        if current:
            gap = w["start"] - prev_end
            duration_so_far = w["end"] - current[0]["start"]
            ends_sentence = current[-1]["word"].endswith((".", "!", "?"))
            if (
                len(current) >= MAX_WORDS_PER_CAPTION
                or duration_so_far > MAX_CAPTION_SECONDS
                or gap > PAUSE_BREAK_SECONDS
                or ends_sentence
            ):
                flush()
                current = []
        current.append(w)
        prev_end = w["end"]
    flush()
    return captions


def merge_short_captions(captions: list[dict], min_frames: int = 12) -> list[dict]:
    """Fold stray trailing chunks (e.g. a lone word left after a sentence
    break) into the previous chunk — anything under ~0.4s flashes too fast
    to read on screen."""
    if not captions:
        return captions
    merged = [dict(captions[0])]
    for cap in captions[1:]:
        duration = cap["endFrame"] - cap["startFrame"]
        if duration < min_frames:
            merged[-1]["text"] = f"{merged[-1]['text']} {cap['text']}"
            merged[-1]["endFrame"] = cap["endFrame"]
        else:
            merged.append(dict(cap))
    return merged


def generate_captions(audio_path: Path) -> list[dict]:
    print(f"[Captions] Transcribing {audio_path.name} with Whisper ({WHISPER_MODEL_SIZE})...")
    words = transcribe_words(audio_path)
    print(f"    -> {len(words)} words transcribed")
    captions = merge_short_captions(group_into_captions(words))
    print(f"    -> {len(captions)} caption chunks")
    return captions


def save_captions(captions: list[dict], audio_path: Path) -> Path:
    out_path = audio_path.with_name(audio_path.stem.replace("_voiceover", "") + "_captions.json")
    out_path.write_text(json.dumps(captions, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.captions <path-to-voiceover-audio>")
        sys.exit(1)
    audio_path = Path(sys.argv[1])
    captions = generate_captions(audio_path)
    out_path = save_captions(captions, audio_path)
    print(f"\nSaved: {out_path}")
