"""Capture the real Linux Tk layout under Xvfb for visual review (no DICOM data).

Development/CI only. Requires ImageMagick's `import` executable and a display:
    xvfb-run -a python scripts/preview_gui.py --output build/gui-preview
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from opendicom_deid.gui.app import App


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/gui-preview"))
    args = parser.parse_args()
    capture = shutil.which("import")
    if not sys.platform.startswith("linux") or capture is None:
        raise SystemExit("GUI previews require Linux, a display, and ImageMagick's import command.")
    args.output.mkdir(parents=True, exist_ok=True)
    app = App()

    def save(window, name: str) -> None:
        app.update()
        subprocess.run(
            [capture, "-window", str(window.winfo_id()), str(args.output / name)],
            check=True,
            timeout=20,
        )

    try:
        save(app, "main.png")
        app.geometry("740x680")
        save(app, "compact.png")
        app.toggle_advanced()
        save(app._advanced_window, "advanced.png")
        app.toggle_advanced()
        app._new_output_folder()
        assert app._folder_window is not None
        save(app._folder_window, "new-folder.png")
        app.tk.call(app._folder_window.protocol("WM_DELETE_WINDOW"))
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
