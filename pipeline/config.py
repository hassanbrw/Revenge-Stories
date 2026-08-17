"""Shared config/env loading for all pipeline stages."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_channel(channel_id: str) -> dict:
    path = ROOT / "config" / "channels" / f"{channel_id}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(name: str) -> str:
    path = ROOT / "config" / "prompts" / f"{name}.txt"
    return path.read_text(encoding="utf-8")
