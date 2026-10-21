"""Strict DICOM reads, with raw private-tag removal before lazy value conversion."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pydicom
from pydicom.datadict import dictionary_VR
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError

from .diagnostics import private_diagnostics


class ReadError(Exception):
    """Intentionally does not carry pydicom's potentially identifying error text."""


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _materialize(ds: Dataset, remove_private: bool) -> None:
    # Dataset iteration / remove_private_tags() can convert a malformed private FD.
    # keys() and deletion do not convert RawDataElement values, including private SQs.
    stack = [ds]
    while stack:
        item = stack.pop()
        if remove_private:
            for tag in list(item.keys()):
                if tag.is_private:
                    del item[tag]
        for tag in list(item.keys()):
            element = item[tag]
            if not tag.is_private:
                try:
                    expected_vr = dictionary_VR(tag)
                except KeyError:
                    if tag.element == 0:
                        expected_vr = "UL"  # Retired generic group length.
                    else:
                        raise ReadError("An unknown public attribute requires specialized handling.") from None
                if element.VR not in {expected_vr, *expected_vr.split(" or ")}:
                    raise ReadError("A public attribute has the wrong DICOM value representation.")
            if element.VR == "SQ":
                stack.extend(element.value)
            elif element.VR == "UN" and not element.tag.is_private:
                raise ReadError("Opaque public data cannot safely be interpreted.")


def read_dicom(path: str | Path, *, remove_private: bool = True) -> Dataset:
    """Read Part 10 or recognizable preamble-less DICOM, never pixels-decompress.

    No process-global convert_wrong_length_to_UN changes: malformed public attributes
    remain errors. Private values are discarded without being interpreted. Warnings
    are treated as read failures, never emitted with patient values or source paths.
    """
    try:
        with private_diagnostics() as diagnostics:
            try:
                ds = pydicom.dcmread(path)
            except InvalidDicomError:
                ds = pydicom.dcmread(path, force=True)
            # A forced read is only accepted with mandatory composite identifiers.
            if not ds.get("SOPClassUID") or not ds.get("SOPInstanceUID"):
                raise ReadError("Missing composite object identifiers.")
            _materialize(ds, remove_private)
            if getattr(ds, "file_meta", None):
                _materialize(ds.file_meta, remove_private)
        if diagnostics.has_warnings:
            raise ReadError("DICOM parser reported invalid metadata.")
        return ds
    except ReadError:
        raise
    except Exception:
        raise ReadError("DICOM could not be read safely.") from None


def looks_like_dicom(path: Path) -> bool:
    """Recognize damaged candidates so they fail rather than silently disappear."""
    if path.suffix.lower() in {".dcm", ".dicom", ".ima"} or path.name.upper() == "DICOMDIR":
        return True
    try:
        with path.open("rb") as stream:
            header = stream.read(132)
        if header[128:132] == b"DICM":
            return True
        # Common initial public groups in preamble-less little/big endian datasets.
        return len(header) >= 8 and header[:2] in {
            b"\x02\x00", b"\x08\x00", b"\x10\x00", b"\x00\x02", b"\x00\x08", b"\x00\x10"
        }
    except OSError:
        return True
