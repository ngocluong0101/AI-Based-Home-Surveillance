"""Tkinter main window: live video feed plus pipeline controls."""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from pathlib import Path

from src.config import CONFIDENCE_THRESHOLD, RTSP_URL, SNAPSHOT_DIR
from src.gui.helpers import (
    SOURCE_FILE,
    SOURCE_STREAM,
    SOURCE_TYPES,
    SOURCE_WEBCAM,
    classify_source,
    format_event_row,
    format_snapshot_row,
    frame_to_ppm,
    list_snapshots,
    load_image_ppm,
    validate_source,
)
from src.gui.worker import STATE_LOADING, STATE_RUNNING, STATE_STOPPED, PipelineWorker

logger = logging.getLogger(__name__)

WINDOW_TITLE = "AI-Based-Home-Surveillance"
POLL_INTERVAL_MS = 30
DEFAULT_CANVAS_SIZE = (800, 450)
CONFIDENCE_RANGE = (0.1, 0.95)
CLOSE_JOIN_TIMEOUT = 3.0
PLACEHOLDER_TEXT = "No video — choose a source and press “Start”"
MAX_EVENT_ROWS = 200
# (column id, heading, width in px)
EVENT_COLUMNS = (("time", "Time", 70), ("person", "Person", 60), ("conf", "Conf", 50), ("snapshot", "Snapshot", 170))
SNAPSHOT_COLUMNS = (("name", "File", 230), ("saved", "Saved at", 150), ("size", "Size", 70))
GALLERY_PREVIEW_PLACEHOLDER = "Select a snapshot on the left to view it"

WorkerFactory = Callable[[str, float, bool], PipelineWorker]


class SurveillanceApp:
    """Main window. All Tk calls stay on the main thread; the worker only fills a queue."""

    def __init__(
        self,
        root: tk.Tk,
        source: str | None = None,
        track: bool = False,
        confidence: float | None = None,
        worker_factory: WorkerFactory = PipelineWorker,
        snapshot_dir: str | Path = SNAPSHOT_DIR,
    ) -> None:
        self.root = root
        self._worker_factory = worker_factory
        self._worker: PipelineWorker | None = None
        self._stop_requested = False
        self._poll_id: str | None = None
        self._photo: tk.PhotoImage | None = None
        self._image_id: int | None = None
        self._placeholder_id: int | None = None
        self._last_status = ""
        self.snapshot_dir = Path(snapshot_dir)
        self._gallery_photo: tk.PhotoImage | None = None
        self._gallery_image_id: int | None = None

        initial_type, initial_value = classify_source(source or RTSP_URL)
        self.source_type = tk.StringVar(value=initial_type)
        self.source_value = tk.StringVar(value=initial_value)
        self.track = tk.BooleanVar(value=track)
        initial_conf = confidence if confidence is not None else CONFIDENCE_THRESHOLD
        self.confidence = tk.DoubleVar(value=min(max(initial_conf, CONFIDENCE_RANGE[0]), CONFIDENCE_RANGE[1]))
        self.status = tk.StringVar(value="Ready")

        root.title(WINDOW_TITLE)
        root.minsize(640, 420)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._on_callback_exception

        self._build_ui()
        self._on_source_type_changed(reset_value=False)
        self._show_placeholder()
        self._poll_id = root.after(POLL_INTERVAL_MS, self._poll)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        controls = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        controls.pack(fill="x")
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Source:").grid(row=0, column=0, sticky="w")
        source_row = ttk.Frame(controls)
        source_row.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        source_row.columnconfigure(1, weight=1)

        self.source_combo = ttk.Combobox(
            source_row, textvariable=self.source_type, values=SOURCE_TYPES, state="readonly", width=18
        )
        self.source_combo.grid(row=0, column=0)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_source_type_changed())

        self.source_entry = ttk.Entry(source_row, textvariable=self.source_value)
        self.source_entry.grid(row=0, column=1, sticky="ew", padx=6)

        self.browse_button = ttk.Button(source_row, text="Browse…", command=self._browse_file)
        self.browse_button.grid(row=0, column=2)

        options = ttk.Frame(controls)
        options.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.track_check = ttk.Checkbutton(options, text="Enable tracking (ByteTrack)", variable=self.track)
        self.track_check.pack(side="left")

        ttk.Label(options, text="Confidence:").pack(side="left", padx=(16, 4))
        self.confidence_scale = ttk.Scale(
            options,
            from_=CONFIDENCE_RANGE[0],
            to=CONFIDENCE_RANGE[1],
            variable=self.confidence,
            command=self._on_confidence_changed,
            length=160,
        )
        self.confidence_scale.pack(side="left")
        self.confidence_label = ttk.Label(options, text=f"{self.confidence.get():.2f}", width=5)
        self.confidence_label.pack(side="left", padx=(4, 0))

        self.stop_button = ttk.Button(options, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        self.start_button = ttk.Button(options, text="Start", command=self.start)
        self.start_button.pack(side="right", padx=(0, 6))

        # The status bar is packed before the notebook so it stays visible when the window shrinks.
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=(6, 2)).pack(
            fill="x", side="bottom"
        )
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=4)
        live_tab = ttk.Frame(self.notebook)
        self.notebook.add(live_tab, text="Live")
        gallery_tab = ttk.Frame(self.notebook)
        self.notebook.add(gallery_tab, text="Snapshots")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._gallery_tab = gallery_tab
        self._build_live_tab(live_tab)
        self._build_gallery_tab(gallery_tab)

    def _build_live_tab(self, parent: ttk.Frame) -> None:
        # Packed before the canvas so it keeps its width when the window is resized.
        events_frame = ttk.LabelFrame(parent, text="PERSON_DETECTED events", padding=4)
        events_frame.pack(side="right", fill="y", padx=(0, 4), pady=4)
        ttk.Label(events_frame, text="Enable tracking to record events", foreground="#777777").pack(
            side="top", anchor="w", pady=(0, 4)
        )
        ttk.Button(events_frame, text="Clear list", command=self._clear_events).pack(side="bottom", fill="x", pady=(4, 0))
        self.event_tree = ttk.Treeview(
            events_frame, columns=[c[0] for c in EVENT_COLUMNS], show="headings", height=12, selectmode="browse"
        )
        for column_id, heading, width in EVENT_COLUMNS:
            self.event_tree.heading(column_id, text=heading)
            self.event_tree.column(column_id, width=width, anchor="w", stretch=False)
        event_scroll = ttk.Scrollbar(events_frame, orient="vertical", command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=event_scroll.set)
        event_scroll.pack(side="right", fill="y")
        self.event_tree.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(
            parent, width=DEFAULT_CANVAS_SIZE[0], height=DEFAULT_CANVAS_SIZE[1], bg="black", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas.bind("<Configure>", lambda _e: self._recenter())

    def _build_gallery_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, padding=(4, 4, 4, 0))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Refresh", command=self._refresh_gallery).pack(side="left")
        self.gallery_count = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.gallery_count).pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, text=f"Folder: {self.snapshot_dir}", foreground="#777777").pack(side="right")

        body = ttk.Frame(parent, padding=4)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(body)
        table_frame.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        self.snapshot_tree = ttk.Treeview(
            table_frame, columns=[c[0] for c in SNAPSHOT_COLUMNS], show="headings", selectmode="browse", height=20
        )
        for column_id, heading, width in SNAPSHOT_COLUMNS:
            self.snapshot_tree.heading(column_id, text=heading)
            self.snapshot_tree.column(column_id, width=width, anchor="w", stretch=False)
        snapshot_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.snapshot_tree.yview)
        self.snapshot_tree.configure(yscrollcommand=snapshot_scroll.set)
        snapshot_scroll.pack(side="right", fill="y")
        self.snapshot_tree.pack(side="left", fill="both", expand=True)
        self.snapshot_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_snapshot_selected())

        preview_frame = ttk.Frame(body)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        self.gallery_canvas = tk.Canvas(preview_frame, bg="black", highlightthickness=0)
        self.gallery_canvas.pack(fill="both", expand=True)
        self.gallery_canvas.bind("<Configure>", lambda _e: self._redraw_gallery())
        self.gallery_caption = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self.gallery_caption, anchor="w").pack(fill="x", pady=(2, 0))

        self._gallery_selected: Path | None = None
        self._gallery_message = ""
        self._show_gallery_message(GALLERY_PREVIEW_PLACEHOLDER)

    # ------------------------------------------------------------- controls
    def _on_source_type_changed(self, reset_value: bool = True) -> None:
        source_type = self.source_type.get()
        if reset_value:
            defaults = {SOURCE_WEBCAM: "0", SOURCE_FILE: "", SOURCE_STREAM: RTSP_URL or ""}
            self.source_value.set(defaults.get(source_type, ""))
        self.browse_button.configure(state="normal" if source_type == SOURCE_FILE else "disabled")

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=[("Video", "*.mp4 *.avi *.mkv *.mov"), ("All files", "*.*")],
        )
        if path:
            self.source_value.set(path)

    def _on_confidence_changed(self, value: str) -> None:
        confidence = float(value)
        self.confidence_label.configure(text=f"{confidence:.2f}")
        if self._worker is not None:
            self._worker.set_confidence(confidence)

    def _set_controls_running(self, running: bool) -> None:
        """Lock the settings that only take effect at start while a pipeline is running."""
        locked = "disabled" if running else "normal"
        self.source_combo.configure(state="disabled" if running else "readonly")
        self.source_entry.configure(state=locked)
        self.track_check.configure(state=locked)
        self.start_button.configure(state=locked)
        self.stop_button.configure(state="normal" if running else "disabled")
        self.browse_button.configure(
            state="normal" if not running and self.source_type.get() == SOURCE_FILE else "disabled"
        )

    def start(self) -> None:
        if self._worker is not None:
            return

        try:
            source = validate_source(self.source_type.get(), self.source_value.get())
        except ValueError as exc:
            messagebox.showwarning("Invalid video source", str(exc), parent=self.root)
            return

        self._stop_requested = False
        self._worker = self._worker_factory(source, self.confidence.get(), self.track.get())
        self._worker.start()
        self._set_controls_running(True)
        self._set_status("Loading YOLO model…")

    def stop(self) -> None:
        if self._worker is None or self._stop_requested:
            return
        self._stop_requested = True
        self._worker.stop()
        self.stop_button.configure(state="disabled")
        self._set_status("Stopping…")

    def close(self) -> None:
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        if self._worker is not None:
            self._worker.stop(timeout=CLOSE_JOIN_TIMEOUT)
        self.root.destroy()

    # ---------------------------------------------------------------- polling
    def _poll(self) -> None:
        self._poll_id = None
        worker = self._worker

        if worker is not None:
            try:
                frame = worker.frames.get_nowait()
            except queue.Empty:
                frame = None
            if frame is not None and worker.state == STATE_RUNNING:
                self._show_frame(frame)
            self._drain_events(worker)

            if worker.state == STATE_LOADING:
                self._set_status("Loading YOLO model…")
            elif worker.state == STATE_RUNNING and not self._stop_requested:
                mode = "tracking" if worker.track else "detection"
                self._set_status(f"Running ({mode}) — {worker.source}")
            elif worker.state == STATE_STOPPED and not worker.is_running:
                self._on_worker_finished(worker)

        self._poll_id = self.root.after(POLL_INTERVAL_MS, self._poll)

    def _drain_events(self, worker: PipelineWorker) -> None:
        """Move every queued PERSON_DETECTED event into the table (newest on top)."""
        while True:
            try:
                event, snapshot_path = worker.events.get_nowait()
            except queue.Empty:
                return
            self.event_tree.insert("", 0, values=format_event_row(event, snapshot_path))
            rows = self.event_tree.get_children()
            if len(rows) > MAX_EVENT_ROWS:
                self.event_tree.delete(rows[-1])

    def _clear_events(self) -> None:
        self.event_tree.delete(*self.event_tree.get_children())

    def _on_worker_finished(self, worker: PipelineWorker) -> None:
        self._worker = None
        self._set_controls_running(False)
        self._show_placeholder()

        if worker.error and not self._stop_requested:
            self._set_status("Error")
            messagebox.showerror("Pipeline error", worker.error, parent=self.root)
        elif self._stop_requested:
            self._set_status("Stopped")
        else:
            self._set_status("Video finished")
        self._stop_requested = False

    # --------------------------------------------------------------- gallery
    def _on_tab_changed(self, _event: Any) -> None:
        # Rescan only when the tab is shown, so the folder is not read while nobody looks at it.
        if self.notebook.select() == str(self._gallery_tab):
            self._refresh_gallery()

    def _refresh_gallery(self) -> None:
        selected_before = self._gallery_selected
        self.snapshot_tree.delete(*self.snapshot_tree.get_children())
        infos = list_snapshots(self.snapshot_dir)
        for info in infos:
            self.snapshot_tree.insert("", "end", iid=str(info.path), values=format_snapshot_row(info))
        self.gallery_count.set(f"{len(infos)} snapshot(s)")

        if selected_before is not None and str(selected_before) in self.snapshot_tree.get_children():
            self.snapshot_tree.selection_set(str(selected_before))
        else:
            self._gallery_selected = None
            self._show_gallery_message(GALLERY_PREVIEW_PLACEHOLDER if infos else "No snapshots saved yet")

    def _on_snapshot_selected(self) -> None:
        selection = self.snapshot_tree.selection()
        if not selection:
            return
        self._gallery_selected = Path(selection[0])
        self._render_selected_snapshot()

    def _render_selected_snapshot(self) -> None:
        path = self._gallery_selected
        if path is None:
            return
        width, height = self._gallery_canvas_size()
        try:
            data = load_image_ppm(path, width, height)
        except ValueError:
            # Usually the cleanup job removed the file after the list was built; not an error.
            logger.info("Snapshot %s is no longer readable (deleted?)", path)
            self._gallery_selected = None
            self._show_gallery_message(f"Could not open {path.name} — it may have been deleted.")
            self._refresh_gallery()
            return

        self._gallery_photo = tk.PhotoImage(data=data, format="PPM")
        self.gallery_canvas.delete("all")
        self._gallery_image_id = self.gallery_canvas.create_image(
            width // 2, height // 2, image=self._gallery_photo, anchor="center"
        )
        self.gallery_caption.set(path.name)

    def _show_gallery_message(self, text: str) -> None:
        self._gallery_message = text
        self._gallery_photo = None
        self._gallery_image_id = None
        self.gallery_canvas.delete("all")
        self.gallery_caption.set("")
        self._draw_gallery_message()

    def _draw_gallery_message(self) -> None:
        width, height = self._gallery_canvas_size()
        self.gallery_canvas.create_text(
            width // 2, height // 2, text=self._gallery_message, fill="#9aa0a6", font=("TkDefaultFont", 12),
            width=max(200, width - 40),
        )

    def _redraw_gallery(self) -> None:
        """Re-fit the preview after the canvas is resized."""
        if self._gallery_selected is not None:
            self._render_selected_snapshot()
        elif self._gallery_message:
            self.gallery_canvas.delete("all")
            self._draw_gallery_message()

    def _gallery_canvas_size(self) -> tuple[int, int]:
        width, height = self.gallery_canvas.winfo_width(), self.gallery_canvas.winfo_height()
        if width <= 1 or height <= 1:
            return DEFAULT_CANVAS_SIZE
        return width, height

    # ---------------------------------------------------------------- display
    def _canvas_size(self) -> tuple[int, int]:
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return DEFAULT_CANVAS_SIZE
        return width, height

    def _show_frame(self, frame: Any) -> None:
        width, height = self._canvas_size()
        try:
            self._photo = tk.PhotoImage(data=frame_to_ppm(frame, width, height), format="PPM")
        except (ValueError, tk.TclError):
            logger.warning("Skipping a frame that could not be displayed", exc_info=True)
            return

        if self._placeholder_id is not None:
            self.canvas.delete(self._placeholder_id)
            self._placeholder_id = None
        if self._image_id is None:
            self._image_id = self.canvas.create_image(width // 2, height // 2, image=self._photo, anchor="center")
        else:
            self.canvas.itemconfigure(self._image_id, image=self._photo)
            self.canvas.coords(self._image_id, width // 2, height // 2)

    def _show_placeholder(self) -> None:
        if self._image_id is not None:
            self.canvas.delete(self._image_id)
            self._image_id = None
        self._photo = None
        if self._placeholder_id is None:
            width, height = self._canvas_size()
            self._placeholder_id = self.canvas.create_text(
                width // 2, height // 2, text=PLACEHOLDER_TEXT, fill="#9aa0a6", font=("TkDefaultFont", 12)
            )

    def _recenter(self) -> None:
        width, height = self._canvas_size()
        for item in (self._image_id, self._placeholder_id):
            if item is not None:
                self.canvas.coords(item, width // 2, height // 2)

    def _set_status(self, text: str) -> None:
        if text != self._last_status:
            self._last_status = text
            self.status.set(text)

    def _on_callback_exception(self, exc_type: type, exc: BaseException, tb: Any) -> None:
        # SIGTERM is mapped to KeyboardInterrupt by main.py; Tk swallows exceptions raised
        # inside callbacks, so route it to a clean shutdown here instead of losing it.
        if issubclass(exc_type, KeyboardInterrupt):
            self.close()
            return
        logger.error("Unhandled error in GUI callback", exc_info=(exc_type, exc, tb))


def run_gui(source: str | None = None, track: bool = False, confidence: float | None = None) -> int:
    """Open the main window and block until it is closed."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        logger.error("Cannot open the GUI (no display available?): %s", exc)
        return 1

    app = SurveillanceApp(root, source=source, track=track, confidence=confidence)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("Shutdown requested, closing GUI")
        try:
            app.close()
        except tk.TclError:
            pass  # window already destroyed
    return 0
