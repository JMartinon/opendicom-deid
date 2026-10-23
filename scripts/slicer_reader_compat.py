"""Keep the known ViewRay private RT plan out of Slicer's image reader.

Run inside 3D Slicer's Python console. Importing this file changes nothing.
``apply_compatibility()`` patches the registered Scalar Volume plugin for this
session, including existing instances. ``restore_compatibility()`` undoes it.
This does not add RT-plan support or alter DICOM files, UIDs, or preferences.
"""

from __future__ import annotations

from functools import wraps

_VIEWRAY_PLAN = "2.16.840.1.114493.1.2.1.4.1.1.481.5"
_STATE = "_opendicom_viewray_reader_compatibility"
_SKIPPED = (
    "Skipped a private ViewRay RT plan in the Scalar Volume image reader. "
    "Its beams were not loaded. Re-examine to refresh previously cached loadables; "
    "CT/MR images, RT structures and dose can still use their appropriate readers."
)
_APPLIED = "ViewRay Scalar Volume compatibility enabled for this Slicer session."
_APPLY_FAILED = "Reader compatibility was not enabled: a compatible Scalar Volume plugin is required."
_RESTORED = "Original Scalar Volume reader restored."
_RESTORE_FAILED = "Reader compatibility could not be restored because the plugin changed."
_DATABASE_FAILED = "Reader compatibility requires an open, readable DICOM database."


def _plugin_class():
    import slicer

    return slicer.modules.dicomPlugins["DICOMScalarVolumePlugin"]


def _active_database():
    import slicer

    return slicer.dicomDatabase


def apply_compatibility(*, plugin_class=None, database_getter=None) -> bool:
    """Exclude only the known ViewRay plan class from Scalar Volume operations.

    Mixed examination lists retain their image files. A stale loadable that
    still includes a private plan is rejected whole: its cached geometry and
    prepared reader cannot safely be reused for a different file list.
    """
    try:
        cls = plugin_class or _plugin_class()
        existing = vars(cls).get(_STATE)
        if existing is not None:
            if all(getattr(cls, name) is wrapper for name, wrapper in existing["wrappers"].items()):
                return True
            raise ValueError("Plugin changed")
        required = ("examineForImport", "examineFiles", "load")
        if not all(callable(getattr(cls, name, None)) for name in required):
            raise ValueError("Unsupported plugin")
        names = (*required, "prepareReaders") if callable(getattr(cls, "prepareReaders", None)) else required
        originals = {name: getattr(cls, name) for name in names}
        own_methods = {name for name in names if name in vars(cls)}
        getter = database_getter or _active_database
        state = {"originals": originals, "own_methods": own_methods, "wrappers": {}, "warned": False}

        def is_private_plan(filename):
            try:
                database = getter()
                if database is None or not database.isOpen:
                    raise ValueError("Closed database")
                return str(database.fileValue(filename, "0008,0016")).strip() == _VIEWRAY_PLAN
            except Exception:
                # Database exceptions can include file paths or patient values.
                raise RuntimeError(_DATABASE_FAILED) from None

        def notice():
            if not state["warned"]:
                print(_SKIPPED)
                state["warned"] = True

        def filtered_files(files):
            result = []
            for filename in files:
                if is_private_plan(filename):
                    notice()
                else:
                    result.append(filename)
            return result

        def blocked(loadable):
            if any(is_private_plan(filename) for filename in loadable.files):
                notice()
                if hasattr(loadable, "preparedSeriesReader"):
                    del loadable.preparedSeriesReader
                return True
            return False

        @wraps(originals["examineForImport"])
        def examine(self, file_lists, *args, **kwargs):
            filtered = [files for group in file_lists if (files := filtered_files(group))]
            if not filtered:
                return []
            return originals["examineForImport"](self, filtered, *args, **kwargs)

        @wraps(originals["examineFiles"])
        def examine_files(self, files, *args, **kwargs):
            filtered = filtered_files(files)
            if not filtered:
                return []
            return originals["examineFiles"](self, filtered, *args, **kwargs)

        @wraps(originals["load"])
        def load(self, loadable, *args, **kwargs):
            if blocked(loadable):
                return False
            return originals["load"](self, loadable, *args, **kwargs)

        state["wrappers"].update(examineForImport=examine, examineFiles=examine_files, load=load)
        if "prepareReaders" in originals:
            @wraps(originals["prepareReaders"])
            def prepare(self, loadables, *args, **kwargs):
                filtered = [loadable for loadable in loadables if not blocked(loadable)]
                if not filtered:
                    return None
                return originals["prepareReaders"](self, filtered, *args, **kwargs)

            state["wrappers"]["prepareReaders"] = prepare
        for name, wrapper in state["wrappers"].items():
            setattr(cls, name, wrapper)
        setattr(cls, _STATE, state)
    except Exception:
        print(_APPLY_FAILED)
        return False
    print(_APPLIED)
    return True


def restore_compatibility(*, plugin_class=None) -> bool:
    """Restore the original methods without overwriting another extension's patch."""
    try:
        cls = plugin_class or _plugin_class()
        state = vars(cls).get(_STATE)
        if state is None:
            return True
        if not all(getattr(cls, name) is wrapper for name, wrapper in state["wrappers"].items()):
            raise ValueError("Plugin changed")
        for name, original in state["originals"].items():
            if name in state["own_methods"]:
                setattr(cls, name, original)
            else:
                delattr(cls, name)
        delattr(cls, _STATE)
    except Exception:
        print(_RESTORE_FAILED)
        return False
    print(_RESTORED)
    return True


if __name__ == "__main__":
    apply_compatibility()
