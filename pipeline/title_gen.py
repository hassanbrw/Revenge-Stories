"""Title generation — produces an original one-line premise strictly
matching the micro-niche patterns of the target channel (see
config/reference/calm_drama_stories_style_guide.md). Explicitly does NOT
invent new antagonist types or themes for "variety" — the niche must stay
consistent with real competitor data, not vary for its own sake (see build
log).
"""

import json
import sys
from pathlib import Path

from pipeline.config import ROOT, load_prompt
from pipeline.llm_client import call_llm, extract_json

EXAMPLE_TITLES = [
    "My Family Ordered $4,000 of Lobster — Then Told Me \"You're Paying\"",
    "My Family Disowned Me for Marrying a Black Man — 9 Years Later Mom Showed Up with Demands",
    "My Parents Sold My Inheritance Behind My Back — I Had the Last Paper",
    "My Parents Disowned Me at My 25th Birthday — My Birth Family Was Three Tables Away",
    "My Dad Mocked Me As 'Uneducated And Worthless' — Then I Told Him Who I Really Was",
    "My Parents Sold Grandma's House Behind Her Back — She'd Outsmarted Them Months Earlier",
    "My Parents Mocked Me For Being 'The Dumb One' — A $47M Check Proved Them Wrong",
    "My Mother-In-Law Said: \"LEAVING YOU WAS THE BEST DECISION MY SON EVER MADE\" — 5 Minutes Later...",
    "My Parents Cut Me Off Over My Sister's Lie — Five Years Later, I Was Her Only Hope In The ER",
    "My Family Excluded Me From Christmas to Sell a House — They Forgot Whose Name Was on the Deed",
]


def _previously_used_titles(channel_id: str) -> list[str]:
    """Scans this channel's past output for titles already generated, so a
    fresh title doesn't repeat the same premise."""
    titles = []
    for base in (ROOT / "out" / channel_id, ROOT / "ready_for_upload" / channel_id):
        if not base.exists():
            continue
        for story_path in base.glob("**/*_story.json"):
            try:
                data = json.loads(story_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("title"):
                titles.append(data["title"])
    return titles


YOUTUBE_TITLE_MAX_CHARS = 100


def generate_title(channel_id: str = "channel-a", max_attempts: int = 3) -> str:
    template = load_prompt("title_prompt")
    avoid = _previously_used_titles(channel_id)
    prompt = template.replace(
        "{{EXAMPLE_TITLES}}", "\n".join(f"- {t}" for t in EXAMPLE_TITLES)
    ).replace(
        "{{AVOID_TITLES}}", "\n".join(f"- {t}" for t in avoid) if avoid else "(none yet)"
    )

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raw = call_llm(prompt, temperature=1.0)
        try:
            result = extract_json(raw)
            title = result["title"].strip()
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            continue

        if not title or title in EXAMPLE_TITLES or title in avoid:
            last_error = ValueError(f"Generated title duplicated an example or a previously used title (attempt {attempt}/{max_attempts})")
            continue
        if len(title) > YOUTUBE_TITLE_MAX_CHARS:
            last_error = ValueError(f"Generated title was {len(title)} chars, over YouTube's {YOUTUBE_TITLE_MAX_CHARS}-char limit (attempt {attempt}/{max_attempts}): {title!r}")
            continue
        return title

    raise ValueError(f"Failed to generate a valid unique title after {max_attempts} attempts. Last error: {last_error}")


if __name__ == "__main__":
    channel_arg = sys.argv[1] if len(sys.argv) > 1 else "channel-a"
    print(generate_title(channel_arg))
