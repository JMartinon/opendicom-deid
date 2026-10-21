"""Create and destroy the real Tk window, including its advanced controls.

Run with a desktop session, or with xvfb-run on Linux CI. Uses synthetic DICOM only.
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.gui.app import App, check_startup


def write_synthetic_ct(source: Path) -> None:
    source.mkdir()
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.PatientID, ds.PatientName = "SYNTHETIC-ID", "Synthetic^Only"
    ds.Modality = "CT"
    ds.Rows, ds.Columns, ds.SamplesPerPixel = 1, 2, 1
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = b"\x00\x00\x01\x00"
    ds.save_as(source / "synthetic.dcm", enforce_file_format=True)


def finish(app: App) -> None:
    deadline = time.monotonic() + 20
    while app._busy:
        assert time.monotonic() < deadline, "The GUI worker did not finish in time."
        app.update()
        time.sleep(0.01)


def main() -> None:
    check_startup()
    app = App()
    try:
        app.update()
        with tempfile.TemporaryDirectory(prefix="opendicom-gui-smoke-") as temporary:
            source = Path(temporary) / "input"
            write_synthetic_ct(source)
            with patch.object(app, "_launch") as launch:
                app.input_path.set(str(source))
                app.input_entry.event_generate("<FocusOut>")
                app.recursive_check.invoke()
                with patch("opendicom_deid.gui.app.ask_directory", return_value=str(source)):
                    app._browse(app.input_path, True)
                app.update()
                launch.assert_not_called()
            app.scan_button.invoke()
            finish(app)
            assert app.scan_summary.get().startswith("1 DICOM files")
            app.output_path.set(str(Path(temporary) / "output"))
            with patch(
                "opendicom_deid.core.anonymizer.scan_directory", wraps=scan_directory
            ) as scan:
                app.start_button.invoke()
                finish(app)
                scan.assert_called_once()
            assert app._report is not None and app._report.ok
            assert app._report.output_count == 1
            print("GUI: input changes stay idle; explicit scan and synthetic processing passed.")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
