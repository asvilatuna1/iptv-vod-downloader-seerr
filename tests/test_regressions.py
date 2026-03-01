import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from iptv_vod_downloader.config import WindowState
from iptv_vod_downloader.downloader import DownloadItem, DownloadManager
from iptv_vod_downloader.gui import IPTVApp


class QueueRegressionTests(unittest.TestCase):
    def test_clear_completed_uses_single_batch_delete(self) -> None:
        app = IPTVApp.__new__(IPTVApp)
        app.queue_items = {
            "done-1": {"status": "completed"},
            "queued-1": {"status": "queued"},
            "done-2": {"status": "completed"},
        }
        app._delete_queue_entries = Mock()

        app._clear_completed_downloads()

        app._delete_queue_entries.assert_called_once_with(["done-1", "done-2"])

    def test_progress_update_does_not_force_full_queue_refresh(self) -> None:
        app = IPTVApp.__new__(IPTVApp)
        app.queue_items = {
            "q1": {"queue_id": "q1", "status": "downloading", "progress": 0.1},
        }
        app._schedule_queue_state_save = Mock()
        app._update_queue_tree_item = Mock()

        queue_refresh, catalog_refresh = app._update_queue_row(
            {"queue_id": "q1", "status": "downloading", "progress": 0.2}
        )

        self.assertFalse(queue_refresh)
        self.assertFalse(catalog_refresh)
        app._schedule_queue_state_save.assert_not_called()
        app._update_queue_tree_item.assert_called_once_with(
            "q1", {"queue_id": "q1", "status": "downloading", "progress": 0.2}
        )

    def test_new_queue_item_still_requests_refresh(self) -> None:
        app = IPTVApp.__new__(IPTVApp)
        app.queue_items = {}
        app._schedule_queue_state_save = Mock()
        app._update_queue_tree_item = Mock()

        queue_refresh, catalog_refresh = app._update_queue_row(
            {"queue_id": "q2", "status": "queued", "progress": 0.0}
        )

        self.assertTrue(queue_refresh)
        self.assertTrue(catalog_refresh)
        app._schedule_queue_state_save.assert_called_once()
        app._update_queue_tree_item.assert_not_called()

    def test_start_requeues_stopped_downloads_when_queue_is_empty(self) -> None:
        app = IPTVApp.__new__(IPTVApp)
        app.queue_items = {
            "q1": {"queue_id": "q1", "status": "stopped"},
            "q2": {"queue_id": "q2", "status": "stopped"},
        }
        app.download_manager = Mock()
        app.status_var = Mock()
        requeue_payload = [Mock(), Mock()]
        app._collect_restartable_queue_items = Mock(return_value=requeue_payload)

        app._start_downloads()

        app._collect_restartable_queue_items.assert_called_once_with({"stopped"})
        app.download_manager.add_items.assert_called_once_with(requeue_payload)
        app.download_manager.resume.assert_called_once()
        app.status_var.set.assert_called_with("Downloads running.")

    def test_start_does_not_lie_when_nothing_can_run(self) -> None:
        app = IPTVApp.__new__(IPTVApp)
        app.queue_items = {"q1": {"queue_id": "q1", "status": "completed"}}
        app.download_manager = Mock()
        app.status_var = Mock()
        app._collect_restartable_queue_items = Mock(return_value=[])

        app._start_downloads()

        app.download_manager.add_items.assert_not_called()
        app.download_manager.resume.assert_not_called()
        app.status_var.set.assert_called_with("No queued downloads to start.")


class DownloadNotificationTests(unittest.TestCase):
    def test_progress_notifications_are_throttled(self) -> None:
        events = []
        manager = DownloadManager(callback=lambda item: events.append((item.status, round(item.progress, 2))))
        item = DownloadItem(
            item_id="1",
            title="Example",
            stream_url="http://example.test/video.mp4",
            target_path=Path("video.mp4"),
        )
        item.status = "downloading"

        with patch("iptv_vod_downloader.downloader.time.monotonic", side_effect=[0.0, 0.05, 0.30]):
            item.progress = 0.01
            manager._notify(item)
            item.progress = 0.02
            manager._notify(item)
            item.progress = 0.03
            manager._notify(item)

        self.assertEqual(events, [("downloading", 0.01), ("downloading", 0.03)])

    def test_force_notification_bypasses_throttle(self) -> None:
        events = []
        manager = DownloadManager(callback=lambda item: events.append(item.status))
        item = DownloadItem(
            item_id="1",
            title="Example",
            stream_url="http://example.test/video.mp4",
            target_path=Path("video.mp4"),
        )
        item.status = "downloading"

        with patch("iptv_vod_downloader.downloader.time.monotonic", side_effect=[0.0, 0.05]):
            manager._notify(item)
            item.status = "completed"
            item.progress = 1.0
            manager._notify(item, force=True)

        self.assertEqual(events, ["downloading", "completed"])

    def test_pause_interrupt_is_requeued_even_if_resume_was_already_pressed(self) -> None:
        manager = DownloadManager(callback=None)
        item = DownloadItem(
            item_id="1",
            title="Example",
            stream_url="http://example.test/video.mp4",
            target_path=Path("video.mp4"),
        )
        manager._pause_requested_queue_id = item.queue_id
        manager._paused = False

        class FailingResponse:
            def __enter__(self):
                raise requests.ConnectionError("interrupted")

            def __exit__(self, exc_type, exc, tb):
                return False

        session = Mock()
        session.get.return_value = FailingResponse()

        manager._download_item(session, item)

        self.assertEqual(item.status, "paused")
        self.assertIsNone(item.error)
        self.assertEqual(manager.queued_items(), [item])
        self.assertIsNone(manager._pause_requested_queue_id)

    def test_generic_interrupt_during_pause_is_requeued(self) -> None:
        manager = DownloadManager(callback=None)
        item = DownloadItem(
            item_id="1",
            title="Example",
            stream_url="http://example.test/video.mp4",
            target_path=Path("video.mp4"),
        )
        manager._pause_requested_queue_id = item.queue_id
        manager._paused = False

        class FailingResponse:
            def __enter__(self):
                raise RuntimeError("stream reader closed")

            def __exit__(self, exc_type, exc, tb):
                return False

        session = Mock()
        session.get.return_value = FailingResponse()

        manager._download_item(session, item)

        self.assertEqual(item.status, "paused")
        self.assertIsNone(item.error)
        self.assertEqual(manager.queued_items(), [item])


class UIStateTests(unittest.TestCase):
    def test_window_state_defaults_match_queue_controls(self) -> None:
        state = WindowState()
        self.assertEqual(state.queue_filter, "All")
        self.assertEqual(state.queue_sort, "Insertion order")


if __name__ == "__main__":
    unittest.main()
