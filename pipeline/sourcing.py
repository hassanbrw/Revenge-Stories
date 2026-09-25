"""Auto-sourcing for the court-case pipeline via serper.dev (Google Search API).

Two jobs:
  - images  : find + download a b-roll still for a narration beat
  - videos  : find candidate YouTube URLs for a clip beat

serper.dev is a thin Google wrapper: POST the query, get structured results.
Needs SERPER_API_KEY in .env. Kept separate from court_case.py so the render
path stays usable even without a serper key (manual URLs still work).

IMPORTANT: auto-picking the "top" court video is a starting point, not gospel —
the top result is rarely trimmed to the exact moment you need. So the fetch step
writes a *resolved* manifest for you to review (and to set each clip's in/out)
before rendering, rather than rendering blind.
"""

import mimetypes
from pathlib import Path

from pipeline.config import ROOT, env

SERPER_SEARCH = "https://google.serper.dev/search"
SERPER_IMAGES = "https://google.serper.dev/images"
SERPER_VIDEOS = "https://google.serper.dev/videos"


def _key() -> str:
    k = env("SERPER_API_KEY", "")
    if not k:
        raise RuntimeError("SERPER_API_KEY not set in .env")
    return k


def search_web(query: str, n: int = 10) -> list[dict]:
    """Return up to n organic web results: [{title, snippet, link}, ...]. Used to
    gather REAL facts about a case so the brief is grounded in retrieved text
    rather than model memory."""
    import requests

    resp = requests.post(
        SERPER_SEARCH, headers={"X-API-KEY": _key(), "Content-Type": "application/json"},
        json={"q": query}, timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("organic", [])[:n]:
        out.append({
            "title": r.get("title", ""),
            "snippet": r.get("snippet", ""),
            "link": r.get("link", ""),
        })
    return out


def search_images(query: str, n: int = 10) -> list[dict]:
    """Return up to n image results: [{imageUrl, title, source}, ...]."""
    import requests

    resp = requests.post(
        SERPER_IMAGES, headers={"X-API-KEY": _key(), "Content-Type": "application/json"},
        json={"q": query}, timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("images", [])[:n]


def search_videos(query: str, n: int = 10) -> list[dict]:
    """Return up to n video results: [{title, link, ...}, ...]. `link` is usually
    a YouTube watch URL, which is exactly what yt-dlp wants."""
    import requests

    resp = requests.post(
        SERPER_VIDEOS, headers={"X-API-KEY": _key(), "Content-Type": "application/json"},
        json={"q": query}, timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("videos", [])[:n]


def download_image(query: str, dest: Path, min_bytes: int = 8000) -> Path | None:
    """Download the first usable image for `query` into `dest`. Walks the result
    list until one actually fetches as a real image (top hits 403 / hotlink-block
    often), so a single dead top result doesn't leave the beat empty."""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    for img in search_images(query, n=12):
        url = img.get("imageUrl")
        if not url:
            continue
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except requests.exceptions.RequestException:
            continue
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image/") or len(r.content) < min_bytes:
            continue
        ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        out = dest.with_suffix(ext)
        out.write_bytes(r.content)
        print(f"    image ok: {query!r} -> {out.relative_to(ROOT)} ({len(r.content)//1024}KB)")
        return out
    print(f"    [warn] no usable image for {query!r}")
    return None


def best_video_url(query: str) -> tuple[str, list[dict]]:
    """Return (top_youtube_url, all_candidates). Prefers a youtube.com/youtu.be
    link; falls back to the first result. Candidates are returned so the caller
    can surface alternatives for human review."""
    vids = search_videos(query, n=10)
    for v in vids:
        link = v.get("link", "")
        if "youtube.com" in link or "youtu.be" in link:
            return link, vids
    return (vids[0].get("link", "") if vids else ""), vids
