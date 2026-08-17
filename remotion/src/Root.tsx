import React from "react";
import { Composition } from "remotion";
import { MainVideo } from "./MainVideo";
import { Thumbnail } from "./Thumbnail";
import sampleCaptions from "../public/sample-captions.json";

const FPS = 30;
const DURATION_IN_FRAMES = 360; // fallback for Studio preview / when no override is passed

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MainVideo"
        component={MainVideo}
        durationInFrames={DURATION_IN_FRAMES}
        fps={FPS}
        width={1280}
        height={720}
        defaultProps={{
          backgroundVideoSrc: "sample-bg.mp4",
          backgroundVideoDurationInFrames: DURATION_IN_FRAMES,
          photoSrc: "sample-photo.png",
          audioSrc: "sample-voice.mp3",
          captions: sampleCaptions,
          introHookText: "I FOUND OUT ON OUR ANNIVERSARY",
        }}
        calculateMetadata={async ({ props }) => {
          // Duration is computed in Python (soundfile) and passed in as a
          // prop — probing the audio file's duration in the browser sandbox
          // rejects arbitrary local file:// paths outside the public folder.
          const { durationInFrames, ...rest } = props as typeof props & {
            durationInFrames?: number;
          };
          return {
            durationInFrames: durationInFrames ?? DURATION_IN_FRAMES,
            props: rest,
          };
        }}
      />
      <Composition
        id="Thumbnail"
        component={Thumbnail}
        durationInFrames={1}
        fps={FPS}
        width={1280}
        height={720}
        defaultProps={{
          photoSrc: "thumb-bg-placeholder.jpg",
          hookText:
            'I spent three weeks planning our anniversary dinner. At 8:12 PM, my home security app sent an alert from a room that should have been empty. My husband texted, "Stuck at work, emergency with the client." The camera said otherwise. I sat there, champagne in hand, and watched everything unravel. Then I saw her face. FURIOUS, I made one call. HE HAD NO IDEA WHAT WAS COMING.',
        }}
      />
    </>
  );
};
