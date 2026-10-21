"""Exercise a packaged executable with a synthetic CT/RT/ViewRay public-plan chain."""

from __future__ import annotations

import json
import platform
import subprocess
import tempfile
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    RTDoseStorage,
    RTPlanStorage,
    RTStructureSetStorage,
    generate_uid,
)

from opendicom_deid.core.sop_classes import VIEWRAY_RT_PLAN_STORAGE

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    relative = {
        "Darwin": "OpenDICOM-De-ID.app/Contents/MacOS/OpenDICOM-De-ID",
        "Windows": "OpenDICOM-De-ID.exe",
        "Linux": "OpenDICOM-De-ID/OpenDICOM-De-ID",
    }[platform.system()]
    executable = ROOT / "dist" / relative
    subprocess.run([str(executable), "--check-gui"], check=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix="opendicom-synthetic-smoke-") as directory:
        root = Path(directory)
        source, output = root / "input", root / "output"
        source.mkdir()
        study, frame = generate_uid(), generate_uid()
        instances = []
        for modality, sop_class in (
            ("CT", CTImageStorage), ("RTSTRUCT", RTStructureSetStorage),
            ("RTPLAN", VIEWRAY_RT_PLAN_STORAGE), ("RTDOSE", RTDoseStorage),
        ):
            meta = FileMetaDataset()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = sop_class
            meta.MediaStorageSOPInstanceUID = generate_uid()
            ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
            ds.SOPClassUID = sop_class
            ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
            ds.StudyInstanceUID, ds.SeriesInstanceUID = study, generate_uid()
            ds.FrameOfReferenceUID = frame
            ds.PatientID, ds.PatientName = "SYNTHETIC-ID", "Synthetic^Only"
            ds.Manufacturer = "Example Devices"
            ds.ManufacturerModelName = "Research Linac"
            ds.SoftwareVersions = ["5.5.4.14", "2.0"]
            ds.Modality = modality
            if modality in {"CT", "RTDOSE"}:
                ds.Rows, ds.Columns, ds.SamplesPerPixel = 1, 2, 1
                ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
                ds.PhotometricInterpretation = "MONOCHROME2"
                ds.PixelData = b"\x00\x00\x01\x00"
                if modality == "RTDOSE":
                    ds.DoseGridScaling = 0.001
                    ds.DoseUnits, ds.DoseType, ds.DoseSummationType = "GY", "PHYSICAL", "PLAN"
                    reference = Dataset()
                    # Reproduce a standard-plan class label pointing by exact
                    # instance UID at the selected ViewRay private plan.
                    reference.ReferencedSOPClassUID = RTPlanStorage
                    reference.ReferencedSOPInstanceUID = instances[2].SOPInstanceUID
                    ds.ReferencedRTPlanSequence = [reference]
            elif modality == "RTSTRUCT":
                reference = Dataset()
                reference.ReferencedSOPClassUID = instances[0].SOPClassUID
                reference.ReferencedSOPInstanceUID = instances[0].SOPInstanceUID
                contour = Dataset()
                contour.ContourImageSequence = [reference]
                roi = Dataset()
                roi.ContourSequence = [contour]
                ds.ROIContourSequence = [roi]
            else:
                ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
                ds.TreatmentMachineName = "SYNTHETIC-ID"
                reference = Dataset()
                reference.ReferencedSOPClassUID = instances[1].SOPClassUID
                reference.ReferencedSOPInstanceUID = instances[1].SOPInstanceUID
                ds.ReferencedStructureSetSequence = [reference]
                beam = Dataset()
                beam.BeamNumber, beam.NumberOfControlPoints = 1, 2
                beam.BeamName = "SYNTHETIC-ID"
                beam.Manufacturer = "Example Devices"
                beam.ManufacturerModelName = "Research Linac"
                beam.TreatmentMachineName = "LINAC-A"
                beam.DeviceSerialNumber = "IDENTIFYING-SERIAL"
                mode = Dataset()
                mode.FluenceMode, mode.FluenceModeID = "NON_STANDARD", "FFF"
                beam.PrimaryFluenceModeSequence = [mode]
                material = Dataset()
                material.MaterialID = "WATER"
                beam.CompensatorSequence = [material]
                beam.BeamType, beam.RadiationType = "STATIC", "PHOTON"
                beam.FinalCumulativeMetersetWeight = 1
                points = []
                for index in range(2):
                    point = Dataset()
                    point.ControlPointIndex, point.CumulativeMetersetWeight = index, index
                    point.GantryAngle, point.NominalBeamEnergy = 90, 6
                    point.IsocenterPosition = [0, 10, 20]
                    device = Dataset()
                    device.RTBeamLimitingDeviceType = "MLCX"
                    device.LeafJawPositions = [-10, -8, 10, 8]
                    point.BeamLimitingDevicePositionSequence = [device]
                    points.append(point)
                beam.ControlPointSequence = points
                ds.BeamSequence = [beam]
                ds.add_new(0x01F10010, "LO", "Synthetic private creator")
                ds.add_new(0x01F11001, "LO", "Synthetic private patient text")
            instances.append(ds)
            directory = source / "synthetic-patient" / modality
            directory.mkdir(parents=True, exist_ok=True)
            ds.save_as(directory / f"{modality}.dcm", enforce_file_format=True)
        report = root / "report.json"
        # Windows --windowed builds have no stdout: verify exit status and files.
        blocked_output, blocked_report = root / "blocked-output", root / "blocked-report.json"
        blocked = subprocess.run(
            [str(executable), "deidentify", str(source), str(blocked_output),
             "--report", str(blocked_report)],
            check=False, timeout=90,
        )
        assert blocked.returncode == 1
        blocked_result = json.loads(blocked_report.read_text(encoding="utf-8"))
        assert blocked_result["status"] == "FAILED" and not blocked_output.exists()
        assert any(f["code"] == "VIEWRAY_PLAN_REVIEW_REQUIRED" for f in blocked_result["findings"])
        subprocess.run(
            [str(executable), "deidentify", str(source), str(output),
             "--report", str(report), "--viewray-public-plan"],
            check=True, timeout=90,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["status"] == "COMPLETED" and result["output_count"] == 4
        assert not any(finding["severity"] == "FAIL" for finding in result["findings"])
        assert any(f["code"] == "VIEWRAY_PRIVATE_PLAN" for f in result["findings"])
        assert any(f["code"] == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in result["findings"])
        assert any(f["code"] == "EQUIPMENT_TEXT_REDACTED" for f in result["findings"])
        paths = list(output.rglob("*.dcm"))
        assert len(paths) == 4
        assert len({path.parent.parent for path in paths}) == 1
        assert len({path.parent for path in paths}) == 4
        assert all(len(path.relative_to(output).parts) == 3 for path in paths)
        assert all(part.startswith("group-") for path in paths
                   for part in path.relative_to(output).parts[:-1])
        saved = {str(ds.Modality): ds for ds in (pydicom.dcmread(p) for p in paths)}
        assert saved["CT"].PixelData == instances[0].PixelData
        reference = saved["RTSTRUCT"].ROIContourSequence[0].ContourSequence[0].ContourImageSequence[0]
        assert reference.ReferencedSOPInstanceUID == saved["CT"].SOPInstanceUID
        reference = saved["RTPLAN"].ReferencedStructureSetSequence[0]
        assert reference.ReferencedSOPInstanceUID == saved["RTSTRUCT"].SOPInstanceUID
        reference = saved["RTDOSE"].ReferencedRTPlanSequence[0]
        assert reference.ReferencedSOPInstanceUID == saved["RTPLAN"].SOPInstanceUID
        assert reference.ReferencedSOPClassUID == VIEWRAY_RT_PLAN_STORAGE
        assert instances[3].ReferencedRTPlanSequence[0].ReferencedSOPClassUID == RTPlanStorage
        assert saved["RTPLAN"].SOPClassUID == VIEWRAY_RT_PLAN_STORAGE
        assert saved["RTPLAN"].file_meta.MediaStorageSOPClassUID == VIEWRAY_RT_PLAN_STORAGE
        assert saved["RTPLAN"].file_meta.TransferSyntaxUID == ImplicitVRLittleEndian
        assert saved["RTPLAN"].TreatmentMachineName == "ANONYMIZED"
        beam = saved["RTPLAN"].BeamSequence[0]
        assert beam.BeamName == "BEAM-1" and beam.TreatmentMachineName == "LINAC-A"
        assert beam.Manufacturer == "Example Devices" and beam.ManufacturerModelName == "Research Linac"
        assert beam.get("DeviceSerialNumber", "") in {"", "ANONYMIZED"}
        assert beam.PrimaryFluenceModeSequence[0].FluenceModeID == "FFF"
        assert beam.CompensatorSequence[0].MaterialID == "WATER"
        assert beam.ControlPointSequence == instances[2].BeamSequence[0].ControlPointSequence
        assert all(ds.Manufacturer == "Example Devices" for ds in saved.values())
        assert all(ds.ManufacturerModelName == "Research Linac" for ds in saved.values())
        assert all(list(ds.SoftwareVersions) == ["5.5.4.14", "2.0"] for ds in saved.values())
        assert saved["RTDOSE"].PixelData == instances[3].PixelData
        assert all(not elem.tag.is_private for ds in saved.values() for elem in ds.iterall())
        assert saved["CT"].SOPInstanceUID != instances[0].SOPInstanceUID
        assert len({str(ds.PatientID) for ds in saved.values()}) == 1
        assert saved["CT"].PatientID != "SYNTHETIC-ID"
    print("Packaged executable: GUI startup, equipment text, neutral beam labels, "
          "ViewRay opt-in, CT/RT references and pixels passed.")


if __name__ == "__main__":
    main()
