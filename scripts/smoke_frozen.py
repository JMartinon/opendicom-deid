"""Exercise the packaged executable with a tiny synthetic CT/RTSTRUCT chain."""

from __future__ import annotations

import json
import platform
import subprocess
import tempfile
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, RTStructureSetStorage, generate_uid

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
        for modality, sop_class in (("CT", CTImageStorage), ("RTSTRUCT", RTStructureSetStorage)):
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
            ds.Modality = modality
            if modality == "CT":
                ds.Rows, ds.Columns, ds.SamplesPerPixel = 1, 2, 1
                ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
                ds.PhotometricInterpretation = "MONOCHROME2"
                ds.PixelData = b"\x00\x00\x01\x00"
            else:
                reference = Dataset()
                reference.ReferencedSOPClassUID = instances[0].SOPClassUID
                reference.ReferencedSOPInstanceUID = instances[0].SOPInstanceUID
                contour = Dataset()
                contour.ContourImageSequence = [reference]
                roi = Dataset()
                roi.ContourSequence = [contour]
                ds.ROIContourSequence = [roi]
            instances.append(ds)
            ds.save_as(source / f"{modality}.dcm", enforce_file_format=True)
        report = root / "report.json"
        # Windows --windowed builds have no stdout: verify exit status and files.
        subprocess.run(
            [str(executable), "deidentify", str(source), str(output), "--report", str(report)],
            check=True, timeout=90,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["status"] == "COMPLETED" and result["output_count"] == 2
        assert not any(finding["severity"] == "FAIL" for finding in result["findings"])
        saved = {str(ds.Modality): ds for ds in (pydicom.dcmread(p) for p in output.glob("*.dcm"))}
        assert saved["CT"].PixelData == instances[0].PixelData
        reference = saved["RTSTRUCT"].ROIContourSequence[0].ContourSequence[0].ContourImageSequence[0]
        assert reference.ReferencedSOPInstanceUID == saved["CT"].SOPInstanceUID
        assert saved["CT"].SOPInstanceUID != instances[0].SOPInstanceUID
        assert saved["CT"].PatientID == saved["RTSTRUCT"].PatientID != "SYNTHETIC-ID"
    print("Packaged executable: GUI startup, synthetic CT/RT references and pixel checks passed.")


if __name__ == "__main__":
    main()
