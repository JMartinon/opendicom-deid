"""Advanced controls expose only implemented, supported settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core.models import Options


class AdvancedSettings(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, padding=(20, 18), style="Card.TFrame")
        self.prefix = tk.StringVar(value="SUBJECT")
        self.date_mode = tk.StringVar(value="Remove dates")
        self.grouping = tk.BooleanVar(value=True)
        self.viewray_public_plan = tk.BooleanVar(value=False)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="Patient ID prefix", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        self.prefix_entry = ttk.Entry(self, textvariable=self.prefix, width=20)
        self.prefix_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="Use a neutral label, never a patient name or original identifier.",
            wraplength=440,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 20))
        ttk.Label(self, text="Dates", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        self.date_combo = ttk.Combobox(
            self,
            textvariable=self.date_mode,
            values=("Remove dates", "Shift consistently per patient"),
            state="readonly",
        )
        self.date_combo.grid(row=2, column=1, sticky="ew")
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="Shifting preserves date intervals within each patient in this batch. "
            "It can retain identifying temporal patterns.",
            wraplength=500,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 20))
        self.group_check = ttk.Checkbutton(
            self,
            text="Preserve patient folders and subfolders",
            variable=self.grouping,
        )
        self.group_check.grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="The nested folder structure is kept using neutral names. Patients sharing "
            "one source folder get separate folders. Uncheck to put all files in one folder.",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 20))
        self.viewray_check = ttk.Checkbutton(
            self, text="ViewRay public-data research export", variable=self.viewray_public_plan
        )
        self.viewray_check.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self, style="CardMuted.TLabel", wraplength=500,
            text="For the recognized private RT plan only. Checks selected public numeric data "
            "and references; removes vendor-private fields. Not validated for independent dose "
            "reconstruction. Full plan preservation and MRIdian re-import are unsupported.",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 20))
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="Private tags are removed. Pixel data is preserved. Validation is always enabled.",
            wraplength=500,
        ).grid(row=8, column=0, columnspan=2, sticky="w")

    def options(self, recursive: bool) -> Options:
        return Options(
            recursive=recursive,
            patient_prefix=self.prefix.get().strip(),
            date_mode="remove" if self.date_mode.get() == "Remove dates" else "shift",
            organization="source" if self.grouping.get() else "flat",
            allow_viewray_public_plan=self.viewray_public_plan.get(),
        )

    def set_enabled(self, enabled: bool) -> None:
        self.prefix_entry.configure(state="normal" if enabled else "disabled")
        self.date_combo.configure(state="readonly" if enabled else "disabled")
        self.group_check.configure(state="normal" if enabled else "disabled")
        self.viewray_check.configure(state="normal" if enabled else "disabled")
