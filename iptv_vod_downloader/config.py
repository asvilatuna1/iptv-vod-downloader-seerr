"""Configuration helpers and persisted UI state for the IPTV VOD downloader."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List

CONFIG_DIR = Path.home() / ".iptv_vod_downloader"
CONFIG_FILE = CONFIG_DIR / "config.json"
QUEUE_STATE_FILE = CONFIG_DIR / "queue_state.json"
UI_STATE_FILE = CONFIG_DIR / "ui_state.json"


@dataclass
class AppConfig:
    """Serializable application configuration."""

    base_url: str = ""
    username: str = ""
    password: str = ""
    download_dir: str = str(Path.home() / "Downloads" / "IPTV-VOD")
    seerr_url: str = ""      # NUEVO: URL de Seerr
    seerr_api_key: str = ""  # NUEVO: API Key de Seerr

    def is_complete(self) -> bool:
        """Return True when the configuration looks usable."""
        # Seerr es opcional, así que solo obligamos a llenar lo de IPTV
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


@dataclass
class WindowState:
    """Persisted window and UI preferences."""

    geometry: str = "1200x800"
    selected_tab: str = "movies"
    queue_filter: str = "All"
    queue_sort: str = "Insertion order"


class JSONStateManager:
    """Small JSON-backed state store."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return default

    def save(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)


class QueueStateManager(JSONStateManager):
    """Persist visible queue entries between app launches."""

    def __init__(self, path: Path = QUEUE_STATE_FILE) -> None:
        super().__init__(path)

    def load_items(self) -> List[Dict[str, Any]]:
        data = self.load(default=[])
        return data if isinstance(data, list) else []

    def save_items(self, items: List[Dict[str, Any]]) -> None:
        self.save(items)


class UIStateManager(JSONStateManager):
    """Persist window geometry and simple UI preferences."""

    def __init__(self, path: Path = UI_STATE_FILE) -> None:
        super().__init__(path)

    def load_state(self) -> WindowState:
        data = self.load(default={})
        if not isinstance(data, dict):
            return WindowState()
        return WindowState(**{**asdict(WindowState()), **data})

    def save_state(self, state: WindowState) -> None:
        self.save(asdict(state))
