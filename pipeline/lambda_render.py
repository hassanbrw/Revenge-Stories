"""Remotion Lambda rendering — uploads per-video generated assets to S3
(Lambda can't read local files) then triggers a cloud render. Dramatically
faster than local CPU rendering: ~20hrs local -> minutes on Lambda, at a
cost of roughly a few dollars per full-length video (see build log).
"""

import json
import subprocess
import tempfile
from pathlib import Path

import boto3
import soundfile as sf

from pipeline.config import ROOT, env
from pipeline.remotion_utils import REMOTION_DIR

AWS_REGION = env("REMOTION_LAMBDA_REGION", "us-east-1")
S3_BUCKET = env("REMOTION_LAMBDA_BUCKET", "")
SERVE_URL = env("REMOTION_LAMBDA_SERVE_URL", "")
FPS = 30

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=env("REMOTION_AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=env("REMOTION_AWS_SECRET_ACCESS_KEY"),
        )
    return _s3_client


def upload_asset(path: Path, prefix: str = "uploads", expires_in: int = 21600) -> str:
    """Upload a file to the Remotion Lambda S3 bucket and return a presigned
    URL (6h default expiry — plenty for a render to fetch it), avoiding any
    need for public bucket/object ACLs."""
    path = Path(path)
    key = f"{prefix}/{path.name}"
    s3 = _get_s3()
    s3.upload_file(str(path), S3_BUCKET, key)
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=expires_in
    )


def probe_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def render_main_video_lambda(
    background_video: Path,
    photo_cutout: Path,
    audio_path: Path,
    captions: list,
    intro_hook: str,
    out_path: Path,
) -> Path:
    if not SERVE_URL or not S3_BUCKET:
        raise RuntimeError("REMOTION_LAMBDA_SERVE_URL / REMOTION_LAMBDA_BUCKET not set in .env")

    audio_info = sf.info(str(audio_path))
    duration_frames = round(audio_info.frames / audio_info.samplerate * FPS)
    background_duration_frames = round(probe_duration_seconds(background_video) * FPS)

    print("[Lambda] Uploading assets to S3...")
    background_url = upload_asset(background_video)
    photo_url = upload_asset(photo_cutout)
    audio_url = upload_asset(audio_path)

    props = {
        "backgroundVideoSrc": background_url,
        "backgroundVideoDurationInFrames": background_duration_frames,
        "photoSrc": photo_url,
        "audioSrc": audio_url,
        "captions": captions,
        "introHookText": intro_hook,
        "durationInFrames": duration_frames,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f)
        props_path = f.name

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Cap concurrent Lambda invocations to stay under the AWS account's
    # concurrency quota (new accounts often start as low as 10 — see build
    # log). framesPerLambda is chosen so total invocations stay well under
    # that; raise this once the account's quota increase is approved.
    frames_per_lambda = max(1, -(-duration_frames // 8))  # ceil division, ~8 lambdas
    cmd = (
        f'npx remotion lambda render "{SERVE_URL}" MainVideo "{out_path}" '
        f'--region={AWS_REGION} --props="{props_path}" --frames-per-lambda={frames_per_lambda}'
    )
    print("[Lambda] Rendering (typically minutes, not hours)...")
    subprocess.run(cmd, cwd=REMOTION_DIR, check=True, shell=True)
    return out_path
