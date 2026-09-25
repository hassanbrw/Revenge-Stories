"""Court-case video assembler — the "corrupt cop / courtroom justice" niche.

Turns a per-video MANIFEST (JSON) into a finished MP4 by wiring together the
three tools that already live on the operator's machine:

  - yt-dlp   -> download just the needed section of each real source clip
  - ai33.pro -> generate the narrator voiceover (reuses pipeline/tts.py client)
  - ffmpeg   -> normalise every segment to one format and concatenate them

Design note: unlike the family-drama pipeline (which writes a 100% original
AI story), this format is CLIP-DRIVEN — real 911 / bodycam / CCTV / court
footage is the content, and narration only bridges between clips. So the unit
of work here is a *segment list*, not a story. Each segment is either:

  - "clip"      : a real source video (YouTube URL + in/out), plays with its
                  own audio; an optional narrator cut-in ("vo") is mixed over
                  it while the clip audio is ducked.
  - "narration" : no footage exists for this beat (see edit sheet BRIDGE rows)
                  -> a still/b-roll image under narrator VO, with burned-in text.

This script is meant to run on the OPERATOR'S PC where yt-dlp + ffmpeg +
AI33_API_KEY are available. It intentionally shells out to yt-dlp/ffmpeg
rather than importing heavy libs, so it stays dependency-light.

Usage:
    python -m pipeline.court_case config/court_cases/guyger.json
    python -m pipeline.court_case config/court_cases/guyger.json --dry-run
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pipeline.config import ROOT, env
from pipeline.tts import _synthesize_ai33

# 1280x720 @ 30fps matches the repo's existing stock-footage target resolution
# (see README stage 7) so downloaded clips, stills and the final render all
# share one geometry and concat can copy streams instead of re-scaling twice.
DEFAULT_RES = "1280x720"
DEFAULT_FPS = 30
DEFAULT_VOICE = "edge_en-US-GuyNeural"  # ai33 voice id; override per-manifest
# A font that ships on most Linux boxes; override with COURT_FONT in .env if
# ffmpeg's drawtext can't find it (drawtext fails hard on a missing font).
FONT = env("COURT_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, streaming failures with the actual stderr so a broken
    yt-dlp/ffmpeg invocation is debuggable instead of a bare exit code."""
    print("  $", " ".join(str(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise RuntimeError(f"command failed ({proc.returncode}): {cmd[0]}")
    return proc


def _probe_duration(path: Path) -> float:
    """Seconds of an audio/video file, via ffprobe — needed so a narration
    still is held exactly as long as its voiceover, no more, no less."""
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]).stdout.strip()
    return float(out)


def _esc(text: str) -> str:
    """Escape text for ffmpeg drawtext (colons, quotes, backslashes all bite)."""
    return (
        text.replace("\\", "\\\\").replace(":", "\\:")
        .replace("'", "’").replace("%", "\\%")
    )


def _drawtext(text: str, res: str) -> str:
    """A lower-third style burned caption. Kept as one reusable filter so every
    segment's on-screen text looks identical (channel consistency)."""
    w = res.split("x")[0]
    return (
        f"drawtext=fontfile='{FONT}':text='{_esc(text)}':"
        f"fontcolor=white:fontsize=42:box=1:boxcolor=black@0.6:boxborderw=18:"
        f"x=(w-text_w)/2:y=h-160:line_spacing=8"
    )


def _norm_filters(res: str, fps: int) -> str:
    """Scale+pad any input to the target canvas without distortion (letterbox
    if aspect differs) so mismatched source clips concat cleanly."""
    w, h = res.split("x")
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}"
    )


def _seg_path(work: Path, idx: int) -> Path:
    # mpegts intermediates concat losslessly via the concat demuxer even across
    # separately-encoded segments, which plain mp4 concat can't do reliably.
    return work / f"seg_{idx:03d}.ts"


def build_clip_segment(seg, idx, work, res, fps, voice_id):
    """clip: download the in/out section with yt-dlp, normalise, optionally mix
    a ducked narrator cut-in, burn any on-screen text, emit an .ts segment."""
    raw = work / f"raw_{idx:03d}.mp4"
    # --download-sections grabs ONLY the needed window instead of the whole
    # (often hour-long) source video -> far less bandwidth and disk.
    section = f"*{seg['start']}-{seg['end']}"
    _run([
        "yt-dlp", "-f", "bv*+ba/b", "--download-sections", section,
        "--force-keyframes-at-cuts", "-o", str(raw), seg["source"],
    ])

    vf = _norm_filters(res, fps)
    if seg.get("text"):
        vf += "," + _drawtext(seg["text"], res)

    out = _seg_path(work, idx)
    if seg.get("vo"):
        # Narrator cut-in: synth VO, duck the clip's own audio under it so the
        # narration is intelligible, then mix. clip_volume defaults low (0.2)
        # because raw court audio is usually loud/echoey.
        vo_mp3 = work / f"vo_{idx:03d}.mp3"
        print(f"[VO] ai33 cut-in for segment {seg.get('id', idx)}")
        _synthesize_ai33(seg["vo"], voice_id, vo_mp3)
        cv = float(seg.get("clip_volume", 0.2))
        _run([
            "ffmpeg", "-y", "-i", str(raw), "-i", str(vo_mp3),
            "-filter_complex",
            f"[0:v]{vf}[v];"
            f"[0:a]volume={cv}[a0];[1:a]volume=1.8[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-f", "mpegts", str(out),
        ])
    else:
        _run([
            "ffmpeg", "-y", "-i", str(raw),
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-f", "mpegts", str(out),
        ])
    return out


def build_narration_segment(seg, idx, work, res, fps, voice_id):
    """narration (BRIDGE): synth VO, hold a still/b-roll (or black) for exactly
    the VO length, burn on-screen text, emit an .ts segment."""
    vo_mp3 = work / f"vo_{idx:03d}.mp3"
    print(f"[VO] ai33 narration for segment {seg.get('id', idx)}")
    _synthesize_ai33(seg["vo"], voice_id, vo_mp3)
    dur = _probe_duration(vo_mp3)

    vf = _norm_filters(res, fps)
    if seg.get("text"):
        vf += "," + _drawtext(seg["text"], res)

    out = _seg_path(work, idx)
    broll = seg.get("broll")
    if broll:
        # Loop the still for the VO's duration (Ken-Burns-free; add zoompan
        # later if wanted). -t bounds it to the audio length.
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", str((ROOT / broll)),
            "-i", str(vo_mp3), "-t", f"{dur:.3f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p",
            "-f", "mpegts", str(out),
        ])
    else:
        w, h = res.split("x")
        _run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=black:s={w}x{h}:r={fps}", "-i", str(vo_mp3),
            "-t", f"{dur:.3f}", "-vf", (vf if seg.get("text") else "null"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p",
            "-f", "mpegts", str(out),
        ])
    return out


def assemble(manifest_path: str, dry_run: bool = False) -> Path:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    res = manifest.get("resolution", DEFAULT_RES)
    fps = int(manifest.get("fps", DEFAULT_FPS))
    voice_id = manifest.get("voice_id", DEFAULT_VOICE)
    segments = manifest["segments"]
    output = ROOT / manifest.get("output", "out/court_case.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Manifest: {manifest.get('title', '(untitled)')}  |  {len(segments)} segments")
    print(f"Voice: {voice_id}  |  {res}@{fps}  ->  {output}")
    if dry_run:
        for i, s in enumerate(segments):
            kind = s["type"]
            extra = s.get("source", "") if kind == "clip" else (s.get("broll", "black"))
            print(f"  [{i:03d}] {kind:9} id={s.get('id','?'):<5} {extra}")
        print("dry-run only — nothing downloaded or rendered.")
        return output

    work = output.parent / (output.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)

    ts_files = []
    for i, seg in enumerate(segments):
        print(f"\n=== segment {i+1}/{len(segments)} (id {seg.get('id','?')}, {seg['type']}) ===")
        if seg["type"] == "clip":
            ts_files.append(build_clip_segment(seg, i, work, res, fps, voice_id))
        elif seg["type"] == "narration":
            ts_files.append(build_narration_segment(seg, i, work, res, fps, voice_id))
        else:
            raise ValueError(f"segment {i}: unknown type {seg['type']!r}")

    # concat demuxer over mpegts intermediates — lossless join, no second
    # re-encode of the whole timeline.
    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in ts_files), encoding="utf-8")
    print(f"\n=== concatenating {len(ts_files)} segments -> {output} ===")
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-bsf:a", "aac_adtstoasc", str(output),
    ], cwd=str(work))
    print(f"\nDONE -> {output}")
    return output


def main():
    ap = argparse.ArgumentParser(description="Assemble a court-case video from a manifest.")
    ap.add_argument("manifest", help="path to the manifest JSON")
    ap.add_argument("--dry-run", action="store_true", help="validate + print the plan, no download/render")
    args = ap.parse_args()
    assemble(args.manifest, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
