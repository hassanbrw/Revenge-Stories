"""Runs one or more videos through the full pipeline, each as its own
subprocess. Defaults to sequential (max_concurrent=1) — a real test with
max_concurrent=3 on this machine (6 cores / 15.7GB RAM) crashed 2 of 3 jobs
with BrokenProcessPool from memory exhaustion, and even the surviving job
ran 2-3x slower than a plain solo run on every stage (see build log). Until
concurrency is re-validated as an actual win, treat max_concurrent>1 as
experimental, not the recommended path.

When concurrency IS used, each subprocess divides the machine's cores by how
many videos are running at once (TTS_WORKERS / WHISPER_CPU_THREADS /
FFMPEG_THREADS), and KOKORO_WORKER_THREADS drops to 1 to avoid the
double-counted oversubscription that caused the crash (TTS_WORKERS workers
x KOKORO_WORKER_THREADS was silently 2x the intended per-job core budget).
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.config import ROOT

CPU_COUNT = os.cpu_count() or 4


def _run_one(title: str, photo_path: Path, channel_id: str, max_concurrent: int) -> dict:
    child_env = dict(os.environ)
    if max_concurrent > 1:
        per_job_cores = max(1, CPU_COUNT // max_concurrent)
        child_env.update({
            "TTS_WORKERS": str(per_job_cores),
            "WHISPER_CPU_THREADS": str(per_job_cores),
            "FFMPEG_THREADS": str(per_job_cores),
            "KOKORO_WORKER_THREADS": "1",
        })
    # max_concurrent == 1: no overrides — each video gets the solo defaults
    # (TTS_WORKERS=6, KOKORO_WORKER_THREADS=2, etc.), already proven fine.
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.orchestrate", title, str(photo_path), channel_id],
        cwd=str(ROOT),
        env=child_env,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - t0
    return {
        "title": title,
        "channel_id": channel_id,
        "ok": result.returncode == 0,
        "elapsed_seconds": elapsed,
        "stdout_tail": "\n".join(result.stdout.splitlines()[-15:]),
        "stderr_tail": "\n".join(result.stderr.splitlines()[-15:]) if result.returncode != 0 else "",
    }


def run_batch(jobs: list[dict], max_concurrent: int = 1) -> list[dict]:
    """jobs: list of {"title": str, "photo_path": str|Path, "channel_id": str}."""
    print(f"[Batch] {len(jobs)} video(s), up to {max_concurrent} running concurrently "
          f"({CPU_COUNT} logical cores, ~{max(1, CPU_COUNT // max_concurrent)} per video)")

    results = []
    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(
                _run_one, job["title"], Path(job["photo_path"]), job.get("channel_id", "channel-a"), max_concurrent
            ): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            res = future.result()
            results.append(res)
            status = "OK" if res["ok"] else "FAILED"
            print(f"[Batch] {status} ({res['elapsed_seconds']/60:.1f} min): {job['title']!r}")
            if not res["ok"]:
                print(f"    stderr tail:\n{res['stderr_tail']}")

    return results


if __name__ == "__main__":
    # Usage: python -m pipeline.batch <jobs.json> [max_concurrent]
    # jobs.json: [{"title": "...", "photo_path": "...", "channel_id": "channel-a"}, ...]
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.batch <jobs.json> [max_concurrent]")
        sys.exit(1)
    jobs_path = Path(sys.argv[1])
    max_concurrent = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

    t0 = time.time()
    results = run_batch(jobs, max_concurrent)
    elapsed = time.time() - t0
    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n[Batch] Done: {ok_count}/{len(jobs)} succeeded in {elapsed/60:.1f} min total")
