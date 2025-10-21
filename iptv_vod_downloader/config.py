"""Configuration helpers for the IPTV VOD downloader."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".iptv_vod_downloader"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    """Serializable application configuration."""

    base_url: str = ""
    username: str = ""
    password: str = ""
    download_dir: str = str(Path.home() / "Downloads" / "IPTV-VOD")

    def is_complete(self) -> bool:
        """Return True when the configuration looks usable."""
        return all(
            [
                self.base_url.strip(),
                self.username.strip(),
                self.password.strip(),
                self.download_dir.strip(),
            ]
        )


class ConfigManager:
    """Persist and retrieve :class:`AppConfig` instances."""

    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = path
        self._config = AppConfig()
        self.load()

    @property
    def config(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        if not self.path.exists():
            self._config = AppConfig()
            return self._config

        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw: Dict[str, Any] = json.load(fh)
        except (json.JSONDecodeError, OSError):
            self._config = AppConfig()
            return self._config

        self._config = AppConfig(**{**asdict(AppConfig()), **raw})
        return self._config

    def save(self, config: AppConfig | None = None) -> None:
        if config is not None:
            self._config = config

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(asdict(self._config), fh, indent=2)

    def update(self, **kwargs: Any) -> AppConfig:
        data = asdict(self._config)
        data.update({k: v for k, v in kwargs.items() if v is not None})
        self._config = AppConfig(**data)
        self.save()
        return self._config
