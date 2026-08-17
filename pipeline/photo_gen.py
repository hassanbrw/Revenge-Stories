"""Protagonist photo processing — the user supplies one photo per video;
this produces a background-removed transparent cutout (for the video body
overlay) while keeping the original (for the thumbnail background panel
and corner crop).
"""

import shutil
import sys
from pathlib import Path

from pipeline.config import ROOT


def process_photo(input_photo: Path, channel_id: str, slug: str) -> dict:
    photo_dir = ROOT / "assets" / "photos" / channel_id
    photo_dir.mkdir(parents=True, exist_ok=True)

    input_photo = Path(input_photo)
    original_path = photo_dir / f"{slug}_original{input_photo.suffix}"
    shutil.copy(input_photo, original_path)

    cutout_path = photo_dir / f"{slug}_cutout.png"
    _remove_background(original_path, cutout_path)

    return {"original": original_path, "cutout": cutout_path}


def _remove_background(input_path: Path, output_path: Path) -> None:
    from io import BytesIO

    from PIL import Image
    from rembg import remove

    with open(input_path, "rb") as f:
        input_data = f.read()
    output_data = remove(input_data)

    # rembg's output canvas keeps the original photo's full dimensions, so
    # the subject often sits off-center within it (e.g. empty margin above
    # the head but none below the shoulders) — cropping to the actual
    # opaque bounding box is what makes "center the photo" downstream
    # actually center the person, not just the canvas (see build log).
    cutout = Image.open(BytesIO(output_data))
    bbox = cutout.split()[-1].getbbox()
    if bbox:
        cutout = cutout.crop(bbox)
    cutout.save(output_path)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python -m pipeline.photo_gen <photo_path> <channel_id> <slug>")
        sys.exit(1)
    result = process_photo(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    print(f"Original: {result['original']}")
    print(f"Cutout:   {result['cutout']}")
