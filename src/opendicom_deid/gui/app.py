"""Responsive Tk desktop: workers exchange data with the UI through a queue."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..cli import report_path_allowed
from ..core.models import Cancelled, Finding, Progress, ScanResult
from ..core.report import Report, format_findings
from .appearance import SystemAppearanceWatcher, macos_window_appearance
from .dialogs import ask_directory
from .licenses import license_text
from .progress import LatestProgress, progress_display
from .settings import AdvancedSettings
from .storage_inspection import StorageInspectionDialog
from .theme import apply_theme, style_plain_widget
from .widgets import AutoHideScrollbar, ThemedScrolledText


def create_output_folder(parent: str, name: str, input_directory: str) -> Path:
    """Create exactly one new folder, keeping it outside the source tree."""
    name = name.strip()
    if (
        not name
        or name in {".", ".."}
        or name.endswith(".")
        or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name)
        or name.split(".", 1)[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
    ):
        raise ValueError("Enter a folder name without path separators or special characters.")
    if not parent.strip():
        raise ValueError("Choose a parent folder first.")
    parent_path = Path(parent).expanduser().resolve(strict=True)
    if not parent_path.is_dir():
        raise ValueError("The parent location must be an existing folder.")
    destination = parent_path / name
    if input_directory.strip():
        source = Path(input_directory.strip()).expanduser().resolve()
        if (
            destination == source
            or destination.is_relative_to(source)
            or source.is_relative_to(destination)
        ):
            raise ValueError(
                "Create the output folder outside the input folder and its parent folders."
            )
    # Do not reuse, overwrite, or remove a pre-existing directory or symlink.
    destination.mkdir(mode=0o700)
    return destination


class App(tk.Tk):
    """One window and one worker at a time; Tk is used only on the main thread."""

    def __init__(self) -> None:
        super().__init__()
        self._appearance_id = None
        self._appearance_watcher = None
        native_appearance = macos_window_appearance(self)
        if native_appearance is None:
            self._appearance_watcher = SystemAppearanceWatcher()
        self._theme_mode = apply_theme(
            self, native_appearance or self._appearance_watcher.current
        )
        self.title("OpenDICOM De-ID")
        self.minsize(740, 680)
        width = min(860, max(740, self.winfo_screenwidth() - 80))
        height = min(800, max(680, self.winfo_screenheight() - 100))
        self.geometry(f"{width}x{height}")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._events: Queue[tuple[str, object]] = Queue()
        self._latest_progress = LatestProgress()
        self._cancel = Event()
        self._busy = False
        self._progress_animating = False
        self._closing = False
        self._advanced_visible = False
        self._folder_window: tk.Toplevel | None = None
        self._inspection_window: StorageInspectionDialog | None = None
        self._unsupported_selection: tuple[str, bool, str] | None = None
        self._last_scan: tuple[str, bool] | None = None
        self._report: Report | None = None
        self._report_input: Path | None = None
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.recursive = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Choose input and output folders to get started.")
        self.counts = tk.StringVar(value="No input scanned")
        self.scan_summary = tk.StringVar(value="Optional preview. De-identify always scans first.")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame = ttk.Frame(self, padding=(24, 16))
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="OpenDICOM De-ID", style="Title.TLabel").grid(sticky="w")
        ttk.Label(
            header, text="DICOM de-identification, on your computer.", style="Muted.TLabel"
        ).grid(row=1, sticky="w", pady=(5, 0))
        ttk.Label(header, text="OFFLINE", style="Badge.TLabel").grid(row=0, column=1, padx=(16, 12))
        ttk.Button(header, text="About", command=self.show_about).grid(row=0, column=2)

        directories = ttk.Frame(frame, style="Card.TFrame", padding=(18, 12))
        directories.grid(row=1, sticky="ew")
        directories.columnconfigure(0, weight=1)
        self.input_entry, self.input_browse = self._directory_row(
            directories, 1, "Input folder", self.input_path, True
        )
        self.output_entry, self.output_browse = self._directory_row(
            directories, 2, "Output folder", self.output_path, False
        )
        self.input_entry.bind("<Return>", lambda _: self.scan())
        ttk.Label(
            directories,
            text="Choose an empty folder outside the input folder, or create a new one.",
            style="CardMuted.TLabel",
        ).grid(row=3, sticky="w", pady=(0, 4))
        options = ttk.Frame(directories, style="Card.TFrame")
        options.grid(row=4, column=0, sticky="ew")
        options.columnconfigure(0, weight=1)
        self.recursive_check = ttk.Checkbutton(
            options,
            text="Include subfolders",
            variable=self.recursive,
            command=self._input_changed,
        )
        self.recursive_check.grid(row=0, column=0, sticky="w")
        self.scan_button = ttk.Button(options, text="Scan input", command=self.scan)
        self.scan_button.grid(row=0, column=2, sticky="e")
        self.inspect_button = ttk.Button(
            options, text="Inspect file…", command=self.show_storage_inspection
        )
        self.inspect_button.grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Label(
            directories, textvariable=self.scan_summary, style="CardMuted.TLabel", wraplength=700
        ).grid(row=5, sticky="w", pady=(4, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=2, sticky="ew", pady=(10, 10))
        actions.columnconfigure(0, weight=1)
        self.advanced_button = ttk.Button(
            actions, text="Advanced options", command=self.toggle_advanced
        )
        self.advanced_button.grid(row=0, sticky="w")
        self.start_button = ttk.Button(
            actions, text="De-identify", command=self.process, style="Primary.TButton"
        )
        self.start_button.grid(row=0, column=1, sticky="e")
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self.cancel, state="disabled"
        )
        self.cancel_button.grid(row=0, column=2, padx=(8, 0))
        self._advanced_window = tk.Toplevel(self)
        self._advanced_window.title("Advanced options")
        style_plain_widget(self._advanced_window, "background")
        self._advanced_window.transient(self)
        self._advanced_window.withdraw()
        self._advanced_window.columnconfigure(0, weight=1)
        self._advanced_window.resizable(False, False)
        self._advanced_window.protocol("WM_DELETE_WINDOW", self.toggle_advanced)
        self.settings = AdvancedSettings(self._advanced_window)
        self.settings.grid(row=0, sticky="ew", padx=20, pady=20)
        ttk.Button(
            self._advanced_window,
            text="Done",
            style="Primary.TButton",
            command=self.toggle_advanced,
        ).grid(row=1, sticky="e", padx=20, pady=(0, 20))

        results = ttk.Frame(frame, style="Card.TFrame", padding=(18, 12))
        results.grid(row=4, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(4, weight=1)
        ttk.Label(results, text="Activity & findings", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.save_button = ttk.Button(
            results, text="Save report…", command=self.save_report, state="disabled"
        )
        self.save_button.grid(row=0, column=1, sticky="e")
        ttk.Label(results, textvariable=self.status, style="Card.TLabel", wraplength=700).grid(
            row=1, columnspan=2, sticky="w", pady=(6, 6)
        )
        self.progress = ttk.Progressbar(results, maximum=100, mode="determinate")
        self.progress.grid(row=2, columnspan=2, sticky="ew")
        ttk.Label(results, textvariable=self.counts, style="CardMuted.TLabel").grid(
            row=3, columnspan=2, sticky="w", pady=(6, 6)
        )
        details_frame = ttk.Frame(results, style="Card.TFrame")
        details_frame.grid(row=4, columnspan=2, sticky="nsew")
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        self.details = tk.Text(
            details_frame,
            height=5,
            width=60,
            wrap="word",
            font="TkTextFont",
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            spacing1=3,
            spacing3=5,
        )
        style_plain_widget(self.details, "muted")
        self.details.grid(row=0, column=0, sticky="nsew")
        self.details_scrollbar = AutoHideScrollbar(
            details_frame, orient="vertical", command=self.details.yview
        )
        self.details_scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.details.configure(yscrollcommand=self.details_scrollbar.set)
        ttk.Label(
            frame, text="Research and development only. Not for clinical use. Review privacy before sharing.",
            style="Muted.TLabel",
        ).grid(row=5, sticky="w", pady=(10, 0))
        self._set_details(
            "All processing stays on this computer.\n\n"
            "Use Scan input for an optional preview, or De-identify to start. "
            "Identifying text in images is not removed."
        )
        self.input_path.trace_add("write", self._input_changed)
        # Keep wrapped text inside the window at the minimum supported size.
        directories.bind(
            "<Configure>", lambda event: self._wrap_labels(directories, event.width - 40)
        )
        results.bind("<Configure>", lambda event: self._wrap_labels(results, event.width - 40))
        self._poll_id = self.after(80, self._poll)
        self._appearance_id = self.after(100, self._poll_system_appearance)

    def _system_appearance(self) -> str:
        native = macos_window_appearance(self)
        if native is not None:
            return native
        if self._appearance_watcher is None:
            self._appearance_watcher = SystemAppearanceWatcher(initial=self._theme_mode)
        return self._appearance_watcher.current

    def _sync_system_appearance(self) -> None:
        mode = self._system_appearance()
        if mode != self._theme_mode:
            self._theme_mode = apply_theme(self, mode)

    def _poll_system_appearance(self) -> None:
        self._sync_system_appearance()
        self._appearance_id = self.after(1000, self._poll_system_appearance)

    def _directory_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        is_input: bool,
    ) -> tuple[ttk.Entry, ttk.Button]:
        area = ttk.Frame(parent, style="Card.TFrame")
        area.grid(row=row, sticky="ew", pady=(0, 8))
        area.columnconfigure(0, weight=1)
        ttk.Label(area, text=label, style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        entry = ttk.Entry(area, textvariable=variable)
        entry.grid(row=1, column=0, sticky="ew")
        browse = ttk.Button(area, text="Browse…", command=lambda: self._browse(variable, is_input))
        browse.grid(row=1, column=1, padx=(8, 0))
        if not is_input:
            self.output_new = ttk.Button(area, text="New folder…", command=self._new_output_folder)
            self.output_new.grid(row=1, column=2, padx=(8, 0))
        return entry, browse

    @staticmethod
    def _wrap_labels(parent: tk.Misc, width: int) -> None:
        for child in parent.winfo_children():
            if isinstance(child, ttk.Label):
                child.configure(wraplength=max(200, width))

    def _browse(self, variable: tk.StringVar, is_input: bool) -> None:
        selected = ask_directory(
            parent=self,
            title="Select input directory"
            if is_input
            else "Select a new or empty output directory",
            mustexist=is_input,
        )
        if selected:
            variable.set(selected)

    def _new_output_folder(self) -> None:
        if self._busy:
            return
        if self._folder_window is not None:
            self._folder_window.lift()
            return
        output = self.output_path.get().strip()
        source = self.input_path.get().strip()
        if output:
            initial = Path(output).expanduser()
            if not initial.is_dir():
                initial = initial.parent
        elif source:
            initial = Path(source).expanduser().parent
        else:
            initial = Path.home()
        if not initial.is_dir():
            initial = Path.home()
        window = tk.Toplevel(self)
        self._folder_window = window
        window.title("Create output folder")
        window.transient(self)
        style_plain_widget(window)
        window.resizable(False, False)
        body = ttk.Frame(window, style="Card.TFrame", padding=24)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Create output folder", style="Section.TLabel").grid(
            row=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            body, text="Choose where to put the de-identified dataset.", style="CardMuted.TLabel"
        ).grid(row=1, columnspan=2, sticky="w", pady=(6, 20))
        parent = tk.StringVar(value=str(initial))
        name = tk.StringVar(value="deidentified")
        error = tk.StringVar()
        ttk.Label(body, text="Parent folder", style="Field.TLabel").grid(row=2, sticky="w")
        ttk.Entry(body, textvariable=parent, width=44).grid(row=3, sticky="ew", pady=(6, 16))

        def browse_parent() -> None:
            location = ask_directory(
                parent=window, title="Choose parent folder", initialdir=parent.get(), mustexist=True
            )
            if location:
                parent.set(location)

        ttk.Button(body, text="Browse…", command=browse_parent).grid(
            row=3, column=1, padx=(8, 0), pady=(6, 16)
        )
        ttk.Label(body, text="New folder name", style="Field.TLabel").grid(row=4, sticky="w")
        entry = ttk.Entry(body, textvariable=name)
        entry.grid(row=5, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(body, textvariable=error, style="CardMuted.TLabel", wraplength=480).grid(
            row=6, columnspan=2, sticky="w", pady=(10, 12)
        )
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=7, columnspan=2, sticky="e")

        def dismiss() -> None:
            self._folder_window = None
            window.destroy()

        def create() -> None:
            try:
                destination = create_output_folder(parent.get(), name.get(), self.input_path.get())
            except ValueError as exc:
                error.set(str(exc))
                return
            except FileExistsError:
                error.set(
                    "That folder already exists. Choose another name, or use Browse in the main window."
                )
                return
            except OSError:
                error.set(
                    "Could not create the folder. Check the parent location and write permissions."
                )
                return
            self.output_path.set(str(destination))
            self.status.set("New empty output folder created. Ready to de-identify.")
            dismiss()
            self.output_entry.focus_set()

        ttk.Button(buttons, text="Cancel", command=dismiss).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Create folder", style="Primary.TButton", command=create).grid(
            row=0, column=1
        )
        window.protocol("WM_DELETE_WINDOW", dismiss)
        window.bind("<Escape>", lambda _: dismiss())
        window.bind("<Return>", lambda _: create())
        window.grab_set()
        entry.focus_set()
        entry.selection_range(0, "end")

    def toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced_window.deiconify()
            self._advanced_window.lift()
            self.advanced_button.configure(text="Close advanced options")
        else:
            self._advanced_window.withdraw()
            self.advanced_button.configure(text="Advanced options")

    def _input_changed(self, *_: str) -> None:
        selection = (self.input_path.get(), self.recursive.get())
        if not self._busy and selection != self._last_scan:
            self.scan_summary.set("Optional preview. De-identify always scans first.")
            self.counts.set("Input selection changed; no preview scan run")

    def _set_details(self, content: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", content)
        self.details.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for widget in (
            self.input_entry,
            self.output_entry,
            self.input_browse,
            self.output_browse,
            self.output_new,
            self.recursive_check,
            self.scan_button,
            self.inspect_button,
            self.start_button,
        ):
            widget.configure(state=state)
        self.settings.set_enabled(not busy)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.save_button.configure(state="normal" if self._report and not busy else "disabled")
        if self._inspection_window is not None:
            self._inspection_window.set_busy(busy)

    def _remember_unsupported(self, findings: list[Finding]) -> None:
        self._unsupported_selection = None
        for finding in findings:
            if finding.code in {"UNSUPPORTED_OBJECT", "VIEWRAY_PLAN_REVIEW_REQUIRED"} and finding.file_id:
                number = finding.file_id.removeprefix("File ")
                if number.isascii() and number.isdecimal():
                    self._unsupported_selection = (
                        self.input_path.get().strip(), self.recursive.get(), str(int(number))
                    )
                    break

    def show_storage_inspection(self) -> None:
        if self._busy:
            return
        value = self.input_path.get().strip()
        if not value:
            self.status.set("Select the same input folder used in the failed run first.")
            return
        if self._inspection_window is not None:
            self._inspection_window.lift()
            return
        recursive = self.recursive.get()
        suggestion = self._unsupported_selection
        file_number = suggestion[2] if suggestion and suggestion[:2] == (value, recursive) else ""

        def inspect(number: int, show_private: bool) -> None:
            from ..core.storage_inspection import inspect_storage

            self.status.set("Inspecting technical format details. No files are changed.")
            self._launch(
                lambda: inspect_storage(
                    Path(value), file_id=number, recursive=recursive,
                    show_private_class_uids=show_private, cancel=self._cancel,
                ),
                "storage_inspection",
            )

        def dismissed() -> None:
            self._inspection_window = None

        def check_identifiers(number: int) -> None:
            from ..core.privacy_inspection import inspect_privacy

            self.status.set("Checking for potential identifying data locally. No files are changed.")
            self._launch(
                lambda: inspect_privacy(
                    Path(value), file_id=number, recursive=recursive, cancel=self._cancel,
                ),
                "storage_inspection",
            )

        self._inspection_window = StorageInspectionDialog(
            self, file_number=file_number, inspect_callback=inspect,
            privacy_callback=check_identifiers,
            cancel_callback=self.cancel, close_callback=dismissed,
        )

    def _launch(self, job: Callable[[], object], kind: str) -> None:
        self._cancel = Event()
        self._set_busy(True)
        self._latest_progress.begin()
        self._show_progress(Progress("Preparing", 0, 0))
        self.counts.set("Preparing…")

        def work() -> None:
            try:
                self._events.put((kind, job()))
            except Cancelled:
                self._events.put(("cancelled", None))
            except Exception:
                # DICOM library errors can contain patient values and paths.
                self._events.put(("error", None))

        Thread(target=work, name="opendicom-worker", daemon=False).start()

    def _progress_event(self, value: Progress) -> None:
        # The worker only writes plain data; the Tk thread paints the newest
        # update once per poll, even when thousands of files finish quickly.
        self._latest_progress.publish(value)

    def _show_progress(self, value: Progress) -> None:
        display = progress_display(value)
        mode = "indeterminate" if display.percentage is None else "determinate"
        if str(self.progress.cget("mode")) != mode:
            self.progress.stop()
            self._progress_animating = False
            self.progress.configure(mode=mode, value=0)
        if mode == "indeterminate" and not self._progress_animating:
            self.progress.start(40)
            self._progress_animating = True
        if display.percentage is not None:
            self.progress.configure(value=display.percentage)
        self.counts.set(display.label)

    def scan(self) -> None:
        if self._busy:
            return
        value = self.input_path.get().strip()
        if not value:
            self.status.set("Select an input directory first.")
            return
        from ..core.scanner import scan_directory

        recursive = self.recursive.get()
        self._last_scan = (value, recursive)
        allow_viewray = self.settings.viewray_public_plan.get()
        self.scan_summary.set("Scanning DICOM files…")
        self.status.set("Inspecting the selected input. No files are changed.")
        self._launch(
            lambda: scan_directory(
                Path(value), recursive=recursive, cancel=self._cancel, progress=self._progress_event,
                allow_viewray_public_plan=allow_viewray,
            ),
            "scan",
        )

    def process(self) -> None:
        if self._busy:
            return
        if not self.input_path.get().strip() or not self.output_path.get().strip():
            self.status.set("Select both an input directory and an output directory.")
            return
        try:
            options = self.settings.options(self.recursive.get())
        except ValueError:
            self.status.set(
                "Use a neutral prefix: 1–16 letters, digits, underscores or hyphens, starting with a letter."
            )
            return
        from ..core.anonymizer import process_batch

        input_dir = Path(self.input_path.get().strip())
        output_dir = Path(self.output_path.get().strip())
        self._report = None
        self._report_input = input_dir
        self._set_details(
            "Scanning, de-identifying, and validating. A completed dataset is published only after checks finish."
        )
        self.status.set(
            "Processing locally. Cancellation waits for active files to finish safely."
        )
        self._launch(
            lambda: process_batch(
                input_dir, output_dir, options, cancel=self._cancel, progress=self._progress_event
            ),
            "report",
        )

    def cancel(self) -> None:
        if self._busy:
            self._cancel.set()
            self.cancel_button.configure(state="disabled")
            self.status.set("Cancelling safely… Please wait for cleanup to finish.")

    def _poll(self) -> None:
        if (progress := self._latest_progress.take()) is not None and self._busy:
            self._show_progress(progress)
        for _ in range(150):
            try:
                kind, value = self._events.get_nowait()
            except Empty:
                break
            self._latest_progress.finish()
            self.progress.stop()
            self._progress_animating = False
            self._set_busy(False)
            if kind == "scan":
                assert isinstance(value, ScanResult)
                self._remember_unsupported(value.findings)
                modalities = " · ".join(
                    f"{name}: {count}" for name, count in sorted(value.modalities.items())
                )
                self.scan_summary.set(
                    f"{value.total} DICOM files" + (f"  |  {modalities}" if modalities else "")
                )
                self.counts.set(f"Scanned: {value.total} DICOM files; skipped: {value.skipped}")
                self.progress.configure(
                    mode="determinate", value=100 if value.ok and value.total else 0
                )
                self.status.set(
                    "Scan complete. Ready to de-identify."
                    if value.ok and value.total
                    else "Input needs attention. Review the scan findings below."
                )
                findings = format_findings(value.findings)
                self._set_details(
                    findings
                    or "Preliminary scan complete. Full validation runs during de-identification."
                )
            elif kind == "report":
                assert isinstance(value, Report)
                self._remember_unsupported(value.findings)
                self._report = value
                self.save_button.configure(state="normal")
                self.counts.set(f"Processed: {value.output_count} / {value.input_count} files")
                self.progress.configure(mode="determinate", value=100 if value.ok else 0)
                self.status.set(
                    {
                        "COMPLETED": "Technical processing completed. Privacy review is required.",
                        "FAILED": "Processing failed. No completed dataset was published.",
                        "CANCELLED": "Cancelled. No completed dataset was published.",
                    }[value.status]
                )
                self._set_details(value.summary())
            elif kind == "storage_inspection":
                assert isinstance(value, dict)
                self.progress.configure(mode="determinate", value=0)
                self.counts.set("File inspection finished")
                self.status.set("File inspection finished. No files were changed.")
                if self._inspection_window is not None:
                    self._inspection_window.show_result(value)
            elif kind == "cancelled":
                if self._inspection_window is not None:
                    self._inspection_window.show_result({"status": "CANCELLED"})
                self.status.set("Cancelled. No completed dataset was published.")
                self.scan_summary.set("Scan cancelled; counts are incomplete.")
                self.counts.set("Cancelled")
                self.progress.configure(mode="determinate", value=0)
            else:
                if self._inspection_window is not None:
                    self._inspection_window.show_result({
                        "status": "FAILED", "message": "The format could not be safely inspected."
                    })
                self.status.set("The operation failed. No success is claimed.")
                self.counts.set("Operation failed")
                self._set_details(
                    "Check directory access, available space, and DICOM support. Parser error text is omitted to protect patient information."
                )
                self.progress.configure(mode="determinate", value=0)
            if self._closing:
                self.destroy()
                return
        self._poll_id = self.after(80, self._poll)

    def save_report(self) -> None:
        if self._report is None or self._report_input is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="Save de-identification report",
            initialfile="deidentification-report.json",
            defaultextension=".json",
            filetypes=[("JSON report", "*.json")],
        )
        if not destination:
            return
        path = Path(destination)
        if not report_path_allowed(path, self._report_input):
            messagebox.showerror(
                "Report location", "Save a .json report outside the input directory.", parent=self
            )
            return
        try:
            self._report.save(path)
        except FileExistsError:
            messagebox.showerror(
                "Report not saved",
                "Choose a new filename. Existing files are never overwritten.",
                parent=self,
            )
            return
        except OSError:
            messagebox.showerror(
                "Report not saved", "Check directory permissions and available space.", parent=self
            )
            return
        self.status.set(
            "Report saved. It contains counts and findings, without original patient values or UID mappings."
        )

    def show_about(self) -> None:
        window = tk.Toplevel(self)
        window.title("About OpenDICOM De-ID")
        style_plain_widget(window, "background")
        window.geometry("680x500")
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        ttk.Label(
            window,
            padding=16,
            text=f"OpenDICOM De-ID {__version__}\nOffline DICOM de-identification\n"
            "BSD-3-Clause · Under development. Not intended for clinical use. "
            "Automated checks are not proof of privacy or regulatory compliance.",
            wraplength=630,
        ).grid(row=0, sticky="ew")
        text = ThemedScrolledText(
            window,
            wrap="word",
            font="TkTextFont",
            padx=12,
            pady=12,
            relief="flat",
            borderwidth=0,
        )
        style_plain_widget(text)
        style_plain_widget(text.frame)
        style_plain_widget(text.vbar)
        text.grid(row=1, sticky="nsew", padx=16)
        text.insert("1.0", license_text())
        text.configure(state="disabled")
        ttk.Button(window, text="Close", command=window.destroy, style="Primary.TButton").grid(
            row=2, pady=12
        )

    def close(self) -> None:
        if self._busy:
            self._closing = True
            self.cancel()
        else:
            self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_appearance_id", None) is not None:
            self.after_cancel(self._appearance_id)
            self._appearance_id = None
        if getattr(self, "_appearance_watcher", None) is not None:
            self._appearance_watcher.stop()
        if getattr(self, "_poll_id", None) is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()


def check_startup() -> None:
    """Exercise Tk and a synthetic header through source and frozen GUI builds."""
    import json
    import sys
    import tempfile
    import time

    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import EncapsulatedPDFStorage, ExplicitVRLittleEndian, generate_uid

    app = App()
    try:
        app.update_idletasks()
        # Read the actual native frame attribute, independently of the setter,
        # so the source and packaged Windows checks catch a wrong HWND as well
        # as missing DWM calls. This never changes an OS preference.
        native_titlebar = None
        if sys.platform == "win32":
            import ctypes

            native_titlebar = ctypes.WinDLL("dwmapi").DwmGetWindowAttribute
            native_titlebar.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ]
            native_titlebar.restype = ctypes.c_int32

        def check_titlebar(window: tk.Tk | tk.Toplevel, mode: str) -> None:
            if native_titlebar is None:
                return
            dark = ctypes.c_int32()
            frame = int(window.wm_frame(), 0)
            result = native_titlebar(frame, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            assert result == 0, f"Could not read Windows title-bar appearance: {result:#x}"
            assert bool(dark.value) == (mode == "dark"), "Windows title bar has the wrong appearance."

        # Verify both packaged palettes and a visibly filled progress element;
        # startup checks must not change any OS appearance preference.
        # Suspend only the UI poll while requesting test palettes, otherwise
        # the current OS preference can race and overwrite a test switch.
        app.after_cancel(app._appearance_id)
        app._appearance_id = None
        native_appearance = macos_window_appearance(app)
        try:
            for mode in ("light", "dark", "light"):
                if native_appearance is not None:
                    # Override this synthetic window, never the OS preference.
                    app.tk.call(
                        "tk::unsupported::MacWindowStyle", "appearance", app,
                        "darkaqua" if mode == "dark" else "aqua",
                    )
                    app._sync_system_appearance()
                    assert app._theme_mode == mode
                else:
                    apply_theme(app, mode)
                app._show_progress(Progress("Scanning", 1, 2))
                app.update()
                check_titlebar(app, mode)
                assert float(app.progress.cget("value")) == 50
                assert app.progress.winfo_height() >= 10
                assert "pbar" in app.progress.identify(
                    app.progress.winfo_width() // 4, app.progress.winfo_height() // 2
                )
            if native_titlebar is not None:
                apply_theme(app, "dark")
            app.toggle_advanced()
            app.update()
            check_titlebar(app._advanced_window, app._theme_mode)
            app.toggle_advanced()
            app.update()
            app.toggle_advanced()
            app.update()
            check_titlebar(app._advanced_window, app._theme_mode)
            app._new_output_folder()
            app.update()
            assert app._folder_window is not None
            check_titlebar(app._folder_window, app._theme_mode)
            if native_titlebar is not None:
                for mode in ("light", "dark", "light"):
                    apply_theme(app, mode)
                    app.update()
                    for window in (app, app._advanced_window, app._folder_window):
                        check_titlebar(window, mode)
                print("GUI: Windows title bars follow light/dark changes, new dialogs, and remapping.")
            # Exercise the same close callback as the window manager without
            # creating an output folder.
            app.tk.call(app._folder_window.protocol("WM_DELETE_WINDOW"))
            app.toggle_advanced()
            app.update()
            assert app._folder_window is None
            assert app.grab_current() is None
        finally:
            if native_appearance is not None:
                app.tk.call("tk::unsupported::MacWindowStyle", "appearance", app, "auto")
            app._sync_system_appearance()
            app._appearance_id = app.after(1000, app._poll_system_appearance)
        # A real worker read verifies that frozen builds contain the header
        # inspector, UID dictionary, and its GUI result path. Synthetic only.
        with tempfile.TemporaryDirectory(prefix="opendicom-startup-") as temporary:
            meta = FileMetaDataset()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = EncapsulatedPDFStorage
            meta.MediaStorageSOPInstanceUID = generate_uid()
            ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
            ds.SOPClassUID = EncapsulatedPDFStorage
            ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
            ds.Modality = "DOC"
            ds.PatientID, ds.PatientName = "SYNTHETIC-ID", "Synthetic^Only"
            ds.save_as(Path(temporary) / "synthetic.dcm", enforce_file_format=True)
            app.input_path.set(temporary)
            app.inspect_button.invoke()
            app.update()
            dialog = app._inspection_window
            assert dialog is not None and not app._busy
            dialog.file_number.set("1")
            dialog.inspect_button.invoke()
            deadline = time.monotonic() + 15
            while app._busy:
                assert time.monotonic() < deadline, "Synthetic GUI inspection timed out."
                app.update()
                time.sleep(0.01)
            text = dialog.details.get("1.0", "end")
            result = json.loads(text)["files"][0]
            assert result["status"] == "INSPECTED"
            assert result["sop_class"]["uid"] == str(EncapsulatedPDFStorage)
            assert result["modality"] == "DOC" and not result["supported_storage_class"]
            assert "SYNTHETIC-ID" not in text and "Synthetic^Only" not in text
            assert str(ds.SOPInstanceUID) not in text and temporary not in text
            dialog.privacy_button.invoke()
            deadline = time.monotonic() + 15
            while app._busy:
                assert time.monotonic() < deadline, "Synthetic identifier inspection timed out."
                app.update()
                time.sleep(0.01)
            text = dialog.details.get("1.0", "end")
            privacy = json.loads(text)
            assert privacy["status"] == "REVIEW_REQUIRED"
            assert privacy["summary"]["direct_identifiers"] >= 2
            assert privacy["summary"]["entity_uids"] >= 1
            assert "SYNTHETIC-ID" not in text and "Synthetic^Only" not in text
            assert str(ds.SOPInstanceUID) not in text and temporary not in text
            dialog.close()
            app.update()
            assert app._inspection_window is None
            assert app.grab_current() is None
    finally:
        app.destroy()


def main() -> int:
    try:
        App().mainloop()
    except tk.TclError:
        # Desktop packages show native startup errors; source users may be on a
        # headless host and can use the CLI without importing Tk.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
