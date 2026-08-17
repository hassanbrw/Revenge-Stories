"""Metadata generation — title/description/tags via Gemini.

Takes a generated story dict (from pipeline.story_generate) and produces
YouTube-ready metadata, informed by title/hook patterns observed in
competitor research for this niche (see README/build log).
"""

import json
import re
import sys
from pathlib import Path

from pipeline.config import ROOT, load_channel, load_prompt
from pipeline.llm_client import call_llm, extract_json

# Reused on every video, matching the real niche's proven pattern of a
# fixed 14-tag list rather than story-specific tags (see build log) — the
# channel's own display name is appended as the 15th, channel-specific tag.
FIXED_TAGS = [
    "stories",
    "revenge stories",
    "family stories",
    "family revenge",
    "reddit family drama",
    "reddit revenge stories",
    "family revenge stories",
    "best revenge stories",
    "true story revenge",
    "family drama stories",
    "reddit family drama stories",
    "reddit stories",
    "revenge reddit",
    "family drama",
]

# Identical on every video in this niche (cross-promo CTA + disclaimer) —
# generated once here rather than re-asked of the LLM every time, to
# guarantee consistency and avoid burning tokens on boilerplate.
DESCRIPTION_FOOTER = """
---
👇 New stories like this every week 👇
🔥 Subscribe to {channel_name} for more family revenge & boundary stories.

---
► ABOUT US
{channel_name} is a channel about family revenge stories, betrayal stories, and quiet boundaries that change everything.
#revenge #revengestories #familydrama #boundaries
───────
⚠️ Content Disclaimer

The stories presented on this channel are inspired by real-life experiences shared on public platforms such as Reddit and other online forums. These narratives are thoughtfully adapted and reimagined for storytelling purposes. Characters, situations, and settings may be fictional, altered, or generated using AI.

This content is created to deliver positive messages, emotional insight, and meaningful reflection, while making the stories more accessible and engaging for viewers. Any resemblance to real persons (living or deceased), names, or organizations is purely coincidental.

We do not claim to represent real individuals or specific companies. Viewer discretion is advised. We encourage respectful discussion and thank you for supporting our creative work.
""".strip()


def generate_metadata(story: dict) -> dict:
    channel = load_channel(story.get("channel_id", "channel-a"))
    channel_name = channel.get("display_name", "this channel")

    template = load_prompt("metadata_prompt")
    excerpt = " ".join(story["full_text"].split()[:1000])
    prompt = (
        template.replace("{{TITLE}}", story["title"])
        .replace("{{PROTAGONIST_NAME}}", story.get("protagonist_name", "the narrator"))
        .replace("{{STORY_EXCERPT}}", excerpt)
    )
    raw = call_llm(prompt, temperature=0.8)
    result = extract_json(raw)
    for key in ("titles", "description_body"):
        if key not in result:
            raise ValueError(f"Metadata response missing '{key}'. Raw response:\n{raw[:800]}")

    footer = DESCRIPTION_FOOTER.format(channel_name=channel_name)
    description = f"{story['title']}\n{result['description_body']}\n\n{footer}"

    return {
        "titles": result["titles"],
        "description": description,
        "tags": FIXED_TAGS + [channel_name],
    }


def save_metadata(metadata: dict, story: dict, channel_id: str) -> Path:
    out_dir = ROOT / "out" / channel_id
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", story["title"].lower()).strip("-")[:60]
    path = out_dir / f"{slug}_metadata.json"
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.metadata_gen <path-to-story.json>")
        sys.exit(1)
    story_path = Path(sys.argv[1])
    story = json.loads(story_path.read_text(encoding="utf-8"))
    metadata = generate_metadata(story)
    out_path = save_metadata(metadata, story, story["channel_id"])
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}")
