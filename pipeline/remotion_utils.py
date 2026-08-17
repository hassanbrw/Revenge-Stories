"""Shared helper for staging generated files where Remotion's renderer is
allowed to read them.

Remotion's headless Chrome renderer refuses to load local files outside the
project's public/ folder, and Remotion re-copies the *entire* public/ folder
before every render — so dumping large generated media (hundreds of MB per
video) directly into remotion/public/ makes every future render slower as
files accumulate there over time. Instead, each render gets its own small
scratch "public dir" containing only that run's files, pointed to via the
REMOTION_ASSET_DIR env var, which remotion.config.ts picks up via
Config.setPublicDir().
"""

import shutil
import tempfile
from pathlib import Path

from pipeline.config import ROOT

REMOTION_DIR = ROOT / "remotion"


def stage_assets(*paths: Path) -> tuple[str, dict]:
    """Copy the given files into a fresh scratch directory. Returns
    (asset_dir, {original_path: relative_filename}) — set REMOTION_ASSET_DIR
    to asset_dir when invoking the Remotion CLI, and use the filenames as
    prop values (components resolve them via staticFile())."""
    asset_dir = Path(tempfile.mkdtemp(prefix="remotion_assets_"))
    mapping = {}
    for path in paths:
        path = Path(path)
        dest = asset_dir / path.name
        shutil.copy(path, dest)
        mapping[path] = path.name
    return str(asset_dir), mapping
