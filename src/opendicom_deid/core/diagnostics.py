"""Keep third-party diagnostics from exposing source values or paths.

pydicom sends some warnings both to Python warnings and to its logger. Catching
warnings alone is insufficient. Suppress its logger on the active processing
thread and retain only a count; no raw message is collected or exported.
"""

from __future__ import annotations

import logging
import threading
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Diagnostics:
    log_count: int = 0
    warning_count: int = 0

    @property
    def has_warnings(self) -> bool:
        return bool(self.log_count or self.warning_count)


_active: ContextVar[Diagnostics | None] = ContextVar("opendicom_diagnostics", default=None)
_lock = threading.RLock()


class _PrivateDiagnosticsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        diagnostics = _active.get()
        if diagnostics is None:
            return True
        if record.levelno >= logging.WARNING:
            diagnostics.log_count += 1
        return False


logging.getLogger("pydicom").addFilter(_PrivateDiagnosticsFilter())


@contextmanager
def private_diagnostics() -> Iterator[Diagnostics]:
    """Serialize warning capture on Python versions with process-global warning filters."""
    with _lock:
        result = Diagnostics()
        token = _active.set(result)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                yield result
                result.warning_count = len(caught)
        finally:
            _active.reset(token)
