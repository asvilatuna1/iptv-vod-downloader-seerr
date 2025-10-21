"""Tkinter GUI for the IPTV VOD downloader."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import sys
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional

from .api import APIError, IPTVClient
from .config import AppConfig, ConfigManager
from .downloader import DownloadItem, DownloadManager
from .utils import build_episode_filename, match_search_term, sanitise_filename


STATUS_LABELS = {
    "queued": "In coda",
    "downloading": "Scaricamento",
    "completed": "Completato",
    "failed": "Errore",
    "paused": "In pausa",
    "removed": "Rimosso",
    "cancelled": "Annullato",
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
        self.title(series.get("name", "Serie"))
        self.resizable(True, True)
        self.geometry("700x500")
        self.parent = parent
        self.client = client
        self.series = series
        self.callback = callback

        self.episodes_map: Dict[str, Dict[str, Any]] = {}
        self.season_episodes: Dict[int, List[str]] = {}
        self._season_labels: Dict[str, int] = {}
        self.all_episode_ids: List[str] = []

        self.season_var = tk.StringVar()

        self.status_var = tk.StringVar(value="Caricamento episodi...")

        status_label = ttk.Label(self, textvariable=self.status_var)
        status_label.pack(fill="x", padx=10, pady=(10, 0))

        self.tree = ttk.Treeview(self, columns=("title", "season", "episode"), show="headings", selectmode="extended")
        self.tree.heading("title", text="Episodio")
        self.tree.heading("season", text="Stagione")
        self.tree.heading("episode", text="Numero")
        self.tree.column("title", width=420, anchor="w")
        self.tree.column("season", width=80, anchor="center")
        self.tree.column("episode", width=80, anchor="center")
        self.tree.pack(expand=True, fill="both", padx=10, pady=10)
        self.tree.bind("<Button-3>", self._show_episode_menu)

        season_frame = ttk.Frame(self)
        season_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(season_frame, text="Stagione").pack(side="left")
        self.season_combo = ttk.Combobox(season_frame, state="readonly", textvariable=self.season_var, width=20, values=())
        self.season_combo.pack(side="left", padx=5)
        ttk.Button(season_frame, text="Seleziona stagione", command=self._select_current_season).pack(side="left", padx=5)
        ttk.Button(season_frame, text="Aggiungi stagione", command=self._add_current_season).pack(side="left", padx=5)
        ttk.Button(season_frame, text="Aggiungi serie completa", command=self._add_entire_series).pack(side="right")

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(button_frame, text="Seleziona tutti", command=self._select_all).pack(side="left")
        add_button = ttk.Button(button_frame, text="Aggiungi episodi selezionati", command=self.on_confirm)
        add_button.pack(side="left", padx=5)

        close_button = ttk.Button(button_frame, text="Chiudi", command=self.destroy)
        close_button.pack(side="right")

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Seleziona tutti", command=self._select_all)
        self.context_menu.add_command(label="Seleziona stagione", command=self._select_current_season)
        self.context_menu.add_command(label="Aggiungi episodi selezionati", command=self.on_confirm)
        self.context_menu.add_command(label="Aggiungi stagione", command=self._add_current_season)
        self.context_menu.add_command(label="Aggiungi serie completa", command=self._add_entire_series)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

        threading.Thread(target=self._load_episodes, daemon=True).start()

    def _load_episodes(self) -> None:
        try:
            info = self.client.get_series_info(str(self.series["series_id"]))
            episodes = info.get("episodes", {})
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
                sorted_eps = sorted(
                    episodes_list,
                    key=lambda ep: int(ep.get("episode_num", 0) or 0),
                )
                for episode in sorted_eps:
                    episode_id = str(episode.get("id"))
                    episode_num = int(episode.get("episode_num", 0) or 0)
                    title = episode.get("title") or episode.get("name") or f"Episodio {episode_num}"
                    values = (title, season_num, episode_num)
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

            self.status_var.set("Seleziona gli episodi da scaricare.")

        self.after(0, populate)

    def _set_error(self, message: str) -> None:
        self.status_var.set(f"Errore: {message}")
        messagebox.showerror("Errore", f"Impossibile caricare gli episodi:\n{message}", parent=self)

    def on_confirm(self) -> None:
        selection = self.tree.selection()
        payloads = self._build_payloads(selection)
        if not payloads:
            messagebox.showinfo("Nessuna selezione", "Seleziona almeno un episodio.", parent=self)
            return
        self.callback(payloads)
        self.destroy()

    # Helpers ----------------------------------------------------------

    @staticmethod
    def _format_season_label(season: int) -> str:
        return "Speciali" if season <= 0 else f"Stagione {season:02d}"

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
        self.tree.selection_set(*self.tree.get_children())

    def _select_current_season(self) -> None:
        season = self._get_selected_season()
        if season is None:
            messagebox.showinfo("Nessuna stagione", "Nessuna stagione disponibile per la selezione.", parent=self)
            return
        episode_ids = self.season_episodes.get(season, [])
        if not episode_ids:
            messagebox.showinfo("Vuota", "La stagione selezionata non contiene episodi disponibili.", parent=self)
            return
        self.tree.selection_set(*episode_ids)

    def _add_current_season(self) -> None:
        season = self._get_selected_season()
        if season is None:
            messagebox.showinfo("Nessuna stagione", "Seleziona una stagione da aggiungere.", parent=self)
            return
        episode_ids = self.season_episodes.get(season, [])
        self._queue_payloads(self._build_payloads(episode_ids), close_dialog=False)

    def _add_entire_series(self) -> None:
        self._queue_payloads(self._build_payloads(self.all_episode_ids), close_dialog=False)

    def _queue_payloads(self, payloads: List[Dict[str, Any]], close_dialog: bool) -> None:
        if not payloads:
            messagebox.showinfo("Nessun episodio", "Nessun episodio disponibile per l'aggiunta.", parent=self)
            return
        self.callback(payloads)
        self.status_var.set(f"Aggiunti {len(payloads)} episodi alla coda.")
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
        self.geometry("1200x800")

        self.config_manager = ConfigManager()
        self.current_config: AppConfig = self.config_manager.config
        self.client: Optional[IPTVClient] = None

        self.download_manager = DownloadManager(callback=self._on_download_update)
        self.download_manager.start()
        self._download_updates: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        self.category_indexes: Dict[str, List[str]] = {"movies": [], "series": []}
        self.items_map: Dict[str, Dict[str, Dict[str, Any]]] = {"movies": {}, "series": {}}
        self.queue_items: Dict[str, Dict[str, Any]] = {}

        self._create_widgets()
        self._load_config_into_form()

        if self.current_config.is_complete():
            self._ensure_client()
            self.refresh_catalog()

        self.after(200, self._process_download_updates)

    # ------------------------------------------------------------------
    # UI construction helpers

    def _create_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.config_frame = ttk.LabelFrame(self, text="Connessione IPTV")
        self.config_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        for col in range(6):
            self.config_frame.columnconfigure(col, weight=1 if col % 2 == 1 else 0)

        self.base_url_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.download_dir_var = tk.StringVar()
        self.status_var = tk.StringVar()

        ttk.Label(self.config_frame, text="URL").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.base_url_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Username").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.username_var).grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Password").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.password_var, show="*").grid(row=1, column=3, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, text="Cartella download").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.config_frame, textvariable=self.download_dir_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        ttk.Button(self.config_frame, text="Sfoglia", command=self._choose_download_dir).grid(row=2, column=3, sticky="ew", padx=5, pady=5)

        ttk.Button(self.config_frame, text="Salva configurazione", command=self._save_config).grid(row=0, column=3, sticky="ew", padx=5, pady=5)
        ttk.Button(self.config_frame, text="Aggiorna catalogo", command=self.refresh_catalog).grid(row=0, column=4, sticky="ew", padx=5, pady=5)

        ttk.Label(self.config_frame, textvariable=self.status_var, foreground="gray").grid(row=3, column=0, columnspan=5, sticky="w", padx=5, pady=5)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        self.movie_tab = self._create_catalog_tab("movies")
        self.series_tab = self._create_catalog_tab("series", is_series=True)

        self.queue_frame = ttk.LabelFrame(self, text="Coda download")
        self.queue_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.queue_frame.columnconfigure(0, weight=1)
        self.queue_frame.rowconfigure(0, weight=1)

        columns = ("title", "kind", "status", "progress", "path")
        self.queue_tree = ttk.Treeview(self.queue_frame, columns=columns, show="headings", selectmode="extended")
        self.queue_tree.heading("title", text="Titolo")
        self.queue_tree.heading("kind", text="Tipo")
        self.queue_tree.heading("status", text="Stato")
        self.queue_tree.heading("progress", text="Progressione")
        self.queue_tree.heading("path", text="Destinazione")
        self.queue_tree.column("title", width=380, anchor="w")
        self.queue_tree.column("kind", width=80, anchor="center")
        self.queue_tree.column("status", width=120, anchor="center")
        self.queue_tree.column("progress", width=120, anchor="center")
        self.queue_tree.column("path", width=420, anchor="w")
        self.queue_tree.grid(row=0, column=0, sticky="nsew")

        queue_scroll = ttk.Scrollbar(self.queue_frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        queue_scroll.grid(row=0, column=1, sticky="ns")

        self.queue_menu = tk.Menu(self.queue_tree, tearoff=0)
        self.queue_menu.add_command(label="Avvia download", command=self._start_downloads)
        self.queue_menu.add_command(label="Metti in pausa", command=self._pause_downloads)
        self.queue_menu.add_command(label="Ferma download", command=self._stop_downloads)
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Rimuovi selezionati", command=self._remove_selected_from_queue)
        self.queue_menu.add_command(label="Pulisci completati", command=self._clear_completed_downloads)
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Apri cartella download", command=self._open_download_folder)
        self.queue_tree.bind("<Button-3>", self._show_queue_menu)

        queue_buttons = ttk.Frame(self.queue_frame)
        queue_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Button(queue_buttons, text="Avvia", command=self._start_downloads).pack(side="left", padx=5)
        ttk.Button(queue_buttons, text="Pausa", command=self._pause_downloads).pack(side="left", padx=5)
        ttk.Button(queue_buttons, text="Ferma", command=self._stop_downloads).pack(side="left", padx=5)
        ttk.Button(queue_buttons, text="Pulisci completati", command=self._clear_completed_downloads).pack(side="left", padx=5)
        ttk.Button(queue_buttons, text="Rimuovi selezionati", command=self._remove_selected_from_queue).pack(side="left", padx=5)
        ttk.Button(queue_buttons, text="Apri cartella download", command=self._open_download_folder).pack(side="right", padx=5)

    def _create_catalog_tab(self, kind: str, is_series: bool = False) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        if kind == "movies":
            self.notebook.add(frame, text="Film")
        else:
            self.notebook.add(frame, text="Serie TV")

        sidebar = ttk.Frame(frame)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(sidebar, text="Categorie").pack(anchor="w", pady=(0, 5))

        listbox = tk.Listbox(sidebar, exportselection=False, height=20)
        listbox.pack(fill="both", expand=True)
        listbox.bind("<<ListboxSelect>>", lambda _event, k=kind: self._on_category_selected(k))

        refresh_button = ttk.Button(sidebar, text="Aggiorna", command=lambda k=kind: self._reload_items(k))
        refresh_button.pack(fill="x", pady=5)

        content = ttk.Frame(frame)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        search_frame = ttk.Frame(content)
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(search_frame, text="Cerca").pack(side="left", padx=5)
        search_var = tk.StringVar()
        entry = ttk.Entry(search_frame, textvariable=search_var)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        entry.bind("<Return>", lambda _event, k=kind: self._on_search(k))
        ttk.Button(search_frame, text="Cerca", command=lambda k=kind: self._on_search(k)).pack(side="left")
        ttk.Button(search_frame, text="Pulisci", command=lambda v=search_var, k=kind: self._clear_search(k, v)).pack(side="left", padx=5)

        columns = ("title", "year", "rating")
        tree = ttk.Treeview(content, columns=columns, show="headings", selectmode="extended")
        tree.heading("title", text="Titolo")
        tree.heading("year", text="Anno")
        tree.heading("rating", text="Valutazione")
        tree.column("title", width=420, anchor="w")
        tree.column("year", width=120, anchor="center")
        tree.column("rating", width=120, anchor="center")
        tree.grid(row=1, column=0, sticky="nsew")

        scroll_y = ttk.Scrollbar(content, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.grid(row=1, column=1, sticky="ns")

        context_menu = tk.Menu(tree, tearoff=0)
        if is_series:
            context_menu.add_command(label="Apri serie", command=self._open_series_dialog)
        context_menu.add_command(label="Aggiungi selezionati alla coda", command=lambda k=kind: self._add_selected_to_queue(k))
        tree.bind("<Button-3>", lambda event, m=context_menu, t=tree: self._show_tree_menu(event, t, m))
        setattr(self, f"{kind}_menu", context_menu)

        action_frame = ttk.Frame(content)
        action_frame.grid(row=2, column=0, sticky="ew", pady=5)

        if is_series:
            ttk.Button(action_frame, text="Apri serie", command=self._open_series_dialog).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Aggiungi selezionati alla coda", command=lambda k=kind: self._add_selected_to_queue(k)).pack(side="right", padx=5)

        setattr(self, f"{kind}_listbox", listbox)
        setattr(self, f"{kind}_tree", tree)
        setattr(self, f"{kind}_search_var", search_var)

        return frame

    # ------------------------------------------------------------------
    # Config handling

    def _load_config_into_form(self) -> None:
        self.base_url_var.set(self.current_config.base_url)
        self.username_var.set(self.current_config.username)
        self.password_var.set(self.current_config.password)
        self.download_dir_var.set(self.current_config.download_dir)

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
        )
        self.config_manager.save(config)
        self.current_config = config
        self.status_var.set("Configurazione salvata.")
        self._ensure_client()
        self.refresh_catalog()

    def _ensure_client(self) -> bool:
        if not self.current_config.is_complete():
            messagebox.showwarning("Configurazione incompleta", "Inserisci tutti i dati della lista IPTV.")
            return False
        try:
            self.client = IPTVClient(
                self.current_config.base_url,
                self.current_config.username,
                self.current_config.password,
            )
            return True
        except Exception as exc:  # pragma: no cover - runtime safeguard
            messagebox.showerror("Errore", f"Impossibile inizializzare il client:\n{exc}")
            return False

    # ------------------------------------------------------------------
    # Catalog handling

    def refresh_catalog(self) -> None:
        if not self._ensure_client():
            return
        self.status_var.set("Caricamento categorie...")

        def worker() -> None:
            try:
                movie_categories = self.client.get_vod_categories()
                series_categories = self.client.get_series_categories()
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: self._on_catalog_error(str(exc)))
                return
            self.after(0, lambda: self._populate_categories(movie_categories, series_categories))

        threading.Thread(target=worker, daemon=True).start()

    def _on_catalog_error(self, message: str) -> None:
        self.status_var.set(f"Errore: {message}")
        messagebox.showerror("Errore", f"Impossibile recuperare le categorie:\n{message}")

    def _populate_categories(self, movie_categories: List[Dict[str, Any]], series_categories: List[Dict[str, Any]]) -> None:
        self.status_var.set("Categorie aggiornate.")
        for kind, listbox, categories in [
            ("movies", self.movies_listbox, movie_categories),
            ("series", self.series_listbox, series_categories),
        ]:
            listbox.delete(0, tk.END)
            index_map = ["0"]
            listbox.insert(tk.END, "Tutte le categorie")
            for category in categories:
                listbox.insert(tk.END, category.get("category_name", "Senza nome"))
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
        tree.insert("", "end", values=("Caricamento...", "", ""))

        def worker() -> None:
            try:
                if kind == "movies":
                    items = self.client.get_vod_streams(category_id=category_id)
                else:
                    items = self.client.get_series(category_id=category_id)
            except Exception as exc:  # pragma: no cover - runtime safeguard
                self.after(0, lambda: self._on_items_error(kind, str(exc)))
                return

            if search_term:
                items = [
                    item
                    for item in items
                    if match_search_term(search_term, item.get("name") or "")
                ]

            items.sort(key=lambda item: (item.get("name") or "").lower())
            self.after(0, lambda: self._populate_items(kind, items))

        threading.Thread(target=worker, daemon=True).start()

    def _on_items_error(self, kind: str, message: str) -> None:
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        tree.delete(*tree.get_children())
        tree.insert("", "end", values=(f"Errore: {message}", "", ""))

    def _populate_items(self, kind: str, items: List[Dict[str, Any]]) -> None:
        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        tree.delete(*tree.get_children())
        data_map: Dict[str, Dict[str, Any]] = {}

        for item in items:
            if kind == "movies":
                identifier = str(item.get("stream_id"))
                year = item.get("year") or ""
                rating = item.get("rating") or item.get("rating5based") or ""
            else:
                identifier = str(item.get("series_id"))
                year = item.get("releaseDate") or ""
                rating = item.get("rating") or ""

            name = item.get("name") or "Senza titolo"
            tree.insert("", "end", iid=identifier, values=(name, year, rating))
            data_map[identifier] = item

        self.items_map[kind] = data_map

    # ------------------------------------------------------------------
    # Queue handling

    def _add_selected_to_queue(self, kind: str) -> None:
        if not self.client or not self.current_config.is_complete():
            messagebox.showwarning("Configurazione", "Configura la connessione prima di aggiungere download.")
            return

        tree: ttk.Treeview = getattr(self, f"{kind}_tree")
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Nessuna selezione", "Seleziona almeno un elemento da scaricare.")
            return

        items_data = self.items_map.get(kind, {})
        download_items: List[DownloadItem] = []

        if kind == "movies":
            for iid in selection:
                stream = items_data.get(iid)
                if not stream:
                    continue
                try:
                    info = self.client.get_vod_info(str(stream["stream_id"]))
                except Exception as exc:  # pragma: no cover - runtime safeguard
                    messagebox.showerror("Errore", f"Impossibile recuperare i dettagli del film:\n{exc}")
                    continue

                meta_info = info.get("info", {}) if isinstance(info.get("info"), dict) else {}
                extension = meta_info.get("container_extension") or stream.get("container_extension") or "mp4"
                title = stream.get("name") or f"Film {stream['stream_id']}"
                release = meta_info.get("releaseDate") or meta_info.get("releasedate") or stream.get("year", "")
                safe_title = sanitise_filename(title)
                if release:
                    release_year = str(release)[:4]
                    safe_title = f"{safe_title} ({release_year})"

                target_dir = Path(self.current_config.download_dir) / "Film"
                target_path = target_dir / f"{safe_title}.{extension}"

                url = self.client.build_movie_stream_url(str(stream["stream_id"]), extension)
                item = DownloadItem(
                    item_id=str(stream["stream_id"]),
                    title=title,
                    stream_url=url,
                    target_path=target_path,
                    kind="movie",
                    meta={"release": release},
                )
                download_items.append(item)

            if download_items:
                self.download_manager.add_items(download_items)
        else:
            # For series we open the episodes dialog
            if len(selection) != 1:
                messagebox.showinfo("Seleziona una serie", "Seleziona una singola serie e usa 'Apri serie' per scegliere gli episodi.")
                return
            self._open_series_dialog()

    def _open_series_dialog(self) -> None:
        tree: ttk.Treeview = self.series_tree
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Nessuna serie selezionata", "Seleziona una serie per visualizzare gli episodi.")
            return
        series_id = selection[0]
        series = self.items_map["series"].get(series_id)
        if not series:
            return

        def callback(items: List[Dict[str, Any]]) -> None:
            self._queue_series_episodes(series, items)

        SeriesEpisodesDialog(self, self.client, series, callback)

    def _queue_series_episodes(self, series: Dict[str, Any], episodes: List[Dict[str, Any]]) -> None:
        download_items: List[DownloadItem] = []
        base_dir = Path(self.current_config.download_dir) / "Serie" / sanitise_filename(series.get("name") or f"Serie_{series.get('series_id')}")

        for payload in episodes:
            episode = payload["episode"]
            season = int(payload["season"])
            episode_num = int(episode.get("episode_num", 0) or 0)
            extension = episode.get("container_extension") or "mp4"
            title = episode.get("title") or episode.get("name") or f"Episodio {episode_num}"
            episode_id = str(episode.get("id"))
            filename = build_episode_filename(season, episode_num, title, extension)
            target_path = base_dir / f"Stagione {season:02d}" / filename
            stream_url = self.client.build_episode_stream_url(episode_id, extension)

            item = DownloadItem(
                item_id=episode_id,
                title=f"{series.get('name')} - S{season:02d}E{episode_num:02d} {title}",
                stream_url=stream_url,
                target_path=target_path,
                kind="episode",
                meta={
                    "series": series.get("name"),
                    "season": season,
                    "episode": episode_num,
                },
            )
            download_items.append(item)

        if download_items:
            self.download_manager.add_items(download_items)

    def _remove_selected_from_queue(self) -> None:
        selection = self.queue_tree.selection()
        if not selection:
            return
        blocked = False
        for queue_id in selection:
            removed = self.download_manager.remove_item(queue_id)
            if removed:
                self._delete_queue_entry(queue_id)
            else:
                item = self.queue_items.get(queue_id)
                if item and item.get("status") not in {"downloading", "queued", "paused"}:
                    self._delete_queue_entry(queue_id)
                else:
                    blocked = True
        if blocked:
            messagebox.showinfo("Download in corso", "Impossibile rimuovere i download attivi. Metti in pausa o ferma prima.")

    def _delete_queue_entry(self, queue_id: str) -> None:
        if self.queue_tree.exists(queue_id):
            self.queue_tree.delete(queue_id)
        self.queue_items.pop(queue_id, None)

    def _start_downloads(self) -> None:
        self.download_manager.resume()
        self.status_var.set("Download in esecuzione.")

    def _pause_downloads(self) -> None:
        self.download_manager.pause()
        self.status_var.set("Download in pausa.")

    def _stop_downloads(self) -> None:
        self.download_manager.stop_all()
        self.status_var.set("Download fermati.")

    def _clear_completed_downloads(self) -> None:
        to_remove = [queue_id for queue_id, item in self.queue_items.items() if item.get("status") == "completed"]
        for queue_id in to_remove:
            self._delete_queue_entry(queue_id)

    def _open_download_folder(self) -> None:
        target = Path(self.current_config.download_dir or Path.home())
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
            messagebox.showerror("Errore", f"Impossibile aprire la cartella:\n{exc}")

    # ------------------------------------------------------------------
    # Download queue updates

    def _on_download_update(self, item: DownloadItem) -> None:
        self._download_updates.put(item.as_dict())

    def _process_download_updates(self) -> None:
        while True:
            try:
                item = self._download_updates.get_nowait()
            except queue.Empty:
                break
            self._update_queue_row(item)
        self.after(500, self._process_download_updates)

    def _update_queue_row(self, item: Dict[str, Any]) -> None:
        queue_id = item["queue_id"]
        progress = item.get("progress", 0.0)
        percent = f"{int(progress * 100)}%" if progress else "0%"
        status = item.get("status", "queued")
        if status in {"removed", "cancelled"}:
            self._delete_queue_entry(queue_id)
            return
        display_status = STATUS_LABELS.get(status, status)
        values = (
            item.get("title", ""),
            "Serie" if item.get("kind") == "episode" else "Film",
            display_status,
            percent,
            item.get("target_path", ""),
        )
        if self.queue_tree.exists(queue_id):
            self.queue_tree.item(queue_id, values=values)
        else:
            self.queue_tree.insert("", "end", iid=queue_id, values=values)
        self.queue_items[queue_id] = item


def run_app() -> None:
    app = IPTVApp()
    app.mainloop()
