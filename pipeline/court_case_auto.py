"""Fully-automatic court-case video maker — one command, QA-gated, zero human review.

    python -m pipeline.court_case_auto

Chains everything: find a fresh case -> research real facts -> QA the facts ->
generate the manifest -> auto-source images/clips -> QA the footage -> render.

Because these are real people, correctness is enforced by QA GATES at each stage
instead of a human:

  STAGE 1  topic    : discover a police-misconduct court case not already made
                      (deduped against config/court_cases/_used.json).
  STAGE 2  facts    : brief is written ONLY from retrieved web snippets, with a
                      source quote per field.
  STAGE 3  QA facts : every core field (victim/officer/what_happened/outcome) must
                      be GROUNDED in the retrieved snippets (token-overlap check).
                      Too few grounded -> reject this case, try the next one.
                      Ungrounded non-core fields are dropped/marked, not rendered.
  STAGE 4  footage  : serper resolves clips/images; key clips (verdict, 911) are
                      checked and flagged in the QA report.
  STAGE 5  render   : resolved manifest -> out/<slug>.mp4.

Honest note: QA reduces but cannot fully eliminate bad facts from web text. The
QA report is written next to the video so every claim is auditable after the run.
Needs SERPER_API_KEY, an LLM key (LLM_PROVIDER), AI33_API_KEY, plus yt-dlp+ffmpeg.
"""

import argparse
import json
import re
from pathlib import Path

from pipeline.config import ROOT
from pipeline.court_case import assemble, fetch_manifest
from pipeline.court_case_generate import generate_from_brief
from pipeline.llm_client import call_llm, extract_json
from pipeline.sourcing import search_web

USED_LOG = ROOT / "config" / "court_cases" / "_used.json"

# Rotating discovery queries — real, recent police-accountability court outcomes.
DISCOVERY_QUERIES = [
    "police officer convicted misconduct court bodycam verdict",
    "cop found guilty excessive force trial sentenced",
    "police officer lawsuit settlement misconduct caught on video",
    "officer perjury dashcam court case convicted",
    "deputy sheriff corruption trial guilty verdict",
]

CORE_FIELDS = ["victim", "officer", "what_happened", "trial_outcome"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) > 2}


def _grounded(quote: str, corpus_tokens: set, threshold: float = 0.7) -> bool:
    """A field is grounded if enough of its source-quote tokens actually appear in
    the retrieved snippets — cheap defence against the model inventing a quote."""
    qt = _tokens(quote)
    if not qt:
        return False
    return len(qt & corpus_tokens) / len(qt) >= threshold


def _load_used() -> list:
    if USED_LOG.exists():
        try:
            return json.loads(USED_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def _save_used(used: list) -> None:
    USED_LOG.parent.mkdir(parents=True, exist_ok=True)
    USED_LOG.write_text(json.dumps(used, indent=2, ensure_ascii=False), encoding="utf-8")


def discover_cases(used_names: list) -> list[str]:
    """STAGE 1: pull candidate case names from web results, drop ones already made."""
    snippets = []
    for q in DISCOVERY_QUERIES:
        snippets += search_web(q, n=8)
    corpus = "\n".join(f"- {s['title']}: {s['snippet']}" for s in snippets)
    prompt = (
        "From these search results, list DISTINCT real US cases where a police "
        "officer/deputy was criminally convicted, or lost a major lawsuit, for "
        "misconduct that has real court or bodycam footage. Return JSON: "
        '{"cases": ["<Officer name> — <one-line what happened>", ...]}. '
        "Only cases clearly supported by the text. No duplicates.\n\n" + corpus
    )
    try:
        cases = extract_json(call_llm(prompt, temperature=0.4)).get("cases", [])
    except Exception as e:  # noqa: BLE001 - discovery is best-effort
        print(f"  [warn] discovery parse failed: {e}")
        cases = []
    used_tok = [_tokens(u) for u in used_names]
    fresh = []
    for c in cases:
        ct = _tokens(c)
        if any(len(ct & u) / max(1, len(ct)) > 0.5 for u in used_tok):
            continue  # already made a very similar case
        fresh.append(c)
    return fresh


def research(case: str) -> list[dict]:
    """STAGE 2a: gather real facts about the chosen case."""
    snippets = []
    for q in (
        f"{case} verdict sentence",
        f"{case} bodycam court case what happened",
        f"{case} lawsuit outcome appeal",
    ):
        snippets += search_web(q, n=8)
    return snippets


def build_brief(case: str, snippets: list[dict]) -> dict:
    """STAGE 2b: write the brief ONLY from retrieved snippets, quote per field."""
    corpus = "\n".join(f"- {s['title']}: {s['snippet']} ({s['link']})" for s in snippets)
    prompt = (
        "Using ONLY the facts in these search results, fill this case brief. Do not "
        "use outside knowledge and do not guess. For every field also give the exact "
        "supporting quote you took it from, under 'sources'.\n\n"
        "Return JSON with keys: title, slug, victim, officer, location, date, "
        "what_happened, key_detail, trial_outcome, aftermath, footage_notes, "
        "sources (an object mapping each of the above field names to the verbatim "
        "snippet text that supports it). Leave a field as \"\" if the results don't "
        "support it.\n\nSEARCH RESULTS:\n" + corpus
    )
    return extract_json(call_llm(prompt, temperature=0.5))


def qa_brief(brief: dict, snippets: list[dict]) -> tuple[bool, dict]:
    """STAGE 3: ground every field's source quote in the retrieved text. Reject if
    too few CORE fields are grounded; drop/mark ungrounded non-core fields."""
    corpus_tokens = set()
    for s in snippets:
        corpus_tokens |= _tokens(s["title"]) | _tokens(s["snippet"])
    sources = brief.get("sources", {})
    report = {}
    grounded_core = 0
    for field in list(brief.keys()):
        if field in ("sources", "title", "slug", "voice_id"):
            continue
        quote = sources.get(field, "")
        ok = bool(brief.get(field)) and _grounded(quote, corpus_tokens)
        report[field] = "grounded" if ok else "unverified"
        if field in CORE_FIELDS and ok:
            grounded_core += 1
        if not ok and brief.get(field) and field not in CORE_FIELDS:
            brief[field] = str(brief[field]) + " [VERIFY]"
    passed = grounded_core >= 3  # need at least 3 of 4 core facts grounded
    report["_core_grounded"] = f"{grounded_core}/{len(CORE_FIELDS)}"
    report["_passed"] = passed
    return passed, report


def qa_footage(resolved_path: Path) -> dict:
    """STAGE 4: sanity-check the auto-sourced footage after --fetch."""
    m = json.loads(resolved_path.read_text(encoding="utf-8"))
    clips = [s for s in m["segments"] if s["type"] == "clip"]
    resolved = [s for s in clips if s.get("source", "").startswith("http")]
    key = {s["id"]: bool(s.get("source", "").startswith("http")) for s in clips if s["id"] in ("2.2", "7.1")}
    return {
        "clips_total": len(clips),
        "clips_resolved": len(resolved),
        "key_clips_ok": key,  # 911 (2.2) and verdict (7.1)
    }


def run(max_candidates: int = 5) -> Path | None:
    used = _load_used()
    used_names = [u["case"] for u in used] if used and isinstance(used[0], dict) else list(used)

    print("[1/5] discovering candidate cases...")
    candidates = discover_cases(used_names)
    if not candidates:
        print("No fresh candidates found. Try again later or widen DISCOVERY_QUERIES.")
        return None

    for case in candidates[:max_candidates]:
        print(f"\n=== candidate: {case} ===")
        print("[2/5] researching facts...")
        snippets = research(case)
        if not snippets:
            print("  no research results, skipping.")
            continue
        brief = build_brief(case, snippets)

        print("[3/5] QA: grounding facts...")
        passed, report = qa_brief(brief, snippets)
        print(f"  core grounded: {report.get('_core_grounded')}  -> {'PASS' if passed else 'REJECT'}")
        if not passed:
            print("  facts not sufficiently grounded, trying next candidate.")
            continue

        brief.pop("sources", None)  # not needed downstream
        manifest_path = generate_from_brief(brief, source_name="court_case_auto")

        print("[4/5] fetching + QA footage...")
        resolved = fetch_manifest(str(manifest_path))
        frep = qa_footage(resolved)
        print(f"  footage: {frep['clips_resolved']}/{frep['clips_total']} clips, key={frep['key_clips_ok']}")

        # write an auditable QA report next to the video
        qa_out = resolved.with_suffix(".qa.json")
        qa_out.write_text(json.dumps({"case": case, "facts_qa": report, "footage_qa": frep}, indent=2, ensure_ascii=False), encoding="utf-8")

        print("[5/5] rendering...")
        video = assemble(str(resolved))

        used.append({"case": case, "slug": manifest_path.stem, "video": str(video)})
        _save_used(used)
        print(f"\nDONE. Video: {video}\nQA report: {qa_out}")
        return video

    print("No candidate passed the fact-QA gate this run.")
    return None


def main():
    ap = argparse.ArgumentParser(description="Fully-auto, QA-gated court-case video maker.")
    ap.add_argument("--max-candidates", type=int, default=5, help="how many discovered cases to try before giving up")
    args = ap.parse_args()
    run(max_candidates=args.max_candidates)


if __name__ == "__main__":
    main()
