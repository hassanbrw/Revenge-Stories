# Agent runbook — automated court-case video production

This file tells a coding agent (Codex CLI, Claude Code, etc.) how to produce
court-case videos on this machine. Follow it exactly.

## What you are doing
Run the fully-automatic, QA-gated pipeline that discovers a real police-misconduct
court case, researches the facts, QA-checks them, sources footage, and renders a
video. Code lives in `pipeline/court_case_auto.py`.

## One-time setup (do this first, only if not already done)
1. Confirm tools are on PATH — do NOT proceed until both print a version:
   - `yt-dlp --version`
   - `ffmpeg -version`
2. Install Python deps:
   - `pip install -r requirements.txt`
   - `pip install python-dotenv requests`
3. Ensure `.env` exists in the repo root with these keys (ask the user for any
   missing value; never invent a key):
   ```
   SERPER_API_KEY=...
   AI33_API_KEY=...
   TTS_ENGINE=ai33
   LLM_PROVIDER=openrouter
   OPENROUTER_API_KEY=...
   ```
4. Sanity check (no network render, just parses): 
   `python -m pipeline.court_case config/court_cases/guyger.json --dry-run`

## Make videos
- One video:      `python -m pipeline.court_case_auto`
- A batch of N:    `python -m pipeline.court_case_auto --count N`

Each video: `out/<slug>.mp4` plus an audit report `config/court_cases/<slug>.resolved.qa.json`.
Already-made cases are skipped automatically via `config/court_cases/_used.json`.

## Your job while it runs (do not skip)
1. Run the command and watch the staged output (`[1/5] ... [5/5] ...`).
2. If a run prints an error, READ it and fix the root cause, then re-run:
   - `ModuleNotFoundError` -> pip install the missing package.
   - `... not set in .env` -> the key is missing; tell the user which one.
   - `yt-dlp`/`ffmpeg` error on one clip -> it is usually a bad auto-picked
     source; open the `*.resolved.json`, replace that clip's `source` with one of
     its `_candidates`, or adjust `start`/`end`, then render that resolved file:
     `python -m pipeline.court_case config/court_cases/<slug>.resolved.json`
   - Fact-QA rejected every candidate -> just run again (it tries new cases), or
     widen `DISCOVERY_QUERIES` in `pipeline/court_case_auto.py`.
3. After each video, open its `*.qa.json` and confirm `facts_qa._passed` is true
   and `footage_qa.key_clips_ok` shows the 911 (`2.2`) and verdict (`7.1`) clips
   resolved. If a key clip is missing, fix its `source` in the resolved manifest
   and re-render before publishing.

## Hard rules
- These are real people. Do NOT edit the narration to add facts the QA did not
  ground. Anything marked `[VERIFY]` must be confirmed against a real source
  before publishing, or cut.
- Prefer public-record footage (court hearings, released bodycam, 911). Keep
  third-party news clips short and always under narration.
- Do not commit `.env`, `out/`, downloaded clips, or `assets/court_case/` media.

## Where everything is
- `pipeline/court_case_auto.py`  — the one-command auto maker (+ `--count` batch)
- `pipeline/court_case_generate.py` — brief -> manifest (fixed `BEATS` structure)
- `pipeline/court_case.py`        — assembler (`--fetch` sources, then renders)
- `pipeline/sourcing.py`          — serper.dev web/image/video search
- `config/court_cases/`           — manifests, briefs, `_used.json`, QA reports
- `config/edit_sheet_guyger.md`, `config/voiceover_guyger.md` — reference/example
