"""Stock footage — auto-fetch nature/aerial/sea background videos via
Pexels and/or Pixabay (both free for commercial use, no attribution
required for content without identifiable people, which nature/aerial/sea
footage is). Downloads build a local library in assets/stock/, reused and
rotated across videos, logged to data/stock_footage_log.csv (source URL +
creator name) for Content ID record-keeping.
"""

import csv
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.config import ROOT, env

# Target render resolution — fetching/transcoding source footage down to this
# avoids Remotion having to decode full 4K source frames on every render
# frame, which was the dominant cost in a long-video render (see build log).
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720

PEXELS_API_KEY = env("PEXELS_API_KEY", "")
PIXABAY_API_KEY = env("PIXABAY_API_KEY", "")

STOCK_DIR = ROOT / "assets" / "stock"
LOG_PATH = ROOT / "data" / "stock_footage_log.csv"
LOG_FIELDS = ["filename", "source", "source_url", "creator_name", "creator_url", "query", "downloaded_at"]

SEARCH_QUERIES = [
    "aerial ocean waves",
    "drone forest nature",
    "misty mountains aerial",
    "aerial coastline sunset",
    "calm sea aerial view",
    "aerial river forest",
    "clouds timelapse nature",
    "aerial waterfall nature",
]


def _log_download(row: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def search_pexels(query: str, per_page: int = 5) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "per_page": per_page, "orientation": "landscape"},
        timeout=30,
    )
    resp.raise_for_status()
    results = []
    for video in resp.json().get("videos", []):
        files = [f for f in video["video_files"] if f.get("file_type") == "video/mp4"]
        files.sort(key=lambda f: abs((f.get("width") or 0) - TARGET_WIDTH))
        if not files:
            continue
        results.append(
            {
                "source": "pexels",
                "download_url": files[0]["link"],
                "source_url": video["url"],
                "creator_name": video["user"]["name"],
                "creator_url": video["user"]["url"],
                "id": video["id"],
            }
        )
    return results


def search_pixabay(query: str, per_page: int = 5) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query, "per_page": per_page, "video_type": "film"},
        timeout=30,
    )
    resp.raise_for_status()
    results = []
    for hit in resp.json().get("hits", []):
        # "small" is Pixabay's ~720p tier — matches our render resolution and
        # avoids downloading/decoding a 4K "large" source we'd only shrink.
        video_tiers = hit.get("videos", {})
        file_info = video_tiers.get("small") or video_tiers.get("medium") or video_tiers.get("large")
        if not file_info:
            continue
        results.append(
            {
                "source": "pixabay",
                "download_url": file_info["url"],
                "source_url": hit["pageURL"],
                "creator_name": hit["user"],
                "creator_url": f"https://pixabay.com/users/{hit['user']}-{hit['user_id']}/",
                "id": hit["id"],
            }
        )
    return results


def _downscale_if_needed(path: Path) -> None:
    """Shrink the clip to TARGET_WIDTH if the source came back larger —
    Remotion decodes the *source* resolution on every render frame
    regardless of output size, so an oversized source directly slows
    rendering (see build log)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    width = int(probe.stdout.strip() or 0)
    if width <= TARGET_WIDTH:
        return
    tmp_path = path.with_suffix(".tmp.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-vf", f"scale={TARGET_WIDTH}:-2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    tmp_path.replace(path)


def download_clip(result: dict, query: str) -> Path:
    filename = f"{result['source']}_{result['id']}.mp4"
    dest = STOCK_DIR / filename
    if not dest.exists():
        resp = requests.get(result["download_url"], stream=True, timeout=60)
        resp.raise_for_status()
        STOCK_DIR.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        _downscale_if_needed(dest)
        _log_download(
            {
                "filename": filename,
                "source": result["source"],
                "source_url": result["source_url"],
                "creator_name": result["creator_name"],
                "creator_url": result["creator_url"],
                "query": query,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return dest


def fetch_new_clips(count: int = 3) -> list[Path]:
    """Search a rotation of nature/aerial/sea queries across both providers
    and download `count` new clips — queries repeat (with a fresh random
    pick from each search's results) once `count` exceeds the number of
    distinct queries, so this can fetch as many clips as a video needs, not
    just up to len(SEARCH_QUERIES) (see build log)."""
    downloaded = []
    for _ in range(count):
        query = random.choice(SEARCH_QUERIES)
        results = search_pexels(query) + search_pixabay(query)
        if not results:
            print(f"    (no results for {query!r} — check PEXELS_API_KEY / PIXABAY_API_KEY)")
            continue
        choice = random.choice(results)
        print(f"[Stock] Downloading {choice['source']} clip for {query!r} (by {choice['creator_name']})")
        downloaded.append(download_clip(choice, query))
    return downloaded


MIN_CLIP_COUNT = 6
MAX_CLIP_COUNT = 20
MINUTES_PER_CLIP = 4  # roughly one new unique clip added to rotation per 4min of video


def _target_clip_count(duration_seconds: float | None) -> int:
    if not duration_seconds:
        return 12  # no duration known (e.g. direct/manual call) — reasonable default
    minutes = duration_seconds / 60
    return min(MAX_CLIP_COUNT, max(MIN_CLIP_COUNT, round(minutes / MINUTES_PER_CLIP)))


def get_background_clip(min_library_size: int = 12) -> Path:
    """Return a background clip path, rotating through the local library.
    Fetches new clips if the library hasn't reached min_library_size yet."""
    return get_background_clips(min_count=min_library_size)[0]


def get_background_clips(duration_seconds: float | None = None, min_count: int | None = None) -> list[Path]:
    """Fetches a brand-new set of clips every call, sized to the video's own
    length — a short video doesn't need 15 unique clips, and a 90min one
    shouldn't feel like it's repeating after 6 (see build log). Explicitly
    NOT reusing the local library between videos — different videos should
    not end up sharing the same background footage (see build log)."""
    target = min_count if min_count is not None else _target_clip_count(duration_seconds)
    print(f"    ...fetching {target} fresh background clips (not reusing prior videos' footage)")
    fresh = fetch_new_clips(count=target)
    if not fresh:
        raise RuntimeError("No stock footage available — check PEXELS_API_KEY / PIXABAY_API_KEY in .env")
    random.shuffle(fresh)
    return fresh


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    paths = fetch_new_clips(count)
    print(f"\nDownloaded {len(paths)} clips:")
    for p in paths:
        print(f"  {p}")
