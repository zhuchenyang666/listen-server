"""模态子窗口：阻塞父窗口操作，并始终显示在父窗口所在的显示器上。"""

from __future__ import annotations

import re
import sys
import tkinter as tk


def monitor_work_area(widget: tk.Misc) -> tuple[int, int, int, int]:
    """返回 widget 所在显示器的工作区 (left, top, right, bottom)。"""
    fallback = (0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight())
    if sys.platform != "win32":
        return fallback
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        hmon = user32.MonitorFromWindow(wintypes.HWND(widget.winfo_id()), 2)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return fallback
        rc = info.rcWork
        return rc.left, rc.top, rc.right, rc.bottom
    except (AttributeError, OSError):
        return fallback


def place_over(win: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    """把 win 居中放在 parent 上方，并限制在 parent 所在显示器的工作区内。"""
    parent = parent.winfo_toplevel()
    parent.update_idletasks()
    left, top, right, bottom = monitor_work_area(parent)
    margin = 8
    title_bar = 40
    width = min(width, right - left - 2 * margin)
    height = min(height, bottom - top - title_bar - 2 * margin)
    cx = parent.winfo_rootx() + parent.winfo_width() // 2
    cy = parent.winfo_rooty() + parent.winfo_height() // 2
    x = min(max(cx - width // 2, left + margin), right - width - margin)
    y = min(max(cy - height // 2, top + margin), bottom - height - title_bar - margin)
    win.geometry(f"{width}x{height}+{x}+{y}")


SHAKE_OFFSETS = (2, -2, 1, -1, 0)
SHAKE_STEP_MS = 50


def shake(win: tk.Toplevel) -> None:
    """提示音 + 左右抖动，提醒用户先处理当前模态窗口。"""
    if getattr(win, "_shaking", False):
        return
    match = re.match(r"\d+x\d+\+(-?\d+)\+(-?\d+)", win.wm_geometry())
    if not match:
        return
    x, y = int(match.group(1)), int(match.group(2))
    scale = float(win.tk.call("tk", "scaling")) * 72 / 96
    win._shaking = True
    win.bell()
    win.lift()
    win.focus_force()

    def step(i: int = 0) -> None:
        if not win.winfo_exists():
            return
        if i >= len(SHAKE_OFFSETS):
            win._shaking = False
            return
        win.wm_geometry(f"+{x + round(SHAKE_OFFSETS[i] * scale)}+{y}")
        win.after(SHAKE_STEP_MS, step, i + 1)

    step()


def _on_button(win: tk.Toplevel, event: tk.Event) -> None:
    # 模态期间，点击主窗口等 grab 范围外的位置会被 Tk 转交给 win，坐标落在 win 之外
    left, top = win.winfo_rootx(), win.winfo_rooty()
    inside = left <= event.x_root < left + win.winfo_width() and top <= event.y_root < top + win.winfo_height()
    if event.widget is win and not inside:
        shake(win)


def show_modal(win: tk.Toplevel, parent: tk.Misc) -> None:
    """显示模态窗口并阻塞，直到 win 被关闭；关闭后把输入焦点还给原来的模态窗口。"""
    previous = win.grab_current()
    win.transient(parent.winfo_toplevel())
    win.bind("<ButtonPress>", lambda e: _on_button(win, e), add="+")
    win.deiconify()
    win.lift()
    win.wait_visibility()
    win.grab_set()
    win.focus_set()
    win.wait_window()
    if previous is not None and previous.winfo_exists():
        previous.grab_set()
        previous.focus_set()
