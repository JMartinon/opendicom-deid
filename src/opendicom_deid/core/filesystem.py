"""Consistent filesystem paths and bounded Windows file-access retries."""

from __future__ import annotations

import ntpath
import re
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import TypeVar

from .models import check_cancel

_T = TypeVar("_T")
_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _extended_windows_path(value: str) -> str:
    """Normalize absolute drive/UNC paths; reject device namespaces."""
    value = value.replace("/", "\\")
    if "\0" in value or value.startswith("\\\\.\\"):
        raise ValueError("Select a filesystem directory, not a device path.")
    if value.startswith("\\\\?\\"):
        value = value[4:]
        if value[:4].upper() == "UNC\\":
            value = "\\\\" + value[4:]
        elif not re.match(r"^[A-Za-z]:\\", value):
            raise ValueError("Select an absolute drive or network-share path.")
    value = ntpath.normpath(value)
    if re.match(r"^[A-Za-z]:\\", value):
        return "\\\\?\\" + value
    if value.startswith("\\\\"):
        parts = value[2:].split("\\")
        if len(parts) >= 2 and all(part and part not in {".", "..", "?"} for part in parts[:2]):
            return "\\\\?\\UNC\\" + value[2:]
    raise ValueError("Select an absolute drive or network-share path.")


def filesystem_path(path: str | Path, *, resolve: bool = True, strict: bool = False) -> Path:
    """Use one absolute representation for all I/O and overlap checks.

    Prefix before resolution so even a long selected root can be accessed.
    Keep the prefix after resolution and while joining children: Windows path
    limits apply to files below a short root as well as to the root itself.
    Other operating systems retain normal pathlib resolution.
    """
    candidate = Path(path).expanduser()
    if sys.platform == "win32":
        candidate = Path(_extended_windows_path(str(candidate.absolute())))
        if resolve:
            candidate = candidate.resolve(strict=strict)
        return Path(_extended_windows_path(str(candidate)))
    return candidate.resolve(strict=strict) if resolve else candidate.absolute()


def is_link_or_junction(path: Path) -> bool:
    """Inspect the requested path before resolving an output link."""
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & 0x20000000)


def display_path(path: Path) -> str:
    """Show ordinary Windows spelling in path fields; never use for I/O checks."""
    value = str(path)
    if sys.platform == "win32":
        if value[:8].upper() == "\\\\?\\UNC\\":
            return "\\\\" + value[8:]
        if value.startswith("\\\\?\\"):
            return value[4:]
    return value


def retry_windows_file_lock(
    operation: Callable[[], _T], *, cancel: Event | None = None,
    retry_access_denied: bool = False,
) -> _T:
    """Retry an operation whose failed attempt is safe to repeat.

    Win32 sharing/lock violations can be temporary. Directory renames/deletions
    can also report access denied while another process holds a handle. Retrying
    never changes permissions; persistent errors still fail. Plain POSIX errno
    values are not evidence of a Windows sharing conflict.
    """
    codes = {32, 33, 5} if retry_access_denied else {32, 33}
    for attempt in range(len(_RETRY_DELAYS) + 1):
        check_cancel(cancel)
        try:
            return operation()
        except OSError as error:
            if getattr(error, "winerror", None) not in codes or attempt == len(_RETRY_DELAYS):
                raise
            delay = _RETRY_DELAYS[attempt]
            if cancel is None:
                time.sleep(delay)
            else:
                cancel.wait(delay)
                check_cancel(cancel)
    raise AssertionError("Unreachable retry state")


def safe_error_details(error: Exception) -> str:
    """Expose only fixed categories and numeric OS codes, never exception text."""
    details = []
    if isinstance(error, OSError):
        for label, attribute in (("Windows error", "winerror"), ("errno", "errno")):
            value = getattr(error, attribute, None)
            if type(value) is int:
                details.append(f"{label} {value}")
    if not details:
        categories = (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, MemoryError)
        details.append(next((cls.__name__ for cls in categories if isinstance(error, cls)), "Exception"))
    return "; ".join(details)
