"""All fixtures are tiny programmatically generated objects, never patient data."""

from __future__ import annotations

import struct
from pathlib import Path

import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    MRImageStorage,
    RTDoseStorage,
    RTImageStorage,
    RTPlanStorage,
    RTStructureSetStorage,
    generate_uid,
)

SOP_CLASSES = {
    "CT": CTImageStorage, "MR": MRImageStorage, "RTDOSE": RTDoseStorage,
    "RTSTRUCT": RTStructureSetStorage, "RTPLAN": RTPlanStorage, "RTIMAGE": RTImageStorage,
}


def make_dataset(
    modality: str = "CT", *, patient_id: str = "SYNTHETIC-PATIENT-1",
    patient_name: str = "Synthetic^Person", study: str | None = None,
    series: str | None = None, frame: str | None = None, instance: str | None = None,
    pixels: bool | None = None,
) -> FileDataset:
    instance = instance or generate_uid()
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SOP_CLASSES[modality]
    meta.MediaStorageSOPInstanceUID = instance
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = instance
    ds.StudyInstanceUID = study or generate_uid()
    ds.SeriesInstanceUID = series or generate_uid()
    ds.FrameOfReferenceUID = frame or generate_uid()
    ds.Modality = modality
    ds.PatientID = patient_id
    ds.PatientName = patient_name
    ds.PatientBirthDate = "19600102"
    ds.PatientSex = "F"
    ds.StudyDate = "20240131"
    ds.StudyTime = "121314.25"
    ds.AcquisitionDateTime = "20240131121314.25-0500"
    ds.AccessionNumber = "SYNTH-ACCESSION"
    ds.InstitutionName = "Synthetic Hospital"
    ds.ReferringPhysicianName = "Synthetic^Physician"
    ds.StudyDescription = "Synthetic description with identity"
    ds.SeriesDescription = "Synthetic series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    if pixels is None:
        pixels = modality in {"CT", "MR", "RTDOSE", "RTIMAGE"}
    if pixels:
        ds.Rows = 2
        ds.Columns = 2
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1 if modality == "CT" else 0
        ds.PixelData = struct.pack("<4h", 0, 1, 100, 1000)
        ds.PixelSpacing = [1.25, 1.5]
        ds.ImagePositionPatient = [-12.5, 3, 25]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceThickness = 2.5
    if modality == "CT":
        ds.RescaleIntercept = -1024
        ds.RescaleSlope = 1
        ds.RescaleType = "HU"
    if modality == "RTDOSE":
        ds.DoseUnits = "GY"
        ds.DoseType = "PHYSICAL"
        ds.DoseSummationType = "PLAN"
        if pixels:
            ds.NumberOfFrames = 2
            ds.PixelData *= 2
            ds.DoseGridScaling = 0.001
            ds.GridFrameOffsetVector = [0, 2.5]
    return ds


def write_dataset(ds: Dataset, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pydicom.dcmwrite(path, ds, enforce_file_format=True)
    return path


def reference(ds: Dataset) -> Dataset:
    item = Dataset()
    item.ReferencedSOPClassUID = ds.SOPClassUID
    item.ReferencedSOPInstanceUID = ds.SOPInstanceUID
    return item


@pytest.fixture
def dicom_factory():
    return make_dataset


@pytest.fixture
def dicom_writer():
    return write_dataset
