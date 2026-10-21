"""Synthetic reproduction of the 661-file / 255-reference ViewRay failure."""

from __future__ import annotations

from collections import Counter

import pydicom
from conftest import make_dataset, reference, write_dataset
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot, walk_elements
from opendicom_deid.core.models import ExpectedOutput, Options
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_batch


def create_viewray_like_dataset(root):
    study, frame, ct_series, mr_series = [generate_uid() for _ in range(4)]
    ct = [make_dataset("CT", study=study, series=ct_series, frame=frame) for _ in range(198)]
    mr = [make_dataset("MR", study=study, series=mr_series, frame=frame) for _ in range(450)]
    struct = make_dataset("RTSTRUCT", study=study, frame=frame)
    plan = make_dataset("RTPLAN", study=study, frame=frame)
    dose_series = generate_uid()
    doses = [make_dataset("RTDOSE", study=study, series=dose_series, frame=frame) for _ in range(11)]
    frame_item, study_item = Dataset(), Dataset()
    frame_item.FrameOfReferenceUID = frame
    series_items = []
    for images, series_uid in [(ct, ct_series), (mr[:43], mr_series)]:
        item = Dataset()
        item.SeriesInstanceUID = series_uid
        item.ContourImageSequence = [reference(image) for image in images]
        series_items.append(item)
    study_item.RTReferencedSeriesSequence = series_items
    frame_item.RTReferencedStudySequence = [study_item]
    struct.ReferencedFrameOfReferenceSequence = [frame_item]
    struct.StructureSetROISequence = []
    struct.ROIContourSequence = []
    for index in range(31):
        roi = Dataset()
        roi.ROINumber = index + 1
        roi.ROIName = f"Synthetic ROI {index + 1}"
        roi.ReferencedFrameOfReferenceUID = frame
        roi.ROIGenerationAlgorithm = "MANUAL"
        struct.StructureSetROISequence.append(roi)
        contour_roi = Dataset()
        contour_roi.ReferencedROINumber = index + 1
        contour = Dataset()
        contour.ContourGeometricType = "CLOSED_PLANAR"
        contour.NumberOfContourPoints = 3
        contour.ContourData = [0, 0, index, 1, 0, index, 0, 1, index]
        contour_roi.ContourSequence = [contour]
        struct.ROIContourSequence.append(contour_roi)
    plan.ReferencedStructureSetSequence = [reference(struct)]
    plan.ReferencedDoseSequence = [reference(dose) for dose in doses[:2]]
    plan.BeamSequence = []
    for index in range(21):
        beam = Dataset()
        beam.BeamNumber = index + 1
        beam.BeamName = f"Synthetic {index + 1}"
        beam.BeamType = "STATIC"
        beam.RadiationType = "PHOTON"
        beam.NumberOfControlPoints = 2
        beam.ControlPointSequence = []
        for cp_index in range(2):
            point = Dataset()
            point.ControlPointIndex = cp_index
            point.GantryAngle = index * 10
            point.NominalBeamEnergy = 6
            point.CumulativeMetersetWeight = cp_index
            point.IsocenterPosition = [12.5, 20, -3.2]
            beam.ControlPointSequence.append(point)
        plan.BeamSequence.append(beam)
    for dose in doses:
        dose.ReferencedRTPlanSequence = [reference(plan)]
    for modality, images in [("CT", ct), ("MR", mr)]:
        for index, ds in enumerate(images):
            ds.InstanceNumber = index + 1
            ds.ImagePositionPatient = [-12.5, 3, index * 2.5]
            write_dataset(ds, root / modality / f"{index:04d}.dcm")
    write_dataset(struct, root / "Plan" / "struct.dcm")
    write_dataset(plan, root / "Plan" / "plan.dcm")
    for index, dose in enumerate(doses):
        write_dataset(dose, root / "Doses" / ("Run1" if index < 6 else "Run2") / f"{index:02d}.dcm")


def test_viewray_661_files_255_nested_references_and_original_failure_detected(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    create_viewray_like_dataset(source)
    scan = scan_directory(source)
    assert scan.ok
    assert scan.total == 661
    assert scan.modalities == {"CT": 198, "MR": 450, "RTDOSE": 11, "RTPLAN": 1, "RTSTRUCT": 1}
    original_uids = {record.snapshot.sop_uid for record in scan.files}
    source_reference_counts = Counter()
    for record in scan.files:
        for values in record.snapshot.references.values():
            source_reference_counts[record.modality] += len(values)
            assert set(values) <= original_uids
    assert source_reference_counts == {"RTDOSE": 11, "RTPLAN": 3, "RTSTRUCT": 241}
    report = process_batch(source, destination, Options(organization="source"))
    assert report.ok, report.to_dict()
    assert report.output_count == 661
    files = sorted(destination.rglob("*.dcm"), key=lambda path: int(path.stem.rsplit("-", 1)[1]))
    saved = [pydicom.dcmread(path) for path in files]
    mapped_uids = {str(ds.SOPInstanceUID) for ds in saved}
    assert len(saved) == 661
    assert all(uid.startswith("2.25.") for uid in mapped_uids)
    assert not mapped_uids.intersection(original_uids)
    output_reference_counts = Counter()
    for ds in saved:
        for values in capture_snapshot(ds).references.values():
            output_reference_counts[ds.Modality] += len(values)
            assert set(values) <= mapped_uids
    assert output_reference_counts == source_reference_counts
    structure = next(ds for ds in saved if ds.Modality == "RTSTRUCT")
    plan = next(ds for ds in saved if ds.Modality == "RTPLAN")
    assert len(structure.StructureSetROISequence) == 31
    assert len(plan.BeamSequence) == 21
    assert len({str(ds.FrameOfReferenceUID) for ds in saved}) == 1
    assert len({str(ds.StudyInstanceUID) for ds in saved}) == 1
    for record, ds in zip(scan.files, saved, strict=True):
        assert record.snapshot.clinical == capture_snapshot(ds).clinical

    # Reproduce the historical bug: anonymize every instance but restore all 255
    # nested references to original values. Readable files alone are insufficient.
    uid_map = {}
    expected = []
    for record, path, ds in zip(scan.files, files, saved, strict=True):
        output_snap = capture_snapshot(ds)
        for tag_path, originals in record.snapshot.entity_uids.items():
            uid_map.update(zip(originals, output_snap.entity_uids[tag_path], strict=True))
        expected.append(ExpectedOutput(record, path, str(ds.PatientID), 0))
        for tag_path, elem in walk_elements(ds):
            if tag_path in record.snapshot.references:
                elem.value = list(record.snapshot.references[tag_path])
        write_dataset(ds, path)
    broken_count = sum(
        uid not in mapped_uids for ds in (pydicom.dcmread(path) for path in files)
        for values in capture_snapshot(ds).references.values() for uid in values
    )
    assert broken_count == 255
    findings = validate_batch(scan, expected, uid_map, options=Options())
    assert any(f.code == "BROKEN_REFERENCES" and f.severity == "FAIL" for f in findings)
    assert sum(f.code == "UID_INTEGRITY" for f in findings) == 13
    assert not any(f.code == "REFERENCE_GRAPH" and f.severity == "PASS" for f in findings)
