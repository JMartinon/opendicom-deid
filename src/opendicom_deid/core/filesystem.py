"""Bounded retries for Windows file sharing conflicts, never for DICOM writes."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event
from typing import TypeVar

from .models import check_cancel

_T = TypeVar("_T")
_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


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
