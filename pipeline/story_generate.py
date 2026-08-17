"""Story generation — 9,000-10,000 word script via multi-pass LLM calls (Gemini).

title -> outline (6-9 beats) -> per-beat expansion (prior context carried
forward) -> concatenation -> consistency pass -> structured JSON with beat
(scene/segment) breaks for the captions/Remotion assembly steps.
"""

import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import ROOT, load_channel, load_prompt
from pipeline.llm_client import call_llm, extract_json as _extract_json

BEAT_MIN = 6
BEAT_MAX = 9
TARGET_TOTAL_WORDS = 9500
# Hard bounds, not aspirational — real runs drifted up to 13-14.5k words
# despite the 9500 target because per-beat expansion routinely overshot its
# stated target and the consistency pass never trims (see build log). A
# trim pass now enforces MAX_TOTAL_WORDS after generation.
MIN_TOTAL_WORDS = 9000
MAX_TOTAL_WORDS = 11000
TRIM_MAX_ATTEMPTS = 2

# Rotated per-video (picked in Python, not left to the LLM) so the same
# engagement technique doesn't repeat on every video — see build log.
# HOOK styles must not require any story-specific knowledge (they land
# before the backstory); MIDPOINT styles can reference the plot since
# enough context exists by then.
HOOK_CTA_STYLES = [
    "Relatability word-prompt: ask viewers to comment a single word or short phrase that signals self-identification with the protagonist's situation (e.g. \"comment 'me' if you've ever been the family scapegoat\") — must NOT require any story-specific plot knowledge, since this comes before the backstory.",
    "Location/time-stamp ask: ask viewers to drop their city and local time in the comments (e.g. \"Drop your city and local time below, I love reading those.\").",
    "Full channel tagline: a short branding line naming \"my channel\" and describing its theme (quiet boundaries, family betrayal, justice finally landing), followed by a like-and-subscribe ask.",
]

MIDPOINT_CTA_STYLES = [
    "Binary prediction fork: pose a two-option choice tied to a decision the protagonist is about to make, and ask viewers to comment which option (A or B) they'd pick.",
    "Guess-the-ending tease: ask viewers to comment their guess at what happens next / what she said or did, without revealing it.",
    "Controversial-opinion bait: restate the antagonist's cruelest line so far as a mild 'hot take' and ask viewers to comment whether they agree or disagree.",
    "Themed word-prompt: ask viewers to comment a single specific word tied to the story's theme (e.g. \"comment BOUNDARIES below\") if the story resonates with them.",
]


def generate_outline(title: str, max_attempts: int = 3) -> dict:
    template = load_prompt("outline_prompt")
    # Two distinct picks (no repeats) so the investigation-beat break and the
    # confrontation-beat break never use the same technique in one video —
    # this is the 2nd mid-story FOMO break added per the competitor audit,
    # since verified source videos run 2-3 breaks and ours ran only 1.
    midpoint_style, second_midpoint_style = random.sample(MIDPOINT_CTA_STYLES, 2)
    prompt = (
        template.replace("{{TITLE}}", title)
        .replace("{{BEAT_MIN}}", str(BEAT_MIN))
        .replace("{{BEAT_MAX}}", str(BEAT_MAX))
        .replace("{{HOOK_CTA_STYLE}}", random.choice(HOOK_CTA_STYLES))
        .replace("{{MIDPOINT_CTA_STYLE}}", midpoint_style)
        .replace("{{SECOND_MIDPOINT_CTA_STYLE}}", second_midpoint_style)
    )

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raw = call_llm(prompt, temperature=0.9)
        try:
            outline = _extract_json(raw)
            beats = outline.get("beats", [])
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            continue

        # DeepSeek (unlike Gemini) has been observed to drop a required key
        # on an occasional beat rather than reliably matching the requested
        # schema — validate before committing to this outline instead of
        # crashing deep in beat-expansion (see build log).
        required_keys = {"beat_number", "beat_title", "summary"}
        if not all(required_keys.issubset(b.keys()) for b in beats):
            last_error = ValueError("One or more beats missing required keys (beat_number/beat_title/summary)")
            continue

        if BEAT_MIN <= len(beats) <= BEAT_MAX:
            return outline

        if len(beats) > BEAT_MAX:
            # Model overshot the count — fold the overflow beats into the final
            # beat rather than discarding the story and retrying from scratch.
            kept, overflow = beats[: BEAT_MAX - 1], beats[BEAT_MAX - 1 :]
            merged_summary = " ".join(b["summary"] for b in overflow)
            kept.append(
                {
                    "beat_number": BEAT_MAX,
                    "beat_title": overflow[0]["beat_title"],
                    "summary": merged_summary,
                }
            )
            outline["beats"] = kept
            return outline

        last_error = ValueError(f"Outline returned only {len(beats)} beats (attempt {attempt}/{max_attempts})")

    raise ValueError(
        f"Failed to get a valid {BEAT_MIN}-{BEAT_MAX} beat outline after {max_attempts} attempts. Last error: {last_error}"
    )


def expand_beat(title: str, protagonist_name: str, outline: dict, beat: dict, prior_text: str, target_words: int) -> str:
    template = load_prompt("expand_prompt")
    prompt = (
        template.replace("{{TITLE}}", title)
        .replace("{{PROTAGONIST_NAME}}", protagonist_name)
        .replace("{{OUTLINE_JSON}}", json.dumps(outline, ensure_ascii=False))
        .replace(
            "{{PRIOR_CONTEXT}}",
            prior_text[-6000:] if prior_text else "(this is the first beat — nothing written yet)",
        )
        .replace("{{BEAT_NUMBER}}", str(beat["beat_number"]))
        .replace("{{BEAT_COUNT}}", str(len(outline["beats"])))
        .replace("{{BEAT_TITLE}}", beat["beat_title"])
        .replace("{{BEAT_SUMMARY}}", beat["summary"])
        .replace("{{TARGET_WORDS}}", str(target_words))
    )
    return call_llm(prompt, temperature=0.85)


def run_consistency_pass(full_script: str) -> str:
    template = load_prompt("consistency_prompt")
    prompt = template.replace("{{FULL_SCRIPT}}", full_script)
    return call_llm(prompt, temperature=0.4)


def run_trim_pass(full_script: str, current_words: int, max_words: int) -> str:
    template = load_prompt("trim_prompt")
    prompt = (
        template.replace("{{FULL_SCRIPT}}", full_script)
        .replace("{{CURRENT_WORDS}}", str(current_words))
        .replace("{{MAX_WORDS}}", str(max_words))
    )
    return call_llm(prompt, temperature=0.4)


def generate_story(title: str, channel_id: str = "channel-a", run_consistency: bool = True) -> dict:
    channel = load_channel(channel_id)

    print(f"[1/4] Generating outline for: {title!r}")
    outline = generate_outline(title)
    beats = outline["beats"]
    protagonist_name = outline.get("protagonist_name", "the narrator")
    print(f"    -> {len(beats)} beats, protagonist: {protagonist_name}")

    target_words_per_beat = max(1000, TARGET_TOTAL_WORDS // len(beats))

    expanded_beats = []
    prior_text = ""
    for i, beat in enumerate(beats, start=1):
        print(f"[2/4] Expanding beat {i}/{len(beats)}: {beat['beat_title']}")
        text = expand_beat(title, protagonist_name, outline, beat, prior_text, target_words_per_beat)
        word_count = len(text.split())
        print(f"    -> {word_count} words")
        expanded_beats.append(
            {
                "beat_number": beat["beat_number"],
                "beat_title": beat["beat_title"],
                "text": text,
                "word_count": word_count,
            }
        )
        prior_text += "\n\n" + text

    print("[3/4] Concatenating full script")
    full_script = "\n\n".join(b["text"] for b in expanded_beats)
    total_words = len(full_script.split())
    print(f"    -> {total_words} words total")

    if run_consistency:
        print("[4/4] Running consistency pass")
        revised = run_consistency_pass(full_script)
        if len(revised.split()) < 0.7 * total_words:
            print("    -> consistency pass output looked truncated/unreliable, keeping pre-pass script")
        else:
            full_script = revised
            total_words = len(full_script.split())
            print(f"    -> {total_words} words after consistency pass")
    else:
        print("[4/4] Skipping consistency pass")

    for attempt in range(1, TRIM_MAX_ATTEMPTS + 1):
        if total_words <= MAX_TOTAL_WORDS:
            break
        print(f"    -> {total_words} words exceeds the {MAX_TOTAL_WORDS} cap, running trim pass ({attempt}/{TRIM_MAX_ATTEMPTS})")
        trimmed = run_trim_pass(full_script, total_words, MAX_TOTAL_WORDS)
        trimmed_words = len(trimmed.split())
        if trimmed_words < 0.7 * total_words:
            print("    -> trim pass output looked truncated/unreliable, keeping pre-trim script")
            break
        full_script = trimmed
        total_words = trimmed_words
        print(f"    -> {total_words} words after trim pass")

    if total_words > MAX_TOTAL_WORDS:
        print(f"    -> WARNING: still {total_words} words after {TRIM_MAX_ATTEMPTS} trim attempts, exceeds {MAX_TOTAL_WORDS} cap")
    elif total_words < MIN_TOTAL_WORDS:
        print(f"    -> WARNING: {total_words} words is under the {MIN_TOTAL_WORDS} floor")

    return {
        "title": title,
        "channel_id": channel_id,
        "protagonist_name": protagonist_name,
        "protagonist_gender": channel.get("protagonist_gender", "female"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "beats": expanded_beats,
        "full_text": full_script,
        "total_word_count": total_words,
    }


def save_story(story: dict, channel_id: str) -> Path:
    out_dir = ROOT / "out" / channel_id
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", story["title"].lower()).strip("-")[:60]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{ts}_{slug}_story.json"
    path.write_text(json.dumps(story, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m pipeline.story_generate "<one-line title>" [channel_id]')
        sys.exit(1)
    title_arg = sys.argv[1]
    channel_arg = sys.argv[2] if len(sys.argv) > 2 else "channel-a"
    story = generate_story(title_arg, channel_arg)
    out_path = save_story(story, channel_arg)
    print(f"\nSaved: {out_path}")
