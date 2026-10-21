"""Create and destroy the real Tk window, including its advanced controls.

Run with a desktop session, or with xvfb-run on Linux CI. No DICOM is read.
"""

from opendicom_deid.gui.app import check_startup


def main() -> None:
    check_startup()


if __name__ == "__main__":
    main()
