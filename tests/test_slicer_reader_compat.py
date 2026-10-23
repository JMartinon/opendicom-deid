"""The optional Slicer guard changes reader routing, never DICOM data."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "slicer_reader_compat.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("slicer_reader_compat", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = load_helper()


@pytest.fixture
def environment():
    class Plugin:
        def __init__(self):
            self.calls = []

        def examineForImport(self, groups):
            self.calls.append(("examineForImport", groups))
            return [group for group in groups]

        def examineFiles(self, files):
            self.calls.append(("examineFiles", files))
            return files

        def prepareReaders(self, loadables, readerApproach=None):
            self.calls.append(("prepareReaders", loadables, readerApproach))
            for loadable in loadables:
                loadable.preparedSeriesReader = (readerApproach, "prepared")

        def load(self, loadable, readerApproach=None):
            self.calls.append(("load", loadable, readerApproach))
            return "loaded"

    database = SimpleNamespace(isOpen=True)
    classes = {"plan": compat._VIEWRAY_PLAN, "image": "1.2.840.10008.5.1.4.1.1.2", "unknown": "9.9.9"}

    def file_value(filename, tag):
        assert tag == "0008,0016"
        return classes[filename]

    database.fileValue = file_value
    return Plugin, database


def apply(environment):
    cls, database = environment
    assert compat.apply_compatibility(plugin_class=cls, database_getter=lambda: database)
    return cls()


def test_examination_filters_only_exact_class_and_preserves_mixed_lists(environment):
    plugin = apply(environment)
    original = [["plan"], ["plan", "image"], [], ["unknown"]]
    assert plugin.examineForImport(original) == [["image"], ["unknown"]]
    assert original == [["plan"], ["plan", "image"], [], ["unknown"]]
    assert plugin.examineFiles(["plan", "image"]) == ["image"]
    assert plugin.examineFiles(["plan"]) == []
    assert plugin.examineForImport([["plan"], []]) == []
    assert len(plugin.calls) == 2


@pytest.mark.parametrize("files", [["plan"], ["image", "plan"]])
def test_cached_loadable_is_blocked_before_pixel_preparation_and_load(environment, files):
    plugin = apply(environment)
    stale = SimpleNamespace(files=files, preparedSeriesReader=("GDCM", "cached"))
    image = SimpleNamespace(files=["image"])
    plugin.prepareReaders([stale, image], readerApproach="GDCM")
    assert not hasattr(stale, "preparedSeriesReader")
    assert image.preparedSeriesReader == ("GDCM", "prepared")
    assert plugin.load(stale, readerApproach="GDCM") is False
    assert plugin.load(image, readerApproach="GDCM") == "loaded"
    assert [call[0] for call in plugin.calls] == ["prepareReaders", "load"]
    assert stale.files == files


def test_prepare_readers_skips_empty_result(environment):
    plugin = apply(environment)
    assert plugin.prepareReaders([SimpleNamespace(files=["plan"])]) is None
    assert plugin.calls == []


def test_existing_instances_and_reloaded_helpers_are_idempotent_and_reversible(environment, capsys):
    cls, database = environment
    instance = cls()
    originals = {name: getattr(cls, name) for name in ("examineForImport", "examineFiles", "prepareReaders", "load")}
    apply(environment)
    wrappers = {name: getattr(cls, name) for name in originals}
    reloaded = load_helper()
    assert reloaded.apply_compatibility(plugin_class=cls, database_getter=lambda: database)
    assert wrappers == {name: getattr(cls, name) for name in originals}
    assert instance.examineFiles(["plan"]) == []
    assert instance.examineFiles(["plan"]) == []
    out = capsys.readouterr().out
    assert out.count(compat._APPLIED) == 1
    assert out.count(compat._SKIPPED) == 1
    assert reloaded.restore_compatibility(plugin_class=cls)
    assert reloaded.restore_compatibility(plugin_class=cls)
    assert originals == {name: getattr(cls, name) for name in originals}
    assert instance.examineFiles(["plan"]) == ["plan"]


def test_restore_preserves_inherited_methods(environment):
    parent, database = environment

    class Child(parent):
        pass

    assert compat.apply_compatibility(plugin_class=Child, database_getter=lambda: database)
    assert compat.restore_compatibility(plugin_class=Child)
    assert "load" not in vars(Child)
    assert Child.load is parent.load


def test_restore_does_not_overwrite_another_patch(environment):
    cls, _ = environment
    apply(environment)
    replacement = lambda self, value: value  # noqa: E731
    cls.load = replacement
    assert not compat.restore_compatibility(plugin_class=cls)
    assert cls.load is replacement
    assert not compat.apply_compatibility(plugin_class=cls)


@pytest.mark.parametrize("closed", [False, True])
def test_unreadable_database_stops_reader_with_fixed_diagnostic(environment, closed):
    cls, database = environment
    plugin = apply(environment)
    database.isOpen = not closed

    def broken(*args):
        raise OSError("SENSITIVE path or patient value")

    database.fileValue = broken
    with pytest.raises(RuntimeError, match="^" + compat._DATABASE_FAILED + "$"):
        plugin.examineFiles(["plan"])
    assert plugin.calls == []


def test_class_filter_does_not_match_other_private_or_standard_plan_classes(environment):
    cls, database = environment
    for uid in (compat._VIEWRAY_PLAN + ".1", "1.2.840.10008.5.1.4.1.1.481.5", "9.9.9", ""):
        database.fileValue = lambda filename, tag, value=uid: value
        plugin = apply(environment)
        assert plugin.examineFiles(["plan"]) == ["plan"]
        assert compat.restore_compatibility(plugin_class=cls)


def test_older_plugin_without_parallel_readers_is_supported(environment):
    cls, database = environment
    del cls.prepareReaders
    assert compat.apply_compatibility(plugin_class=cls, database_getter=lambda: database)
    assert cls().examineFiles(["plan"]) == []
    assert compat.restore_compatibility(plugin_class=cls)


def test_unsupported_plugin_is_not_partially_patched(capsys):
    class Plugin:
        def load(self, value):
            return value

    original = Plugin.load
    assert not compat.apply_compatibility(plugin_class=Plugin)
    assert Plugin.load is original
    assert capsys.readouterr().out.strip() == compat._APPLY_FAILED
