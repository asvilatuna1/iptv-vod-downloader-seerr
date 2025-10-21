"""Utility helpers for filesystem-safe naming and formatting."""

from __future__ import annotations

import re
from pathlib import Path

INVALID_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1F]'
INVALID_RE = re.compile(INVALID_FILENAME_CHARS)
WHITESPACE_RE = re.compile(r"\s+")


def sanitise_filename(value: str, replacement: str = "_") -> str:
    """Return a filesystem safe representation of *value*."""
    cleaned = INVALID_RE.sub(replacement, value.strip())
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_episode_filename(season: int, episode: int, title: str, extension: str) -> str:
    safe_title = sanitise_filename(title)
    return f"S{season:02d}E{episode:02d} - {safe_title}.{extension}"
