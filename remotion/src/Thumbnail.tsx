import React, { useLayoutEffect, useRef, useState } from "react";
import { AbsoluteFill, Img, continueRender, delayRender } from "remotion";
import { loadFont } from "@remotion/google-fonts/LeagueSpartan";
import { resolveSrc } from "./resolveSrc";

const { fontFamily } = loadFont("normal", { weights: ["800", "900"] });

const BASE_BODY_FONT_SIZE = 40;
const BASE_HIGHLIGHT_FONT_SIZE = 40;
const BASE_FINAL_FONT_SIZE = 50;
// The text block is measured after first paint and rescaled to fill the
// column top-to-bottom instead of leaving empty margins when the hook is
// short (see build log) — clamped so a very long hook doesn't shrink to
// illegible, and a very short one doesn't blow up absurdly large.
const MIN_SCALE = 0.55;
const MAX_SCALE = 1.9;

// Rotation for the flowing body text: white (setup), yellow (rising detail),
// plus a third accent color — randomly sky blue or light green each render
// (not red; red is reserved for the final highlight box below).
const BASE_PALETTE = ["#ffffff", "#ffd400"];
const ACCENT_COLORS = ["#4fc3f7", "#8de971"]; // sky blue / light green
const HIGHLIGHT_COLORS = ["#ffd400", "#ff2f2f"]; // yellow then red, for the final punch lines
const HIGHLIGHT_TEXT_COLORS: Record<string, string> = {
  "#ffd400": "#000000",
  "#ff2f2f": "#ffffff",
};
const SECTION_COUNT = 3;

export type ThumbnailProps = {
  photoSrc: string; // protagonist photo — reused as both blurred background and the sharp side panel
  hookText: string;
};

const splitIntoChunks = (text: string): string[] =>
  text
    .split(/(?<=[,.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);

// Distribute chunks into N color sections with roughly equal word counts.
// Assigns each chunk to the section its midpoint word-position falls into
// (proportional to total length), rather than greedily filling sections in
// order — greedy fill lets one long early chunk blow past its section's
// budget and starve whichever section comes last.
const groupIntoSections = (chunks: string[], sectionCount: number): string[][] => {
  const wordCounts = chunks.map((c) => c.split(/\s+/).length);
  const totalWords = wordCounts.reduce((a, b) => a + b, 0);
  const targetPerSection = totalWords / sectionCount;

  const sections: string[][] = Array.from({ length: sectionCount }, () => []);
  let cumulative = 0;
  for (let i = 0; i < chunks.length; i++) {
    cumulative += wordCounts[i];
    const midpoint = cumulative - wordCounts[i] / 2;
    const sectionIndex = Math.min(sectionCount - 1, Math.floor(midpoint / targetPerSection));
    sections[sectionIndex].push(chunks[i]);
  }
  return sections.filter((s) => s.length > 0);
};

const shuffle = <T,>(arr: T[]): T[] => {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
};

export const Thumbnail: React.FC<ThumbnailProps> = ({ photoSrc, hookText }) => {
  const chunks = splitIntoChunks(hookText);
  const highlightCount = Math.min(2, chunks.length > 3 ? 2 : 0);
  const highlightStart = chunks.length - highlightCount;
  const bodyChunks = chunks.slice(0, highlightStart);
  const highlightChunks = chunks.slice(highlightStart);
  const sections = groupIntoSections(bodyChunks, SECTION_COUNT);
  const accentColor = ACCENT_COLORS[Math.floor(Math.random() * ACCENT_COLORS.length)];
  const sectionPalette = shuffle([...BASE_PALETTE, accentColor]);
  const resolvedPhotoSrc = resolveSrc(photoSrc);

  const columnRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [handle] = useState(() => delayRender("fit thumbnail text to column"));
  // Text reflow isn't linear with font size (bigger font -> fewer words per
  // line -> disproportionately more lines), so a single "scale by the
  // measured ratio" guess badly overshot in practice (see build log) — a
  // bounded binary search converges on the actual largest fitting size.
  const search = useRef({ lo: MIN_SCALE, hi: MAX_SCALE, iteration: 0, settled: false, doneCalled: false });

  useLayoutEffect(() => {
    const s = search.current;
    const finish = () => {
      if (!s.doneCalled) {
        s.doneCalled = true;
        continueRender(handle);
      }
    };

    if (s.settled) {
      finish();
      return;
    }

    const column = columnRef.current;
    const content = contentRef.current;
    if (!column || !content) {
      s.settled = true;
      finish();
      return;
    }

    const available = column.clientHeight;
    const measured = content.scrollHeight;
    const fits = measured <= available;

    if (fits) {
      s.lo = scale;
    } else {
      s.hi = scale;
    }
    s.iteration += 1;

    const MAX_ITERATIONS = 6;
    if (s.iteration >= MAX_ITERATIONS) {
      s.settled = true;
      if (scale !== s.lo) {
        setScale(s.lo); // one more commit at the last confirmed-fitting size
      } else {
        finish();
      }
      return;
    }

    setScale((s.lo + s.hi) / 2);
  }, [scale, handle]);

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <Img
        src={resolvedPhotoSrc}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          filter: "blur(16px) brightness(0.24)",
          // scaleX(-1) mirrors the photo for the thumbnail — video and
          // thumbnail need opposite facing directions, per explicit user
          // correction (see build log).
          transform: "scale(1.15) scaleX(-1)",
        }}
      />

      {/* Dedicated dark scrim behind the text column — guarantees contrast
          regardless of what colors happen to be in the underlying photo. */}
      <div style={{ position: "absolute", left: 0, top: 0, width: "63%", height: "100%", backgroundColor: "rgba(0,0,0,0.4)" }} />

      <div style={{ position: "absolute", right: 0, top: 0, width: "40%", height: "100%" }}>
        <Img
          src={resolvedPhotoSrc}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            objectPosition: "top",
            // photo was reading dim/shadowed — brighten + add a touch of
            // contrast so it pops instead of looking washed out (see build log)
            filter: "brightness(1.25) contrast(1.12)",
            // mirrored — see build log, thumbnail needs the opposite facing
            // direction from the video's photo overlay
            transform: "scaleX(-1)",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            // lightened from 0.55 — that dark edge was the main source of
            // the "dim" look reported (see build log)
            background: "linear-gradient(to right, rgba(0,0,0,0.25), rgba(0,0,0,0))",
          }}
        />
      </div>

      <div
        ref={columnRef}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: "63%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "10px 16px 10px 42px",
        }}
      >
        <div ref={contentRef}>
          {/* Continuous flowing paragraph — natural word-wrap, not one line per clause */}
          <div
            style={{
              fontFamily,
              fontWeight: 800,
              textTransform: "uppercase",
              fontSize: BASE_BODY_FONT_SIZE * scale,
              lineHeight: 1.28,
            }}
          >
            {sections.map((section, i) => (
              <span
                key={i}
                style={{
                  color: sectionPalette[i % sectionPalette.length],
                  WebkitTextStroke: "2.6px black",
                  paintOrder: "stroke fill",
                }}
              >
                {section.join(" ")}{" "}
              </span>
            ))}
          </div>

          {/* Final punchline(s) — highlighter-box callouts. Only the last one
              (the climax line) gets the bigger/bolder/outlined treatment. */}
          {highlightChunks.map((chunk, i) => {
            const color = HIGHLIGHT_COLORS[i % HIGHLIGHT_COLORS.length];
            const isFinal = i === highlightChunks.length - 1;
            return (
              <div
                key={i}
                style={{
                  display: "inline-block",
                  alignSelf: "flex-start",
                  backgroundColor: color,
                  color: HIGHLIGHT_TEXT_COLORS[color],
                  fontFamily,
                  textTransform: "uppercase",
                  fontSize: (isFinal ? BASE_FINAL_FONT_SIZE : BASE_HIGHLIGHT_FONT_SIZE) * scale,
                  lineHeight: isFinal ? 1.22 : 1.28,
                  padding: isFinal ? "2px 12px" : "2px 10px",
                  marginTop: isFinal ? 8 : 6,
                  ...(isFinal
                    ? { WebkitTextStroke: "3px black", paintOrder: "stroke fill" as const }
                    : {}),
                }}
              >
                {chunk}
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
