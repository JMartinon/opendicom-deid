"""Local, read-only format inspection for desktop users."""

from __future__ import annotations

import json
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .theme import style_plain_widget
from .widgets import ThemedScrolledText


class StorageInspectionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        file_number: str,
        inspect_callback: Callable[[int, bool], None],
        privacy_callback: Callable[[int], None],
        cancel_callback: Callable[[], None],
        close_callback: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.title("Inspect DICOM file")
        style_plain_widget(self, "background")
        self.transient(parent)
        self.geometry("700x620")
        self.minsize(580, 520)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._inspect_callback = inspect_callback
        self._privacy_callback = privacy_callback
        self._cancel_callback = cancel_callback
        self._close_callback = close_callback
        self._busy = False
        self._closing = False
        self._result_text = ""
        self.file_number = tk.StringVar(value=file_number)
        self.show_private_class_uids = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Enter the File number from the failed report, then inspect.")

        body = ttk.Frame(self, padding=20)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)
        ttk.Label(body, text="Inspect DICOM file", style="Title.TLabel").grid(sticky="w")
        ttk.Label(
            body,
            text="Inspect the format or check for potential identifying data locally. "
            "No files are changed or uploaded. Keep the same folder and subfolder setting "
            "as the failed run. Identifier checks show field locations, never their values, "
            "and cannot establish that a file is anonymous.",
            style="Muted.TLabel",
            wraplength=640,
        ).grid(row=1, sticky="ew", pady=(8, 16))
        controls = ttk.Frame(body)
        controls.grid(row=2, sticky="ew")
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="File number").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.number_entry = ttk.Entry(controls, textvariable=self.file_number, width=12)
        self.number_entry.grid(row=0, column=1, sticky="w")
        self.inspect_button = ttk.Button(
            controls, text="Inspect format", command=self.inspect
        )
        self.inspect_button.grid(row=0, column=2, sticky="e")
        self.privacy_button = ttk.Button(
            controls, text="Check identifiers", style="Primary.TButton", command=self.inspect_privacy
        )
        self.privacy_button.grid(row=0, column=3, sticky="e", padx=(8, 0))
        self.private_check = ttk.Checkbutton(
            body,
            text="Show vendor-specific storage class IDs in format details",
            variable=self.show_private_class_uids,
        )
        self.private_check.grid(row=3, sticky="w", pady=(12, 6))
        ttk.Label(body, textvariable=self.status, wraplength=640, style="Muted.TLabel").grid(
            row=4, sticky="ew", pady=(4, 10)
        )
        self.details = ThemedScrolledText(
            body, wrap="word", font="TkFixedFont", height=12,
            relief="flat", borderwidth=0,
            padx=10, pady=10, state="disabled",
        )
        self.details.grid(row=5, sticky="nsew")
        buttons = ttk.Frame(body)
        buttons.grid(row=6, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        self.copy_button = ttk.Button(
            buttons, text="Copy report", command=self.copy_result, state="disabled"
        )
        self.copy_button.grid(row=0, column=0, sticky="w")
        self.close_button = ttk.Button(buttons, text="Close", command=self.close)
        self.close_button.grid(row=0, column=1, sticky="e")
        self.file_number.trace_add("write", self._selection_changed)
        self.show_private_class_uids.trace_add("write", self._selection_changed)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _: self.close())
        self.grab_set()
        self.number_entry.focus_set()

    def _set_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _selection_changed(self, *_: str) -> None:
        self._result_text = ""
        self._set_details("")
        self.copy_button.configure(state="disabled")
        self.status.set("Choose Inspect format or Check identifiers for this selection.")

    def _number(self) -> int | None:
        if self._busy:
            return None
        number = self.file_number.get().strip()
        if not number.isascii() or not number.isdecimal() or len(number) > 12 or int(number) < 1:
            self.status.set("Enter a positive File number, for example 199 for File 000199.")
            return None
        return int(number)

    def inspect(self) -> None:
        number = self._number()
        if number is None:
            return
        self._selection_changed()
        self.status.set("Reading format details locally…")
        self._inspect_callback(number, self.show_private_class_uids.get())

    def inspect_privacy(self) -> None:
        number = self._number()
        if number is None:
            return
        self._selection_changed()
        self.status.set("Checking for potential identifying fields locally…")
        self._privacy_callback(number)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for widget in (
            self.number_entry, self.private_check, self.inspect_button, self.privacy_button
        ):
            widget.configure(state=state)
        self.copy_button.configure(state="normal" if self._result_text and not busy else "disabled")
        self.close_button.configure(text="Cancel" if busy else "Close")

    def show_result(self, result: dict) -> None:
        if self._closing:
            self.close()
            return
        self._result_text = json.dumps(result, indent=2)
        self._set_details(self._result_text)
        if result.get("inspection_type") == "privacy":
            complete = result.get("status") == "REVIEW_REQUIRED"
            summary = result.get("summary", {})
            found = any(summary.get(key, 0) for key in (
                "direct_identifiers", "person_names", "private_identifier_matches"
            ))
            if complete:
                self.status.set(
                    "Potential identifying fields found. The report contains no field values. "
                    "Private and unreadable content still requires review."
                    if found else
                    "No direct identifiers detected in inspected fields. This does not establish "
                    "anonymity; review the remaining fields and inspection limits."
                )
            else:
                self.status.set("Identifier inspection could not finish. No privacy clearance given.")
            self.copy_button.configure(state="normal" if complete else "disabled")
            return
        complete = bool(result.get("files")) and all(
            item.get("status") == "INSPECTED" for item in result["files"]
        )
        self.status.set(
            "Inspection complete. Copy the technical details to share for troubleshooting."
            if complete else "Inspection could not finish. See the details below."
        )
        self.copy_button.configure(state="normal" if complete else "disabled")

    def copy_result(self) -> None:
        if not self._result_text or self._busy or self.copy_button.instate(["disabled"]):
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._result_text)
        except tk.TclError:
            self.status.set("Could not copy. Select and copy the displayed technical details manually.")
            return
        self.status.set("Report copied. No patient field values included.")

    def close(self) -> None:
        if self._busy:
            self._closing = True
            self.status.set("Cancelling inspection…")
            self._cancel_callback()
            return
        self._close_callback()
        self.destroy()
