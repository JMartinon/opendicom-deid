"""A few structural sanity checks, explicitly not a complete IOD validator."""

from __future__ import annotations

import math
import struct

from pydicom.dataset import Dataset
from pydicom.pixels.utils import get_expected_length
from pydicom.uid import UID

from .sop_classes import VIEWRAY_RT_PLAN_STORAGE


def _whole_number(value) -> int:
    number = int(value)
    if float(value) != number:
        raise ValueError
    return number


def _public_control_point_geometry(point: Dataset) -> bool:
    """Require some readable public geometry, without asserting a complete IOD."""
    present = False
    if "GantryAngle" in point:
        if not math.isfinite(float(point.GantryAngle)):
            raise ValueError
        present = True
    if "IsocenterPosition" in point:
        values = point.IsocenterPosition
        if len(values) != 3 or not all(math.isfinite(float(value)) for value in values):
            raise ValueError
        present = True
    if "BeamLimitingDevicePositionSequence" in point:
        devices = point.BeamLimitingDevicePositionSequence
        if not devices:
            raise ValueError
        for device in devices:
            positions = device.get("LeafJawPositions")
            if not positions or not all(math.isfinite(float(value)) for value in positions):
                raise ValueError
        present = True
    return present


def _viewray_public_plan_error(ds: Dataset) -> str | None:
    """Bounded public-data export, never an alias or full vendor IOD validation.

    Run after private attributes have been removed. Reject a shell whose only
    treatment representation was private. Public beam/control-point semantics
    follow PS3.3 C.8.8.14; this does not establish what private fields contained.
    """
    if any(key in ds for key in ("PixelData", "FloatPixelData", "DoubleFloatPixelData")):
        return "The ViewRay public-plan export cannot handle a plan with an image payload."
    beams = ds.get("BeamSequence")
    if not beams:
        return (
            "The ViewRay plan has no public Beam Sequence after private fields are removed. "
            "Its proprietary treatment representation requires specialized handling."
        )
    try:
        beam_numbers: set[int] = set()
        for beam in beams:
            number = _whole_number(beam.BeamNumber)
            if number < 1 or number in beam_numbers:
                raise ValueError
            beam_numbers.add(number)
            points = beam.ControlPointSequence
            count = _whole_number(beam.NumberOfControlPoints)
            if count < 2 or count != len(points):
                raise ValueError
            weights = []
            for index, point in enumerate(points):
                if _whole_number(point.ControlPointIndex) != index:
                    raise ValueError
                weight = float(point.CumulativeMetersetWeight)
                if not math.isfinite(weight) or weight < 0:
                    raise ValueError
                weights.append(weight)
                has_geometry = _public_control_point_geometry(point)
                if index == 0 and not has_geometry:
                    raise ValueError
            if weights[0] != 0 or weights[-1] <= 0 or weights != sorted(weights):
                raise ValueError
            if "FinalCumulativeMetersetWeight" in beam and not math.isclose(
                float(beam.FinalCumulativeMetersetWeight), weights[-1], rel_tol=1e-7, abs_tol=1e-9
            ):
                raise ValueError
    except (AttributeError, KeyError, ValueError, TypeError, OverflowError):
        return (
            "The ViewRay public beam/control-point representation is missing or inconsistent. "
            "This export requires numbered beams, coherent control points, geometry, and meterset weights."
        )
    return None


def _valid_encapsulation(value: bytes, frames: int) -> bool:
    """Check item boundaries and offset-table targets without decoding image content."""
    if len(value) < 8 or value[:4] != b"\xfe\xff\x00\xe0":
        return False
    table_length = struct.unpack_from("<I", value, 4)[0]
    if table_length % 4 or table_length > len(value) - 8:
        return False
    offsets = [item[0] for item in struct.iter_unpack("<I", memoryview(value)[8:8 + table_length])]
    start = position = 8 + table_length
    fragments: set[int] = set()
    while position < len(value):
        if len(value) - position < 8:
            return False
        tag, length = struct.unpack_from("<II", value, position)
        if tag == 0xE0DDFFFE:
            if length or position + 8 != len(value):
                return False
            position += 8
            break
        if tag != 0xE000FFFE or not length or length % 2 or length > len(value) - position - 8:
            return False
        fragments.add(position - start)
        position += 8 + length
    return len(fragments) >= frames and (
        not offsets or (
            len(offsets) == frames and offsets[0] == 0
            and offsets == sorted(set(offsets)) and set(offsets) <= fragments
        )
    )


def structural_error(ds: Dataset) -> str | None:
    """Return a safe fixed message for obvious missing/truncated image payloads.

    Legacy point/isodose and DVH-only RTDOSE objects need no image grid. They are
    deliberately accepted without Rows/Columns/PixelData; their numbers are still
    included in the source/output preservation checks.
    """
    sop_class = str(ds.SOPClassUID)
    if sop_class == VIEWRAY_RT_PLAN_STORAGE:
        return _viewray_public_plan_error(ds)
    pixels = [key for key in ("PixelData", "FloatPixelData", "DoubleFloatPixelData") if key in ds]
    if len(pixels) > 1:
        return "An object has conflicting pixel representations."
    if not pixels:
        if sop_class == "1.2.840.10008.5.1.4.1.1.4.2":
            return None if ds.get("SpectroscopyData") else "An MR spectroscopy object lacks its data."
        if sop_class not in {
            "1.2.840.10008.5.1.4.1.1.481.2",  # RT Dose
            "1.2.840.10008.5.1.4.1.1.481.3",  # RT Structure Set
            "1.2.840.10008.5.1.4.1.1.481.5",  # RT Plan
            "1.2.840.10008.5.1.4.1.1.481.8",  # RT Ion Plan
        }:
            return "An image object lacks pixel data."
        return None
    try:
        if any(int(ds.get(key, 0)) <= 0 for key in ("Rows", "Columns", "SamplesPerPixel", "BitsAllocated")):
            return "Image dimensions or pixel encoding metadata are missing or invalid."
        if int(ds.get("NumberOfFrames", 1)) <= 0:
            return "The image frame count is invalid."
        value = ds.get(pixels[0])
        if not isinstance(value, bytes) or not value:
            return "The pixel data is empty or unsupported."
        transfer = getattr(ds, "file_meta", {}).get("TransferSyntaxUID")
        if transfer and any(codec in UID(transfer).name for codec in ("MPEG", "HEVC")):
            return "Video transfer syntaxes require specialized handling."
        compressed = bool(transfer and UID(transfer).is_compressed)
        if compressed:
            if not _valid_encapsulation(value, int(ds.get("NumberOfFrames", 1))):
                return "The encapsulated pixel data has missing or invalid frame fragments."
        else:
            expected = get_expected_length(ds, unit="bytes")
            if len(value) not in {expected, expected + (expected % 2)}:
                return "The pixel byte count does not match the image dimensions."
        if sop_class == "1.2.840.10008.5.1.4.1.1.481.2":
            scaling = float(ds.get("DoseGridScaling", 0))
            if not math.isfinite(scaling) or scaling <= 0:
                return "A grid dose lacks a finite, positive dose grid scaling factor."
    except (AttributeError, KeyError, ValueError, TypeError):
        return "Image encoding metadata cannot safely be interpreted."
    return None
