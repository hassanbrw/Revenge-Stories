import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);

// Per-render asset staging dir, set by the Python pipeline so large
// generated media (audio/video/photos) doesn't have to live inside — and
// get re-copied out of — the project's own public/ folder on every render.
if (process.env.REMOTION_ASSET_DIR) {
  Config.setPublicDir(process.env.REMOTION_ASSET_DIR);
}
