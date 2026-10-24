"""Match Windows' native title bars to the application's OS-selected palette."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes


def _apply_windows_titlebar(window: tk.Misc) -> bool:
    """Style the current native frame on the Tk thread, without moving focus."""
    try:
        if not window.winfo_ismapped():
            return False
        # Tk's winfo_id is the client window. The window-manager frame owns
        # the title bar; resolve it anew after mapping/recreating a wrapper.
        hwnd = int(window.wm_frame(), 0)
        if not hwnd:
            return False
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        set_attribute = dwm.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        dark = wintypes.BOOL(window._root()._theme_mode == "dark")
        # DWMWA_USE_IMMERSIVE_DARK_MODE: opt the native frame into dark mode.
        # https://learn.microsoft.com/windows/apps/desktop/modernize/ui/apply-windows-themes
        if set_attribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)) != 0:
            return False
        user = ctypes.WinDLL("user32", use_last_error=True)
        set_position = user.SetWindowPos
        set_position.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        set_position.restype = wintypes.BOOL
        # Refresh the frame without resizing, moving, raising or activating it.
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        set_position(hwnd, None, 0, 0, 0, 0, 0x0037)
        return True
    except (AttributeError, OSError, ValueError, tk.TclError):
        # Native decoration is optional on older Windows/Tk builds. A failed
        # appearance request must not interrupt a DICOM operation or startup.
        return False


def style_window_chrome(window: tk.Misc) -> None:
    """Refresh mapped windows and register newly created/withdrawn dialogs."""
    if sys.platform != "win32":
        return
    if not getattr(window, "_windows_titlebar_binding", None):
        def mapped(event: tk.Event) -> None:
            # Toplevel bindings also receive their children's map events.
            if event.widget is window:
                _apply_windows_titlebar(window)

        window._windows_titlebar_binding = window.bind("<Map>", mapped, add="+")
    _apply_windows_titlebar(window)
