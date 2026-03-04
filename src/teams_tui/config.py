"""Application configuration and XDG paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir


APP_NAME = "teams-tui"


@dataclass
class Config:
    """Holds all runtime paths and settings."""

    data_dir: Path = field(default_factory=lambda: Path(user_data_dir(APP_NAME)))
    display_server: str = field(default="x11")

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        cfg.display_server = _detect_display_server()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        return cfg

    @property
    def browser_profile_dir(self) -> Path:
        """Chromium persistent profile directory."""
        p = self.data_dir / "chromium-profile"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_db_path(self) -> Path:
        return self.data_dir / "cache.db"

    @property
    def is_wayland(self) -> bool:
        return self.display_server == "wayland"


def _detect_display_server() -> str:
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        return "wayland"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11"
