"""Small, dependency-free desktop theme shared by the Tk views."""

from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

BACKGROUND = "#F3F5F7"
SURFACE = "#FFFFFF"
TEXT = "#202C38"
MUTED = "#5D6B79"
BORDER = "#D9E0E7"
ACCENT = "#345D7E"


def apply_theme(root: tk.Tk) -> None:
    """Use system fonts and a quiet, consistent ttk palette on all platforms."""
    # Clam ships with Tcl/Tk and lets Linux, Windows, and macOS share readable
    # control states without adding a theme package to offline distributions.
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BACKGROUND)
    default = font.nametofont("TkDefaultFont")
    family = default.actual("family")
    size = 10
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        font.nametofont(name).configure(family=family, size=size)
    style.configure(".", font=(family, size), foreground=TEXT, background=BACKGROUND)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=(family, 23, "bold"))
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Section.TLabel", background=SURFACE, font=(family, 12, "bold"))
    style.configure("Field.TLabel", background=SURFACE, font=(family, size, "bold"))
    style.configure(
        "Badge.TLabel",
        background="#E4ECF2",
        foreground=ACCENT,
        font=(family, 9, "bold"),
        padding=(10, 6),
    )
    style.configure(
        "TButton",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        relief="flat",
        borderwidth=1,
        padding=(12, 6),
        focuscolor=ACCENT,
        width=0,
    )
    style.map(
        "TButton",
        background=[("disabled", "#EDF0F3"), ("pressed", "#DEE6ED"), ("active", "#EAF0F5")],
        foreground=[("disabled", "#8C96A0")],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground=SURFACE,
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        font=(family, size, "bold"),
        padding=(20, 8),
        focuscolor=SURFACE,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", "#CBD5DE"), ("pressed", "#24445F"), ("active", "#416D91")],
        foreground=[("disabled", "#647482"), ("!disabled", SURFACE)],
        bordercolor=[("disabled", "#CBD5DE"), ("!disabled", ACCENT)],
    )
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        padding=(9, 6),
        insertcolor=TEXT,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", "#F0F2F4")])
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        bordercolor=BORDER,
        arrowcolor=MUTED,
        padding=(8, 7),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure(
        "TCheckbutton",
        background=SURFACE,
        padding=(0, 4),
        focuscolor=ACCENT,
        indicatorbackground=SURFACE,
        indicatorforeground=ACCENT,
        upperbordercolor=BORDER,
        lowerbordercolor=BORDER,
        indicatormargin=(0, 0, 6, 0),
    )
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE)],
        foreground=[("disabled", "#8C96A0")],
        indicatorbackground=[("selected", ACCENT)],
        indicatorforeground=[("selected", SURFACE)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=ACCENT,
        troughcolor="#E6EBEF",
        borderwidth=0,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        bordercolor="#E6EBEF",
        arrowsize=4,
        sliderlength=8,
        gripsize=0,
    )
    # Clam sizes these native elements through arrowsize; thickness is ignored
    # on Tk 8.6/9. Remove optional text from the progress layout to keep it slim.
    style.layout(
        "Horizontal.TProgressbar",
        [
            (
                "Horizontal.Progressbar.trough",
                {
                    "sticky": "nsew",
                    "children": [
                        ("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"}),
                    ],
                },
            ),
        ],
    )
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "Vertical.TScrollbar",
        background="#DCE3E9",
        troughcolor=SURFACE,
        borderwidth=0,
        arrowsize=12,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        gripsize=0,
    )
    style.layout(
        "Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {
                    "sticky": "ns",
                    "children": [
                        ("Vertical.Scrollbar.thumb", {"sticky": "nswe"}),
                    ],
                },
            ),
        ],
    )
    root.option_add("*Listbox.font", (family, size))
    root.option_add("*Listbox.background", SURFACE)
    root.option_add("*Listbox.foreground", TEXT)
