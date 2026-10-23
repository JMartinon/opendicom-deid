"""Bridge SlicerRT's legacy database setting from Slicer's open database.

Run inside 3D Slicer's Python console, not a standalone Python interpreter.
Importing this module does not change settings. ``apply_compatibility()`` saves
the original legacy setting before updating it; ``restore_compatibility()``
restores that original state. Neither function reads or changes DICOM files.
"""

from __future__ import annotations

from pathlib import Path

_LEGACY = "DatabaseDirectory"
_BACKUP = "OpenDICOM/SlicerRTDatabaseCompatibility"
_VERSION = _BACKUP + "/version"
_STATE = _BACKUP + "/state"
_VALUE = _BACKUP + "/value"
_APPLY_OK = "SlicerRT database compatibility setting updated. Retry the DICOM import."
_APPLY_FAILED = "Database compatibility update failed; open a valid DICOM database in Slicer first."
_RESTORE_OK = "Original SlicerRT database setting restored."
_RESTORE_FAILED = "Database compatibility restore failed; no valid saved setting was available."


def _sync(settings) -> None:
    settings.sync()
    if int(settings.status()) != 0:  # QSettings.NoError
        raise RuntimeError("Settings could not be saved")


def _backup_state(settings) -> str | None:
    if not any(settings.contains(key) for key in (_VERSION, _STATE, _VALUE)):
        return None
    state = settings.value(_STATE)
    if settings.value(_VERSION) != "1" or state not in {"present", "absent"}:
        raise ValueError("Invalid settings backup")
    if (state == "present") != settings.contains(_VALUE):
        raise ValueError("Incomplete settings backup")
    return state


def _save_original(settings) -> None:
    if _backup_state(settings) is not None:
        return  # Reapplying must not overwrite the original setting.
    present = settings.contains(_LEGACY)
    original = settings.value(_LEGACY) if present else None
    if present:
        settings.setValue(_VALUE, original)
    settings.setValue(_STATE, "present" if present else "absent")
    _sync(settings)
    settings.setValue(_VERSION, "1")
    _sync(settings)
    if _backup_state(settings) != ("present" if present else "absent"):
        raise ValueError("Settings backup verification failed")
    if present and settings.value(_VALUE) != original:
        raise ValueError("Settings backup verification failed")


def _active_directory(database) -> str:
    if database is None or not database.isOpen:
        raise ValueError("No open database")
    directory = database.databaseDirectory
    filename = database.databaseFilename
    if not isinstance(directory, str) or not directory or not isinstance(filename, str):
        raise ValueError("Invalid database location")
    expected = Path(directory) / "ctkDICOM.sql"
    if not expected.is_file() or expected.resolve() != Path(filename).resolve():
        raise ValueError("Active database does not match its directory")
    return directory


def apply_compatibility(*, settings=None, database=None) -> bool:
    """Point legacy SlicerRT at the active database after saving the old setting."""
    try:
        if settings is None:
            import qt

            settings = qt.QSettings()
        if database is None:
            import slicer

            database = slicer.dicomDatabase
        directory = _active_directory(database)
        _save_original(settings)
        settings.setValue(_LEGACY, directory)
        _sync(settings)
        if settings.value(_LEGACY) != directory:
            raise ValueError("Settings update verification failed")
    except Exception:
        # Exceptions may contain local paths or database contents.
        print(_APPLY_FAILED)
        return False
    print(_APPLY_OK)
    return True


def restore_compatibility(*, settings=None) -> bool:
    """Restore the original legacy value, or remove the key if originally absent."""
    try:
        if settings is None:
            import qt

            settings = qt.QSettings()
        state = _backup_state(settings)
        if state is None:
            raise ValueError("No saved setting")
        original = settings.value(_VALUE) if state == "present" else None
        if state == "present":
            settings.setValue(_LEGACY, original)
        else:
            settings.remove(_LEGACY)
        _sync(settings)
        if settings.contains(_LEGACY) != (state == "present"):
            raise ValueError("Settings restore verification failed")
        if state == "present" and settings.value(_LEGACY) != original:
            raise ValueError("Settings restore verification failed")
        settings.remove(_BACKUP)
        _sync(settings)
    except Exception:
        print(_RESTORE_FAILED)
        return False
    print(_RESTORE_OK)
    return True


if __name__ == "__main__":
    apply_compatibility()
