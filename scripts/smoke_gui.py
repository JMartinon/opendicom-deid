"""Create and destroy the real Tk window, including its advanced controls.

Run with a desktop session, or with xvfb-run on Linux CI. Uses synthetic DICOM only.
"""

import hashlib
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    EncapsulatedPDFStorage,
    ExplicitVRLittleEndian,
    generate_uid,
)

from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.gui.app import App, check_startup


def write_synthetic_ct(source: Path) -> FileDataset:
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
    return ds


def finish(app: App) -> None:
    deadline = time.monotonic() + 20
    while app._busy:
        assert time.monotonic() < deadline, "The GUI worker did not finish in time."
        app.update()
        time.sleep(0.01)


def check_storage_inspection(app: App, source: Path) -> None:
    """Exercise the offline diagnostic workflow without an output directory."""
    original = write_synthetic_ct(source)
    private_uid = "1.2.826.0.1.3680043.10.543.998.1"
    for name, sop_class, modality in (
        ("z-private-SyntheticOnly.dcm", private_uid, "RTPLAN"),
        ("zz-standard-SyntheticOnly.dcm", EncapsulatedPDFStorage, "DOC"),
    ):
        ds = deepcopy(original)
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = sop_class
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
        ds.Modality = modality
        ds.save_as(source / name, enforce_file_format=True)
    before = {
        path: hashlib.sha256(path.read_bytes()).digest() for path in source.iterdir()
    }
    previous_report = app._report
    app.input_path.set(str(source))
    app.output_path.set("")
    app.scan_button.invoke()
    finish(app)
    app.inspect_button.invoke()
    app.update()
    dialog = app._inspection_window
    assert dialog is not None and not app._busy
    assert dialog.file_number.get() == "2"
    assert dialog.copy_button.instate(["disabled"])
    dialog.inspect_button.invoke()
    assert app._busy and app.inspect_button.instate(["disabled"])
    finish(app)
    redacted_text = dialog.details.get("1.0", "end").strip()
    redacted = json.loads(redacted_text)
    assert redacted["files"][0]["file_id"] == "File 000002"
    assert redacted["files"][0]["sop_class"]["uid"] == "REDACTED"
    assert private_uid not in redacted_text
    for sensitive in (
        str(source), "Synthetic^Only", "SYNTHETIC-ID", "SyntheticOnly.dcm",
        str(original.SOPInstanceUID), str(original.StudyInstanceUID),
        str(original.SeriesInstanceUID),
    ):
        assert sensitive not in redacted_text

    dialog.show_private_class_uids.set(True)
    app.update()
    assert dialog.copy_button.instate(["disabled"])
    dialog.inspect_button.invoke()
    finish(app)
    revealed_text = dialog.details.get("1.0", "end").strip()
    revealed = json.loads(revealed_text)
    assert revealed["files"][0]["sop_class"]["uid"] == private_uid
    with patch.object(dialog, "clipboard_clear") as clear, patch.object(
        dialog, "clipboard_append"
    ) as append:
        dialog.copy_button.invoke()
        clear.assert_called_once()
        append.assert_called_once()
        assert json.loads(append.call_args.args[0]) == revealed

    dialog.file_number.set("3")
    dialog.show_private_class_uids.set(False)
    app.update()
    assert dialog.copy_button.instate(["disabled"])
    dialog.inspect_button.invoke()
    finish(app)
    standard = json.loads(dialog.details.get("1.0", "end"))
    assert standard["files"][0]["sop_class"]["uid"] == str(EncapsulatedPDFStorage)
    assert not standard["files"][0]["supported_storage_class"]
    assert app._report is previous_report
    assert before == {
        path: hashlib.sha256(path.read_bytes()).digest() for path in source.iterdir()
    }
    dialog.close()
    app.update()
    assert app._inspection_window is None and app.grab_current() is None
    app.input_path.set(str(source / "changed-selection"))
    app.inspect_button.invoke()
    app.update()
    dialog = app._inspection_window
    assert dialog is not None and dialog.file_number.get() == ""
    dialog.close()

    def wait_for_cancellation(*_args, cancel, **_kwargs) -> dict:
        assert cancel.wait(5), "The dialog did not cancel its worker."
        return {"status": "CANCELLED"}

    app.input_path.set(str(source))
    app.inspect_button.invoke()
    dialog = app._inspection_window
    assert dialog is not None
    dialog.file_number.set("2")
    with patch(
        "opendicom_deid.core.storage_inspection.inspect_storage",
        side_effect=wait_for_cancellation,
    ):
        dialog.inspect_button.invoke()
        assert app._busy
        dialog.close()
        finish(app)
    assert app._inspection_window is None and app.grab_current() is None
    print("GUI: local format inspection, private class opt-in, and safe copying passed.")


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
            check_storage_inspection(app, Path(temporary) / "inspection-input")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
