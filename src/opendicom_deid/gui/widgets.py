"""Small themed widgets built on Tk's standard text and scrolling behavior."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import style_plain_widget


class AutoHideScrollbar(ttk.Scrollbar):
    """A grid-managed standard scrollbar that appears only when needed.

    Tk still handles the thumb, dragging, page clicks, and scroll fractions.
    Hiding a full-range thumb avoids suggesting that a short report can scroll.
    """

    def set(self, first: str | float, last: str | float) -> None:
        if float(first) <= 0 and float(last) >= 1:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


class ThemedScrolledText(tk.Text):
    """ScrolledText-compatible text with a themed, automatically hidden bar.

    Text methods operate on the text widget; grid/pack/place operate on its
    enclosing frame, matching tkinter.scrolledtext.ScrolledText. ``frame`` and
    ``vbar`` remain public for layout and desktop smoke checks.
    """

    def __init__(self, master: tk.Misc, *, role: str = "surface", **kwargs: object) -> None:
        self.frame = ttk.Frame(master, style="Card.TFrame")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.vbar = AutoHideScrollbar(self.frame, orient="vertical")
        self.vbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        kwargs["yscrollcommand"] = self.vbar.set
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("relief", "flat")
        super().__init__(self.frame, **kwargs)
        self.grid(row=0, column=0, sticky="nsew")
        self.vbar.configure(command=self.yview)
        style_plain_widget(self, role)

        # Use the same geometry delegation as Python's ScrolledText, without
        # copying any Text operations such as configure, insert, or selection.
        methods = vars(tk.Pack).keys() | vars(tk.Grid).keys() | vars(tk.Place).keys()
        for name in methods.difference(vars(tk.Text)):
            if not name.startswith("_") and name not in {"config", "configure"}:
                setattr(self, name, getattr(self.frame, name))
