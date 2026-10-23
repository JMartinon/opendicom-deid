"""Read system appearance without importing Tk or changing desktop settings."""

from __future__ import annotations

import configparser
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal

ThemeMode = Literal["light", "dark"]
_COMMAND_TIMEOUT = 1.0
_WINDOWS_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def macos_window_appearance(root: object) -> ThemeMode | None:
    """Probe Tk's effective native appearance, on the GUI thread only.

    Tk documents this native query in its macosx/README. Unlike querying the
    global preference alone, it also follows automatic time-of-day changes.
    Older Tk builds or non-Aqua windows simply use the background OS probe.
    """
    try:
        tk = root.tk
        if tk.call("tk", "windowingsystem") != "aqua":
            return None
        dark = tk.getboolean(tk.call("tk::unsupported::MacWindowStyle", "isdark", root))
        return "dark" if dark else "light"
    except Exception:
        # Do not import tkinter here: core-only Python installations may lack
        # _tkinter, and an unsupported Tcl command raises a toolkit exception.
        return None


def _command(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    environment = None
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        # PyInstaller prepends its bundled libraries to LD_LIBRARY_PATH. Host
        # gdbus/gsettings must use their own system libraries. Sanitize only
        # the child's environment so concurrent DICOM/Tk work is unaffected.
        environment = os.environ.copy()
        if "LD_LIBRARY_PATH_ORIG" in environment:
            environment["LD_LIBRARY_PATH"] = environment["LD_LIBRARY_PATH_ORIG"]
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    try:
        return subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            timeout=_COMMAND_TIMEOUT, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _command_text(arguments: list[str]) -> str | None:
    result = _command(arguments)
    if result is None or result.returncode != 0 or len(result.stdout) > 4096:
        return None
    return result.stdout.strip()


def _windows_appearance() -> ThemeMode | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_PERSONALIZE) as key:
            value, kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        if kind == winreg.REG_DWORD and value in (0, 1):
            return "light" if value else "dark"
    except FileNotFoundError:
        # Windows does not necessarily create this value until a user changes
        # the default application appearance; the unset default is light.
        return "light"
    except (ImportError, OSError):
        pass
    return None


def _macos_appearance() -> ThemeMode | None:
    result = _command(["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"])
    if result is None:
        return None
    if result.returncode == 1:
        # The preference is absent in light mode. Native Tk is preferred where
        # available, so a missing global key cannot override a dark native view.
        return "light"
    if result.returncode == 0:
        return {"dark": "dark", "light": "light"}.get(result.stdout.strip().casefold())
    return None


def _portal_appearance() -> ThemeMode | None:
    response = _command_text([
        "gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
        "--object-path", "/org/freedesktop/portal/desktop", "--method",
        "org.freedesktop.portal.Settings.Read", "org.freedesktop.appearance", "color-scheme",
    ])
    if response is None:
        return None
    # Read is available on older portal versions as well. Its documented
    # variant has two wrappers; accept the one-wrapper form some backends emit.
    value = re.fullmatch(r"\(\s*<\s*(?:<\s*)?uint32\s+([0-9]+)\s*(?:>\s*)?>\s*,?\s*\)", response.strip())
    return {"1": "dark", "2": "light"}.get(value[1]) if value else None


def _gnome_appearance() -> ThemeMode | None:
    result = _command_text(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if result is not None:
        preference = {"prefer-dark": "dark", "prefer-light": "light"}.get(result.strip("'\""))
        if preference:
            return preference
    # Older GTK desktops have a theme name but no color-scheme preference.
    theme = _command_text(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
    if theme:
        return "dark" if re.search(r"(?:^|[-_:\s])dark(?:$|[-_:\s])", theme.strip("'\"").casefold()) else "light"
    return None


def _kde_appearance() -> ThemeMode | None:
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".config"
    try:
        with (base / "kdeglobals").open(encoding="utf-8") as stream:
            contents = stream.read(65537)
        if len(contents) > 65536:
            return None
        settings = configparser.ConfigParser(interpolation=None, strict=False)
        settings.read_string(contents)
        background = settings.get("Colors:Window", "BackgroundNormal", fallback="")
        channels = [int(component.strip()) for component in background.split(",")]
        if len(channels) == 3 and all(0 <= component <= 255 for component in channels):
            red, green, blue = channels
            return "dark" if 299 * red + 587 * green + 114 * blue < 128000 else "light"
    except (OSError, ValueError, configparser.Error):
        pass
    return None


def detect_system_appearance() -> ThemeMode | None:
    """Read OS preferences; return None when detection is unavailable.

    Linux probes are bounded local commands (no shell, no network) and should
    run on the watcher thread, not in a Tk callback. The standardized desktop
    portal wins, followed by the current desktop's legacy settings.
    """
    if sys.platform == "win32":
        return _windows_appearance()
    if sys.platform == "darwin":
        return _macos_appearance()
    if sys.platform.startswith("linux"):
        portal = _portal_appearance()
        if portal:
            return portal
        if "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").casefold():
            return _kde_appearance() or _gnome_appearance()
        return _gnome_appearance()
    return None


class SystemAppearanceWatcher:
    """Cache appearance on one worker; the GUI reads current and applies colors.

    Construct once, read current from a Tk timer, and call stop when closing.
    Failed probes preserve the last known mode. The worker never touches Tk,
    never writes settings, and waits between probes rather than busy-polling.
    """

    def __init__(
        self, *, initial: ThemeMode = "light", interval: float = 3.0,
        reader: Callable[[], ThemeMode | None] | None = None,
    ) -> None:
        if initial not in {"light", "dark"}:
            raise ValueError("Unknown initial appearance.")
        if not math.isfinite(interval) or interval < 0.5:
            raise ValueError("Appearance polling must wait at least half a second.")
        self._current = initial
        self._interval = interval
        self._reader = reader or detect_system_appearance
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._watch, name="opendicom-appearance", daemon=True)
        self._thread.start()

    @property
    def current(self) -> ThemeMode:
        with self._lock:
            return self._current

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                detected = self._reader()
            except Exception:
                # Appearance discovery is optional and must never interrupt a
                # dataset operation or generate an unhandled worker exception.
                detected = None
            if detected in ("light", "dark") and not self._stop.is_set():
                with self._lock:
                    self._current = detected
            if self._stop.wait(self._interval):
                return

    def stop(self) -> None:
        """Signal shutdown immediately; an in-flight probe has a bounded timeout."""
        self._stop.set()
