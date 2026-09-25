"""Manifest GENERATOR for the court-case pipeline — the reusable template.

This is the "only the case changes" layer promised over pipeline/court_case.py.
The video STRUCTURE (which beats, their order, style, B&W grade, clip vs
narration) is fixed here in BEATS. Only the CONTENT of each beat — narration,
captions, and the serper search queries — is written per case by the LLM, from
a small CASE BRIEF you fill in.

Flow:
    1. Write a brief:  config/court_cases/<slug>.brief.json  (see _brief.example.json)
    2. Generate:       python -m pipeline.court_case_generate config/court_cases/<slug>.brief.json
                       -> writes config/court_cases/<slug>.json  (a full manifest)
    3. Fetch + render with pipeline/court_case.py as usual:
                       python -m pipeline.court_case config/court_cases/<slug>.json --fetch
                       python -m pipeline.court_case config/court_cases/<slug>.resolved.json

Facts come only from the brief; the prompt forbids inventing any. Beats whose
facts are missing get a "[VERIFY]" marker instead of a fabrication.
"""

import argparse
import json
import re
from pathlib import Path

from pipeline.config import ROOT, load_prompt
from pipeline.llm_client import call_llm, extract_json

# The fixed spine. Each beat: id, type, optional style/grade, and a `role` that
# tells the LLM what the beat is for. clip beats also carry default start/end
# placeholders (the operator sets the real in/out after --fetch). Edit this list
# once to change the format for ALL future videos.
BEATS = [
    {"id": "0.1", "type": "clip", "style": "headline", "start": "00:00:00", "end": "00:00:08",
     "role": "Cold-open: a short news clip announcing the outcome. text = a title card like 'THE STATE OF <STATE> v. <OFFICER>'."},
    {"id": "0.3", "type": "narration", "image": True,
     "role": "The hook (~30-40s): who the victim was, and the officer's one-sentence 'mistake' defense that this video will dismantle."},
    {"id": "1.1", "type": "narration", "image": True,
     "role": "Victim backstory — humanise them before the incident."},
    {"id": "1.3", "type": "narration", "image": True,
     "role": "The setting and the ordinary moment right before it happened."},
    {"id": "2.1", "type": "narration",
     "role": "Lead-in to the 911 call: how the officer came to be there."},
    {"id": "2.2", "type": "clip", "start": "00:00:00", "end": "00:02:00",
     "role": "The real 911 call. vo = a short cut-in telling the viewer what to listen for."},
    {"id": "3.1", "type": "clip", "grade": "bw", "start": "00:00:00", "end": "00:01:20",
     "role": "Bodycam of the aftermath / arrival. vo = brief context cut-in."},
    {"id": "4.1", "type": "clip", "grade": "bw", "start": "00:00:00", "end": "00:01:20",
     "role": "CCTV / surveillance showing the officer's path or the scene. vo = brief cut-in."},
    {"id": "4.2", "type": "narration", "image": True,
     "role": "The single most damning detail of the case (the thing that destroys the 'mistake' story)."},
    {"id": "5.1", "type": "clip", "grade": "bw", "start": "00:00:00", "end": "00:02:00",
     "role": "Courtroom testimony / cross-examination where the story falls apart. vo = brief cut-in."},
    {"id": "6.3", "type": "narration",
     "role": "The legal reason the defense failed (state the principle plainly)."},
    {"id": "7.1", "type": "clip", "start": "00:00:00", "end": "00:00:45",
     "role": "The verdict being read. text = the verdict in caps (e.g. 'GUILTY'). vo = '' (let it play)."},
    {"id": "7.3", "type": "clip", "start": "00:00:00", "end": "00:01:30",
     "role": "The emotional payoff moment of the case (sentencing / a family statement). vo = '' or one short line."},
    {"id": "8.1", "type": "narration", "image": True,
     "role": "A 4-part epilogue (what happened after) and a one-line subscribe call to action."},
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "court-case"


def generate(brief_path: str) -> Path:
    brief = json.loads(Path(brief_path).read_text(encoding="utf-8"))
    return generate_from_brief(brief, source_name=Path(brief_path).name)


def generate_from_brief(brief: dict, source_name: str = "(inline)") -> Path:
    beats_for_prompt = [
        {k: b[k] for k in ("id", "type", "role") if k in b} for b in BEATS
    ]
    prompt = (
        load_prompt("court_case_prompt")
        .replace("{{BRIEF}}", json.dumps(brief, indent=2, ensure_ascii=False))
        .replace("{{BEATS}}", json.dumps(beats_for_prompt, indent=2, ensure_ascii=False))
    )

    print(f"[gen] writing narration + queries for {len(BEATS)} beats via LLM...")
    content = extract_json(call_llm(prompt, temperature=0.7))

    # Merge fixed structure (BEATS) with per-case content (LLM). Structure is
    # authoritative: the model only supplies vo/text/query/image_query.
    segments = []
    for b in BEATS:
        c = content.get(b["id"], {})
        seg = {"id": b["id"], "type": b["type"]}
        for k in ("style", "grade", "start", "end"):
            if k in b:
                seg[k] = b[k]
        if b["type"] == "clip":
            seg["source"] = "TODO_fill_by_--fetch"
            if c.get("query"):
                seg["query"] = c["query"]
            if c.get("vo"):
                seg["vo"] = c["vo"]
            if c.get("text"):
                seg["text"] = c["text"]
        else:  # narration
            seg["vo"] = c.get("vo", "[VERIFY] narration missing for this beat")
            if c.get("text"):
                seg["text"] = c["text"]
            if b.get("image") and c.get("image_query"):
                seg["image_query"] = c["image_query"]
        segments.append(seg)

    title = brief.get("title", "Court Case")
    slug = _slug(brief.get("slug", title))
    manifest = {
        "title": title,
        "voice_id": brief.get("voice_id", "edge_en-US-GuyNeural"),
        "resolution": "1280x720",
        "fps": 30,
        "output": f"out/{slug}.mp4",
        "_generated_from": source_name,
        "segments": segments,
    }
    out = ROOT / "config" / "court_cases" / f"{slug}.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[gen] manifest -> {out}")
    print("Next:  python -m pipeline.court_case", out.relative_to(ROOT), "--fetch")
    print("Then review the .resolved.json (facts marked [VERIFY], clip in/out) and render it.")
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate a court-case manifest from a case brief.")
    ap.add_argument("brief", help="path to the case-brief JSON (see config/court_cases/_brief.example.json)")
    args = ap.parse_args()
    generate(args.brief)


if __name__ == "__main__":
    main()
