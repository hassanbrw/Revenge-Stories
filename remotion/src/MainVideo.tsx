import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Loop,
  OffthreadVideo,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Captions } from "./Captions";
import { AudioWaveform } from "./AudioWaveform";
import { ProgressBar } from "./ProgressBar";
import { resolveSrc } from "./resolveSrc";
import type { MainVideoProps } from "./types";

const HOOK_DURATION_FRAMES = 75; // ~2.5s at 30fps

export const MainVideo: React.FC<MainVideoProps> = ({
  backgroundVideoSrc,
  backgroundVideoDurationInFrames,
  photoSrc,
  audioSrc,
  musicSrc,
  captions,
  introHookText,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const resolvedBackgroundVideoSrc = resolveSrc(backgroundVideoSrc);
  const resolvedPhotoSrc = resolveSrc(photoSrc);
  const resolvedAudioSrc = resolveSrc(audioSrc);
  const resolvedMusicSrc = musicSrc ? resolveSrc(musicSrc) : undefined;

  const bgScale = interpolate(frame, [0, durationInFrames], [1, 1.12], {
    extrapolateRight: "clamp",
  });
  const photoScale = interpolate(frame, [0, durationInFrames], [1, 1.06], {
    extrapolateRight: "clamp",
  });

  const hookOpacity = interpolate(
    frame,
    [0, 10, HOOK_DURATION_FRAMES - 15, HOOK_DURATION_FRAMES],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Loop durationInFrames={backgroundVideoDurationInFrames}>
          <OffthreadVideo
            src={resolvedBackgroundVideoSrc}
            muted
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${bgScale})`,
            }}
          />
        </Loop>
      </AbsoluteFill>

      <AbsoluteFill
        style={{
          background:
            "linear-gradient(to right, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0.05) 45%, rgba(0,0,0,0.2) 100%)",
        }}
      />

      <AbsoluteFill
        style={{
          alignItems: "flex-start",
          justifyContent: "flex-end",
          paddingLeft: 40,
        }}
      >
        <Img
          src={resolvedPhotoSrc}
          style={{
            height: "82%",
            width: "auto",
            maxWidth: "42%",
            objectFit: "contain",
            objectPosition: "bottom",
            transform: `scale(${photoScale})`,
            transformOrigin: "bottom center",
            filter: "drop-shadow(0 20px 30px rgba(0,0,0,0.45))",
          }}
        />
      </AbsoluteFill>

      <Captions captions={captions} />
      <AudioWaveform audioSrc={resolvedAudioSrc} />
      <ProgressBar />

      {frame < HOOK_DURATION_FRAMES && (
        <AbsoluteFill
          style={{
            alignItems: "center",
            justifyContent: "center",
            padding: "0 160px",
          }}
        >
          <div
            style={{
              opacity: hookOpacity,
              color: "white",
              fontFamily: "Arial, Helvetica, sans-serif",
              fontWeight: 900,
              fontSize: 72,
              textAlign: "center",
              lineHeight: 1.15,
              textShadow: "0 4px 24px rgba(0,0,0,0.8)",
            }}
          >
            {introHookText}
          </div>
        </AbsoluteFill>
      )}

      <Audio src={resolvedAudioSrc} />
      {resolvedMusicSrc ? <Audio src={resolvedMusicSrc} volume={0.12} /> : null}
    </AbsoluteFill>
  );
};
