"""Platform folder dialogs with predictable click interaction on X11."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog


def ask_directory(*, parent: tk.Misc, **options: object) -> str:
    """Keep native dialogs, and make the X11 path menu open on a full click.

    Tk's Unix directory chooser uses a flush menubutton: its menu overlaps
    the button while the mouse is still pressed. The release can select the
    current path immediately. Post below the control, after release, using
    the same virtual event as keyboard activation. Scope this to directory
    dialog widgets; ordinary application menus are unaffected.
    """
    if parent.tk.call("tk", "windowingsystem") != "x11":
        return filedialog.askdirectory(parent=parent, **options) or ""

    def prepare(event: tk.Event) -> None:
        # Tk creates these Tcl widgets itself, so event.widget may be a path
        # string rather than a registered tkinter widget object.
        pending = [str(event.widget)]
        while pending:
            widget = pending.pop()
            pending.extend(parent.tk.splitlist(parent.tk.call("winfo", "children", widget)))
            if parent.tk.call("winfo", "class", widget) == "TMenubutton":
                parent.tk.call(widget, "configure", "-direction", "below")
                parent.tk.call("bind", widget, "<ButtonPress-1>", "break")
                parent.tk.call(
                    "bind", widget, "<ButtonRelease-1>",
                    "event generate %W <<Invoke>>; break",
                )

    # bind_class registers its Python callback on the root even when called
    # through a Toplevel. Delete through that same owner so Tkinter's command
    # registry cannot retain a stale entry and try to delete it again at exit.
    root = parent._root()
    previous = root.bind_class("TkChooseDir", "<Map>")
    callback = root.bind_class("TkChooseDir", "<Map>", prepare, add="+")
    try:
        # Depending on the Tcl object's internal representation, Tkinter may
        # return either "" or () for cancellation. Expose one string contract.
        return filedialog.askdirectory(parent=parent, **options) or ""
    finally:
        root.tk.call("bind", "TkChooseDir", "<Map>", previous or "")
        if callback:
            root.deletecommand(callback)
