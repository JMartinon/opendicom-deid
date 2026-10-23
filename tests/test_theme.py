"""Appearance preferences must be optional, private and tolerant of bad files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from opendicom_deid.gui import theme
except ModuleNotFoundError as error:
    if error.name not in {"tkinter", "_tkinter"}:
        raise
    pytest.skip("Theme tests require the optional Tcl/Tk GUI runtime.", allow_module_level=True)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_theme_preference_round_trip_stores_only_appearance(tmp_path, mode):
    path = tmp_path / "settings" / "appearance.json"
    assert theme.persist_theme(mode, path)
    assert theme.load_theme(path) == mode
    assert json.loads(path.read_text()) == {"theme": mode}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("contents", [
    "", "{broken json", "[]", "null", '{"theme":"neon"}', '{"theme":[]}',
    '{"theme":"dark"}' + " " * 4096,
])
def test_bad_preferences_use_readable_default(tmp_path, contents):
    path = tmp_path / "appearance.json"
    path.write_text(contents)
    assert theme.load_theme(path) == "light"


def test_missing_preference_uses_light(tmp_path):
    assert theme.load_theme(tmp_path / "absent.json") == "light"


def test_failed_write_preserves_previous_preference_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / "appearance.json"
    assert theme.persist_theme("light", path)

    def deny_replace(*args):
        raise PermissionError("Simulated locked appearance settings")

    monkeypatch.setattr(Path, "replace", deny_replace)
    assert not theme.persist_theme("dark", path)
    assert theme.load_theme(path) == "light"
    assert list(tmp_path.iterdir()) == [path]


def test_unwritable_preference_folder_does_not_break_application(tmp_path):
    parent = tmp_path / "not-a-folder"
    parent.write_text("existing file")
    assert not theme.persist_theme("dark", parent / "appearance.json")
    assert parent.read_text() == "existing file"


def test_unknown_theme_cannot_be_saved(tmp_path):
    path = tmp_path / "appearance.json"
    with pytest.raises(ValueError):
        theme.persist_theme("unknown", path)
    assert not path.exists()


@pytest.mark.parametrize("platform,variable", [
    ("win32", "APPDATA"),
    ("linux", "XDG_CONFIG_HOME"),
])
def test_uses_platform_preference_location(monkeypatch, tmp_path, platform, variable):
    monkeypatch.setattr(theme.sys, "platform", platform)
    monkeypatch.setenv(variable, str(tmp_path))
    assert theme.preference_path() == tmp_path / "OpenDICOM-De-ID/appearance.json"


def test_relative_xdg_config_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(theme.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-location")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert theme.preference_path() == tmp_path / ".config/OpenDICOM-De-ID/appearance.json"


def _luminance(color):
    channels = [int(color[start:start + 2], 16) / 255 for start in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(first, second):
    a, b = sorted((_luminance(first), _luminance(second)))
    return (b + 0.05) / (a + 0.05)


@pytest.mark.parametrize("palette", theme.PALETTES.values(), ids=theme.PALETTES)
def test_enabled_text_and_progress_have_readable_contrast(palette):
    for text_color in (palette.text, palette.muted):
        for surface in (palette.surface, palette.background):
            assert _contrast(text_color, surface) >= 4.5
    for surface in (palette.accent, palette.primary_active, palette.primary_pressed):
        assert _contrast(palette.primary_text, surface) >= 4.5
    assert _contrast(palette.text, palette.selection) >= 4.5
    assert _contrast(palette.progress, palette.trough) >= 3
