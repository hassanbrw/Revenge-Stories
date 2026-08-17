"""Re-render the 2 already-produced videos with the new fixes applied
(centered photo, multi-clip background, Poppins captions, League Spartan
thumbnail) — reuses existing story/voiceover/captions, no new TTS or
whisper work needed.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\usa\New folder")

from pipeline.ffmpeg_render import render_main_video_ffmpeg
from pipeline.photo_gen import process_photo
from pipeline.stock_footage import get_background_clips
from pipeline.thumbnail_gen import generate_thumbnail_hook, render_thumbnail

ROOT = Path(r"C:\Users\usa\New folder")
OUT_DIR = ROOT / "out" / "channel-a"
PROTAGONIST_PHOTO = Path(r"C:\Users\usa\Documents\protagonist.jpeg")
FPS = 30

JOBS = [
    {
        "slug": "my-husband-s-mistress-got-pregnant-then-she-texted-me-a-ques",
        "story_json": "20260802-064507_my-husband-s-mistress-got-pregnant-then-she-texted-me-a-ques_story.json",
    },
    {
        "slug": "my-husband-toasted-our-marriage-at-dinner-i-toasted-back-wit",
        "story_json": "20260802-053313_my-husband-toasted-our-marriage-at-dinner-i-toasted-back-wit_story.json",
    },
]

for job in JOBS:
    slug = job["slug"]
    print(f"\n{'='*60}\nRe-rendering: {slug}\n{'='*60}")
    t0 = time.time()

    story = json.loads((OUT_DIR / job["story_json"]).read_text(encoding="utf-8"))
    captions = json.loads((OUT_DIR / f"{slug}_captions.json").read_text(encoding="utf-8"))
    audio_path = OUT_DIR / f"{slug}_voiceover.wav"
    metadata = json.loads((OUT_DIR / f"{slug}_metadata.json").read_text(encoding="utf-8"))
    intro_hook = metadata["titles"][0]

    print("[1/4] Regenerating photo cutout (tight-crop fix)")
    photos = process_photo(PROTAGONIST_PHOTO, "channel-a", slug)

    print("[2/4] Selecting background footage")
    duration_seconds = max(c["endFrame"] for c in captions) / FPS
    background_videos = get_background_clips(duration_seconds)
    print(f"    -> {len(background_videos)} clips ({duration_seconds/60:.0f}min video)")

    print("[3/4] Rendering main video")
    video_path = render_main_video_ffmpeg(
        background_videos, photos["cutout"], audio_path, captions, intro_hook,
        OUT_DIR / f"{slug}_video.mp4",
    )
    print(f"    -> {video_path}")

    print("[4/4] Regenerating thumbnail")
    hook_text = generate_thumbnail_hook(story)
    print(f"    -> hook: {hook_text}")
    thumbnail_path = render_thumbnail(photos["original"], hook_text, OUT_DIR / f"{slug}_thumbnail.png")
    print(f"    -> {thumbnail_path}")

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")

print("\n\nALL RERENDERS DONE")
