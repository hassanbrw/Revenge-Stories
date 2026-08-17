# Pod-side image — runs the ENTIRE pipeline (title -> story -> metadata ->
# photo cutout -> voiceover -> captions -> stock footage -> render ->
# thumbnail -> upload package) via `pipeline/orchestrate.py`. The one thing
# that can't live in here: generating the protagonist photo itself, since
# that's done through an interactive browser session (Google Flow), not
# pipeline code — this image expects a photo file to already exist and be
# mounted in, same as orchestrate.py already expects locally.
FROM node:20-bookworm-slim

# ffmpeg          -> native video rendering (pipeline/ffmpeg_render.py)
# espeak-ng       -> Kokoro TTS phonemization backend
# python3/pip     -> the pipeline itself
# the libX*/libatk/libpango/etc block -> Remotion's bundled headless-Chrome
#   renderer needs these at runtime for the thumbnail still-render
#   (pipeline/thumbnail_gen.py); the main video render stays on ffmpeg and
#   never touches Chrome.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    ffmpeg espeak-ng \
    ca-certificates curl wget \
    libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libgbm1 \
    libasound2 libxrandr2 libxkbcommon0 libxfixes3 libxcomposite1 \
    libxdamage1 libpango-1.0-0 libcairo2 libcups2 fonts-liberation \
    libnspr4 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps (cached as its own layer — changes far less often than pipeline code) ---
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt

# --- Remotion / Node deps ---
COPY remotion/package.json remotion/package-lock.json remotion/
RUN cd remotion && npm ci
COPY remotion/ remotion/
# Pre-warm Remotion's bundled headless-Chrome download at build time so a
# freshly-started pod doesn't pay for it on its first render. Best-effort:
# a running pod still has internet if this ever needs to fall back to a
# lazy download (e.g. after a Remotion version bump changes the binary).
RUN cd remotion && (npx remotion browser ensure || true)

# --- Pipeline code + prompts/channel config (changes most often — last layer) ---
COPY pipeline/ pipeline/
COPY config/ config/

ENV PYTHONUNBUFFERED=1

# Matches the existing local CLI exactly:
#   docker run --env-file .env.pod -v <in>:/data/in -v <out>:/app/out -v <out>:/app/ready_for_upload \
#     ghcr.io/<owner>/<repo>:latest "auto" /data/in/photo.jpg channel-a
ENTRYPOINT ["python3", "-m", "pipeline.orchestrate"]
