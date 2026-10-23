"""The Slicer workaround must be reversible and never print database details."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "slicer_database_compat.py"
_SPEC = importlib.util.spec_from_file_location("slicer_database_compat", _SCRIPT)
compat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compat)


class Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.sync_error = False

    def contains(self, key):
        return key in self.values

    def value(self, key):
        return self.values.get(key)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        for candidate in list(self.values):
            if candidate == key or candidate.startswith(key + "/"):
                del self.values[candidate]

    def sync(self):
        pass

    def status(self):
        return int(self.sync_error)


@pytest.fixture
def database(tmp_path):
    filename = tmp_path / "ctkDICOM.sql"
    filename.touch()
    return SimpleNamespace(
        isOpen=True, databaseDirectory=str(tmp_path), databaseFilename=str(filename),
    )


@pytest.mark.parametrize("original", [None, "", "/previous/database"])
def test_apply_and_restore_original_state(database, original):
    values = {"Unrelated": "unchanged"}
    if original is not None:
        values[compat._LEGACY] = original
    settings = Settings(values)
    assert compat.apply_compatibility(settings=settings, database=database)
    assert settings.value(compat._LEGACY) == database.databaseDirectory
    assert compat.restore_compatibility(settings=settings)
    assert settings.values == values


def test_reapply_keeps_original_backup_when_database_changes(database, tmp_path):
    settings = Settings({compat._LEGACY: "/original"})
    assert compat.apply_compatibility(settings=settings, database=database)
    new_directory = tmp_path / "second"
    new_directory.mkdir()
    new_file = new_directory / "ctkDICOM.sql"
    new_file.touch()
    database.databaseDirectory = str(new_directory)
    database.databaseFilename = str(new_file)
    assert compat.apply_compatibility(settings=settings, database=database)
    assert settings.value(compat._LEGACY) == str(new_directory)
    assert compat.restore_compatibility(settings=settings)
    assert settings.values == {compat._LEGACY: "/original"}


@pytest.mark.parametrize("invalid", ["closed", "missing", "different_file", "empty"])
def test_invalid_database_does_not_change_settings(database, invalid):
    settings = Settings({compat._LEGACY: "/original"})
    if invalid == "closed":
        database.isOpen = False
    elif invalid == "missing":
        Path(database.databaseFilename).unlink()
    elif invalid == "different_file":
        database.databaseFilename = str(Path(database.databaseDirectory) / "other.sql")
    else:
        database.databaseDirectory = ""
    assert not compat.apply_compatibility(settings=settings, database=database)
    assert settings.values == {compat._LEGACY: "/original"}


def test_failed_backup_never_changes_legacy_setting(database):
    settings = Settings({compat._LEGACY: "/original"})
    settings.sync_error = True
    assert not compat.apply_compatibility(settings=settings, database=database)
    assert settings.value(compat._LEGACY) == "/original"


@pytest.mark.parametrize("backup", [
    {},
    {compat._STATE: "present"},
    {compat._VERSION: "1", compat._STATE: "present"},
    {compat._VERSION: "1", compat._STATE: "absent", compat._VALUE: "/bad"},
])
def test_missing_or_invalid_backup_cannot_restore(backup):
    original = {compat._LEGACY: "/original", **backup}
    settings = Settings(original)
    assert not compat.restore_compatibility(settings=settings)
    assert settings.values == original


def test_invalid_existing_backup_cannot_be_overwritten(database):
    original = {compat._LEGACY: "/original", compat._STATE: "present"}
    settings = Settings(original)
    assert not compat.apply_compatibility(settings=settings, database=database)
    assert settings.values == original


def test_exceptions_and_success_messages_do_not_disclose_values(database, capsys):
    class BrokenSettings(Settings):
        def setValue(self, key, value):
            raise OSError("SENSITIVE database path and patient text")

    assert not compat.apply_compatibility(settings=BrokenSettings(), database=database)
    assert capsys.readouterr().out.strip() == compat._APPLY_FAILED
    settings = Settings({compat._LEGACY: "SENSITIVE"})
    assert compat.apply_compatibility(settings=settings, database=database)
    assert capsys.readouterr().out.strip() == compat._APPLY_OK
    assert compat.restore_compatibility(settings=settings)
    assert capsys.readouterr().out.strip() == compat._RESTORE_OK
