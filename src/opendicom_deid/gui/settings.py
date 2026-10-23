"""Advanced controls expose only implemented, supported settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core.models import Options


class AdvancedSettings(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, padding=(20, 14), style="Card.TFrame")
        self.prefix = tk.StringVar(value="SUBJECT")
        self.date_mode = tk.StringVar(value="Remove dates")
        self.text_policy = tk.StringVar(value="Preserve clinical names and descriptions")
        self.grouping = tk.BooleanVar(value=True)
        self.viewray_public_plan = tk.BooleanVar(value=False)
        self.workers = tk.StringVar(value="1")
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
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))
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
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 12))
        ttk.Label(self, text="RT information", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 12)
        )
        self.text_policy_combo = ttk.Combobox(
            self,
            textvariable=self.text_policy,
            values=(
                "Preserve clinical names and descriptions",
                "Scrub clinical names and descriptions",
            ),
            state="readonly",
            width=40,
        )
        self.text_policy_combo.grid(row=4, column=1, sticky="ew")
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="Patient, clinician and hospital identifiers are removed in both modes. "
            "Preserved clinical text is screened for known identifiers and still needs local review.",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 12))
        self.group_check = ttk.Checkbutton(
            self,
            text="Preserve patient folders and subfolders",
            variable=self.grouping,
        )
        self.group_check.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="The nested folder structure is kept using neutral names. Patients sharing "
            "one source folder get separate folders. Uncheck to put all files in one folder.",
            wraplength=500,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 12))
        self.viewray_check = ttk.Checkbutton(
            self, text="ViewRay public-data research export", variable=self.viewray_public_plan
        )
        self.viewray_check.grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self, style="CardMuted.TLabel", wraplength=500,
            text="For the recognized private RT plan only. Checks selected public numeric data "
            "and references; removes vendor-private fields. Not validated for independent dose "
            "reconstruction. Full plan preservation and MRIdian re-import are unsupported.",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 12))
        ttk.Label(self, text="Parallel files", style="Field.TLabel").grid(
            row=10, column=0, sticky="w", padx=(0, 12)
        )
        self.workers_combo = ttk.Combobox(
            self, textvariable=self.workers, values=("1", "2", "4", "8"),
            state="readonly", width=8,
        )
        self.workers_combo.grid(row=10, column=1, sticky="ew")
        ttk.Label(
            self, style="CardMuted.TLabel", wraplength=500,
            text="1 is the default. More workers may speed up de-identification and use more "
            "memory. Scanning and final validation still run once for the whole batch.",
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 12))
        ttk.Label(
            self,
            style="CardMuted.TLabel",
            text="Private tags are removed. Pixel data is preserved. Validation is always enabled.",
            wraplength=500,
        ).grid(row=12, column=0, columnspan=2, sticky="w")

    def options(self, recursive: bool) -> Options:
        return Options(
            recursive=recursive,
            patient_prefix=self.prefix.get().strip(),
            date_mode="remove" if self.date_mode.get() == "Remove dates" else "shift",
            organization="source" if self.grouping.get() else "flat",
            allow_viewray_public_plan=self.viewray_public_plan.get(),
            text_policy=(
                "clinical"
                if self.text_policy.get() == "Preserve clinical names and descriptions"
                else "strict"
            ),
            workers=int(self.workers.get()),
        )

    def set_enabled(self, enabled: bool) -> None:
        self.prefix_entry.configure(state="normal" if enabled else "disabled")
        self.date_combo.configure(state="readonly" if enabled else "disabled")
        self.text_policy_combo.configure(state="readonly" if enabled else "disabled")
        self.group_check.configure(state="normal" if enabled else "disabled")
        self.viewray_check.configure(state="normal" if enabled else "disabled")
        self.workers_combo.configure(state="readonly" if enabled else "disabled")
