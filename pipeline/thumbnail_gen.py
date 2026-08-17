"""Thumbnail generation — Gemini-generated 50-60 word hook text, rendered as
a single PNG via the Remotion "Thumbnail" composition (npx remotion still).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline.config import ROOT, load_prompt
from pipeline.llm_client import call_llm
from pipeline.remotion_utils import REMOTION_DIR, stage_assets


def generate_thumbnail_hook(story: dict) -> str:
    template = load_prompt("thumbnail_prompt")
    excerpt = " ".join(story["full_text"].split()[:1000])
    prompt = (
        template.replace("{{TITLE}}", story["title"])
        .replace("{{PROTAGONIST_NAME}}", story.get("protagonist_name", "the narrator"))
        .replace("{{STORY_EXCERPT}}", excerpt)
    )
    text = call_llm(prompt, temperature=0.9).strip().strip('"')
    # DeepSeek (unlike Gemini) sometimes adds markdown emphasis around the
    # closing punch line — the Thumbnail composition renders plain text, so
    # literal *asterisks*/_underscores_ would otherwise show up on-screen.
    return re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text)


def render_thumbnail(photo: Path, hook_text: str, out_path: Path) -> Path:
    asset_dir, names = stage_assets(photo)
    props = {
        "photoSrc": names[Path(photo)],
        "hookText": hook_text,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f)
        props_path = f.name

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = f'npx remotion still src/index.ts Thumbnail "{out_path}" --props="{props_path}"'
    print(f"[Thumbnail] Rendering still...")
    env = {**os.environ, "REMOTION_ASSET_DIR": asset_dir}
    subprocess.run(cmd, cwd=REMOTION_DIR, check=True, shell=True, env=env)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.thumbnail_gen <story.json> <photo> [out.png]")
        sys.exit(1)
    story = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    photo = Path(sys.argv[2])
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "out" / story["channel_id"] / "thumbnail.png"

    print("[Thumbnail] Generating hook text...")
    hook_text = generate_thumbnail_hook(story)
    print(f"    -> {hook_text}")

    render_thumbnail(photo, hook_text, out_path)
    print(f"\nSaved: {out_path}")
