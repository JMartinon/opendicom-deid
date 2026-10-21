"""Synthetic conventional/ion RT coverage, not a vendor conformance claim."""

from __future__ import annotations

import pydicom
import pytest
from conftest import make_dataset, reference, write_dataset
from pydicom.dataset import Dataset
from pydicom.uid import RTIonPlanStorage, generate_uid

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot, walk_elements
from opendicom_deid.core.scanner import scan_directory


def test_linked_ct_mr_and_all_conventional_rt_classes_plus_ion_plan(tmp_path):
    """RT image and ion plan belong in the same preserved object graph as dose/ROI."""
    study, frame = generate_uid(), generate_uid()
    datasets = {
        key: make_dataset(modality, study=study, frame=frame)
        for key, modality in {
            "ct": "CT", "mr": "MR", "structure": "RTSTRUCT", "plan": "RTPLAN",
            "dose": "RTDOSE", "image": "RTIMAGE", "ion": "RTPLAN",
        }.items()
    }
    ct, mr, structure, plan, dose, image, ion = datasets.values()
    ion.SOPClassUID = RTIonPlanStorage
    ion.file_meta.MediaStorageSOPClassUID = RTIonPlanStorage

    frame_item = Dataset()
    frame_item.FrameOfReferenceUID = frame
    study_item = Dataset()
    series_items = []
    for source_image in (ct, mr):
        series_item = Dataset()
        series_item.SeriesInstanceUID = source_image.SeriesInstanceUID
        series_item.ContourImageSequence = [reference(source_image)]
        series_items.append(series_item)
    study_item.RTReferencedSeriesSequence = series_items
    frame_item.RTReferencedStudySequence = [study_item]
    structure.ReferencedFrameOfReferenceSequence = [frame_item]
    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Synthetic ROI"
    roi.ReferencedFrameOfReferenceUID = frame
    structure.StructureSetROISequence = [roi]
    roi_contour = Dataset()
    roi_contour.ReferencedROINumber = 1
    contour = Dataset()
    contour.ContourGeometricType = "CLOSED_PLANAR"
    contour.NumberOfContourPoints = 3
    contour.ContourData = [0, 0, 0, 10, 0, 0, 0, 10, 0]
    contour.ContourImageSequence = [reference(ct)]
    roi_contour.ContourSequence = [contour]
    structure.ROIContourSequence = [roi_contour]

    plan.ReferencedStructureSetSequence = [reference(structure)]
    plan.ReferencedDoseSequence = [reference(dose)]
    beam = Dataset()
    beam.BeamNumber = 1
    beam.BeamType = "STATIC"
    beam.RadiationType = "PHOTON"
    beam.NumberOfControlPoints = 1
    point = Dataset()
    point.ControlPointIndex = 0
    point.GantryAngle = 90
    point.NominalBeamEnergy = 6
    point.IsocenterPosition = [12.5, 20, -3.2]
    beam.ControlPointSequence = [point]
    plan.BeamSequence = [beam]
    dose.ReferencedRTPlanSequence = [reference(plan)]
    image.ReferencedRTPlanSequence = [reference(plan)]
    image.RTImagePosition = [-200, 200]
    image.ImagePlanePixelSpacing = [0.5, 0.5]
    image.RTImageSID = 1500
    image.RadiationMachineSAD = 1000
    image.GantryAngle = 90

    ion.ReferencedStructureSetSequence = [reference(structure)]
    ion_beam = Dataset()
    ion_beam.BeamNumber = 1
    ion_beam.BeamType = "STATIC"
    ion_beam.RadiationType = "PROTON"
    ion_beam.ScanMode = "MODULATED"
    ion_beam.NumberOfControlPoints = 1
    ion_point = Dataset()
    ion_point.ControlPointIndex = 0
    ion_point.NominalBeamEnergy = 150
    ion_point.NumberOfScanSpotPositions = 2
    ion_point.ScanSpotPositionMap = [1.5, -2, 3, 4.5]
    ion_point.ScanSpotMetersetWeights = [0.25, 0.75]
    ion_beam.IonControlPointSequence = [ion_point]
    ion.IonBeamSequence = [ion_beam]

    source, destination = tmp_path / "source", tmp_path / "output"
    originals = {}
    for index, (key, dataset) in enumerate(datasets.items(), 1):
        dataset.InstanceNumber = index
        originals[index] = capture_snapshot(dataset)
        write_dataset(dataset, source / "SYNTHETIC-PATIENT" / key / "object.dcm")
    scan = scan_directory(source)
    assert scan.ok, scan.findings
    assert scan.modalities == {
        "CT": 1, "MR": 1, "RTSTRUCT": 1, "RTPLAN": 2, "RTDOSE": 1, "RTIMAGE": 1,
    }
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    assert report.output_count == 7
    saved = {
        int(dataset.InstanceNumber): dataset
        for dataset in (pydicom.dcmread(path) for path in destination.rglob("*.dcm"))
    }
    assert len({str(dataset.PatientID) for dataset in saved.values()}) == 1
    assert len({str(dataset.FrameOfReferenceUID) for dataset in saved.values()}) == 1
    replacements = {
        originals[index].sop_uid: str(dataset.SOPInstanceUID)
        for index, dataset in saved.items()
    }
    assert not replacements.keys() & set(replacements.values())
    for index, dataset in saved.items():
        before, after = originals[index], capture_snapshot(dataset)
        assert after.sop_class_uid == before.sop_class_uid
        assert after.technical_uids == before.technical_uids
        assert after.clinical == before.clinical
        assert after.references == {
            path: tuple(replacements[uid] for uid in uids)
            for path, uids in before.references.items()
        }
        assert dataset.PatientIdentityRemoved == "YES"
        assert dataset.PatientName != "Synthetic^Person"
        assert all(not element.tag.is_private for _, element in walk_elements(dataset))
    assert sum(len(uids) for snap in originals.values() for uids in snap.references.values()) == 8
    assert saved[6].PixelData == image.PixelData
    assert saved[7].IonBeamSequence[0].IonControlPointSequence[0].ScanSpotMetersetWeights == [0.25, 0.75]
    assert any(f.code == "REFERENCE_GRAPH" and f.severity == "PASS" for f in report.findings)


@pytest.mark.parametrize("modality", ["RTPLAN", "RTSTRUCT", "RTDOSE", "RTIMAGE"])
def test_rt_storage_class_does_not_accept_inconsistent_modality(tmp_path, modality):
    dataset = make_dataset(modality)
    dataset.Modality = "MR"
    write_dataset(dataset, tmp_path / "input" / "synthetic.dcm")
    report = process_batch(tmp_path / "input", tmp_path / "output")
    assert not report.ok
    assert report.output_count == 0
    assert any(f.code == "MODALITY" and f.severity == "FAIL" for f in report.findings)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("pixels", [None, b"\x00\x00"])
def test_rt_image_requires_complete_pixel_data(tmp_path, pixels):
    dataset = make_dataset("RTIMAGE")
    if pixels is None:
        del dataset.PixelData
    else:
        dataset.PixelData = pixels
    write_dataset(dataset, tmp_path / "input" / "synthetic.dcm")
    report = process_batch(tmp_path / "input", tmp_path / "output")
    assert not report.ok
    assert report.output_count == 0
    assert any(f.code == "INVALID_STRUCTURE" and f.severity == "FAIL" for f in report.findings)
    assert not (tmp_path / "output").exists()
