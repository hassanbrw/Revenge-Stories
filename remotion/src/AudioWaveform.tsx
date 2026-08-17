import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { useAudioData, visualizeAudio } from "@remotion/media-utils";

const BAR_COUNT = 48;

export const AudioWaveform: React.FC<{ audioSrc: string }> = ({ audioSrc }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const audioData = useAudioData(audioSrc);

  if (!audioData) return null;

  const frequencies = visualizeAudio({
    fps,
    frame,
    audioData,
    numberOfSamples: 64,
  });

  const bars = frequencies.slice(0, BAR_COUNT);

  return (
    <div
      style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        width: "100%",
        height: 140,
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "center",
        gap: 4,
        paddingBottom: 24,
        background: "linear-gradient(to top, rgba(0,0,0,0.55), rgba(0,0,0,0))",
      }}
    >
      {bars.map((v, i) => (
        <div
          key={i}
          style={{
            width: 6,
            height: Math.max(4, v * 110),
            borderRadius: 3,
            backgroundColor: "#ffffff",
            opacity: 0.85,
          }}
        />
      ))}
    </div>
  );
};
