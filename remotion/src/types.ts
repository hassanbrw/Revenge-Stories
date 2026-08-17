export type Caption = {
  text: string;
  startFrame: number;
  endFrame: number;
};

export type MainVideoProps = {
  backgroundVideoSrc: string;
  backgroundVideoDurationInFrames: number; // the raw clip's own length, so it can be looped for the full video
  photoSrc: string;
  audioSrc: string;
  musicSrc?: string;
  captions: Caption[];
  introHookText: string;
};
