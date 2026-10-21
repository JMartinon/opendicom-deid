"""Responsive Tk desktop: workers exchange data with the UI through a queue."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .. import __version__
from ..cli import report_path_allowed
from ..core.models import Cancelled, Progress, ScanResult
from ..core.report import Report
from .licenses import license_text
from .settings import AdvancedSettings


class App(tk.Tk):
    """One window and one worker at a time; Tk is used only on the main thread."""

    def __init__(self) -> None:
        super().__init__()
        self.title("OpenDICOM De-ID")
        self.minsize(660, 630)
        self.geometry("760x750")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._events: Queue[tuple[str, object]] = Queue()
        self._cancel = Event()
        self._busy = False
        self._closing = False
        self._advanced_visible = False
        self._last_scan: tuple[str, bool] | None = None
        self._report: Report | None = None
        self._report_input: Path | None = None
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.recursive = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Select an input directory to scan your dataset.")
        self.counts = tk.StringVar(value="No input scanned")
        self.scan_summary = tk.StringVar(value="DICOM files and modalities will appear here.")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame = ttk.Frame(self, padding=24)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(12, weight=1)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="OpenDICOM De-ID", font=("TkDefaultFont", 23, "bold")).grid(
            sticky="w"
        )
        ttk.Button(header, text="About", command=self.show_about).grid(row=0, column=1, sticky="e")
        ttk.Label(frame, text="Offline DICOM de-identification").grid(
            row=1, column=0, sticky="w", pady=(4, 20)
        )
        self.input_entry, self.input_browse = self._directory_row(
            frame, 2, "Input directory", self.input_path, True
        )
        self.output_entry, self.output_browse = self._directory_row(
            frame, 3, "Output directory", self.output_path, False
        )
        self.input_entry.bind("<FocusOut>", lambda _: self._scan_changed_input())
        self.input_entry.bind("<Return>", lambda _: self.scan())
        options = ttk.Frame(frame)
        options.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        options.columnconfigure(0, weight=1)
        self.recursive_check = ttk.Checkbutton(
            options,
            text="Include subdirectories",
            variable=self.recursive,
            command=self._scan_changed_input,
        )
        self.recursive_check.grid(row=0, column=0, sticky="w")
        self.scan_button = ttk.Button(options, text="Scan input", command=self.scan)
        self.scan_button.grid(row=0, column=1, sticky="e")
        ttk.Label(frame, textvariable=self.scan_summary, wraplength=650).grid(
            row=5, sticky="w", pady=(8, 8)
        )
        self.advanced_button = ttk.Button(
            frame, text="Advanced options ▸", command=self.toggle_advanced
        )
        self.advanced_button.grid(row=6, sticky="w", pady=(0, 4))
        self._advanced_window = tk.Toplevel(self)
        self._advanced_window.title("Advanced options")
        self._advanced_window.transient(self)
        self._advanced_window.withdraw()
        self._advanced_window.columnconfigure(0, weight=1)
        self._advanced_window.protocol("WM_DELETE_WINDOW", self.toggle_advanced)
        self.settings = AdvancedSettings(self._advanced_window)
        self.settings.grid(row=0, sticky="ew", padx=10, pady=10)
        ttk.Button(
            self._advanced_window,
            text="Done",
            command=self.toggle_advanced,
        ).grid(row=1, sticky="e", padx=24, pady=(0, 18))
        actions = ttk.Frame(frame)
        actions.grid(row=8, sticky="ew", pady=(10, 12))
        actions.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(actions, text="De-identify", command=self.process)
        self.start_button.grid(row=0, column=0, sticky="w", ipadx=18, ipady=4)
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self.cancel, state="disabled"
        )
        self.cancel_button.grid(row=0, column=1, padx=(8, 0))
        self.progress = ttk.Progressbar(frame, maximum=100, mode="determinate")
        self.progress.grid(row=9, sticky="ew")
        ttk.Label(frame, textvariable=self.counts).grid(row=10, sticky="w", pady=(4, 6))
        ttk.Label(frame, textvariable=self.status, wraplength=650).grid(
            row=11, sticky="w", pady=(0, 8)
        )
        self.details = ScrolledText(
            frame, height=9, width=60, wrap="word", font="TkTextFont", state="disabled"
        )
        self.details.grid(row=12, sticky="nsew")
        footer = ttk.Frame(frame)
        footer.grid(row=13, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, text="Privacy review required before sharing.", wraplength=450).grid(
            row=0, sticky="w"
        )
        self.save_button = ttk.Button(
            footer, text="Save report…", command=self.save_report, state="disabled"
        )
        self.save_button.grid(row=0, column=1, sticky="e")
        self._set_details(
            "All processing stays on this computer.\n\n"
            "Pixels are preserved, including any burned-in identifying text. "
            "Automated checks do not establish that a dataset is safe to share."
        )
        self.after(80, self._poll)

    def _directory_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        is_input: bool,
    ) -> tuple[ttk.Entry, ttk.Button]:
        area = ttk.Frame(parent)
        area.grid(row=row, sticky="ew", pady=(0, 10))
        area.columnconfigure(0, weight=1)
        ttk.Label(area, text=label).grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = ttk.Entry(area, textvariable=variable)
        entry.grid(row=1, column=0, sticky="ew")
        browse = ttk.Button(area, text="Browse…", command=lambda: self._browse(variable, is_input))
        browse.grid(row=1, column=1, padx=(8, 0))
        return entry, browse

    def _browse(self, variable: tk.StringVar, is_input: bool) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Select input directory"
            if is_input
            else "Select a new or empty output directory",
            mustexist=is_input,
        )
        if selected:
            variable.set(selected)
            if is_input:
                self.scan()

    def toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced_window.deiconify()
            self._advanced_window.lift()
            self.advanced_button.configure(text="Advanced options ▾")
        else:
            self._advanced_window.withdraw()
            self.advanced_button.configure(text="Advanced options ▸")

    def _scan_changed_input(self) -> None:
        selection = (self.input_path.get(), self.recursive.get())
        if not self._busy and selection != self._last_scan and self.input_path.get().strip():
            self.scan()

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
            self.recursive_check,
            self.scan_button,
            self.start_button,
        ):
            widget.configure(state=state)
        self.settings.set_enabled(not busy)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.save_button.configure(state="normal" if self._report and not busy else "disabled")

    def _launch(self, job: Callable[[], object], kind: str) -> None:
        self._cancel = Event()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
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
        self._events.put(("progress", value))

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
        self.scan_summary.set("Scanning DICOM files…")
        self.status.set("Inspecting the selected input. No files are changed.")
        self._launch(
            lambda: scan_directory(
                Path(value), recursive=recursive, cancel=self._cancel, progress=self._progress_event
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
            "Processing locally. Cancellation waits for the current file to finish safely."
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
        for _ in range(150):
            try:
                kind, value = self._events.get_nowait()
            except Empty:
                break
            if kind == "progress":
                assert isinstance(value, Progress)
                if value.total:
                    self.progress.stop()
                    self.progress.configure(
                        mode="determinate", value=100 * value.completed / value.total
                    )
                phase = {
                    "scan": "Scanning",
                    "scanning": "Scanning",
                    "anonymize": "De-identifying",
                    "anonymizing": "De-identifying",
                    "de-identifying": "De-identifying",
                    "validate": "Validating",
                    "validating": "Validating",
                }.get(value.phase.lower(), "Processing")
                self.counts.set(f"{phase}: {value.completed} / {value.total} files")
                continue
            self.progress.stop()
            self._set_busy(False)
            if kind == "scan":
                assert isinstance(value, ScanResult)
                modalities = " · ".join(
                    f"{name}: {count}" for name, count in sorted(value.modalities.items())
                )
                self.scan_summary.set(
                    f"{value.total} DICOM files" + (f"  |  {modalities}" if modalities else "")
                )
                self.counts.set(f"Scanned: {value.total} DICOM files; skipped: {value.skipped}")
                self.status.set(
                    "Scan complete. Ready to de-identify."
                    if value.ok and value.total
                    else "Input needs attention. Review the scan findings below."
                )
                findings = "\n".join(
                    f"{finding.severity}: {finding.message}" for finding in value.findings
                )
                self._set_details(
                    findings
                    or "Preliminary scan complete. Full validation runs during de-identification."
                )
            elif kind == "report":
                assert isinstance(value, Report)
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
            elif kind == "cancelled":
                self.status.set("Cancelled. No completed dataset was published.")
                self.scan_summary.set("Scan cancelled; counts are incomplete.")
                self.progress.configure(mode="determinate", value=0)
            else:
                self.status.set("The operation failed. No success is claimed.")
                self._set_details(
                    "Check directory access, available space, and DICOM support. Parser error text is omitted to protect patient information."
                )
                self.progress.configure(mode="determinate", value=0)
            if self._closing:
                self.destroy()
                return
        self.after(80, self._poll)

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
        window.geometry("680x500")
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        ttk.Label(
            window,
            padding=16,
            text=f"OpenDICOM De-ID {__version__}\nOffline DICOM de-identification\n"
            "BSD-3-Clause · Automated checks are not proof of privacy or regulatory compliance.",
            wraplength=630,
        ).grid(row=0, sticky="ew")
        text = ScrolledText(window, wrap="word", font="TkTextFont", padx=12, pady=12)
        text.grid(row=1, sticky="nsew", padx=16)
        text.insert("1.0", license_text())
        text.configure(state="disabled")
        ttk.Button(window, text="Close", command=window.destroy).grid(row=2, pady=12)

    def close(self) -> None:
        if self._busy:
            self._closing = True
            self.cancel()
        else:
            self.destroy()


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
