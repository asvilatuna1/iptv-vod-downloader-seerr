"""Download queue and worker management."""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

import requests

from .api import DEFAULT_HEADERS
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
    _last_notified_status: str = field(default="", init=False, repr=False)
    _last_notified_percent: int = field(default=-1, init=False, repr=False)
    _last_notified_error: Optional[str] = field(default=None, init=False, repr=False)
    _last_notify_at: float = field(default=0.0, init=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "stream_url": self.stream_url,
            "target_path": str(self.target_path),
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "meta": self.meta,
            "queue_id": self.queue_id,
        }


class DownloadCancelled(RuntimeError):
    """Raised when a single queued download is cancelled by the user."""


class DownloadStopped(RuntimeError):
    """Raised when the active download is stopped by the user."""


class DownloadManager:
    """Simple serial download worker."""

    _progress_notify_interval = 0.2
    _idle_wait_timeout = 0.1
    _connect_timeout = 5
    _read_timeout = 10
    _chunk_size = 1024 * 128  # 128 KiB

    def __init__(self, callback: Optional[StatusCallback] = None) -> None:
        self._queue: List[DownloadItem] = []
        self._lock = threading.Lock()
        self._has_items = threading.Event()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._paused = False
        self._worker: Optional[threading.Thread] = None
        self._callback = callback
        self._current_item: Optional[DownloadItem] = None
        self._current_response: Optional[requests.Response] = None
        self._cancelled_queue_ids: set[str] = set()
        self._pause_requested_queue_id: Optional[str] = None

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._paused = False
        self._worker = threading.Thread(target=self._run, name="DownloadWorker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._has_items.set()
        self._interrupt_current_download()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)

    def pause(self) -> None:
        self._paused = True
        self._pause_event.clear()
        item = self._current_item
        if item and item.status == "downloading":
            self._pause_requested_queue_id = item.queue_id
            item.status = "paused"
            self._notify(item, force=True)
            self._interrupt_current_download()

    def resume(self) -> None:
        self.start()
        self._paused = False
        self._pause_event.set()
        item = self._current_item
        if item and item.status == "paused":
            item.status = "downloading"
            self._notify(item)

    def stop_all(self) -> None:
        self._paused = False
        self._pause_event.set()
        self._stop_event.set()
        self._has_items.set()
        with self._lock:
            queued = list(self._queue)
            self._queue.clear()
        for item in queued:
            item.status = "stopped"
            item.error = "stopped by user"
            self._notify(item)

        current = self._current_item
        if current and current.status in {"downloading", "paused"}:
            current.status = "stopped"
            current.error = "stopped by user"
            self._notify(current, force=True)
        self._interrupt_current_download()

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
                if item.queue_id == queue_id and item.status in {"queued", "paused"}:
                    del self._queue[idx]
                    item.status = "removed"
                    self._notify(item)
                    return True
            current = self._current_item
            if current and current.queue_id == queue_id and current.status in {"downloading", "paused"}:
                self._cancelled_queue_ids.add(queue_id)
                self._interrupt_current_download()
                return True
        return False

    def queued_items(self) -> List[DownloadItem]:
        with self._lock:
            return list(self._queue)

    # Internal helpers -------------------------------------------------

    def _run(self) -> None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        # Streaming endpoints return raw media so relax Accept header.
        session.headers["Accept"] = "*/*"
        while not self._stop_event.is_set():
            self._pause_event.wait()
            item = self._next_item()
            if item is None:
                self._has_items.wait(timeout=self._idle_wait_timeout)
                continue
            self._download_item(session, item)
        self._current_response = None
        self._worker = None
        self._stop_event.clear()
        self._current_item = None

    def _next_item(self) -> Optional[DownloadItem]:
        with self._lock:
            if not self._queue:
                self._has_items.clear()
                return None
            # pop the first queued task
            item = self._queue.pop(0)
            self._current_item = item
            return item

    def _download_item(self, session: requests.Session, item: DownloadItem) -> None:
        item.status = "downloading"
        item.error = None
        self._notify(item, force=True)

        target = item.target_path
        ensure_directory(target.parent)
        temp_path = target.with_suffix(target.suffix + ".part")
        existing_size = temp_path.stat().st_size if temp_path.exists() else 0

        if target.exists():
            item.status = "completed"
            item.progress = 1.0
            item.error = None
            self._notify(item, force=True)
            self._current_item = None
            return

        try:
            request_headers: dict[str, str] = {}
            if existing_size:
                request_headers["Range"] = f"bytes={existing_size}-"
            with session.get(
                item.stream_url,
                stream=True,
                timeout=(self._connect_timeout, self._read_timeout),
                headers=request_headers,
            ) as resp:
                self._current_response = resp
                resp.raise_for_status()
                total = self._resolve_total_size(resp, existing_size)
                downloaded = existing_size
                chunk_size = self._chunk_size

                file_mode = "ab" if existing_size and resp.status_code == 206 else "wb"
                if file_mode == "wb":
                    downloaded = 0
                    existing_size = 0

                if total:
                    item.progress = min(0.99, downloaded / total)
                    self._notify(item)

                with temp_path.open(file_mode) as fh:
                    start_time = time.time()
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if self._stop_event.is_set():
                            raise DownloadStopped("Download stopped by user.")
                        if item.queue_id in self._cancelled_queue_ids:
                            raise DownloadCancelled("Download cancelled by user.")
                        while self._paused and not self._stop_event.is_set():
                            if item.queue_id in self._cancelled_queue_ids:
                                raise DownloadCancelled("Download cancelled by user.")
                            item.status = "paused"
                            self._notify(item)
                            self._pause_event.wait(timeout=0.2)
                        if item.status == "paused" and not self._paused:
                            item.status = "downloading"
                            self._notify(item)
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
            item.error = None
            self._notify(item, force=True)
        except requests.RequestException as exc:
            if not self._handle_transfer_exception(item, temp_path, exc):
                item.status = "failed"
                item.error = str(exc)
                self._notify(item, force=True)
        except DownloadCancelled as exc:
            item.status = "cancelled"
            item.error = str(exc)
            item.progress = 0.0
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self._notify(item, force=True)
        except DownloadStopped as exc:
            item.status = "stopped"
            item.error = str(exc)
            self._notify(item, force=True)
        except Exception as exc:  # pragma: no cover - runtime safeguard
            if not self._handle_transfer_exception(item, temp_path, exc):
                item.status = "failed"
                item.error = str(exc)
                self._notify(item, force=True)
        finally:
            self._current_response = None
            if self._pause_requested_queue_id == item.queue_id:
                self._pause_requested_queue_id = None
            with suppress(KeyError):
                self._cancelled_queue_ids.remove(item.queue_id)
            self._current_item = None

    def _handle_transfer_exception(self, item: DownloadItem, temp_path: Path, exc: Exception) -> bool:
        if item.queue_id in self._cancelled_queue_ids:
            item.status = "cancelled"
            item.error = "Download cancelled by user."
            item.progress = 0.0
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self._notify(item, force=True)
            return True
        if self._stop_event.is_set():
            item.status = "stopped"
            item.error = "Download stopped by user."
            self._notify(item, force=True)
            return True
        if self._pause_requested_queue_id == item.queue_id or self._paused:
            item.status = "paused"
            item.error = None
            self._requeue_front(item)
            self._notify(item, force=True)
            return True
        return False

    def _requeue_front(self, item: DownloadItem) -> None:
        with self._lock:
            self._queue.insert(0, item)
            self._has_items.set()

    def _interrupt_current_download(self) -> None:
        response = self._current_response
        if response is None:
            return
        with suppress(Exception):
            response.close()

    def _notify(self, item: DownloadItem, force: bool = False) -> None:
        if not self._callback:
            return

        percent = max(0, min(100, int(item.progress * 100)))
        now = time.monotonic()
        status_changed = item.status != item._last_notified_status
        error_changed = item.error != item._last_notified_error
        percent_changed = percent != item._last_notified_percent

        if not force and not status_changed and not error_changed:
            if not percent_changed:
                return
            if now - item._last_notify_at < self._progress_notify_interval:
                return

        self._callback(item)
        item._last_notified_status = item.status
        item._last_notified_percent = percent
        item._last_notified_error = item.error
        item._last_notify_at = now

    @staticmethod
    def _resolve_total_size(resp: requests.Response, existing_size: int) -> int:
        content_range = resp.headers.get("Content-Range", "")
        if "/" in content_range:
            tail = content_range.rsplit("/", 1)[-1]
            if tail.isdigit():
                return int(tail)
        content_length = resp.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            length = int(content_length)
            if resp.status_code == 206:
                return existing_size + length
            return length
        return 0
