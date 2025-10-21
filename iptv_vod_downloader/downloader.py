"""Download queue and worker management."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

import requests

from .utils import ensure_directory

StatusCallback = Callable[["DownloadItem"], None]


@dataclass
class DownloadItem:
    """Represents a queued movie or episode download."""

    item_id: str
    title: str
    stream_url: str
    target_path: Path
    kind: str = "movie"  # either "movie" or "episode"
    meta: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    progress: float = 0.0
    error: Optional[str] = None
    queue_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "target_path": str(self.target_path),
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "meta": self.meta,
            "queue_id": self.queue_id,
        }


class DownloadManager:
    """Simple serial download worker."""

    def __init__(self, callback: Optional[StatusCallback] = None) -> None:
        self._queue: List[DownloadItem] = []
        self._lock = threading.Lock()
        self._has_items = threading.Event()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._callback = callback

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run, name="DownloadWorker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._has_items.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)

    def add_items(self, items: Iterable[DownloadItem]) -> None:
        with self._lock:
            for item in items:
                self._queue.append(item)
                self._notify(item)
            if self._queue:
                self._has_items.set()

    def remove_item(self, queue_id: str) -> bool:
        with self._lock:
            for idx, item in enumerate(self._queue):
                if item.queue_id == queue_id and item.status == "queued":
                    del self._queue[idx]
                    item.status = "removed"
                    self._notify(item)
                    return True
        return False

    def queued_items(self) -> List[DownloadItem]:
        with self._lock:
            return list(self._queue)

    # Internal helpers -------------------------------------------------

    def _run(self) -> None:
        session = requests.Session()
        while not self._stop_event.is_set():
            item = self._next_item()
            if item is None:
                self._has_items.wait(timeout=0.5)
                continue
            self._download_item(session, item)

    def _next_item(self) -> Optional[DownloadItem]:
        with self._lock:
            if not self._queue:
                self._has_items.clear()
                return None
            # pop the first queued task
            return self._queue.pop(0)

    def _download_item(self, session: requests.Session, item: DownloadItem) -> None:
        item.status = "downloading"
        item.progress = 0.0
        item.error = None
        self._notify(item)

        target = item.target_path
        ensure_directory(target.parent)
        temp_path = target.with_suffix(target.suffix + ".part")

        if target.exists():
            item.status = "completed"
            item.progress = 1.0
            item.error = None
            self._notify(item)
            return

        try:
            with session.get(item.stream_url, stream=True, timeout=(5, 300)) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                chunk_size = 1024 * 512  # 512 KiB

                with temp_path.open("wb") as fh:
                    start_time = time.time()
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if self._stop_event.is_set():
                            raise RuntimeError("Download stopped by user.")
                        if not chunk:
                            continue
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            item.progress = downloaded / total
                        else:
                            # fallback to time-based updates
                            elapsed = time.time() - start_time
                            item.progress = min(0.99, elapsed / 10.0)
                        self._notify(item)

            temp_path.replace(target)
            item.status = "completed"
            item.progress = 1.0
            self._notify(item)
        except Exception as exc:  # pragma: no cover - runtime safeguard
            item.status = "failed"
            item.error = str(exc)
            item.progress = 0.0
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self._notify(item)

    def _notify(self, item: DownloadItem) -> None:
        if self._callback:
            self._callback(item)
