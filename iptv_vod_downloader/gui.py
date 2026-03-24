"""Tkinter GUI for the IPTV VOD downloader."""

from __future__ import annotations

import io
import queue
import threading
import tkinter as tk
import sys
import re
from pathlib import Path
from urllib.parse import urljoin
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image, ImageTk

from .api import APIError, IPTVClient
from .config import AppConfig, ConfigManager, QueueStateManager, UIStateManager, WindowState
from .downloader import DownloadItem, DownloadManager
from .utils import build_episode_filename, match_search_term, sanitise_filename


STATUS_LABELS = {
    "queued": "Queued",
    "downloading": "Downloading",
    "completed": "Completed",
    "failed": "Error",
    "paused": "Paused",
    "stopped": "Stopped",
    "removed": "Removed",
    "cancelled": "Cancelled",
}


class SeriesEpisodesDialog(tk.Toplevel):
    """Dialog used to select episodes to download for a given series."""

    def __init__(
        self,
        parent: tk.Tk,
        client: IPTVClient,
        series: Dict[str, Any],
        callback,
    ) -> None:
        super().__init__(parent)
        self.title(series.get("name", "Series"))
        self.resizable(True, True)
        self.geometry("980x720")
        self.minsize(900, 640)
        self.parent = parent
        self.client = client
        self.series = series
        self.callback = callback

        self.episodes_map: Dict[str, Dict[str, Any]] = {}
        self.season_episodes: Dict[int, List[str]] = {}
        self._season_labels: Dict[str, int] = {}
        self.all_episode_ids: List[str] = []

        self.season_var = tk.StringVar()

        self.status_var = tk.StringVar(value="Loading episodes...")

        status_label = ttk.Label(self, textvariable=self.status_var)
        status_label.pack(fill="x", padx=10, pady=(10, 0))

        content_frame = ttk.Frame(self)
        content_frame.pack(expand=True, fill="both", padx=10, pady=10)

        poster_frame = ttk.Frame(content_frame, width=220)
        poster_frame.pack(side="left", fill="y", padx=(0, 10))
        poster_frame.pack_propagate(False)

        self.poster_label = ttk.Label(
            poster_frame,
            text="Poster unavailable",
            anchor="center",
            justify="center",
            wraplength=200,
        )
        self.poster_label.pack(expand=True, fill="both")
        self.poster_image: Optional[ImageTk.PhotoImage] = None

        tree_frame = ttk.Frame(content_frame)
        tree_frame.pack(side="left", expand=True, fill="both")

        self.tree = ttk.Treeview(tree_frame, columns=("episode", "title"), show="headings", selectmode="extended")
        self.tree.heading("episode", text="Episode number")
        self.tree.heading("title", text="Episode title")
        self.tree.column("episode", width=130, anchor="center", stretch=False)
        self.tree.column("title", width=560, anchor="w")
        self.tree.tag_configure("season_header", background="#e9edf2", font=("TkDefaultFont", 9, "bold"))
        self.tree.pack(expand=True, fill="both")
        self.tree.bind("<Button-3>", self._show_episode_menu)

        season_frame = ttk.Frame(self)
        season_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(season_frame, text="Season").pack(side="left")
        self.season_combo = ttk.Combobox(season_frame, state="readonly", textvariable=self.season_var, width=20, values=())
        self.season_combo.pack(side="left", padx=5)
        ttk.Button(season_frame, text="Select season", command=self._select_current_season).pack(side="left", padx=5)
        ttk.Button(season_frame, text="Queue season", command=self._add_current_season).pack(side="left", padx=5)
        ttk.Button(season_frame, text="Queue full series", command=self._add_entire_series).pack(side="right")

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(button_frame, text="Select all", command=self._select_all).pack(side="left")
        add_button = ttk.Button(button_frame, text="Queue selected episodes", command=self.on_confirm)
        add_button.pack(side="left", padx=5)

        close_button = ttk.Button(button_frame, text="Close", command=self.destroy)
        close_button.pack(side="right")

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Select all", command=self._select_all)
        self.context_menu.add_command(label="Select season", command=self._select_current_season)
        self.context_menu.add_command(label="Queue selected episodes", command=self.on_confirm)
        self.context_menu.add_command(label="Queue season", command=self._add_current_season)
        self.context_menu.add_command(label="Queue full series", command=self._add_entire_series)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

        threading.Thread(target=self._load_episodes, daemon=True).start()

    def _load_episodes(self) -> None:
        poster_bytes: Optional[bytes] = None
        try:
            info = self.client.get_series_info(str(self.series["series_id"]))
            episodes = info.get("episodes", {})
            poster_bytes = self._download_poster_bytes(info)
        except Exception as exc:  # pragma: no cover - runtime safeguard
            self.after(0, lambda: self._set_error(str(exc)))
            return

        def populate() -> None:
            self.tree.delete(*self.tree.get_children())
            self.episodes_map.clear()
            self.season_episodes.clear()
            self._season_labels.clear()
            self.all_episode_ids = []
            for season_key in sorted(episodes.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                episodes_list = episodes[season_key]
                try:
                    season_num = int(season_key)
                except (TypeError, ValueError):
                    season_num = 0
                season_label = self._format_season_label(season_num)
                season_header_id = f"season-header-{season_num}"
                self.tree.insert(
                    "",
                    "end",
                    iid=season_header_id,
                    values=("", f"----- {season_label} -----"),
                    tags=("season_header",),
                )
                sorted_eps = sorted(
                    episodes_list,
                    key=lambda ep: int(ep.get("episode_num", 0) or 0),
                )
                for episode in sorted_eps:
                    episode_id = str(episode.get("id"))
                    episode_num = int(episode.get("episode_num", 0) or 0)
                    title = episode.get("title") or episode.get("name") or f"Episode {episode_num}"
                    values = (episode_num, title)
                    self.tree.insert("", "end", iid=episode_id, values=values)
                    self.episodes_map[episode_id] = {
                        "season": season_num,
                        "episode": episode,
                    }
                    self.season_episodes.setdefault(season_num, []).append(episode_id)
                    self.all_episode_ids.append(episode_id)

            season_labels: List[str] = []
            for season in sorted(self.season_episodes.keys()):
                label = self._format_season_label(season)
                season_labels.append(label)
                self._season_labels[label] = season

            if season_labels:
                self.season_combo["values"] = season_labels
                self.season_var.set(season_labels[0])
            else:
                self.season_combo["values"] = ()
                self.season_var.set("")

            self._update_poster(poster_bytes)
            self.status_var.set("Select the episodes to download.")

        self.after(0, populate)

    def _download_poster_bytes(self, info: Dict[str, Any]) -> Optional[bytes]:
        poster_url = self._extract_poster_url(info)
        if not poster_url:
            return None
        try:
            return self.client.fetch_resource(poster_url)
        except Exception:
            return None

    def _extract_poster_url(self, series_info: Dict[str, Any]) -> Optional[str]:
        raw_info = series_info.get("info")
        info_block = raw_info if isinstance(raw_info, dict) else {}
        candidates = [
            info_block.get("cover_big"),
            info_block.get("cover"),
            info_block.get("stream_icon"),
            self.series.get("cover_big"),
            self.series.get("cover"),
            self.series.get("stream_icon"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            url = str(candidate).strip()
            if not url:
                continue
            if url.startswith("http://") or url.startswith("https://"):
                return url
            return urljoin(f"{self.client.base_url}/", url.lstrip("/"))
        return None

    def _update_poster(self, data: Optional[bytes]) -> None:
        if not data:
            self.poster_label.configure(image="", text="Poster unavailable")
            self.poster_label.image = None  # type: ignore[attr-defined]
            self.poster_image = None
            return

        try:
            image = Image.open(io.BytesIO(data))
        except Exception:
            self.poster_label.configure(image="", text="Poster unavailable")
            self.poster_label.image = None  # type: ignore[attr-defined]
            self.poster_image = None
            return

        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")

        image.thumbnail((220, 330), Image.LANCZOS)
        poster = ImageTk.PhotoImage(image)
        self.poster_label.configure(image=poster, text="")
        self.poster_label.image = poster  # type: ignore[attr-defined]
        self.poster_image = poster

    def _set_error(self, message: str) -> None:
        self.status_var.set(f"Error: {message}")
        messagebox.showerror("Error", f"Unable to load episodes:\n{message}", parent=self)

    def on_confirm(self) -> None:
        selection = self.tree.selection()
        payloads = self._build_payloads(selection)
        if not payloads:
            messagebox.showinfo("No selection", "Select at least one episode.", parent=self)
            return
        self.callback(payloads)
        self.destroy()

    @staticmethod
    def _format_season_label(season: int) -> str:
        return "Specials" if season <= 0 else f"Season {season:02d}"

    def _build_payloads(self, episode_ids: Iterable[str]) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for item_id in episode_ids:
            data = self.episodes_map.get(item_id)
            if not data:
                continue
            payloads.append(
                {
                    "series": self.series,
                    "season": data["season"],
                    "episode": data["episode"],
                }
            )
        return payloads

    def _get_selected_season(self) -> Optional[int]:
        if not self._season_labels:
            return None
        return self._season_labels.get(self.season_var.get())

    def _select_all(self) -> None:
        self.tree.selection_set(*self.all_episode_ids)

    def _select_current_season(self) -> None:
        season = self._get_selected_season()
        if season is None:
            messagebox.showinfo("No season", "No season is available for selection.", parent=self)
            return
        episode_ids = self.season_episodes.get(season, [])
        if not episode_ids:
            messagebox.showinfo("Empty", "The selected season does not contain any available episodes.", parent=self)
            return
        self.tree.selection_set(*episode_ids)

    def _add_current_season(self) -> None:
        season = self._get_selected_season()
        if season is None:
            messagebox.showinfo("No season", "Select a season to queue.", parent=self)
            return
        episode_ids = self.season_episodes.get(season, [])
        self._queue_payloads(self._build_payloads(episode_ids), close_dialog=False)

    def _add_entire_series(self) -> None:
        self._queue_payloads(self._build_payloads(self.all_episode_ids), close_dialog=False)

    def _queue_payloads(self, payloads: List[Dict[str, Any]], close_dialog: bool) -> None:
        if not payloads:
            messagebox.showinfo("No episodes", "No episodes are available to queue.", parent=self)
            return
        self.callback(payloads)
        self.status_var.set(f"Queued {len(payloads)} episodes.")
        if close_dialog:
            self.destroy()

    def _show_episode_menu(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.tree.focus(iid)
        else:
            for sel in self.tree.selection():
                self.tree.selection_remove(sel)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()


class IPTVApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("IPTV VOD Downloader")
        self.config_manager = ConfigManager()
        self.queue_state_manager = QueueStateManager()
        self.ui_state_manager = UIStateManager()
        self.window_state: WindowState = self.ui_state_manager.load_state()
        self.geometry(self.window_state.geometry)
        self.minsize(1080, 760)

        self.current_config: AppConfig = self.config_manager.config
        self.client: Optional[IPTVClient] = None

        self.download_manager = DownloadManager(callback=self._on_download_update)
        self.download_manager.start()
        self._download_updates: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._catalog_request_token = 0
        self._items_request_tokens: Dict[str, int] = {"movies": 0, "series": 0}
        self._queue_state_save_job: Optional[str] = None

        self.category_indexes: Dict[str, List[str]] = {"movies": [], "series": []}
        self.items_map: Dict[str, Dict[str, Dict[str, Any]]] = {"movies": {}, "series": {}}
        self.queue_items: Dict[str, Dict[str, Any]] = {}

        self._create_widgets()
        self._load_config_into_form()
        self._load_queue_state()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.current_config.is_complete():
            self._ensure_client()
            self.refresh_catalog()

        self._restore_window_state()
        self.after(200, self._process_download_updates)

    # ------------------------------------------------------------------
    # UI construction helpers

    def _create_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.config_frame = ttk.LabelFrame(self, text="IPTV Connection & Seerr")
        self.config_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        for col in range(6):
            self.config_frame.columnconfigure(col, weight=1 if col % 2 == 1 else 0)

        self.base_url_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.download_dir_var = tk.StringVar()
        self.seerr_url_var = tk.StringVar()
        self.seerr_key_var = tk.StringVar()
        self.status_var = tk.StringVar()

        ttk.Label(self.config_frame, text="URL").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.base_url_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Username").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.username_var).grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Password").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.password_var, show="*").grid(row=1, column=3, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Download folder").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.download_dir_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        ttk.Button(self.config_frame, text="Browse", command=self._choose_download_dir).grid(row=2, column=3, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Seerr URL").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.seerr_url_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        ttk.Label(self.config_frame, text="Seerr API").grid(row=3, column=2, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.seerr_key_var).grid(row=3, column=3, sticky="ew", padx=5, pady=5)

        ttk.Button(self.config_frame, text="Save settings", command=self._save_config).grid(row=0, column=4, sticky="ew", padx=5, pady=5)
        ttk.Button(self.config_frame, text="Test connection", command=self._test_connection).grid(row=1, column=4, sticky="ew", padx=5, pady=5)
        ttk.Button(self.config_frame, text="Refresh catalog", command=self.refresh_catalog).grid(row=2, column=4, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, textvariable=self.status_var, foreground="gray").grid(row=4, column=0, columnspan=5, sticky="w", padx=5, pady=5)

        self.main_pane = ttk.Panedwindow(self, orient="vertical")
        self.main_pane.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        notebook_container = ttk.Frame(self.main_pane)
        notebook_container.columnconfigure(0, weight=1)
        notebook_container.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(notebook_container)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.movie_tab = self._create_catalog_tab("movies")
        self.series_tab = self._create_catalog_tab("series", is_series=True)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self._save_ui_state())

        self.queue_frame = ttk.LabelFrame(self.main_pane, text="Download Queue")
        self.queue_frame.columnconfigure(0, weight=1)
        self.queue_frame.rowconfigure(1, weight=1)

        queue_toolbar = ttk.Frame(self.queue_frame)
        queue_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=(5, 0))
        queue_toolbar.columnconfigure(6, weight=1)

        self.queue_filter_var = tk.StringVar(value=self.window_state.queue_filter)
        self.queue_sort_var = tk.StringVar(value=self.window_state.queue_sort)
        self.queue_summary_var = tk.StringVar(value="Queue is empty.")

        ttk.Label(queue_toolbar, text="Filter").grid(row=0, column=0, sticky="w", padx=(0, 5))
        queue_filter = ttk.Combobox(
            queue_toolbar,
            state="readonly",
            textvariable=self.queue_filter_var,
            width=16,
            values=("All", "Queued", "Downloading", "Paused", "Failed", "Stopped", "Completed"),
        )
        queue_filter.grid(row=0, column=1, sticky="w")
        queue_filter.bind("<<ComboboxSelected>>", lambda _event: (self._refresh_queue_view(), self._save_ui_state()))

        ttk.Label(queue_toolbar, text="Sort").grid(row=0, column=2, sticky="w", padx=(10, 5))
        queue_sort = ttk.Combobox(
            queue_toolbar,
            state="readonly",
            textvariable=self.queue_sort_var,
            width=16,
            values=("Insertion order", "Title", "Status", "Path"),
        )
        queue_sort.grid(row=0, column=3, sticky="w")
        queue_sort.bind("<<ComboboxSelected>>", lambda _event: (self._refresh_queue_view(), self._save_ui_state()))

        ttk.Label(queue_toolbar, textvariable=self.queue_summary_var, foreground="gray").grid(row=0, column=6, sticky="e")

        columns = ("title", "kind", "status", "progress", "path", "error")
        self.queue_tree = ttk.Treeview(self.queue_frame, columns=columns, show="headings", selectmode="extended")
        self.queue_tree.heading("title", text="Title")
        self.queue_tree.heading("kind", text="Type")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("progress", text="Progress")
        self.queue_tree.heading("path", text="Destination")
        self.queue_tree.heading("error", text="Details")
        self.queue_tree.column("title", width=380, anchor="w")
        self.queue_tree.column("kind", width=80, anchor="center")
        self.queue_tree.column("status", width=120, anchor="center")
        self.queue_tree.column("progress", width=150, anchor="center")
        self.queue_tree.column("path", width=300, anchor="w")
        self.queue_tree.column("error", width=240, anchor="w")
        self.queue_tree.grid(row=1, column=0, sticky="nsew")
        self.queue_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_queue_details())

        queue_scroll = ttk.Scrollbar(self.queue_frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        queue_scroll.grid(row=1, column=1, sticky="ns")

        self.queue_menu = tk.Menu(self.queue_tree, tearoff=0)
        self.queue_menu.add_command(label="Start downloads", command=self._start_downloads)
        self.queue_menu.add_command(label="Pause downloads", command=self._pause_downloads)
        self.queue_menu.add_command(label="Stop downloads", command=self._stop_downloads)
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Retry failed downloads", command=self._retry_selected_failed_downloads)
        self.queue_menu.add_command(label="Remove selected", command=self._remove_selected_from_queue)
        self.queue_menu.add_command(label="Clear completed", command=self._clear_completed_downloads)
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Open download folder", command=self._open_download_folder)
        self.queue_menu.add_command(label="Open selected folder", command=self._open_selected_download_folder)
        self.queue_tree.bind("<Button-3>", self._show_queue_menu)

        queue_buttons = ttk.Frame(self.queue_frame)
        queue_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=5, padx=5)
        for col in range(4):
            queue_buttons.columnconfigure(col, weight=1)
        ttk.Button(queue_buttons, text="Start", command=self._start_downloads).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Pause", command=self._pause_downloads).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Stop", command=self._stop_downloads).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Retry failed", command=self._retry_failed_downloads).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Clear completed", command=self._clear_completed_downloads).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Remove selected", command=self._remove_selected_from_queue).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Open download folder", command=self._open_download_folder).grid(row=1, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(queue_buttons, text="Open selected folder", command=self._open_selected_download_folder).grid(row=1, column=3, sticky="ew", padx=3, pady=3)

        self.queue_details_var = tk.StringVar(value="")
        ttk.Label(self.queue_frame, textvariable=self.queue_details_var, foreground="gray", anchor="w").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=(0, 5)
        )

        self.main_pane.add(notebook_container, weight=3)
        self.main_pane.add(self.queue_frame, weight=2)

    def _create_catalog_tab(self, kind: str, is_series: bool = False) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        if kind == "movies":
            self.notebook.add(frame, text="Movies")
        else:
            self.notebook.add(frame, text="TV Series")

        sidebar = ttk.Frame(frame)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(sidebar, text="Categories").pack(anchor="w", pady=(0, 5))

        listbox = tk.Listbox(sidebar, exportselection=False, height=20, width=28)
        listbox.pack(fill="both", expand=True)
        listbox.bind("<<ListboxSelect>>", lambda _event, k=kind: self._on_category_selected(k))

        refresh_button = ttk.Button(sidebar, text="Refresh", command=lambda k=kind: self._reload_items(k))
        refresh_button.pack(fill="x", pady=5)

        content = ttk.Frame(frame)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        search_frame = ttk.Frame(content)
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="Search").pack(side="left", padx=5)
        search_var = tk.StringVar()
        entry = ttk.Entry(search_frame, textvariable=search_var)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        entry.bind("<Return>", lambda _event, k=kind: self._on_search(k))
        ttk.Button(search_frame, text="Search", command=lambda k=kind: self._on_search(k)).pack(side="left")
        ttk.Button(search_frame, text="Clear", command=lambda v=search_var, k=kind: self._clear_search(k, v)).pack(side="left", padx=5)

        result_status_var = tk.StringVar(value="")
        ttk.Label(content, textvariable=result_status_var, foreground="gray", anchor="w").grid(row=1, column=0, sticky="ew", pady=(0, 5))

        columns = ("title", "year")
        tree = ttk.Treeview(content, columns=columns, show="headings", selectmode="extended")
        tree.heading("title", text="Title", command=lambda k=kind: self._set_catalog_sort(k, "Title"))
        tree.heading("year", text="Year", command=lambda k=kind: self._set_catalog_sort(k, "Year"))
        tree.column("title", width=420, anchor="w")
        tree.column("year", width=120, anchor="center")
        tree.grid(row=2, column=0, sticky="nsew")
        tree.tag_configure("queued_item", foreground="#0b5cad")
        tree.tag_configure("downloaded_item", foreground="#2b7a0b")

        scroll_y = ttk.Scrollbar(content, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.grid(row=2, column=1, sticky="ns")

        context_menu = tk.Menu(tree, tearoff=0)
        if is_series:
            context_menu.add_command(label="Open series", command=self._open_series_dialog)
            context_menu.add_command(label="Queue full series", command=self._queue_entire_selected_series)
        context_menu.add_command(label="Queue selected items", command=lambda k=kind: self._add_selected_to_queue(k))
        
        # NUEVO: Boton en clic derecho
        context_menu.add_separator()
        context_menu.add_command(label="Search in Seerr", command=lambda k=kind: self._search_in_seerr(k))

        tree.bind("<Button-3>", lambda event, m=context_menu, t=tree: self._show_tree_menu(event, t, m))
        if is_series:
            tree.bind("<Double-1>", self._on_series_tree_double_click)
        setattr(self, f"{kind}_menu", context_menu)

        action_frame = ttk.Frame(content)
        action_frame.grid(row=3, column=0, sticky="ew", pady=5)

        if is_series:
            ttk.Button(action_frame, text="Open series", command=self._open_series_dialog).pack(side="left", padx=5)
            ttk.Button(action_frame, text="Queue full series", command=self._queue_entire_selected_series).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Queue selected items", command=lambda k=kind: self._add_selected_to_queue(k)).pack(side="right", padx=5)

        setattr(self, f"{kind}_listbox", listbox)
        setattr(self, f"{kind}_tree", tree)
        setattr(self, f"{kind}_search_var", search_var)
        setattr(self, f"{kind}_results_var", result_status_var)
        setattr(self, f"{kind}_sort_mode", "Title")
        setattr(self, f"{kind}_sort_desc", False)
        self._update_catalog_headings(kind)

        return frame

    def _restore_window_state(self) -> None:
        try:
            tab_index = 0 if self.window_state.selected_tab == "movies" else 1
            self.notebook.select(tab_index)
        except tk.TclError:
            pass
        self.queue_filter_var.set(self.window_state.queue_filter)
        self.queue_sort_var.set(self.window_state.queue_sort)
        self._refresh_queue_view()

    def _save_ui_state(self) -> None:
        selected = "movies"
        try:
            selected = "movies" if self.notebook.index(self.notebook.select()) == 0 else "series"
        except tk.TclError:
            pass
        state = WindowState(
            geometry=self.geometry(),
            selected_tab=selected,
            queue_filter=self.queue_filter_var.get(),
            queue_sort=self.queue_sort_var.get(),
        )
        self.ui_state_manager.save_state(state)

    def _schedule_queue_state_save(self) -> None:
        if self._queue_state_save_job:
            self.after_cancel(self._queue_state_save_job)
        self._queue_state_save_job = self.after(300, self._save_queue_state)

    def _save_queue_state(self) -> None:
        self._queue_state_save_job = None
        items = [
            item
            for item in self.queue_items.values()
            if item.get("status") not in {"removed", "cancelled"}
        ]
        self.queue_state_manager.save_items(items)

    def _load_queue_state(self) -> None:
        for item in self.queue_state_manager.load_items():
            queue_id = item.get("queue_id")
            stream_url = item.get("stream_url")
            target_path = item.get("target_path")
            item_id = item.get("item_id")
            if not queue_id or not stream_url or not target_path or not item_id:
                continue
            status = str(item.get("status", "queued"))
            if status in {"queued", "paused", "downloading"}:
                self.download_manager.add_items(
                    [
                        DownloadItem(
                            item_id=str(item_id),
                            title=str(item.get("title", "Download")),
                            stream_url=str(stream_url),
                            target_path=Path(str(target_path)),
                            kind=str(item.get("kind", "movie")),
                            meta=dict(item.get("meta") or {}),
                            queue_id=str(queue_id),
                        )
                    ]
                )
            else:
                self._update_queue_row(item)
        self._refresh_queue_view()

    def _bind_shortcuts(self) -> None:
        self.bind("<F5>", lambda _event: self.refresh_catalog())
        self.bind("<Delete>", self._on_delete_pressed)
        self.bind("<Return>", self._on_return_pressed)
        self.bind("<Control-a>", self._on_select_all_pressed)

    def _on_close(self) -> None:
        self._save_ui_state()
        self._save_queue_state()
        self.download_manager.stop()
        self.destroy()

    def _test_connection(self) -> None:
        if not self._ensure_client():
            return
        self.status_var.set("Testing connection...")

        def worker() -> None:
            try:
                self.client.check_connection()
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: messagebox.showerror("Connection", f"Connection test failed:\n{exc}"))
                self.after(0, lambda: self.status_var.set(f"Error: {exc}"))
                return
            self.after(0, lambda: self.status_var.set("Connection successful."))
            self.after(0, lambda: messagebox.showinfo("Connection", "Connection successful."))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Config handling

    def _load_config_into_form(self) -> None:
        self.base_url_var.set(self.current_config.base_url)
        self.username_var.set(self.current_config.username)
        self.password_var.set(self.current_config.password)
        self.download_dir_var.set(self.current_config.download_dir)
        self.seerr_url_var.set(self.current_config.seerr_url)
        self.seerr_key_var.set(self.current_config.seerr_api_key)

    def _choose_download_dir(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.download_dir_var.get() or str(Path.home()))
        if directory:
            self.download_dir_var.set(directory)

    def _save_config(self) -> None:
        download_dir = self.download_dir_var.get().strip()
        if not download_dir:
            download_dir = str(Path.home() / "Downloads" / "IPTV-VOD")
            self.download_dir_var.set(download_dir)
        config = AppConfig(
            base_url=self.base_url_var.get().strip(),
            username=self.username_var.get().strip(),
            password=self.password_var.get().strip(),
            download_dir=download_dir,
            seerr_url=self.seerr_url_var.get().strip(),
            seerr_api_key=self.seerr_key_var.get().strip()
        )
        self.config_manager.save(config)
        self.current_config = config
        self.status_var.set("Settings saved.")
        self._ensure_client()
        self.refresh_catalog()

    def _ensure_client(self) -> bool:
        if not self.current_config.is_complete():
            messagebox.showwarning("Incomplete settings", "Enter all IPTV playlist details before continuing.")
            return False
        try:
            self.client = IPTVClient(
                self.current_config.base_url,
                self.current_config.username,
                self.current_config.password,
                seerr_url=self.current_config.seerr_url,
                seerr_key=self.current_config.seerr_api_key
            )
            return True
        except Exception as exc:  # pragma: no cover - runtime safeguard
            messagebox.showerror("Error", f"Unable to initialize the client:\n{exc}")
            return False

    # ------------------------------------------------------------------
    # Catalog handling

    def refresh_catalog(self) -> None:
        if not self._ensure_client():
            return
        self.status_var.set("Loading categories...")
        self._catalog_request_token += 1
        request_token = self._catalog_request_token

        def worker() -> None:
            try:
                movie_categories = self.client.get_vod_categories()
                series_categories = self.client.get_series_categories()
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: self._on_catalog_error(str(exc), request_token))
                return
            self.after(0, lambda: self._populate_categories(movie_categories, series_categories, request_token))

        threading.Thread(target=worker, daemon=True).start()

    def _on_catalog_error(self, message: str, request_token: int) -> None:
        if request_token != self._catalog_request_token:
            return
        self.status_var.set(f"Error: {message}")
        messagebox.showerror("Error", f"Unable to retrieve categories:\n{message}")

    def _populate_categories(
        self,
        movie_categories: List[Dict[str, Any]],
        series_categories: List[Dict[str, Any]],
        request_token: int,
    ) -> None:
        if request_token != self._catalog_request_token:
            return
        self.status_var.set("Categories refreshed.")
        for kind, listbox, categories in [
            ("movies", self.movies_listbox, movie_categories),
            ("series", self.series_listbox, series_categories),
        ]:
            listbox.delete(0, tk.END)
            index_map = ["0"]
            listbox.insert(tk.END, "All categories")
            for category in categories:
                listbox.insert(tk.END, category.get("category_name", "Untitled"))
                index_map.append(str(category.get("category_id")))
            self.category_indexes[kind] = index_map
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(0)
            self._load_items(kind, category_id="0")

    def _on_category_selected(self, kind: str) -> None:
        listbox: tk.Listbox = getattr(self, f"{kind}_listbox")
        selection = listbox.curselection()
        if not selection:
            return
        index = selection[0]
        category_id = self.category_indexes.get(kind, ["0"])[index]
        self._load_items(kind, category_id=category_id)

    def _on_search(self, kind: str) -> None:
        term_var: tk.StringVar = getattr(self, f"{kind}_search_var")
        term = term_var.get().strip()
        listbox: tk.Listbox = getattr(self, f"{kind}_listbox")
        selection = listbox.curselection()
        category_id = "0"
        if selection:
            category_id = self.category_indexes.get(kind, ["0"])[selection[0]]
        self._load_items(kind, category_id=category_id, search_term=term)

    def _clear_search(self, kind: str, var: tk.StringVar) -> None:
        var.set("")
        self._on_search(kind)

    def _reload_items(self, kind: str) -> None:
        listbox: tk.Listbox = getattr(self, f"{kind}_listbox")
        selection = listbox.curselection()
        category_id = "0"
        if selection:
            category_id = self.category_indexes.get(kind, ["0"])[selection[0]]
        self._load_items(kind, category_id=category_id)

    def _show_tree_menu(self, event: tk.Event, tree: ttk.Treeview, menu: tk.Menu) -> None:
        iid = tree.identify_row(event.y)
        if iid:
            if iid not in tree.selection():
                tree.selection_set(iid)
            tree.focus(iid)
        else:
            for sel in tree.selection():
                tree.selection_remove(sel)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_series_tree_double_click(self, event: tk.Event) -> None:
        if self.series_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.series_tree.identify_row(event.y):
            self._open_series_dialog()

    def _show_queue_menu(self, event: tk.Event) -> None:
        iid = self.queue_tree.identify_row(event.y)
        if iid:
            if iid not in self.queue_tree.selection():
                self.queue_tree.selection_set(iid)
            self.queue_tree.focus(iid)
        else:
            for sel in self.queue_tree.selection():
                self.queue_tree.selection_remove(sel)
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()

    def _load_items(self, kind: str, category_id: str, search_term: Optional[str] = None) -> None:
        if not self.client:
            return
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        tree.delete(*tree.get_children())
        results_var: tk.StringVar = getattr(self, f"{kind}_results_var")
        results_var.set("Loading results...")
        self._items_request_tokens[kind] += 1
        request_token = self._items_request_tokens[kind]

        def worker() -> None:
            try:
                if kind == "movies":
                    items = self.client.get_vod_streams(category_id=category_id)
                else:
                    items = self.client.get_series(category_id=category_id)
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: self._on_items_error(kind, str(exc), request_token))
                return

            if search_term:
                items = [
                    item
                    for item in items
                    if match_search_term(search_term, item.get("name") or "")
                ]

            self.after(0, lambda: self._populate_items(kind, items, request_token))

        threading.Thread(target=worker, daemon=True).start()

    def _on_items_error(self, kind: str, message: str, request_token: int) -> None:
        if request_token != self._items_request_tokens[kind]:
            return
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        tree.delete(*tree.get_children())
        results_var: tk.StringVar = getattr(self, f"{kind}_results_var")
        results_var.set(f"Error: {message}")

    def _populate_items(self, kind: str, items: List[Dict[str, Any]], request_token: Optional[int] = None) -> None:
        if request_token is not None and request_token != self._items_request_tokens[kind]:
            return
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        tree.delete(*tree.get_children())
        prepared_items: List[Dict[str, Any]] = []

        for item in items:
            if kind == "movies":
                identifier = str(item.get("stream_id"))
                info = item.get("info") if isinstance(item.get("info"), dict) else None
                year = self._normalise_year(
                    item.get("year"),
                    item.get("releasedate"),
                    item.get("releaseDate"),
                    item.get("release_date"),
                    info.get("year") if info else None,
                    info.get("releasedate") if info else None,
                    info.get("releaseDate") if info else None,
                )
            else:
                identifier = str(item.get("series_id"))
                info = item.get("info") if isinstance(item.get("info"), dict) else None
                year = self._normalise_year(
                    item.get("releaseDate"),
                    item.get("releasedate"),
                    item.get("start"),
                    item.get("year"),
                    info.get("releasedate") if info else None,
                    info.get("releaseDate") if info else None,
                    info.get("year") if info else None,
                )

            name = item.get("name") or "Untitled"
            item["display_year"] = year
            item["_tree_identifier"] = identifier
            prepared_items.append(item)

        sorted_items = self._sort_catalog_items(kind, prepared_items)
        data_map: Dict[str, Dict[str, Any]] = {}
        for item in sorted_items:
            identifier = str(item.pop("_tree_identifier"))
            name = item.get("name") or "Untitled"
            year = item.get("display_year", "")
            tags = self._catalog_item_tags(kind, item)
            tree.insert("", "end", iid=identifier, values=(name, year), tags=tags)
            if year:
                item["display_year"] = year
            data_map[identifier] = item

        self.items_map[kind] = data_map
        results_var: tk.StringVar = getattr(self, f"{kind}_results_var")
        if not sorted_items:
            results_var.set("No results.")
        else:
            results_var.set(f"{len(sorted_items)} results.")

    def _sort_catalog_items(self, kind: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sort_mode = getattr(self, f"{kind}_sort_mode")
        sort_desc = getattr(self, f"{kind}_sort_desc")
        if sort_mode == "Year":
            return sorted(
                items,
                key=lambda item: (
                    int(item.get("display_year")) if str(item.get("display_year", "")).isdigit() else 0,
                    (item.get("name") or "").lower(),
                ),
                reverse=sort_desc,
            )
        return sorted(
            items,
            key=lambda item: ((item.get("name") or "").lower(), item.get("display_year", "")),
            reverse=sort_desc,
        )

    def _set_catalog_sort(self, kind: str, sort_mode: str) -> None:
        current_mode = getattr(self, f"{kind}_sort_mode")
        current_desc = getattr(self, f"{kind}_sort_desc")
        if current_mode == sort_mode:
            setattr(self, f"{kind}_sort_desc", not current_desc)
        else:
            setattr(self, f"{kind}_sort_mode", sort_mode)
            setattr(self, f"{kind}_sort_desc", sort_mode == "Year")
        self._update_catalog_headings(kind)
        self._apply_current_sort(kind)

    def _update_catalog_headings(self, kind: str) -> None:
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        sort_mode = getattr(self, f"{kind}_sort_mode")
        sort_desc = getattr(self, f"{kind}_sort_desc")
        title_heading = "Title"
        year_heading = "Year"
        if sort_mode == "Title":
            title_heading = f"Title {'v' if sort_desc else '^'}"
        elif sort_mode == "Year":
            year_heading = f"Year {'v' if sort_desc else '^'}"
        tree.heading("title", text=title_heading, command=lambda k=kind: self._set_catalog_sort(k, "Title"))
        tree.heading("year", text=year_heading, command=lambda k=kind: self._set_catalog_sort(k, "Year"))

    def _apply_current_sort(self, kind: str) -> None:
        current_items = list(self.items_map.get(kind, {}).values())
        if not current_items:
            return
        self._populate_items(kind, current_items)

    def _normalise_year(self, *values: Any) -> str:
        for value in values:
            year = self._extract_year(value)
            if year:
                return year
        return ""

    @staticmethod
    def _extract_year(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            year = int(value)
            if 1900 <= year <= 2100:
                return str(year)
            return ""
        text = str(value).strip()
        match = re.search(r"(\d{4})", text)
        if not match:
            return ""
        year = int(match.group(1))
        if 1900 <= year <= 2100:
            return str(year)
        return ""

    def _update_tree_year(self, kind: str, identifier: str, year: str) -> None:
        if not year:
            return
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        if not tree.exists(identifier):
            return
        values = list(tree.item(identifier, "values"))
        if len(values) < 2:
            return
        values[1] = year
        tree.item(identifier, values=values)
        if identifier in self.items_map.get(kind, {}):
            self.items_map[kind][identifier]["display_year"] = year
            self.items_map[kind][identifier]["year"] = year

    def _catalog_item_tags(self, kind: str, item: Dict[str, Any]) -> tuple[str, ...]:
        tags: List[str] = []
        if self._is_catalog_item_queued(kind, item):
            tags.append("queued_item")
        if self._is_catalog_item_downloaded(kind, item):
            tags.append("downloaded_item")
        return tuple(tags)

    def _is_catalog_item_queued(self, kind: str, item: Dict[str, Any]) -> bool:
        item_id = str(item.get("stream_id") if kind == "movies" else item.get("series_id"))
        for queued in self.queue_items.values():
            if queued.get("status") in {"removed", "cancelled"}:
                continue
            if kind == "movies" and queued.get("kind") == "movie" and str(queued.get("item_id")) == item_id:
                return True
            if kind == "series" and queued.get("kind") == "episode" and queued.get("meta", {}).get("series") == item.get("name"):
                return True
        return False

    def _is_catalog_item_downloaded(self, kind: str, item: Dict[str, Any]) -> bool:
        if kind == "movies":
            target = self._infer_movie_target_path(item)
            return target.exists() if target else False
        target_dir = self._infer_series_target_dir(item)
        return target_dir.exists()

    def _infer_movie_target_path(self, movie: Dict[str, Any]) -> Optional[Path]:
        extension = movie.get("container_extension") or "mp4"
        title = movie.get("name") or f"Movie {movie.get('stream_id')}"
        year_value = self._normalise_year(
            movie.get("display_year"),
            movie.get("year"),
            movie.get("releaseDate"),
            movie.get("releasedate"),
            movie.get("release_date"),
        )
        safe_title = sanitise_filename(title)
        if year_value:
            safe_title = f"{safe_title} ({year_value})"
        return Path(self.current_config.download_dir) / "Movies" / f"{safe_title}.{extension}"

    def _infer_series_target_dir(self, series: Dict[str, Any]) -> Path:
        series_name = series.get("name") or f"Series_{series.get('series_id')}"
        series_year = self._normalise_year(
            series.get("display_year"),
            series.get("year"),
            series.get("releaseDate"),
            series.get("releasedate"),
            series.get("start"),
        )
        folder_name = sanitise_filename(series_name)
        if series_year:
            folder_name = f"{folder_name} ({series_year})"
        return Path(self.current_config.download_dir) / "Series" / folder_name

    def _is_duplicate_download(self, item: DownloadItem) -> bool:
        if item.target_path.exists():
            return True
        for queued in self.queue_items.values():
            if queued.get("status") in {"removed", "cancelled"}:
                continue
            if str(queued.get("queue_id")) == item.queue_id and queued.get("status") in {"failed", "stopped"}:
                continue
            if str(queued.get("target_path")) == str(item.target_path):
                return True
            if queued.get("kind") == item.kind and str(queued.get("item_id")) == item.item_id:
                return True
        return False

    def _queue_item_matches_filter(self, item: Dict[str, Any]) -> bool:
        filter_value = self.queue_filter_var.get()
        status = item.get("status")
        if filter_value == "All":
            return True
        if filter_value == "Queued":
            return status == "queued"
        if filter_value == "Downloading":
            return status == "downloading"
        if filter_value == "Paused":
            return status == "paused"
        if filter_value == "Failed":
            return status == "failed"
        if filter_value == "Stopped":
            return status == "stopped"
        if filter_value == "Completed":
            return status == "completed"
        return True

    def _queue_sort_key(self, item: Dict[str, Any]) -> Any:
        sort_value = self.queue_sort_var.get()
        if sort_value == "Title":
            return (item.get("title", "").lower(), item.get("queue_id", ""))
        if sort_value == "Status":
            return (STATUS_LABELS.get(str(item.get("status", "")), ""), item.get("title", "").lower())
        if sort_value == "Path":
            return (item.get("target_path", ""), item.get("title", "").lower())
        return ()

    def _queue_row_values(self, item: Dict[str, Any]) -> tuple[str, str, str, str, str, str]:
        progress = item.get("progress", 0.0)
        percent = f"{int(progress * 100)}%" if progress else "0%"
        status = item.get("status", "queued")
        return (
            item.get("title", ""),
            "Series" if item.get("kind") == "episode" else "Movie",
            STATUS_LABELS.get(status, str(status)),
            percent,
            item.get("target_path", ""),
            item.get("error", "") or "",
        )

    def _update_queue_summary(self) -> None:
        total = len([item for item in self.queue_items.values() if item.get("status") not in {"removed", "cancelled"}])
        queued = len([item for item in self.queue_items.values() if item.get("status") == "queued"])
        downloading = len([item for item in self.queue_items.values() if item.get("status") == "downloading"])
        failed = len([item for item in self.queue_items.values() if item.get("status") == "failed"])
        self.queue_summary_var.set(f"Total {total} | Queued {queued} | Downloading {downloading} | Failed {failed}")

    def _update_queue_tree_item(self, queue_id: str, item: Dict[str, Any]) -> None:
        if not self._queue_item_matches_filter(item):
            if self.queue_tree.exists(queue_id):
                self._refresh_queue_view()
            return
        if not self.queue_tree.exists(queue_id):
            self._refresh_queue_view()
            return
        self.queue_tree.item(queue_id, values=self._queue_row_values(item))
        self._update_queue_details()

    def _refresh_queue_view(self) -> None:
        selected = set(self.queue_tree.selection())
        self.queue_tree.delete(*self.queue_tree.get_children())

        visible_items = [
            item
            for item in self.queue_items.values()
            if item.get("status") not in {"removed", "cancelled"} and self._queue_item_matches_filter(item)
        ]
        if self.queue_sort_var.get() != "Insertion order":
            visible_items.sort(key=self._queue_sort_key)

        for item in visible_items:
            queue_id = str(item.get("queue_id"))
            self.queue_tree.insert("", "end", iid=queue_id, values=self._queue_row_values(item))
            if queue_id in selected:
                self.queue_tree.selection_add(queue_id)

        self._update_queue_summary()
        self._update_queue_details()

    def _refresh_catalog_views(self) -> None:
        for kind in ("movies", "series"):
            if self.items_map.get(kind):
                self._apply_current_sort(kind)

    def _update_queue_details(self) -> None:
        selection = self.queue_tree.selection()
        if not selection:
            self.queue_details_var.set("")
            return
        item = self.queue_items.get(selection[0], {})
        detail = item.get("error") or item.get("target_path") or ""
        self.queue_details_var.set(str(detail))

    def _get_selected_queue_item(self) -> Optional[Dict[str, Any]]:
        selection = self.queue_tree.selection()
        if not selection:
            return None
        return self.queue_items.get(selection[0])

    def _open_selected_download_folder(self) -> None:
        item = self._get_selected_queue_item()
        if not item:
            messagebox.showinfo("No selection", "Select a download from the queue.")
            return
        target = Path(str(item.get("target_path", self.current_config.download_dir))).parent
        self._open_path(target)

    def _open_path(self, target: Path) -> None:
        try:
            target.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                import os
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":  # pragma: no cover - platform specific
                import subprocess
                subprocess.Popen(["open", str(target)])
            else:  # pragma: no cover - platform specific
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:  # pragma: no cover - runtime safeguard
            messagebox.showerror("Error", f"Unable to open the folder:\n{exc}")

    def _on_delete_pressed(self, _event: tk.Event) -> str | None:
        if self.focus_get() == self.queue_tree:
            self._remove_selected_from_queue()
            return "break"
        return None

    def _on_return_pressed(self, _event: tk.Event) -> str | None:
        focus = self.focus_get()
        if focus == self.series_tree:
            self._open_series_dialog()
            return "break"
        if focus in {self.movies_tree, self.series_tree}:
            current_tab = "movies" if focus == self.movies_tree else "series"
            self._add_selected_to_queue(current_tab)
            return "break"
        return None

    def _on_select_all_pressed(self, _event: tk.Event) -> str | None:
        focus = self.focus_get()
        if focus in {self.movies_tree, self.series_tree, self.queue_tree}:
            focus.selection_set(*focus.get_children())
            return "break"
        return None

    # ------------------------------------------------------------------
    # Queue handling

    def _add_selected_to_queue(self, kind: str) -> None:
        if not self.client or not self.current_config.is_complete():
            messagebox.showwarning("Configuration", "Configure the IPTV connection before queuing downloads.")
            return

        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select at least one item to download.")
            return

        items_data = self.items_map.get(kind, {})
        download_items: List[DownloadItem] = []
        skipped_duplicates = 0

        if kind == "movies":
            for iid in selection:
                stream = items_data.get(iid)
                if not stream:
                    continue
                try:
                    info = self.client.get_vod_info(str(stream["stream_id"]))
                except Exception as exc:  # pragma: no cover - runtime safeguard
                    messagebox.showerror("Error", f"Unable to retrieve movie details:\n{exc}")
                    continue

                meta_info = info.get("info", {}) if isinstance(info.get("info"), dict) else {}
                extension = meta_info.get("container_extension") or stream.get("container_extension") or "mp4"
                
                # Respetamos el nombre limpio si le hicimos la busqueda de Seerr
                clean_title = re.sub(r'^\[(OK|MISS)\]\s*', '', stream.get("name") or "")
                title = clean_title or f"Movie {stream['stream_id']}"
                
                year_value = self._normalise_year(
                    stream.get("display_year"),
                    stream.get("year"),
                    meta_info.get("year"),
                    meta_info.get("releaseDate"),
                    meta_info.get("releasedate"),
                    meta_info.get("release_date"),
                )

                safe_title = sanitise_filename(title)
                if year_value:
                    safe_title = f"{safe_title} ({year_value})"
                    stream["display_year"] = year_value
                    self._update_tree_year("movies", iid, year_value)

                target_dir = Path(self.current_config.download_dir) / "Movies"
                target_path = target_dir / f"{safe_title}.{extension}"

                url = self.client.build_movie_stream_url(str(stream["stream_id"]), extension)
                item = DownloadItem(
                    item_id=str(stream["stream_id"]),
                    title=title,
                    stream_url=url,
                    target_path=target_path,
                    kind="movie",
                    meta={"year": year_value},
                )
                if self._is_duplicate_download(item):
                    skipped_duplicates += 1
                    continue
                download_items.append(item)

            if download_items:
                self.download_manager.add_items(download_items)
            if download_items or skipped_duplicates:
                self.status_var.set(
                    f"Queued {len(download_items)} movies."
                    + (f" Skipped {skipped_duplicates} duplicates." if skipped_duplicates else "")
                )
        else:
            if len(selection) != 1:
                messagebox.showinfo("Select a series", "Select a single series and use 'Open series' to choose episodes.")
                return
            self._open_series_dialog()

    def _open_series_dialog(self) -> None:
        tree: ttk.Treeview = self.series_tree
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("No series selected", "Select a series to browse its episodes.")
            return
        series_id = selection[0]
        series = self.items_map["series"].get(series_id)
        if not series:
            return

        def callback(items: List[Dict[str, Any]]) -> None:
            self._queue_series_episodes(series, items)

        SeriesEpisodesDialog(self, self.client, series, callback)

    def _queue_entire_selected_series(self) -> None:
        if not self.client or not self.current_config.is_complete():
            messagebox.showwarning("Configuration", "Configure the IPTV connection before queuing downloads.")
            return

        tree: ttk.Treeview = self.series_tree
        selection = tree.selection()
        if len(selection) != 1:
            messagebox.showinfo("Select a series", "Select a single series to queue the full series.")
            return

        series_id = selection[0]
        series = self.items_map["series"].get(series_id)
        if not series:
            return

        self.status_var.set(f"Loading episodes for {series.get('name', 'series')}...")

        def worker() -> None:
            try:
                info = self.client.get_series_info(str(series["series_id"]))
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: messagebox.showerror("Error", f"Unable to retrieve episodes:\n{exc}"))
                self.after(0, lambda: self.status_var.set(f"Error: {exc}"))
                return

            payloads: List[Dict[str, Any]] = []
            episodes = info.get("episodes", {})
            for season_key in sorted(episodes.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                try:
                    season_num = int(season_key)
                except (TypeError, ValueError):
                    season_num = 0
                sorted_eps = sorted(
                    episodes[season_key],
                    key=lambda ep: int(ep.get("episode_num", 0) or 0),
                )
                for episode in sorted_eps:
                    payloads.append(
                        {
                            "series": series,
                            "season": season_num,
                            "episode": episode,
                        }
                    )

            self.after(0, lambda: self._queue_series_episodes(series, payloads))

        threading.Thread(target=worker, daemon=True).start()

    def _queue_series_episodes(self, series: Dict[str, Any], episodes: List[Dict[str, Any]]) -> None:
        download_items: List[DownloadItem] = []
        skipped_duplicates = 0
        
        clean_series_name = re.sub(r'^\[(OK|MISS)\]\s*', '', series.get("name") or "")
        series_name = clean_series_name or f"Series_{series.get('series_id')}"
        
        series_year = self._normalise_year(
            series.get("display_year"),
            series.get("year"),
            series.get("releaseDate"),
            series.get("releasedate"),
            series.get("start"),
        )
        folder_name = sanitise_filename(series_name)
        if series_year:
            folder_name = f"{folder_name} ({series_year})"
        base_dir = Path(self.current_config.download_dir) / "Series" / folder_name

        for payload in episodes:
            episode = payload["episode"]
            season = int(payload["season"])
            episode_num = int(episode.get("episode_num", 0) or 0)
            extension = episode.get("container_extension") or "mp4"
            title = episode.get("title") or episode.get("name") or f"Episode {episode_num}"
            episode_id = str(episode.get("id"))
            filename = build_episode_filename(season, episode_num, title, extension)
            target_path = base_dir / f"Season {season:02d}" / filename
            stream_url = self.client.build_episode_stream_url(episode_id, extension)

            item = DownloadItem(
                item_id=episode_id,
                title=f"{series_name} - S{season:02d}E{episode_num:02d} {title}",
                stream_url=stream_url,
                target_path=target_path,
                kind="episode",
                meta={
                    "series": series_name,
                    "series_year": series_year,
                    "series_id": str(series.get("series_id")),
                    "season": season,
                    "episode": episode_num,
                },
            )
            if self._is_duplicate_download(item):
                skipped_duplicates += 1
                continue
            download_items.append(item)

        if download_items:
            self.download_manager.add_items(download_items)
        self.status_var.set(
            f"Queued {len(download_items)} episodes."
            + (f" Skipped {skipped_duplicates} duplicates." if skipped_duplicates else "")
        )

    def _remove_selected_from_queue(self) -> None:
        selection = self.queue_tree.selection()
        if not selection:
            return
        blocked = False
        removable_queue_ids: List[str] = []
        for queue_id in selection:
            removed = self.download_manager.remove_item(queue_id)
            if removed:
                item = self.queue_items.get(queue_id)
                if item and item.get("status") not in {"downloading", "paused"}:
                    removable_queue_ids.append(queue_id)
            else:
                item = self.queue_items.get(queue_id)
                if item and item.get("status") not in {"downloading", "queued", "paused"}:
                    removable_queue_ids.append(queue_id)
                else:
                    blocked = True
        self._delete_queue_entries(removable_queue_ids)
        if blocked:
            messagebox.showinfo("Active downloads", "Active downloads cannot be removed. Pause or stop them first.")

    def _delete_queue_entry(self, queue_id: str) -> None:
        self._delete_queue_entries([queue_id])

    def _delete_queue_entries(self, queue_ids: Iterable[str]) -> None:
        removed_any = False
        for queue_id in queue_ids:
            if self.queue_items.pop(queue_id, None) is not None:
                removed_any = True
        if not removed_any:
            return
        self._refresh_queue_view()
        self._schedule_queue_state_save()
        self._refresh_catalog_views()

    def _start_downloads(self) -> None:
        active_queue_statuses = {"queued", "downloading", "paused"}
        if not any(item.get("status") in active_queue_statuses for item in self.queue_items.values()):
            restart_items = self._collect_restartable_queue_items({"stopped"})
            if restart_items:
                self.download_manager.add_items(restart_items)
                self.status_var.set(f"Re-queued {len(restart_items)} stopped downloads.")
            else:
                self.status_var.set("No queued downloads to start.")
                return
        self.download_manager.resume()
        self.status_var.set("Downloads running.")

    def _pause_downloads(self) -> None:
        self.download_manager.pause()
        self.status_var.set("Downloads paused.")

    def _stop_downloads(self) -> None:
        self.status_var.set("Downloads stopped.")
        threading.Thread(target=self.download_manager.stop_all, daemon=True).start()

    def _retry_failed_downloads(self) -> None:
        self._retry_queue_items(self.queue_items.keys(), require_selection=False)

    def _retry_selected_failed_downloads(self) -> None:
        self._retry_queue_items(self.queue_tree.selection(), require_selection=True)

    def _retry_queue_items(self, queue_ids: Iterable[str], require_selection: bool) -> None:
        retry_items = self._collect_restartable_queue_items({"failed", "stopped"}, queue_ids)

        if not retry_items:
            if require_selection:
                messagebox.showinfo("No failed selection", "Select at least one failed download to retry.")
            else:
                messagebox.showinfo("No failed downloads", "There are no failed downloads to retry.")
            return

        self.download_manager.add_items(retry_items)
        total = len(retry_items)
        self.status_var.set(f"Re-queued {total} failed downloads.")

    def _collect_restartable_queue_items(
        self,
        allowed_statuses: set[str],
        queue_ids: Optional[Iterable[str]] = None,
    ) -> List[DownloadItem]:
        retry_items: List[DownloadItem] = []
        source_ids = queue_ids if queue_ids is not None else self.queue_items.keys()
        for queue_id in source_ids:
            item = self.queue_items.get(queue_id)
            if not item or item.get("status") not in allowed_statuses:
                continue
            retry_item = self._build_retry_download_item(item)
            if retry_item and not self._is_duplicate_download(retry_item):
                retry_items.append(retry_item)
        return retry_items

    def _build_retry_download_item(self, item: Dict[str, Any]) -> Optional[DownloadItem]:
        stream_url = item.get("stream_url")
        target_path = item.get("target_path")
        queue_id = item.get("queue_id")
        item_id = item.get("item_id")
        if not stream_url or not target_path or not queue_id or not item_id:
            return None

        return DownloadItem(
            item_id=str(item_id),
            title=item.get("title", "Download"),
            stream_url=str(stream_url),
            target_path=Path(str(target_path)),
            kind=str(item.get("kind", "movie")),
            meta=dict(item.get("meta") or {}),
            queue_id=str(queue_id),
        )

    def _clear_completed_downloads(self) -> None:
        to_remove = [queue_id for queue_id, item in self.queue_items.items() if item.get("status") == "completed"]
        self._delete_queue_entries(to_remove)

    def _open_download_folder(self) -> None:
        target = Path(self.current_config.download_dir or Path.home())
        self._open_path(target)

    # ------------------------------------------------------------------
    # Download queue updates

    def _on_download_update(self, item: DownloadItem) -> None:
        self._download_updates.put(item.as_dict())

    def _process_download_updates(self) -> None:
        needs_queue_refresh = False
        needs_catalog_refresh = False
        while True:
            try:
                item = self._download_updates.get_nowait()
            except queue.Empty:
                break
            queue_refresh, catalog_refresh = self._update_queue_row(item)
            needs_queue_refresh = needs_queue_refresh or queue_refresh
            needs_catalog_refresh = needs_catalog_refresh or catalog_refresh
        if needs_queue_refresh:
            self._refresh_queue_view()
        if needs_catalog_refresh:
            self._refresh_catalog_views()
        self.after(100, self._process_download_updates)

    def _update_queue_row(self, item: Dict[str, Any]) -> tuple[bool, bool]:
        queue_id = item["queue_id"]
        previous = self.queue_items.get(queue_id, {})
        status = item.get("status", "queued")
        if status in {"removed", "cancelled"}:
            self.queue_items.pop(queue_id, None)
            self._schedule_queue_state_save()
            return True, True
        self.queue_items[queue_id] = item
        is_new_item = not previous
        status_changed = previous.get("status") != status
        if is_new_item or status_changed:
            self._schedule_queue_state_save()
            catalog_refresh = status_changed and status in {"queued", "completed", "failed", "stopped"}
            return True, catalog_refresh
        self._update_queue_tree_item(queue_id, item)
        return False, False

    # ------------------------------------------------------------------
    # Seerr Integration (On Demand Search)
    
    def _search_in_seerr(self, kind: str) -> None:
        if not self.client or not getattr(self.client, "seerr", None):
            messagebox.showwarning("Seerr", "Please configure Seerr credentials first.")
            return

        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        selection = tree.selection()
        if not selection:
            return

        def worker() -> None:
            media_type = "movie" if kind == "movies" else "tv"
            items_data = self.items_map.get(kind, {})
            
            for iid in selection:
                item = items_data.get(iid)
                if not item:
                    continue
                
                tmdb_id = item.get("tmdb_id")
                original_title = item.get("name", "")
                
                # Limpiar si ya le habíamos puesto [OK] o [MISS] antes
                clean_title = re.sub(r'^\[(OK|MISS)\]\s*', '', original_title)
                
                # Buscar en Seerr
                found = self.client.seerr.check_availability(tmdb_id, media_type, clean_title)
                
                prefix = "[OK]" if found else "[MISS]"
                new_name = f"{prefix} {clean_title}"
                
                # Actualizar la interfaz (se debe hacer en el hilo principal)
                self.after(0, lambda i=iid, n=new_name, f=found: self._update_tree_name(kind, i, n, f))

        # Iniciar la búsqueda en segundo plano
        threading.Thread(target=worker, daemon=True).start()

    def _update_tree_name(self, kind: str, iid: str, new_name: str, found: bool) -> None:
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        if not tree.exists(iid):
            return
            
        values = list(tree.item(iid, "values"))
        if not values:
            return
            
        values[0] = new_name
        tags = list(tree.item(iid, "tags"))
        
        # Si lo encuentra, lo pintamos de verde
        if found and "downloaded_item" not in tags:
            tags.append("downloaded_item")
        # Si no lo encuentra, nos aseguramos de quitar el color verde si lo tenía
        elif not found and "downloaded_item" in tags:
            tags.remove("downloaded_item")
            
        tree.item(iid, values=values, tags=tags)
        
        if iid in self.items_map.get(kind, {}):
            self.items_map[kind][iid]["name"] = new_name


def run_app() -> None:
    app = IPTVApp()
    app.mainloop()
