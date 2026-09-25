# Claude Code / agent instructions

For automated court-case video production, follow **[AGENTS.md](AGENTS.md)** —
it has the setup, the batch commands, error handling, and the hard rules.

Quick reference:
- One video:   `python -m pipeline.court_case_auto`
- Batch of N:   `python -m pipeline.court_case_auto --count N`

Never invent facts about a real case; anything marked `[VERIFY]` must be
confirmed or cut before publishing. Don't commit `.env`, `out/`, or downloaded media.
