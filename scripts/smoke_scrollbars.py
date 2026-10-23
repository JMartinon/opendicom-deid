"""Exercise actual Tk scrolling in both palettes; use a desktop or xvfb-run."""

from __future__ import annotations

import tkinter as tk

from opendicom_deid.gui.theme import PALETTES, apply_theme
from opendicom_deid.gui.widgets import ThemedScrolledText


def check_scrollbars(root: tk.Tk) -> None:
    """Verify standard scrolling, resizing, auto-hide, and read-only content."""
    original = getattr(root, "_theme_mode", "light")
    window = tk.Toplevel(root)
    window.title("Synthetic scrollbar check")
    window.geometry("460x280")
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    text = ThemedScrolledText(window, height=5, width=40, wrap="word")
    text.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
    short = "A short synthetic report."
    long = "\n".join(f"Synthetic finding {number:03d}." for number in range(100))

    def replace(content: str) -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", content)
        text.yview_moveto(0)
        text.configure(state="disabled")
        root.update()

    try:
        for mode in ("light", "dark"):
            apply_theme(root, mode)
            replace(short)
            assert not text.vbar.winfo_ismapped(), "A report that fits should have no scrollbar."
            full_width = text.winfo_width()
            replace(long)
            assert text.vbar.winfo_ismapped(), "An overflowing report needs a scrollbar."
            assert text.vbar.winfo_width() >= 12, "The thumb needs a usable pointer target."
            assert text.winfo_width() < full_width
            assert text.cget("background") == PALETTES[mode].surface
            assert 0 <= text.yview()[0] < text.yview()[1] < 1

            # Drag the real ttk thumb; no replacement pointer handlers are used.
            x = text.vbar.winfo_width() // 2
            thumb = [
                y for y in range(text.vbar.winfo_height()) if "thumb" in text.vbar.identify(x, y)
            ]
            assert thumb, "An overflowing report must have a visible native thumb."
            start = (thumb[0] + thumb[-1]) // 2
            end = text.vbar.winfo_height() // 2
            text.vbar.event_generate("<ButtonPress-1>", x=x, y=start)
            text.vbar.event_generate("<B1-Motion>", x=x, y=end)
            text.vbar.event_generate("<ButtonRelease-1>", x=x, y=end)
            root.update()
            assert text.yview()[0] > 0, "Dragging the native thumb must move the report."

            # Exercise the usual scrollbar command for page movement and end.
            command = text.vbar.cget("command")
            root.tk.call(command, "moveto", 1)
            root.update()
            assert text.yview()[1] == 1
            assert text.vbar.winfo_ismapped(), "The bar stays present at the bottom."
            root.tk.call(command, "scroll", -1, "pages")
            root.update()
            assert text.yview()[1] < 1
            assert text.get("1.0", "end-1c") == long
            assert str(text.cget("state")) == "disabled"

            # Shrinking content removes both the track and the unused gutter.
            replace(short)
            assert not text.vbar.winfo_ismapped()
            assert text.winfo_width() == full_width
            window.geometry("600x360")
            root.update()
            assert not text.vbar.winfo_ismapped()
            window.geometry("460x280")
            root.update()
    finally:
        window.destroy()
        apply_theme(root, original)
    print("GUI: native scrollbar drag, page scrolling, auto-hide, and light/dark palettes passed.")


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    try:
        check_scrollbars(root)
    finally:
        root.destroy()
