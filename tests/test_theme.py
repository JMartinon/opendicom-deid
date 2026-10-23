"""Both OS-selected palettes keep text and progress readable."""

from __future__ import annotations

import pytest

try:
    from opendicom_deid.gui import theme
except ModuleNotFoundError as error:
    if error.name not in {"tkinter", "_tkinter"}:
        raise
    pytest.skip("Theme tests require the optional Tcl/Tk GUI runtime.", allow_module_level=True)


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
    assert _contrast(palette.scroll, palette.surface) >= 3
