"""Multiprocessing workers must never enter desktop or CLI initialization."""

from __future__ import annotations

import builtins
import multiprocessing
import runpy
import sys
from pathlib import Path

import pytest

from opendicom_deid import __main__ as entrypoint


def _finished_worker():
    # PyInstaller's freeze_support dispatches and exits inside worker processes.
    raise SystemExit(0)


def test_frozen_worker_diverts_before_application_imports(monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert not name.startswith("opendicom_deid"), "Worker reached application imports"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(multiprocessing, "freeze_support", _finished_worker)
    monkeypatch.setattr(builtins, "__import__", checked_import)
    script = Path(__file__).resolve().parents[1] / "scripts" / "desktop_entry.py"
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")
    assert stopped.value.code == 0


@pytest.mark.parametrize("arguments", [[], ["--check-gui"], ["--multiprocessing-fork"]])
def test_public_entrypoint_diverts_workers_before_gui_or_cli(monkeypatch, arguments):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert name not in {"gui.app", "cli"}, "Worker reached desktop or CLI initialization"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", ["opendicom-deid", *arguments])
    monkeypatch.setattr(multiprocessing, "freeze_support", _finished_worker)
    monkeypatch.setattr(builtins, "__import__", checked_import)
    with pytest.raises(SystemExit) as stopped:
        entrypoint.main()
    assert stopped.value.code == 0
