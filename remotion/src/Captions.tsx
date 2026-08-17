import React from "react";
import { useCurrentFrame } from "remotion";
import type { Caption } from "./types";

export const Captions: React.FC<{ captions: Caption[] }> = ({ captions }) => {
  const frame = useCurrentFrame();
  const active = captions.find((c) => frame >= c.startFrame && frame < c.endFrame);
  if (!active) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: "42%",
        right: "5%",
        top: "46%",
        transform: "translateY(-50%)",
        display: "flex",
        justifyContent: "center",
        textAlign: "center",
      }}
    >
      <div
        style={{
          display: "inline-block",
          backgroundColor: "rgba(0,0,0,0.75)",
          color: "white",
          fontFamily: "Arial, Helvetica, sans-serif",
          fontWeight: 700,
          fontSize: 46,
          lineHeight: 1.4,
          padding: "16px 28px",
          borderRadius: 18,
        }}
      >
        {active.text}
      </div>
    </div>
  );
};
