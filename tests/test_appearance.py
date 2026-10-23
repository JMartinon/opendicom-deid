"""OS appearance detection works without importing Tk or changing settings."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from opendicom_deid.gui import appearance


def _result(text="", code=0):
    return subprocess.CompletedProcess([], code, stdout=text)


@pytest.mark.parametrize("value,expected", [(0, "light"), (1, "dark")])
def test_macos_uses_native_window_appearance(value, expected):
    tk = SimpleNamespace(call=Mock(side_effect=["aqua", value]), getboolean=bool)
    root = SimpleNamespace(tk=tk)
    assert appearance.macos_window_appearance(root) == expected
    assert tk.call.call_args.args == ("tk::unsupported::MacWindowStyle", "isdark", root)


def test_macos_native_probe_unavailable_is_optional():
    assert appearance.macos_window_appearance(object()) is None
    tk = SimpleNamespace(call=Mock(side_effect=RuntimeError("unsupported Tcl command")))
    assert appearance.macos_window_appearance(SimpleNamespace(tk=tk)) is None


def test_non_aqua_window_does_not_call_macos_command():
    tk = SimpleNamespace(call=Mock(return_value="x11"))
    assert appearance.macos_window_appearance(SimpleNamespace(tk=tk)) is None
    tk.call.assert_called_once_with("tk", "windowingsystem")


@pytest.mark.parametrize("value,kind,expected", [(0, 4, "dark"), (1, 4, "light"), (2, 4, None), ("0", 1, None)])
def test_windows_reads_application_mode_not_system_taskbar(monkeypatch, value, kind, expected):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    registry = SimpleNamespace(
        HKEY_CURRENT_USER=object(), REG_DWORD=4,
        OpenKey=Mock(return_value=Key()), QueryValueEx=Mock(return_value=(value, kind)),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(appearance.sys, "platform", "win32")
    assert appearance.detect_system_appearance() == expected
    assert registry.QueryValueEx.call_args.args[1] == "AppsUseLightTheme"
    registry.OpenKey.assert_called_once_with(registry.HKEY_CURRENT_USER, appearance._WINDOWS_PERSONALIZE)


@pytest.mark.parametrize("error,expected", [(FileNotFoundError(), "light"), (PermissionError(), None)])
def test_windows_unset_default_and_access_failure(monkeypatch, error, expected):
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=object(), OpenKey=Mock(side_effect=error),
    ))
    assert appearance._windows_appearance() == expected


@pytest.mark.parametrize("result,expected", [
    (_result("Dark\n"), "dark"), (_result("Light\n"), "light"),
    (_result("", 1), "light"), (_result("", 2), None), (None, None),
])
def test_macos_global_preference_fallback(monkeypatch, result, expected):
    command = Mock(return_value=result)
    monkeypatch.setattr(appearance, "_command", command)
    monkeypatch.setattr(appearance.sys, "platform", "darwin")
    assert appearance.detect_system_appearance() == expected
    command.assert_called_once_with(["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"])


@pytest.mark.parametrize("text,expected", [
    ("(<<uint32 1>>,)\n", "dark"), ("(<uint32 2>,)", "light"),
    ("(<<uint32 0>>,)", None), ("(<uint32 99>,)", None), ("1", None), (None, None),
])
def test_portal_variants_and_unknown_preferences(monkeypatch, text, expected):
    command = Mock(return_value=text)
    monkeypatch.setattr(appearance, "_command_text", command)
    assert appearance._portal_appearance() == expected
    assert command.call_args.args[0][-2:] == ["org.freedesktop.appearance", "color-scheme"]


def test_linux_portal_wins_over_legacy_settings(monkeypatch):
    monkeypatch.setattr(appearance.sys, "platform", "linux")
    monkeypatch.setattr(appearance, "_portal_appearance", lambda: "dark")
    legacy = Mock(side_effect=AssertionError("No legacy probe is needed"))
    monkeypatch.setattr(appearance, "_gnome_appearance", legacy)
    assert appearance.detect_system_appearance() == "dark"


def test_kde_fallback_has_priority_over_stale_gnome_setting(monkeypatch):
    monkeypatch.setattr(appearance.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(appearance, "_portal_appearance", lambda: None)
    monkeypatch.setattr(appearance, "_kde_appearance", lambda: "dark")
    monkeypatch.setattr(appearance, "_gnome_appearance", lambda: "light")
    assert appearance.detect_system_appearance() == "dark"


@pytest.mark.parametrize("values,expected", [
    (["'prefer-dark'"], "dark"), (["'prefer-light'"], "light"),
    (["'default'", "'Adwaita-dark'"], "dark"),
    ([None, "'Mint-Y-Dark-Aqua'"], "dark"),
    ([None, "'Adwaita'"], "light"), ([None, None], None),
])
def test_gtk_color_preference_and_older_theme_fallback(monkeypatch, values, expected):
    monkeypatch.setattr(appearance, "_command_text", Mock(side_effect=values))
    assert appearance._gnome_appearance() == expected


@pytest.mark.parametrize("contents,expected", [
    pytest.param("[Colors:Window]\nBackgroundNormal=35,38,41\n", "dark", id="dark-window"),
    pytest.param("[Colors:Window]\nBackgroundNormal=239,240,241\n", "light", id="light-window"),
    pytest.param("[Colors:Window]\nBackgroundNormal=999,0,0\n", None, id="out-of-range-color"),
    pytest.param("[Colors:Window]\nBackgroundNormal=not-a-color\n", None, id="invalid-color"),
    pytest.param("broken config", None, id="invalid-config"),
    pytest.param("x" * 65537, None, id="oversized-config"),
])
def test_kde_reads_bounded_actual_window_color(monkeypatch, tmp_path, request, contents, expected):
    # Pytest exports the node ID through PYTEST_CURRENT_TEST; Windows limits
    # environment values to 32,767 characters. Keep oversized data out of IDs.
    assert len(request.node.nodeid + " (teardown)") < 32767
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "kdeglobals").write_text(contents)
    assert appearance._kde_appearance() == expected
    assert (tmp_path / "kdeglobals").read_text() == contents


def test_command_is_local_bounded_and_has_no_shell(monkeypatch):
    run = Mock(return_value=_result("value"))
    monkeypatch.setattr(appearance.subprocess, "run", run)
    assert appearance._command_text(["gsettings", "get", "schema", "key"]) == "value"
    args, kwargs = run.call_args
    assert args[0] == ["gsettings", "get", "schema", "key"]
    assert kwargs["timeout"] == 1.0
    assert not kwargs.get("shell", False)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize("original", [None, "", "/original/system/lib"])
def test_frozen_linux_probe_uses_host_libraries_without_changing_parent(monkeypatch, original):
    monkeypatch.setattr(appearance.sys, "platform", "linux")
    monkeypatch.setattr(appearance.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal:/original/system/lib")
    if original is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)
    before = appearance.os.environ.copy()
    run = Mock(return_value=_result("(<uint32 1>,)"))
    monkeypatch.setattr(appearance.subprocess, "run", run)
    appearance._command(["gdbus", "call"])
    child = run.call_args.kwargs["env"]
    assert child is not appearance.os.environ
    if original is None:
        assert "LD_LIBRARY_PATH" not in child
    else:
        assert child["LD_LIBRARY_PATH"] == original
    assert appearance.os.environ == before


@pytest.mark.parametrize("platform,frozen", [("linux", False), ("darwin", True), ("win32", True)])
def test_library_path_cleanup_is_only_for_frozen_linux(monkeypatch, platform, frozen):
    monkeypatch.setattr(appearance.sys, "platform", platform)
    monkeypatch.setattr(appearance.sys, "frozen", frozen, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unchanged/library/path")
    run = Mock(return_value=_result("value"))
    monkeypatch.setattr(appearance.subprocess, "run", run)
    appearance._command(["appearance-probe"])
    assert run.call_args.kwargs["env"] is None
    assert appearance.os.environ["LD_LIBRARY_PATH"] == "/unchanged/library/path"


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("probe", 1)])
def test_missing_or_hung_command_is_optional(monkeypatch, failure):
    monkeypatch.setattr(appearance.subprocess, "run", Mock(side_effect=failure))
    assert appearance._command_text(["gsettings", "get", "schema", "key"]) is None


def test_failed_or_oversized_command_output_is_ignored(monkeypatch):
    monkeypatch.setattr(appearance, "_command", Mock(side_effect=[_result("dark", 1), _result("x" * 4097)]))
    assert appearance._command_text(["probe"]) is None
    assert appearance._command_text(["probe"]) is None


def _eventually(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_watcher_caches_changes_keeps_last_known_mode_and_stops():
    readings = iter(("dark", None, "light"))
    calls = []

    def reader():
        calls.append(threading.current_thread().ident)
        return next(readings, None)

    watcher = appearance.SystemAppearanceWatcher(reader=reader, interval=0.5)
    try:
        _eventually(lambda: watcher.current == "dark")
        _eventually(lambda: len(calls) >= 2)
        assert watcher.current == "dark"
        _eventually(lambda: watcher.current == "light")
        assert len(set(calls)) == 1
        assert calls[0] != threading.current_thread().ident
    finally:
        watcher.stop()
        watcher._thread.join(timeout=1)
    assert not watcher._thread.is_alive()


def test_slow_probe_does_not_block_gui_reads_or_shutdown():
    entered, release = threading.Event(), threading.Event()

    def reader():
        entered.set()
        release.wait(2)
        return "dark"

    watcher = appearance.SystemAppearanceWatcher(reader=reader)
    try:
        assert entered.wait(1)
        assert watcher.current == "light"
        watcher.stop()
        assert watcher.current == "light"
    finally:
        release.set()
        watcher._thread.join(timeout=1)
    assert watcher.current == "light"
    assert not watcher._thread.is_alive()


def test_unexpected_probe_error_preserves_appearance():
    called = threading.Event()

    def reader():
        called.set()
        raise RuntimeError("optional OS probe failed")

    watcher = appearance.SystemAppearanceWatcher(reader=reader, initial="dark")
    try:
        assert called.wait(1)
        assert watcher.current == "dark"
    finally:
        watcher.stop()
        watcher._thread.join(timeout=1)


@pytest.mark.parametrize("interval", [0, -1, 0.1, float("nan"), float("inf")])
def test_watcher_cannot_busy_poll(interval):
    with pytest.raises(ValueError):
        appearance.SystemAppearanceWatcher(interval=interval)
