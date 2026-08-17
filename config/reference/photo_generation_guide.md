# Protagonist Photo — Generation & Processing Guide

Photo generation itself is NOT automated in `pipeline/` — there is no
prompt-file/API call for it, unlike title/story/metadata/thumbnail-hook
generation. It's done manually, per video, via Google Flow (Nano Banana Pro
model) through browser automation, then handed to `pipeline/photo_gen.py`
(background-removal cutout) as an already-existing file. This doc is the
durable record of that manual step's requirements, so the process doesn't
live only in one person's/session's memory.

## 1. Image-generation prompt — fixed requirements

Every AI-generated protagonist image must include these elements, on top of
whatever profession-specific styling (wardrobe, contextual details) the
individual story calls for:

- **Red lipstick.**
- **Camera-facing** — direct eye contact with the camera, never a
  profile/side angle.
- **Mid-torso headshot framing** — a chest-up portrait crop, not full-body
  and not a tight face-only crop.
- **Background blurred/out-of-focus, contextual to her profession** (e.g. a
  blurred office for a CPA, a blurred hospital corridor for a surgeon) —
  never a plain flat backdrop, and never the subject's own sharp photo
  reused/duplicated as its own blurred background.
- **Face brightly and evenly lit** — clean, even, bright studio-style light
  on the face specifically, matching the competitor channel's own
  thumbnail photos. Do NOT use moody backlit/golden-hour lighting that
  leaves shadow gradients across the face — a warm/contextual light source
  can exist in the blurred background, but the face itself needs separate,
  flat, bright lighting.
- **Facing-direction instruction** — bake an explicit "facing left" (or
  whichever direction is needed that run) instruction into the prompt, but
  never rely on this alone (see Section 3 — generation isn't reliably
  consistent even with the instruction).

## 2. Download

Always download the generated image at **2K** (upscaled) — never keep the
1K original size.

## 3. Orientation — video vs. thumbnail must be mirror opposites

The protagonist's photo must face **left-to-right in the video** and
**right-to-left in the thumbnail** — always mirror opposites, never the
same orientation in both places.

- In code, this is handled by [ffmpeg_render.py](../../pipeline/ffmpeg_render.py)
  (no `hflip` — video uses the source photo as-supplied) and
  [Thumbnail.tsx](../../remotion/src/Thumbnail.tsx) (`scaleX(-1)` on both
  the blurred-background and sharp photo layers).
- That code fix alone is **not sufficient** — it assumes the source photo
  already faces left-to-right, which isn't guaranteed. AI generation is not
  consistent photo-to-photo even with the facing-direction prompt
  instruction from Section 1.
- **Before every run:** visually check which way the specific photo's
  face/shoulders are actually angled (use a known-correct reference photo's
  lapel/shoulder-forward-side as the comparison indicator). If it isn't
  already facing left-to-right, pre-flip it (e.g. PIL
  `ImageOps.mirror()` / `transpose(Image.FLIP_LEFT_RIGHT)`) **before**
  passing it into `process_photo()`, so the fixed downstream code (video =
  as-supplied, thumbnail = mirrored) ends up correct regardless of which
  way that particular generation happened to face.

## 4. Two output crops — video vs. thumbnail

Produce **two** files from the one downloaded (2K) image:

1. **Video version** — the full downloaded image, headroom-trimmed only
   (see Section 5). Do not shorten it further. This is what
   `process_photo()` takes as input.
2. **Thumbnail version** — a separate, tighter crop: **~78% down from the
   top of the source image** (head down through the upper chest, stopping
   before the forearms/elbows). This was directly confirmed correct against
   an alternative ~58%-down shoulder-level crop, which read as too tight.
   Never use the full video-length version directly in a thumbnail.

## 5. Headroom crop — check every photo individually

Before either crop, check the empty space above the head. **Do not apply a
fixed crop percentage across images** — the empty headroom varies
per-generation (observed anywhere from ~8% to ~19% of the frame height). A
blind fixed-percent crop that works for one image can cut straight through
another's hair. Always look at the specific image first, note where the
hairline/crown actually starts, and crop just above it with a small natural
margin — never into the hair or head.

## Why this matters

All five sections above came from direct user corrections/confirmations
after reviewing real generated images and real rendered videos — they are
fixed baseline requirements for every protagonist photo, not one-off
preferences for a single video. See also
[calm_drama_stories_style_guide.md](calm_drama_stories_style_guide.md)
Section 4 for profession-specific wardrobe/context styling that layers on
top of these fixed rules.
