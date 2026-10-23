"""Quiet light/dark desktop palettes, with no third-party theme dependency."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from tkinter import font, ttk
from typing import Literal

ThemeMode = Literal["light", "dark"]


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    text: str
    muted: str
    border: str
    accent: str
    active: str
    pressed: str
    disabled: str
    disabled_text: str
    primary_active: str
    primary_pressed: str
    primary_disabled: str
    primary_disabled_text: str
    selection: str
    badge: str
    badge_text: str
    trough: str
    scroll: str
    progress: str
    primary_text: str = "#FFFFFF"


PALETTES: dict[ThemeMode, Palette] = {
    "light": Palette(
        background="#F3F5F7", surface="#FFFFFF", text="#202C38", muted="#5D6B79",
        border="#D9E0E7", accent="#345D7E", active="#EAF0F5", pressed="#DEE6ED",
        disabled="#EDF0F3", disabled_text="#78838E", primary_active="#416D91",
        primary_pressed="#24445F", primary_disabled="#CBD5DE",
        primary_disabled_text="#647482", selection="#DCE8F2", badge="#E4ECF2",
        badge_text="#345D7E", trough="#E6EBEF", scroll="#C3CED8", progress="#345D7E",
    ),
    "dark": Palette(
        background="#171C22", surface="#222A33", text="#E6EBF0", muted="#AEB9C5",
        border="#46515E", accent="#426A8C", active="#303C49", pressed="#39495A",
        disabled="#272F39", disabled_text="#8995A3", primary_active="#4D7596",
        primary_pressed="#345672", primary_disabled="#344353",
        primary_disabled_text="#A0ADBB", selection="#35516B", badge="#2D3D4C",
        badge_text="#BED3E5", trough="#394451", scroll="#596B7D", progress="#8DAFCD",
    ),
}


def preference_path() -> Path:
    """Store appearance alone in the user's normal application settings folder."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        configured = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".config"
    return base / "OpenDICOM-De-ID" / "appearance.json"


def load_theme(path: Path | None = None) -> ThemeMode:
    try:
        path = path or preference_path()
        # A settings file is tiny. Do not read an accidentally substituted large file.
        if path.stat().st_size > 4096:
            return "light"
        settings = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(settings, dict) and settings.get("theme") in PALETTES:
            return settings["theme"]
    except (OSError, ValueError, TypeError):
        pass
    return "light"


def persist_theme(mode: str, path: Path | None = None) -> bool:
    """Best-effort atomic preference write; an unwritable folder cannot stop work."""
    if mode not in PALETTES:
        raise ValueError("Choose light or dark appearance.")
    temporary: Path | None = None
    try:
        path = path or preference_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".appearance-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"theme": mode}, stream)
            stream.write("\n")
        temporary.replace(path)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def current_palette(widget: tk.Misc) -> Palette:
    return PALETTES[getattr(widget._root(), "_theme_mode", "light")]


def style_plain_widget(widget: tk.Misc, role: str = "surface") -> None:
    """Theme a classic Tk widget and remember its role for subsequent switches."""
    if role not in {"surface", "background", "muted"}:
        raise ValueError("Unknown theme surface.")
    widget._theme_role = role
    _style_plain(widget, current_palette(widget))


def _style_plain(widget: tk.Misc, palette: Palette) -> None:
    if isinstance(widget, ttk.Widget):
        # Several ttk controls subclass their classic Tk counterparts, but
        # accept colors through ttk.Style rather than widget options.
        return
    role = getattr(widget, "_theme_role", "surface")
    background = palette.background if role == "background" else palette.surface
    foreground = palette.muted if role == "muted" else palette.text
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget.configure(background=background)
    elif isinstance(widget, (tk.Text, tk.Entry, tk.Listbox)):
        options = dict(
            background=background, foreground=foreground,
            selectbackground=palette.selection, selectforeground=palette.text,
            highlightbackground=palette.border, highlightcolor=palette.accent,
        )
        if isinstance(widget, (tk.Text, tk.Entry)):
            options["insertbackground"] = palette.text
        widget.configure(**options)
    elif isinstance(widget, tk.Scrollbar):
        widget.configure(
            background=palette.scroll, activebackground=palette.muted,
            troughcolor=palette.surface, highlightbackground=palette.surface,
            borderwidth=0, relief="flat",
        )
    elif isinstance(widget, (tk.Frame, tk.LabelFrame)):
        widget.configure(background=background)
    elif isinstance(widget, (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton, tk.Menu)):
        widget.configure(background=background, foreground=foreground)


def _refresh_plain_widgets(root: tk.Misc, palette: Palette) -> None:
    pending = [root]
    while pending:
        widget = pending.pop()
        _style_plain(widget, palette)
        if isinstance(widget, ttk.Combobox):
            # Tk creates a combobox popup in Tcl, outside Tkinter's widget
            # registry. An already-used popup must change along with its field.
            popups = list(widget.tk.splitlist(widget.tk.call("winfo", "children", widget)))
            while popups:
                popup = popups.pop()
                popup_class = widget.tk.call("winfo", "class", popup)
                if popup_class == "Listbox":
                    widget.tk.call(
                        popup, "configure", "-background", palette.surface,
                        "-foreground", palette.text, "-selectbackground", palette.selection,
                        "-selectforeground", palette.text, "-highlightbackground", palette.border,
                    )
                elif popup_class == "Menu":
                    widget.tk.call(
                        popup, "configure", "-background", palette.surface,
                        "-foreground", palette.text, "-activebackground", palette.selection,
                        "-activeforeground", palette.text,
                    )
                popups.extend(widget.tk.splitlist(widget.tk.call("winfo", "children", popup)))
        pending.extend(widget.winfo_children())


def apply_theme(root: tk.Tk, mode: ThemeMode | None = None) -> ThemeMode:
    """Apply a palette to ttk and all open Tk views, without altering their contents."""
    if mode is None:
        mode = load_theme()
    if mode not in PALETTES:
        raise ValueError("Choose light or dark appearance.")
    root._theme_mode = mode
    root._theme_role = "background"
    p = PALETTES[mode]
    # Clam ships with Tcl/Tk and exposes control colors on every supported OS.
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    default = font.nametofont("TkDefaultFont")
    family = default.actual("family")
    size = 10
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        font.nametofont(name).configure(family=family, size=size)
    style.configure(".", font=(family, size), foreground=p.text, background=p.background)
    style.configure("TFrame", background=p.background)
    style.configure("Card.TFrame", background=p.surface)
    style.configure("TLabel", background=p.background, foreground=p.text)
    style.configure("Muted.TLabel", foreground=p.muted)
    style.configure("Title.TLabel", font=(family, 23, "bold"))
    style.configure("Card.TLabel", background=p.surface)
    style.configure("CardMuted.TLabel", background=p.surface, foreground=p.muted)
    style.configure("Section.TLabel", background=p.surface, font=(family, 12, "bold"))
    style.configure("Field.TLabel", background=p.surface, font=(family, size, "bold"))
    style.configure(
        "Badge.TLabel", background=p.badge, foreground=p.badge_text,
        font=(family, 9, "bold"), padding=(10, 6),
    )
    style.configure(
        "TButton", background=p.surface, foreground=p.text, bordercolor=p.border,
        lightcolor=p.surface, darkcolor=p.surface, relief="flat", borderwidth=1,
        padding=(12, 6), focuscolor=p.accent, width=0,
    )
    style.map(
        "TButton", background=[("disabled", p.disabled), ("pressed", p.pressed), ("active", p.active)],
        foreground=[("disabled", p.disabled_text)], bordercolor=[("focus", p.accent)],
    )
    style.configure(
        "Primary.TButton", background=p.accent, foreground=p.primary_text,
        bordercolor=p.accent, lightcolor=p.accent, darkcolor=p.accent,
        font=(family, size, "bold"), padding=(20, 8), focuscolor=p.primary_text,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", p.primary_disabled), ("pressed", p.primary_pressed), ("active", p.primary_active)],
        foreground=[("disabled", p.primary_disabled_text), ("!disabled", p.primary_text)],
        bordercolor=[("disabled", p.primary_disabled), ("!disabled", p.accent)],
    )
    style.configure(
        "TEntry", fieldbackground=p.surface, foreground=p.text, bordercolor=p.border,
        lightcolor=p.surface, darkcolor=p.surface, padding=(9, 6), insertcolor=p.text,
        selectbackground=p.selection, selectforeground=p.text,
    )
    style.map(
        "TEntry", bordercolor=[("focus", p.accent)],
        fieldbackground=[("disabled", p.disabled)], foreground=[("disabled", p.disabled_text)],
    )
    style.configure(
        "TCombobox", fieldbackground=p.surface, background=p.surface,
        bordercolor=p.border, arrowcolor=p.muted, padding=(8, 7),
        lightcolor=p.surface, darkcolor=p.surface,
    )
    style.map(
        "TCombobox", fieldbackground=[("disabled", p.disabled), ("readonly", p.surface)],
        foreground=[("disabled", p.disabled_text), ("readonly", p.text)],
        selectbackground=[("readonly", p.surface)], selectforeground=[("readonly", p.text)],
        bordercolor=[("focus", p.accent)], background=[("active", p.active)],
    )
    style.configure(
        "TCheckbutton", background=p.surface, padding=(0, 4), focuscolor=p.accent,
        indicatorbackground=p.surface, indicatorforeground=p.accent,
        upperbordercolor=p.border, lowerbordercolor=p.border, indicatormargin=(0, 0, 6, 0),
    )
    style.map(
        "TCheckbutton", background=[("active", p.surface)],
        foreground=[("disabled", p.disabled_text)],
        indicatorbackground=[("selected", p.accent)], indicatorforeground=[("selected", p.primary_text)],
    )
    style.configure(
        "Horizontal.TProgressbar", background=p.progress, troughcolor=p.trough,
        borderwidth=0, lightcolor=p.progress, darkcolor=p.progress, bordercolor=p.trough,
        arrowsize=12, sliderlength=20, gripsize=0,
    )
    style.layout(
        "Horizontal.TProgressbar",
        [("Horizontal.Progressbar.trough", {"sticky": "nsew", "children": [
            ("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"}),
        ]})],
    )
    style.configure("TSeparator", background=p.border)
    style.configure(
        "Vertical.TScrollbar", background=p.scroll, troughcolor=p.surface,
        borderwidth=0, arrowsize=12, bordercolor=p.surface, lightcolor=p.surface,
        darkcolor=p.surface, gripsize=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", p.muted), ("pressed", p.muted)])
    style.layout(
        "Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"sticky": "nswe"}),
        ]})],
    )
    for widget_class in ("Listbox", "Menu"):
        root.option_add(f"*{widget_class}.font", (family, size))
        root.option_add(f"*{widget_class}.background", p.surface)
        root.option_add(f"*{widget_class}.foreground", p.text)
        root.option_add(f"*{widget_class}.selectBackground", p.selection)
        root.option_add(f"*{widget_class}.selectForeground", p.text)
    _refresh_plain_widgets(root, p)
    return mode
