# Family-Drama YouTube Pipeline

Build spec: `C:\Users\usa\Documents\youtube-automation-spec.md`

Turns a one-line title/premise into a fully original, AI-written, long-form
narrated YouTube video (family drama / betrayal niche), across two channels.
No Reddit involved — input is a user-provided title each run.

## Status

- [x] Project scaffold (this structure)
- [x] Competitor research (title/hook/thumbnail patterns — see chat log)
- [x] Video template (`remotion/`) — structurally verified with placeholder assets
- [ ] Story-generation script (`pipeline/story_generate.py`) — in progress, awaiting GEMINI_API_KEY to test
- [ ] Metadata-generation script (`pipeline/metadata_gen.py`) — built, untested
- [ ] TTS script (`pipeline/tts.py`)
- [ ] Captioning script (`pipeline/captions.py`)
- [ ] Stock footage script (`pipeline/stock_footage.py`)
- [ ] Thumbnail Remotion composition
- [ ] Orchestration script (`pipeline/orchestrate.py`)
- [ ] End-to-end test run

## Layout

```
config/
  channels/channel-a.json   per-channel settings (voice, output dir, protagonist gender)
  channels/channel-b.json
  prompts/                  LLM prompt templates (outline/expand/consistency/metadata)
data/                       runtime state (e.g. stock_footage_log.csv, once step 6 lands)
pipeline/                   Python stages, one module per spec step
assets/
  photos/<channel-id>/      protagonist overlay photos
  music/                    royalty-free background music
  stock/                    downloaded nature/aerial/sea footage library
out/<channel-id>/           generated story/metadata/video/thumbnail per run
logs/
remotion/                   (to be scaffolded later — parametrized composition
                             shared by both channels: background video, photo,
                             voiceover, captions data, intro hook text)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in Pexels/Pixabay/ElevenLabs keys when you reach those steps
```

Story/metadata generation uses the Google Gemini free tier (`gemini-2.5-flash`) — get a free key at
https://aistudio.google.com/apikey and set `GEMINI_API_KEY` in `.env`. (Switched from local Ollama:
this machine has no discrete GPU, so CPU-only 7B inference was taking 20+ minutes per beat and
undershooting word-count targets.)

## Conventions

- Channel A and Channel B share one pipeline/codebase; differences are expressed
  purely through `config/channels/*.json`, not separate code paths.
- Protagonist is always female (`protagonist_gender` in channel config; enforced
  in the outline prompt) — hard constraint per user direction.
- The existing `Documents/my-video` and `~/my-video` Remotion projects are left
  untouched — this pipeline's `remotion/` folder will be a fresh, parametrized
  composition built later in the build order.
