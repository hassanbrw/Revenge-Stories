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


# Caption presets, matched to the reference channel's look (see build notes):
#   headline    - big bold ALL-CAPS, centred, dark box  (title beats)
#   lower_third - news banner, bottom-left               (default captions)
#   location    - small label on a RED box               (place pins e.g. "DALLAS, TX")
def _drawtext(text: str, res: str, style: str = "lower_third") -> str:
    """One reusable caption filter so every segment's on-screen text looks
    identical (channel consistency). `style` picks the preset."""
    if style == "headline":
        t = _esc(text.upper())
        return (
            f"drawtext=fontfile='{FONT}':text='{t}':fontcolor=white:fontsize=58:"
            f"box=1:boxcolor=black@0.72:boxborderw=26:x=(w-text_w)/2:y=(h-text_h)/2:"
            f"line_spacing=10"
        )
    if style == "location":
        return (
            f"drawtext=fontfile='{FONT}':text='{_esc(text.upper())}':fontcolor=white:"
            f"fontsize=34:box=1:boxcolor=red@0.85:boxborderw=14:x=70:y=h-150"
        )
    # lower_third (default)
    return (
        f"drawtext=fontfile='{FONT}':text='{_esc(text)}':fontcolor=white:fontsize=42:"
        f"box=1:boxcolor=black@0.62:boxborderw=18:x=70:y=h-160:line_spacing=8"
    )


def _norm_filters(res: str, fps: int, grade: str = "") -> str:
    """Scale+pad any input to the target canvas without distortion (letterbox
    if aspect differs) so mismatched source clips concat cleanly. `grade=bw`
    desaturates + lifts contrast to signal 'serious / past events', the way the
    reference channel treats courtroom and bodycam footage."""
    w, h = res.split("x")
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}"
    )
    if grade == "bw":
        vf += ",hue=s=0,eq=contrast=1.12:brightness=0.02"
    return vf


def _kenburns(res: str, fps: int, dur: float) -> str:
    """Slow constant zoom on a still — the reference channel puts this on almost
    every static image so nothing sits dead on screen. Pre-scale up first so the
    zoom stays smooth instead of jittering on integer pixel steps."""
    w, h = res.split("x")
    frames = max(1, int(dur * fps))
    return (
        f"scale=2560:-2,zoompan=z='min(zoom+0.0009,1.25)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},setsar=1"
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

    # grade:"bw" -> desaturate court/bodycam footage; text/style -> caption preset
    vf = _norm_filters(res, fps, grade=seg.get("grade", ""))
    if seg.get("text"):
        vf += "," + _drawtext(seg["text"], res, seg.get("style", "lower_third"))

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
    style = seg.get("style", "lower_third")

    out = _seg_path(work, idx)
    broll = seg.get("broll")
    # Fall back to a black hold if the still hasn't been added yet, so the whole
    # video can be rendered with clips first and images dropped in later,
    # instead of crashing on the first missing asset.
    if broll and not (ROOT / broll).exists():
        print(f"  [warn] broll not found ({broll}) -> black hold for this segment")
        broll = None
    if broll:
        # Ken Burns slow-zoom on the still for exactly the VO length, then
        # caption. Matches the reference channel (no static images sit dead).
        vf = _kenburns(res, fps, dur)
        if seg.get("text"):
            vf += "," + _drawtext(seg["text"], res, style)
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", str((ROOT / broll)),
            "-i", str(vo_mp3), "-t", f"{dur:.3f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p",
            "-f", "mpegts", str(out),
        ])
    else:
        # No still supplied -> hold black under the VO (still burn any caption).
        w, h = res.split("x")
        vf = _drawtext(seg["text"], res, style) if seg.get("text") else "null"
        _run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=black:s={w}x{h}:r={fps}", "-i", str(vo_mp3),
            "-t", f"{dur:.3f}", "-vf", vf,
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


def fetch_manifest(manifest_path: str) -> Path:
    """Resolve a manifest's queries via serper.dev: download a b-roll image for
    every narration beat that has an `image_query`, and fill each clip beat's
    `source` from its `query`. Writes a *.resolved.json* for you to review (set
    in/out timestamps, swap any wrong clip) before rendering — never renders
    blind off an auto-picked video."""
    from pipeline.sourcing import best_video_url, download_image

    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    img_dir = ROOT / "assets" / "court_case"

    for i, seg in enumerate(manifest["segments"]):
        sid = str(seg.get("id", i)).replace(".", "_")
        if seg["type"] == "narration" and seg.get("image_query"):
            if seg.get("broll") and (ROOT / seg["broll"]).exists():
                continue  # already have this still
            print(f"[img] segment {seg.get('id', i)}: {seg['image_query']!r}")
            out = download_image(seg["image_query"], img_dir / sid)
            if out:
                seg["broll"] = str(out.relative_to(ROOT))
        elif seg["type"] == "clip" and seg.get("query"):
            src = seg.get("source", "")
            if src and not src.startswith("TODO"):
                continue  # a real URL is already set — leave it
            print(f"[vid] segment {seg.get('id', i)}: {seg['query']!r}")
            url, candidates = best_video_url(seg["query"])
            seg["source"] = url or seg.get("source", "")
            # keep alternatives for human review; ignored by the renderer
            seg["_candidates"] = [
                {"title": c.get("title", ""), "link": c.get("link", "")}
                for c in candidates[:5]
            ]
            print(f"      -> {url or '(none found)'}")

    out_path = path.with_suffix(".resolved.json")
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResolved manifest written -> {out_path}")
    print("REVIEW IT: set each clip's start/end, swap any wrong clip (see _candidates), then render it.")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Assemble a court-case video from a manifest.")
    ap.add_argument("manifest", help="path to the manifest JSON")
    ap.add_argument("--dry-run", action="store_true", help="validate + print the plan, no download/render")
    ap.add_argument("--fetch", action="store_true", help="serper.dev: download b-roll images + resolve clip URLs, write a *.resolved.json for review")
    args = ap.parse_args()
    if args.fetch:
        fetch_manifest(args.manifest)
    else:
        assemble(args.manifest, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
