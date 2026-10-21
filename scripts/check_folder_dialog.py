"""Exercise the real X11 directory chooser with mouse clicks under Xvfb.

Requires xdotool. Tests both root-parented and nested modal folder dialogs.
Uses only an empty temporary folder tree; never reads a DICOM dataset.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from opendicom_deid.gui.app import App
from opendicom_deid.gui.dialogs import ask_directory


def exercise(app: App, parent: tk.Misc, initial: Path, *, fixed: bool) -> bool:
    """Click and release the path menu, then select a different ancestor."""
    failures: list[BaseException] = []
    posted: list[bool] = []
    prefix = str(parent).rstrip(".")
    dialog = f"{prefix}.__tk_choosedir"
    button = f"{dialog}.contents.f1.menu"
    menu = f"{button}.menu"
    cancel = f"{dialog}.contents.f2.cancel"
    original_binding = app.bind_class("TkChooseDir", "<Map>")

    def mouse_click(x: int, y: int) -> None:
        subprocess.run(
            # --sync waits for motion, which never arrives when a reopened
            # chooser puts this control under the pointer's current position.
            ["xdotool", "mousemove", str(x), str(y), "mousedown", "1"],
            check=True, timeout=5,
        )
        app.update()
        subprocess.run(["xdotool", "mouseup", "1"], check=True, timeout=5)
        app.update()

    def interact() -> None:
        try:
            x = int(app.tk.call("winfo", "rootx", button))
            y = int(app.tk.call("winfo", "rooty", button))
            width = int(app.tk.call("winfo", "width", button))
            height = int(app.tk.call("winfo", "height", button))
            mouse_click(x + width // 2, y + height // 2)
            posted.append(bool(int(app.tk.call("winfo", "viewable", menu))))
            if fixed:
                assert posted[-1], "Path dropdown closed immediately after releasing the mouse."
                # The penultimate ancestor is the temporary parent directory.
                index = int(app.tk.call(menu, "index", "last")) - 1
                entry_y = int(app.tk.call(menu, "yposition", index))
                next_y = int(app.tk.call(menu, "yposition", index + 1))
                menu_x = int(app.tk.call("winfo", "rootx", menu))
                menu_y = int(app.tk.call("winfo", "rooty", menu))
                mouse_click(menu_x + 20, menu_y + (entry_y + next_y) // 2)
                selected = str(app.tk.call(button, "cget", "-textvariable"))
                assert app.tk.getvar(selected) == str(initial.parent)
                assert int(app.tk.call("winfo", "viewable", dialog))
        except BaseException as exc:
            failures.append(exc)
        finally:
            if int(app.tk.call("winfo", "exists", menu)):
                app.tk.call("event", "generate", menu, "<Escape>")
            app.tk.call(cancel, "invoke")

    def timeout() -> None:
        failures.append(AssertionError("The directory chooser interaction timed out."))
        # Tk's Unix chooser waits on this variable; end the wait even if a
        # regression prevents the cancel button from being constructed.
        app.tk.setvar("::tk::Priv(selectFilePath)", "")

    interaction = app.after(150, interact)
    watchdog = app.after(15_000, timeout)
    chooser = ask_directory if fixed else filedialog.askdirectory
    try:
        result = chooser(parent=parent, initialdir=str(initial), mustexist=True)
    finally:
        app.after_cancel(interaction)
        app.after_cancel(watchdog)
    assert result == ""
    assert app.bind_class("TkChooseDir", "<Map>") == original_binding
    if failures:
        raise failures[0]
    assert posted, "The folder chooser interaction did not run."
    return posted[0]


def main() -> None:
    if shutil.which("xdotool") is None:
        raise SystemExit("Install xdotool, then run this check under xvfb-run.")
    app = App()
    try:
        if app.tk.call("tk", "windowingsystem") != "x11":
            raise SystemExit("This regression check exercises Tk's X11 folder chooser.")
        app.update()
        with tempfile.TemporaryDirectory(prefix="opendicom-folder-dialog-") as temporary:
            initial = Path(temporary) / "child"
            initial.mkdir()
            baseline_open = exercise(app, app, initial, fixed=False)
            print(f"Unmodified Tk dropdown stays open after release: {baseline_open}")
            exercise(app, app, initial, fixed=True)
            assert app.grab_current() is None
            modal = tk.Toplevel(app)
            modal.transient(app)
            modal.grab_set()
            app.update()
            try:
                exercise(app, modal, initial, fixed=True)
                assert app.grab_current() == modal
            finally:
                modal.destroy()
            assert app.grab_current() is None
            print("Folder dropdown click, ancestor selection, cancellation, and modal grabs passed.")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
