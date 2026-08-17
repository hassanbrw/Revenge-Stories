"""Orchestration — wires every pipeline stage together: a one-line title
(and a protagonist photo) in, a finished video + thumbnail + metadata file
out.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import soundfile as sf

from pipeline.captions import generate_captions
from pipeline.config import ROOT, env
from pipeline.ffmpeg_render import render_main_video_ffmpeg
from pipeline.lambda_render import render_main_video_lambda
from pipeline.metadata_gen import generate_metadata, save_metadata
from pipeline.photo_gen import process_photo
from pipeline.remotion_utils import REMOTION_DIR, stage_assets
from pipeline.stock_footage import get_background_clips
from pipeline.story_generate import generate_story, save_story
from pipeline.thumbnail_gen import generate_thumbnail_hook, render_thumbnail
from pipeline.title_gen import generate_title
from pipeline.tts import generate_voiceover

FPS = 30
# ffmpeg = native composition, no browser, ~58x faster than local Remotion
# rendering at effectively no cost (see build log); local/lambda kept as
# fallbacks.
RENDER_ENGINE = env("RENDER_ENGINE", "ffmpeg")


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def probe_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def render_main_video(
    background_videos: list,
    photo_cutout: Path,
    audio_path: Path,
    captions: list,
    intro_hook: str,
    out_path: Path,
) -> Path:
    """Dispatches to ffmpeg (free, fast, default), local Remotion (free,
    slow), or Lambda (paid, fast) rendering based on RENDER_ENGINE in .env.
    Only the ffmpeg engine cycles through multiple background clips; the
    Remotion/Lambda fallbacks still take a single looped clip."""
    if RENDER_ENGINE == "lambda":
        return render_main_video_lambda(
            background_videos[0], photo_cutout, audio_path, captions, intro_hook, out_path
        )
    if RENDER_ENGINE == "local":
        return render_main_video_local(
            background_videos[0], photo_cutout, audio_path, captions, intro_hook, out_path
        )
    return render_main_video_ffmpeg(
        background_videos, photo_cutout, audio_path, captions, intro_hook, out_path
    )


def render_main_video_local(
    background_video: Path,
    photo_cutout: Path,
    audio_path: Path,
    captions: list,
    intro_hook: str,
    out_path: Path,
) -> Path:
    audio_info = sf.info(str(audio_path))
    duration_frames = round(audio_info.frames / audio_info.samplerate * FPS)
    background_duration_frames = round(probe_duration_seconds(background_video) * FPS)

    asset_dir, names = stage_assets(background_video, photo_cutout, audio_path)
    props = {
        "backgroundVideoSrc": names[Path(background_video)],
        "backgroundVideoDurationInFrames": background_duration_frames,
        "photoSrc": names[Path(photo_cutout)],
        "audioSrc": names[Path(audio_path)],
        "captions": captions,
        "introHookText": intro_hook,
        "durationInFrames": duration_frames,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f)
        props_path = f.name

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = f'npx remotion render src/index.ts MainVideo "{out_path}" --props="{props_path}"'
    print("[Orchestrate] Rendering main video (can take a while for a long narration)...")
    env = {**os.environ, "REMOTION_ASSET_DIR": asset_dir}
    subprocess.run(cmd, cwd=REMOTION_DIR, check=True, shell=True, env=env)
    return out_path


def save_upload_package(metadata: dict, thumbnail_path: Path, slug: str, channel_id: str, video_path: Path | None = None) -> Path:
    """Moves the upload-relevant pieces (title/description/tags + thumbnail
    + the final video) into their own date-stamped folder, separate from the
    working out/ directory full of story/captions/audio files — makes it
    fast to find what's actually needed for uploading a given video. The
    video is *moved* (not copied) since it's the largest file by far and
    nothing downstream in the pipeline needs it left in out/ afterward."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    package_dir = ROOT / "ready_for_upload" / channel_id / f"{date_str}_{slug}"
    package_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"Date: {date_str}", "", "TITLE OPTIONS:"]
    lines += [f"  - {t}" for t in metadata.get("titles", [])]
    lines += ["", "DESCRIPTION:", metadata.get("description", ""), "", "TAGS:", ", ".join(metadata.get("tags", []))]
    (package_dir / "title_description.txt").write_text("\n".join(lines), encoding="utf-8")

    if video_path is not None:
        shutil.move(str(video_path), str(package_dir / "video.mp4"))

    shutil.copy(thumbnail_path, package_dir / "thumbnail.png")
    return package_dir


def run_pipeline(title: str | None, channel_id: str, photo_path: Path) -> dict:
    if title is None:
        print("[0/7] No title given — generating one from the micro-niche style guide")
        title = generate_title(channel_id)
        print(f"    -> {title!r}")

    slug = slugify(title)
    out_dir = ROOT / "out" / channel_id

    print("=" * 60)
    print(f"[1/7] Generating story: {title!r}")
    story = generate_story(title, channel_id)
    story_path = save_story(story, channel_id)
    print(f"    -> {story_path} ({story['total_word_count']} words)")

    print("[2/7] Generating metadata")
    metadata = generate_metadata(story)
    metadata_path = save_metadata(metadata, story, channel_id)
    print(f"    -> {metadata_path}")

    print("[3/7] Processing protagonist photo")
    photos = process_photo(photo_path, channel_id, slug)
    print(f"    -> original: {photos['original']}")
    print(f"    -> cutout:   {photos['cutout']}")

    print("[4/7] Generating voiceover")
    audio_path = generate_voiceover(story, channel_id)

    print("[5/7] Generating captions")
    captions = generate_captions(audio_path)
    captions_path = out_dir / f"{slug}_captions.json"
    captions_path.write_text(json.dumps(captions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    -> {captions_path}")

    print("[6/7] Selecting background footage")
    duration_seconds = (max(c["endFrame"] for c in captions) / FPS) if captions else None
    background_videos = get_background_clips(duration_seconds)
    print(f"    -> {len(background_videos)} clips in rotation ({duration_seconds/60:.0f}min video)")

    intro_hook = metadata["titles"][0]

    print("[7/7] Rendering final video")
    video_path = render_main_video(
        background_videos,
        photos["cutout"],
        audio_path,
        captions,
        intro_hook,
        out_dir / f"{slug}_video.mp4",
    )
    print(f"    -> {video_path}")

    print("[Thumbnail] Generating hook text + rendering still")
    hook_text = generate_thumbnail_hook(story)
    thumbnail_path = render_thumbnail(photos["original"], hook_text, out_dir / f"{slug}_thumbnail.png")
    print(f"    -> {thumbnail_path}")

    print("[Upload package] Moving video + copying title/description/thumbnail to ready_for_upload/")
    package_dir = save_upload_package(metadata, thumbnail_path, slug, channel_id, video_path)
    print(f"    -> {package_dir}")

    print("=" * 60)
    print("DONE — ready for manual upload:")
    print(f"  video:     {video_path}")
    print(f"  thumbnail: {thumbnail_path}")
    print(f"  metadata:  {metadata_path}")
    print(f"  package:   {package_dir}")

    return {
        "story": story_path,
        "metadata": metadata_path,
        "video": video_path,
        "thumbnail": thumbnail_path,
        "captions": captions_path,
        "package": package_dir,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('Usage: python -m pipeline.orchestrate "<title>|auto" <photo_path> [channel_id]')
        sys.exit(1)
    title_arg = sys.argv[1]
    photo_arg = Path(sys.argv[2])
    channel_arg = sys.argv[3] if len(sys.argv) > 3 else "channel-a"
    run_pipeline(None if title_arg == "auto" else title_arg, channel_arg, photo_arg)
