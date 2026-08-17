import { staticFile } from "remotion";

/**
 * Props carry either a path relative to the public dir (local renders —
 * see remotion.config.ts's REMOTION_ASSET_DIR override) or a full S3/HTTPS
 * URL (Lambda renders, where per-video generated files are uploaded to S3
 * since Lambda can't read local files). Resolve whichever was given.
 */
export const resolveSrc = (src: string): string =>
  /^https?:\/\//.test(src) ? src : staticFile(src);
