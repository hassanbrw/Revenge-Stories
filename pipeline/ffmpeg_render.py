"""Main-video rendering via a direct FFmpeg composition — replaces the
Remotion/headless-Chrome renderer for the main video. Remotion rendered this
same composition by screenshotting a browser page once per frame (~22hrs for
a 60min video); compositing it natively in FFmpeg instead (loop, overlay,
subtitles, showfreqs — the same native approach a desktop editor like CapCut
uses) renders the identical layout in ~20-25min, on the same hardware, for
free (see build log benchmark: 3min sample rendered in 68s).

Thumbnail generation stays on Remotion — it's a single still frame, so
render speed was never a factor there.
"""

import json
import subprocess
import tempfile
import textwrap
from pathlib import Path

from pipeline.config import ROOT, env

FPS = 30
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
HOOK_DURATION_SECONDS = 2.5
HOOK_DURATION_FRAMES = int(HOOK_DURATION_SECONDS * FPS)
HOOK_FONT_SIZE = 60
# 0 = let ffmpeg auto-detect thread count; batch.py overrides this
# per-process when multiple videos render concurrently, so they split cores
# instead of each defaulting to all of them.
FFMPEG_THREADS = int(env("FFMPEG_THREADS", "0"))
# Rough bold-Arial average glyph width heuristic, used to wrap the hook text
# to the same ~960px safe width MainVideo.tsx used via CSS padding — drawtext
# has no browser-style auto-wrap, so long hooks would otherwise run off-frame.
HOOK_CHARS_PER_LINE = int((VIDEO_WIDTH - 320) / (HOOK_FONT_SIZE * 0.58))

GRADIENT_PATH = ROOT / "assets" / "gradient_overlay.png"
FONTS_DIR = ROOT / "assets" / "fonts"
NORMALIZED_STOCK_DIR = ROOT / "assets" / "stock" / "normalized"


def _ensure_gradient() -> Path:
    """Left-side dark gradient overlay, matching MainVideo.tsx's
    linear-gradient(to right, rgba(0,0,0,.35) 0%, rgba(0,0,0,.05) 45%, rgba(0,0,0,.2) 100%).
    Generated once and reused across every render — identical every time."""
    if GRADIENT_PATH.exists():
        return GRADIENT_PATH
    from PIL import Image

    stops = [(0.0, 0.35), (0.45, 0.05), (1.0, 0.20)]
    grad = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT))
    for x in range(VIDEO_WIDTH):
        t = x / (VIDEO_WIDTH - 1)
        alpha = stops[-1][1]
        for i in range(len(stops) - 1):
            t0, a0 = stops[i]
            t1, a1 = stops[i + 1]
            if t0 <= t <= t1:
                frac = (t - t0) / (t1 - t0) if t1 > t0 else 0
                alpha = a0 + (a1 - a0) * frac
                break
        col = (0, 0, 0, int(alpha * 255))
        for y in range(VIDEO_HEIGHT):
            grad.putpixel((x, y), col)
    GRADIENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    grad.save(GRADIENT_PATH)
    return GRADIENT_PATH


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _normalize_clip(path: Path) -> Path:
    """Force every stock clip to identical resolution/fps/codec (cached per
    source file) so the concat demuxer can stitch them with a fast stream
    copy — mixed-aspect-ratio sources would otherwise break a raw concat."""
    NORMALIZED_STOCK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = NORMALIZED_STOCK_DIR / path.name
    if out_path.exists():
        return out_path
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={FPS},setsar=1",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    return out_path


def _build_background(background_videos: list, target_seconds: float, out_path: Path) -> Path:
    """Cycles through the clip library, cutting to the next clip whenever
    one ends, until the concatenated stream covers the full video — a
    single clip previously looped for the entire runtime (see build log)."""
    normalized = [_normalize_clip(Path(p)) for p in background_videos]
    durations = [_probe_duration(p) for p in normalized]

    list_path = out_path.with_suffix(".txt")
    lines, total, i = [], 0.0, 0
    while total < target_seconds:
        idx = i % len(normalized)
        posix = str(normalized[idx].resolve()).replace("\\", "/")
        lines.append(f"file '{posix}'")
        total += durations[idx]
        i += 1
    list_path.write_text("\n".join(lines), encoding="utf-8")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)],
        check=True, capture_output=True,
    )
    return out_path


def _frame_to_ts(frame: int) -> str:
    total_seconds = frame / FPS
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _sanitize_ass_text(text: str) -> str:
    # Curly braces trigger ASS override-tag parsing; captions are
    # transcribed speech and shouldn't legitimately contain them.
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def _write_ass(captions: list, out_path: Path) -> None:
    """Caption style matches Captions.tsx: white bold text on a black
    ~75%-opaque rounded box, positioned right-of-center, vertically centered.
    Captions that fall entirely within the intro-hook window are dropped, and
    ones straddling it are clipped to start after it, so the two text
    overlays never sit on top of each other (see build log)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Poppins,46,&H00FFFFFF,&H000000FF,&H00000000,&H40000000,1,0,0,0,100,100,0,0,3,14,0,5,420,60,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in captions:
        start_frame = c["startFrame"]
        end_frame = c["endFrame"]
        if end_frame <= HOOK_DURATION_FRAMES:
            continue
        start_frame = max(start_frame, HOOK_DURATION_FRAMES)
        start = _frame_to_ts(start_frame)
        end = _frame_to_ts(end_frame)
        text = _sanitize_ass_text(c["text"])
        lines.append(f"Dialogue: 0,{start},{end},Caption,,0,0,0,,{text}\n")
    out_path.write_text("".join(lines), encoding="utf-8")


def _wrap_hook_text(text: str) -> str:
    return "\n".join(textwrap.wrap(text, width=HOOK_CHARS_PER_LINE))


def _ass_filter_arg(ass_path: Path) -> str:
    # ffmpeg filter option values treat ':' as a separator, so a Windows
    # drive letter must be escaped; forward slashes avoid backslash issues.
    posix = str(ass_path).replace("\\", "/")
    return posix.replace(":", "\\:")


def render_main_video_ffmpeg(
    background_videos,
    photo_cutout: Path,
    audio_path: Path,
    captions: list,
    intro_hook: str,
    out_path: Path,
) -> Path:
    # Accept either one clip (back-compat) or a list to cycle through.
    if isinstance(background_videos, (str, Path)):
        background_videos = [background_videos]
    photo_cutout = Path(photo_cutout)
    audio_path = Path(audio_path)
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gradient_path = _ensure_gradient()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        ass_path = tmp_dir / "captions.ass"
        hook_path = tmp_dir / "hook.txt"
        _write_ass(captions, ass_path)
        hook_path.write_text(_wrap_hook_text(intro_hook), encoding="utf-8")

        duration_frames = max(c["endFrame"] for c in captions) if captions else 0
        duration_seconds = duration_frames / FPS

        print(f"    ...cycling {len(background_videos)} background clips to cover the full runtime")
        bg_path = _build_background(background_videos, duration_seconds, tmp_dir / "background.mp4")

        ass_arg = _ass_filter_arg(ass_path)
        hook_arg = _ass_filter_arg(hook_path)
        fonts_arg = _ass_filter_arg(FONTS_DIR)
        # bundled font, not a system path — the previous hardcoded
        # C:/Windows/Fonts reference only worked on Windows and broke
        # rendering entirely on the Linux pod (see build log)
        hook_font_arg = _ass_filter_arg(FONTS_DIR / "Poppins-Bold.ttf")
        gradient_arg = str(gradient_path)

        filter_complex = (
            f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1[bg];"
            f"[bg][2:v]overlay=0:0[bgg];"
            # vertically centered on the left, not bottom-anchored. Photo is
            # used as-is (not flipped) here — the thumbnail is the one that
            # gets flipped instead, per explicit user correction: video and
            # thumbnail need opposite facing directions, and this was the
            # wrong one flipped (see build log).
            f"[1:v]scale=-1:590[photo_s];"
            f"[bgg][photo_s]overlay=40:(main_h-overlay_h)/2[withphoto];"
            f"[withphoto]drawtext=textfile='{hook_arg}':fontcolor=white:fontsize={HOOK_FONT_SIZE}:line_spacing=8:"
            f"fontfile='{hook_font_arg}':x=(w-text_w)/2:y=(h-text_h)/2:"
            f"box=0:shadowcolor=black@0.8:shadowx=0:shadowy=3:"
            f"enable='lt(t,{HOOK_DURATION_SECONDS})'[withhook];"
            f"[withhook]subtitles='{ass_arg}':fontsdir='{fonts_arg}'[withcaps];"
            f"[3:a]showfreqs=s={VIDEO_WIDTH}x140:mode=bar:ascale=sqrt:colors=white:win_size=4096[wave];"
            f"[withcaps][wave]overlay=0:main_h-overlay_h[vout]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(bg_path),
            "-loop", "1", "-i", str(photo_cutout),
            "-loop", "1", "-i", gradient_arg,
            "-i", str(audio_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "3:a",
            "-t", f"{duration_seconds:.3f}",
            "-r", str(FPS),
            "-threads", str(FFMPEG_THREADS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac",
            str(out_path),
        ]
        print("[FFmpeg] Rendering main video natively (no browser)...")
        subprocess.run(cmd, check=True)

    return out_path
