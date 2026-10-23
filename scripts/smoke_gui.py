"""Create and destroy the real Tk window, including its advanced controls.

Run with a desktop session, or with xvfb-run on Linux CI. Uses synthetic DICOM only.
"""

import hashlib
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    EncapsulatedPDFStorage,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    generate_uid,
)

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Cancelled, Progress
from opendicom_deid.core.report import Report
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.sop_classes import VIEWRAY_RT_PLAN_STORAGE
from opendicom_deid.gui.app import App, check_startup
from opendicom_deid.gui.theme import current_palette


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


def check_progress_and_theme(app: App) -> None:
    """Observe real Tk painting while a controlled worker waits between stages."""
    phases = [
        Progress("Inventory", 0, 0),
        Progress("Scanning", 2, 4),
        Progress("De-identifying", 1, 4),
        Progress("Validating", 3, 4),
        Progress("Rechecking source files", 2, 4),
        Progress("Publishing", 0, 0),
    ]
    ready = [Event() for _ in phases]
    advance = [Event() for _ in phases]

    def worker():
        for index, progress in enumerate(phases):
            app._progress_event(progress)
            ready[index].set()
            assert advance[index].wait(10), "The GUI stopped responding to stage progress."
        return Report(status="COMPLETED", input_count=4, output_count=4)

    original_theme = app._theme_mode
    try:
        app._launch(worker, "report")
        for index, progress in enumerate(phases):
            deadline = time.monotonic() + 5
            while not ready[index].is_set() or app._latest_progress._value is not None:
                assert time.monotonic() < deadline, "Progress did not reach the main Tk thread."
                app.update()
                time.sleep(0.01)
            app.update()
            assert app._busy
            assert app.theme_button.instate(["!disabled"])
            if progress.total:
                assert str(app.progress.cget("mode")) == "determinate"
                expected = 100 * progress.completed / progress.total
                assert float(app.progress.cget("value")) == expected
                assert f"{int(expected)}% of this stage" in app.counts.get()
                assert app.progress.winfo_height() >= 10
                # The filled element must be visible, not only its numeric value.
                width, height = app.progress.winfo_width(), app.progress.winfo_height()
                assert "pbar" in app.progress.identify(int(width * 0.1), height // 2)
                assert "pbar" not in app.progress.identify(int(width * 0.9), height // 2)
                with patch("opendicom_deid.gui.app.persist_theme", return_value=True) as saved:
                    app.theme_button.invoke()
                    app.update()
                    saved.assert_called_once_with(app._theme_mode)
                assert float(app.progress.cget("value")) == expected
                assert app.details.cget("background") == current_palette(app).surface
                assert app._advanced_window.cget("background") == current_palette(app).background
                assert app.progress.winfo_height() >= 10
            else:
                assert str(app.progress.cget("mode")) == "indeterminate"
                assert "0 / 0" not in app.counts.get()
            advance[index].set()
        finish(app)
        assert app._report.ok and float(app.progress.cget("value")) == 100
    finally:
        for event in advance:
            event.set()
        if app._theme_mode != original_theme:
            with patch("opendicom_deid.gui.app.persist_theme", return_value=True):
                app.theme_button.invoke()
    print("GUI: visible stage progress, responsive theme switching, and final completion passed.")


def check_progress_completion(app: App) -> None:
    """Reproduce final progress arriving between the poll's two mailbox reads."""
    def poll_once() -> None:
        app.after_cancel(app._poll_id)
        app._poll()

    app._set_busy(True)
    app._latest_progress.begin()
    app._show_progress(Progress("Validating", 4, 4))
    take = app._latest_progress.take

    def finish_between_reads():
        value = take()
        app._progress_event(Progress("Publishing", 0, 0))
        app._events.put(("report", Report(status="COMPLETED", input_count=4, output_count=4)))
        return value

    with patch.object(app._latest_progress, "take", side_effect=finish_between_reads):
        poll_once()
    for _ in range(2):
        assert not app._busy and not app._progress_animating
        assert str(app.progress.cget("mode")) == "determinate"
        assert float(app.progress.cget("value")) == 100
        assert app.counts.get() == "Processed: 4 / 4 files"
        poll_once()

    # A stopped widget can remain indeterminate. The next operation must start
    # its timer even when changing its mode is unnecessary.
    app.progress.configure(mode="indeterminate", value=0)
    app.progress.stop()
    app._set_busy(True)
    app._latest_progress.begin()
    app._show_progress(Progress("Inventory", 0, 0))
    assert app._progress_animating
    initial = float(app.progress.cget("value"))
    deadline = time.monotonic() + 3
    while float(app.progress.cget("value")) == initial:
        assert time.monotonic() < deadline, "Indeterminate animation did not restart."
        app.update()
        time.sleep(0.01)
    app._events.put(("report", Report(status="COMPLETED", input_count=4, output_count=4)))
    poll_once()
    assert not app._progress_animating and not app._busy
    print("GUI: completion/progress race and indeterminate timer restart passed.")


def check_viewray_public_plan(app: App, root: Path) -> None:
    """Verify explicit consent reaches both scan paths and survives cancellation."""
    source, output = root / "viewray-input", root / "viewray-output"
    original = write_synthetic_ct(source)
    plan = deepcopy(original)
    del plan.PixelData
    plan.SOPClassUID = plan.file_meta.MediaStorageSOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    plan.SOPInstanceUID = plan.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    plan.SeriesInstanceUID = generate_uid()
    plan.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    plan.Modality = "RTPLAN"
    beam = Dataset()
    beam.BeamNumber = 1
    beam.BeamName = "Anterior field"
    beam.BeamDescription = "6 MV photon field"
    beam.BeamType, beam.RadiationType = "STATIC", "PHOTON"
    beam.NumberOfControlPoints = 2
    beam.FinalCumulativeMetersetWeight = 1
    points = []
    for index in range(2):
        point = Dataset()
        point.ControlPointIndex = index
        point.CumulativeMetersetWeight = index
        point.GantryAngle = 90
        point.IsocenterPosition = [0, 10, 20]
        point.NominalBeamEnergy = 6
        device = Dataset()
        device.RTBeamLimitingDeviceType = "MLCX"
        device.LeafJawPositions = [-10, -8, 10, 8]
        point.BeamLimitingDevicePositionSequence = [device]
        points.append(point)
    beam.ControlPointSequence = points
    plan.BeamSequence = [beam]
    plan.add_new(0x01F10010, "LO", "Synthetic private creator")
    plan.add_new(0x01F11001, "LO", "Synthetic private patient text")
    plan.save_as(source / "viewray-plan.dcm", enforce_file_format=True)
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in source.iterdir()}
    app.input_path.set(str(source))
    app.output_path.set(str(output))
    assert not app.settings.viewray_public_plan.get()
    assert not app.settings.options(True).allow_viewray_public_plan
    assert app.settings.options(True).text_policy == "clinical"
    assert app.settings.text_policy_combo.instate(["readonly", "!disabled"])
    with patch("opendicom_deid.core.scanner.scan_directory", wraps=scan_directory) as scan:
        app.scan_button.invoke()
        finish(app)
        assert scan.call_args.kwargs["allow_viewray_public_plan"] is False
    assert "Full proprietary plan preservation is unsupported" in app.details.get("1.0", "end")
    app.start_button.invoke()
    finish(app)
    assert app._report is not None and not app._report.ok
    assert any(f.code == "VIEWRAY_PLAN_REVIEW_REQUIRED" for f in app._report.findings)
    assert not output.exists()

    app.advanced_button.invoke()
    app.settings.viewray_check.invoke()
    app.update()
    assert app.settings.viewray_public_plan.get()
    with patch("opendicom_deid.core.scanner.scan_directory", wraps=scan_directory) as scan:
        app.scan_button.invoke()
        assert app.settings.viewray_check.instate(["disabled"])
        assert app.settings.text_policy_combo.instate(["disabled"])
        finish(app)
        assert scan.call_args.kwargs["allow_viewray_public_plan"] is True
    assert app.scan_summary.get().startswith("2 DICOM files")
    assert "vendor-private fields are removed" in app.details.get("1.0", "end")
    with patch("opendicom_deid.core.anonymizer.scan_directory", wraps=scan_directory) as scan, patch(
        "opendicom_deid.core.anonymizer.process_batch", wraps=process_batch
    ) as process:
        app.start_button.invoke()
        assert app.settings.text_policy_combo.instate(["disabled"])
        finish(app)
        assert scan.call_args.kwargs["allow_viewray_public_plan"] is True
        assert process.call_args.args[2].text_policy == "clinical"
    assert app._report is not None and app._report.ok and app._report.output_count == 2
    assert any(f.code == "VIEWRAY_PRIVATE_PLAN" for f in app._report.findings)
    saved = {str(ds.Modality): ds for ds in (pydicom.dcmread(p) for p in output.rglob("*.dcm"))}
    assert saved["RTPLAN"].SOPClassUID == VIEWRAY_RT_PLAN_STORAGE
    assert saved["RTPLAN"].BeamSequence == plan.BeamSequence
    assert all(not elem.tag.is_private for elem in saved["RTPLAN"].iterall())
    assert app.settings.text_policy_combo.instate(["readonly", "!disabled"])

    strict_output = root / "viewray-strict-output"
    app.settings.text_policy_combo.current(1)
    app.settings.text_policy_combo.event_generate("<<ComboboxSelected>>")
    app.update()
    assert app.settings.options(True).text_policy == "strict"
    app.output_path.set(str(strict_output))
    with patch("opendicom_deid.core.anonymizer.process_batch", wraps=process_batch) as process:
        app.start_button.invoke()
        assert app.settings.text_policy_combo.instate(["disabled"])
        finish(app)
        assert process.call_args.args[2].text_policy == "strict"
    assert app._report is not None and app._report.ok
    strict_plan = next(
        ds for ds in (pydicom.dcmread(p) for p in strict_output.rglob("*.dcm"))
        if ds.Modality == "RTPLAN"
    )
    assert strict_plan.BeamSequence[0].BeamName == "BEAM-1"
    assert str(strict_plan.BeamSequence[0].get("BeamDescription", "")) in {"", "ANONYMIZED"}
    assert strict_plan.BeamSequence[0].ControlPointSequence == beam.ControlPointSequence
    app.settings.text_policy_combo.current(0)
    assert app.settings.options(True).text_policy == "clinical"

    def wait_for_cancellation(_source, _recursive, cancel, _progress, **kwargs):
        assert kwargs["allow_viewray_public_plan"] is True
        assert cancel.wait(5), "The processing worker did not receive cancellation."
        raise Cancelled()

    cancelled_output = root / "viewray-cancelled"
    app.output_path.set(str(cancelled_output))
    with patch("opendicom_deid.core.anonymizer.scan_directory", side_effect=wait_for_cancellation):
        app.start_button.invoke()
        assert app._busy and app.settings.viewray_check.instate(["disabled"])
        app.settings.viewray_check.invoke()
        assert app.settings.viewray_public_plan.get()
        app.cancel_button.invoke()
        finish(app)
    assert app._report is not None and app._report.status == "CANCELLED"
    assert not cancelled_output.exists()
    assert app.settings.viewray_check.instate(["!disabled"])
    assert app.settings.text_policy_combo.instate(["readonly", "!disabled"])
    assert app.settings.viewray_public_plan.get()
    assert before == {path: hashlib.sha256(path.read_bytes()).digest() for path in source.iterdir()}
    app.settings.viewray_check.invoke()
    app.advanced_button.invoke()
    print("GUI: ViewRay opt-in, clinical/strict text policies, and cancellation controls passed.")


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
    dialog.file_number.set("2")
    dialog.privacy_button.invoke()
    assert app._busy and dialog.privacy_button.instate(["disabled"])
    finish(app)
    privacy_text = dialog.details.get("1.0", "end").strip()
    privacy = json.loads(privacy_text)
    assert privacy["inspection_type"] == "privacy"
    assert privacy["status"] == "REVIEW_REQUIRED" and privacy["privacy_review"] == "REQUIRED"
    assert privacy["summary"]["direct_identifiers"] >= 2
    assert privacy["summary"]["entity_uids"] >= 1
    for sensitive in (str(source), "Synthetic^Only", "SYNTHETIC-ID", str(original.SOPInstanceUID)):
        assert sensitive not in privacy_text
    with patch.object(dialog, "clipboard_clear"), patch.object(dialog, "clipboard_append") as append:
        dialog.copy_button.invoke()
        assert json.loads(append.call_args.args[0]) == privacy
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

    for function, button_name in (
        ("opendicom_deid.core.storage_inspection.inspect_storage", "inspect_button"),
        ("opendicom_deid.core.privacy_inspection.inspect_privacy", "privacy_button"),
    ):
        app.input_path.set(str(source))
        app.inspect_button.invoke()
        dialog = app._inspection_window
        assert dialog is not None
        dialog.file_number.set("2")
        with patch(function, side_effect=wait_for_cancellation):
            getattr(dialog, button_name).invoke()
            assert app._busy
            dialog.close()
            finish(app)
        assert app._inspection_window is None and app.grab_current() is None
    print("GUI: local format/identifier inspection, safe copying, and cancellation passed.")


def main() -> None:
    check_startup()
    app = App()
    try:
        app.update()
        check_progress_and_theme(app)
        check_progress_completion(app)
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
            check_viewray_public_plan(app, Path(temporary))
            check_storage_inspection(app, Path(temporary) / "inspection-input")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
