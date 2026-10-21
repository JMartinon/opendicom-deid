"""Create and destroy the real Tk window, including its advanced controls.

Run with a desktop session, or with xvfb-run on Linux CI. No DICOM is read.
"""

from opendicom_deid.gui.app import App


def main() -> None:
    app = App()
    app.update_idletasks()
    app.toggle_advanced()
    app.update()
    app.toggle_advanced()
    app.destroy()


if __name__ == "__main__":
    main()
