# Family-Drama YouTube Pipeline

Turns a one-line title/premise into a fully original, AI-written, long-form
narrated YouTube video (family drama / betrayal niche), across two channels.
No Reddit involved — input is a user-provided (or auto-generated) title each
run. Fully built and running in production; this file maps every stage to
where its rules actually live, so nothing depends on one person's memory.

## Pipeline stages, and where each stage's rules live

| # | Stage | Code | Rules live in |
|---|---|---|---|
| 1 | **Title** | `pipeline/title_gen.py` | `config/prompts/title_prompt.txt` — niche title formula, hard 100-char limit, retries against past titles to avoid duplicates. |
| 2 | **Story/script** | `pipeline/story_generate.py` | `config/prompts/outline_prompt.txt` (6-9 beat structure: cold-open order, branding beat, 2 distinct mid-story CTA breaks, leitmotif, 4-part epilogue), `expand_prompt.txt` (per-beat writing rules, time-skip markers, dialogue brevity), `consistency_prompt.txt`, `trim_prompt.txt` / `expand_more_prompt.txt` (enforce the 9,000-11,000 word bound both directions), `validate_prompt.txt` + `refine_prompt.txt` (auto-checks the structural checklist after generation and patches whatever's missing — runs on every generation, see step `[6/6]`). |
| 3 | **Metadata** (title options, description, tags) | `pipeline/metadata_gen.py` | `config/prompts/metadata_prompt.txt` (LLM writes only the 📝 recap + 📌 lessons bullets); `FIXED_TAGS` and `DESCRIPTION_FOOTER` in `metadata_gen.py` itself are literal templates, not LLM-generated, so they never drift. |
| 4 | **Protagonist photo** (manual — not automated) | `pipeline/photo_gen.py` (background-removal cutout only) | `config/reference/photo_generation_guide.md` — full prompt requirements (red lipstick, camera-facing, mid-torso framing, blurred profession-matched background, bright even lighting), 2K download, mirror-opposite orientation rule for video vs. thumbnail + per-photo visual check, per-photo headroom crop (never a fixed %). Generation itself happens via Google Flow through browser automation, per-run. |
| 5 | **Voiceover (TTS)** | `pipeline/tts.py` | Kokoro (local, free) is the default engine — inline comments explain why (ai33.pro outage) and the exact TTS_WORKERS/TTS_CHUNKS/KOKORO_WORKER_THREADS tuning for local vs. pod (`.env.example`, `.env.pod.example`). |
| 6 | **Captions** | `pipeline/captions.py` | Whisper `base` model + beam_size=1 + VAD — inline comment explains the accuracy/speed tradeoff measured against `small`. |
| 7 | **Stock footage** | `pipeline/stock_footage.py` | Pexels/Pixabay, rotated and logged to `data/stock_footage_log.csv` for Content ID record-keeping — inline comments explain licensing (no attribution needed for nature/aerial footage w/ no identifiable people) and the 1280x720 target-resolution rationale. |
| 8 | **Render** | `pipeline/ffmpeg_render.py` (default), `pipeline/lambda_render.py` / local Remotion (fallbacks) | ffmpeg-native compositing (~58x faster than the old Remotion/headless-Chrome path) is the default (`RENDER_ENGINE` in `.env.example`); photo overlay orientation fix documented in `photo_generation_guide.md` Section 3. |
| 9 | **Thumbnail** | `pipeline/thumbnail_gen.py`, `remotion/src/Thumbnail.tsx` | `config/prompts/thumbnail_prompt.txt` (50-65 word hook text); orientation (`scaleX(-1)`, opposite of the video) and the two-crop rule (full image for video, ~78%-down crop for thumbnail) in `photo_generation_guide.md`. 3 hook-text variants generated per video. |
| 10 | **Upload package + Drive delivery** | `pipeline/orchestrate.py` (`save_upload_package`, `zip_and_upload_package`) | Zips video + all thumbnail variants + title/description, uploads via `rclone` to `gdrive:Family Drama Video Pipeline/Finished Videos/<channel_id>/` — best-effort, never fails the run if rclone/Drive is unavailable. |

`pipeline/orchestrate.py` wires all of the above together — `run_pipeline()` for a
fresh title, `run_pipeline_resume(story_path, photo_path, channel_id)` to pick
up an already-generated story (skips straight to metadata/photo/voiceover/...).

## Infra (Docker / pod / CI)

- **GitHub:** [hassanbrw/Revenge-Stories](https://github.com/hassanbrw/Revenge-Stories) — `main` branch, auto-builds via `.github/workflows/docker-publish.yml`.
- **Pod image:** `ghcr.io/hassanbrw/revenge-stories:latest` (public pull). `Dockerfile` runs the whole pipeline except photo generation (that stays a manual browser-automation step regardless of where the rest runs).
- **Default: run locally, not on a pod** — pod rental is a real per-hour cost and needs explicit confirmation each time it's used; it exists and works (`.env.pod.example`, Vast.ai API) but isn't the default.
- **`.env.example`** / **`.env.pod.example`** — every tunable env var with an explanation of what it controls and why.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in real API keys
```

Story/metadata/thumbnail-hook generation uses OpenRouter (DeepSeek) by default — no daily
cap, pay-per-token (`LLM_PROVIDER=openrouter` in `.env`). Gemini's free tier is available as
an alternative but caps at 20 requests/day.

## Conventions

- Channel A and Channel B share one pipeline/codebase; differences are expressed
  purely through `config/channels/*.json`, not separate code paths.
- Protagonist is always female (`protagonist_gender` in channel config; enforced
  in the outline prompt) — hard constraint per user direction.
- Every generated story/metadata/thumbnail must refer to the channel only as
  "my channel" — never invent a display name.
- Windows console stdout/stderr are reconfigured to UTF-8 at the top of
  `orchestrate.py` — don't remove this, arbitrary Unicode (e.g. a stock-footage
  creator's name) will crash a plain cp1252 console otherwise.
