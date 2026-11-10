from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

UI_FONT = "Microsoft YaHei UI" if sys.platform == "win32" else "TkDefaultFont"
MONO_FONT = "Consolas" if sys.platform == "win32" else "TkFixedFont"

BORDER = "#7a7a7a"
BORDER_FOCUS = "#0067c0"
FIELD_BG = "#ffffff"
FIELD_READONLY_BG = "#f5f5f5"
TEXT = "#1a1a1a"
PLACEHOLDER = "#9a9a9a"
HINT = "#6b6b6b"

ROW_EVEN_BG = "#ffffff"
ROW_ODD_BG = "#f4f6f9"
ROW_HOVER_BG = "#4ca3e8"
ROW_HOVER_FG = "#ffffff"
ROW_SELECTED_BG = "#2a83cf"  # 比悬浮色更深
ROW_SELECTED_FG = "#ffffff"

HEADING_BG = "#dde3ea"
HEADING_ACTIVE_BG = "#cfd7e1"
HEADING_BORDER = "#a9b3bf"

TOOLTIP_BG = "#ffffff"
TOOLTIP_BORDER = "#8a8a8a"


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def cursor_monitor(root: tk.Misc) -> tuple[int, tuple[int, int, int, int]]:
    """返回鼠标所在显示器的 DPI 和工作区 (left, top, right, bottom)。"""
    fallback = (round(root.winfo_fpixels("1i")),
                (0, 0, root.winfo_screenwidth(), root.winfo_screenheight()))
    if sys.platform != "win32":
        return fallback
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    try:
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        rc = info.rcWork
        return dpi_x.value or 96, (rc.left, rc.top, rc.right, rc.bottom)
    except (AttributeError, OSError):
        return fallback


def window_dpi(widget: tk.Misc) -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        return ctypes.windll.user32.GetDpiForWindow(widget.winfo_id()) or None
    except (AttributeError, OSError):
        return None


def _setup_heading(style: ttk.Style, scale: float, line: int) -> None:
    # vista 主题的表头由系统绘制、无法改背景色，换用 clam 的表头元素
    if "Flat.Treeheading.cell" not in style.element_names():
        style.element_create("Flat.Treeheading.cell", "from", "clam", "Treeheading.cell")
        style.element_create("Flat.Treeheading.border", "from", "clam", "Treeheading.border")
    style.layout("Treeview.Heading", [
        ("Flat.Treeheading.cell", {"sticky": "nswe"}),
        ("Flat.Treeheading.border", {"sticky": "nswe", "children": [
            ("Treeheading.padding", {"sticky": "nswe", "children": [
                ("Treeheading.image", {"side": "right", "sticky": ""}),
                ("Treeheading.text", {"sticky": "we"}),
            ]}),
        ]}),
    ])
    style.configure(
        "Treeview.Heading", font="TkHeadingFont", foreground=TEXT,
        background=HEADING_BG, bordercolor=HEADING_BORDER,
        lightcolor=HEADING_BG, darkcolor=HEADING_BORDER,
        # 表头高度不会随多行文字自动增加，额外留出半行上下边距以容纳「8% / CPU」两行
        relief="flat", borderwidth=1, padding=(0, int(4 * scale) + line // 2),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", HEADING_ACTIVE_BG), ("pressed", HEADING_ACTIVE_BG)],
        lightcolor=[("active", HEADING_ACTIVE_BG)],
    )


def setup_theme(root: tk.Tk, font_size: int, dpi: int) -> None:
    """按指定 DPI 设置字体与控件尺寸；窗口移到不同缩放比例的显示器时可再次调用。"""
    root.tk.call("tk", "scaling", dpi / 72)

    style = ttk.Style(root)
    if "vista" in style.theme_names() and style.theme_use() != "vista":
        style.theme_use("vista")

    if sys.platform == "win32":
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkCaptionFont"):
            tkfont.nametofont(name).configure(family=UI_FONT, size=font_size)
        tkfont.nametofont("TkHeadingFont").configure(family=UI_FONT, size=font_size, weight="bold")
        tkfont.nametofont("TkFixedFont").configure(family=MONO_FONT, size=font_size)
    else:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
            tkfont.nametofont(name).configure(size=font_size)

    scale = dpi / 96
    line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    style.configure("Treeview", rowheight=line + int(12 * scale), foreground=TEXT)
    style.map(
        "Treeview",
        background=[("selected", ROW_SELECTED_BG)],
        foreground=[("selected", ROW_SELECTED_FG)],
    )
    _setup_heading(style, scale, line)
    style.configure("TButton", width=-6, padding=(int(10 * scale), int(4 * scale)))
    style.configure("Accent.TButton", font=(UI_FONT, font_size, "bold"))
    style.configure("TLabelframe.Label", font="TkDefaultFont")
    style.configure("Hint.TLabel", foreground=HINT)
    root.option_add("*Menu.font", "TkMenuFont")
    root.option_add("*TCombobox*Listbox.font", "TkTextFont")


class SolidEntry(tk.Frame):
    """1px 实线边框输入框，获得焦点时边框变为蓝色，可选占位提示文字。"""

    def __init__(self, master: tk.Misc, textvariable: tk.StringVar, *, width: int = 20,
                 readonly: bool = False, pad: tuple[int, int] = (6, 3),
                 placeholder: str = "") -> None:
        bg = FIELD_READONLY_BG if readonly else FIELD_BG
        super().__init__(master, background=bg, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        self.variable = textvariable
        self.entry = tk.Entry(
            self, textvariable=textvariable, width=width, relief="flat", borderwidth=0,
            highlightthickness=0, font="TkTextFont", foreground=TEXT,
            background=bg, readonlybackground=FIELD_READONLY_BG,
            insertwidth=1, state="readonly" if readonly else "normal",
        )
        self.entry.pack(fill="both", expand=True, padx=pad[0], pady=pad[1])
        self.entry.bind("<FocusIn>", lambda _: self._set_border(BORDER_FOCUS), add="+")
        self.entry.bind("<FocusOut>", lambda _: self._set_border(BORDER), add="+")

        self.placeholder: tk.Label | None = None
        if placeholder:
            self.set_placeholder(placeholder)

    def set_placeholder(self, text: str) -> None:
        if self.placeholder is None:
            self.placeholder = tk.Label(self, font="TkTextFont", foreground=PLACEHOLDER,
                                        background=self.entry.cget("background"),
                                        borderwidth=0, padx=0, pady=0, cursor="xterm")
            self.placeholder.bind("<Button-1>", lambda _: self.entry.focus_set())
            self.variable.trace_add("write", lambda *_: self._update_placeholder())
        self.placeholder.configure(text=text)
        self.after_idle(self._update_placeholder)

    def _update_placeholder(self) -> None:
        if self.placeholder is None or not self.winfo_exists():
            return
        if self.variable.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.entry, x=1, rely=0.5, anchor="w")

    def _set_border(self, color: str) -> None:
        self.configure(highlightbackground=color, highlightcolor=color)

    def focus_set(self) -> None:
        self.entry.focus_set()

    def set_state(self, enabled: bool) -> None:
        self.entry.configure(state="normal" if enabled else "readonly")


class SolidText(tk.Frame):
    """1px 实线边框多行文本框。"""

    def __init__(self, master: tk.Misc, *, height: int = 3, pad: tuple[int, int] = (6, 3)) -> None:
        super().__init__(master, background=FIELD_BG, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        self.text = tk.Text(self, height=height, relief="flat", borderwidth=0, highlightthickness=0,
                            font="TkTextFont", foreground=TEXT, background=FIELD_BG,
                            insertwidth=1, wrap="none", undo=True)
        self.text.pack(fill="both", expand=True, padx=pad[0], pady=pad[1])
        self.text.bind("<FocusIn>", lambda _: self._set_border(BORDER_FOCUS), add="+")
        self.text.bind("<FocusOut>", lambda _: self._set_border(BORDER), add="+")

    def _set_border(self, color: str) -> None:
        self.configure(highlightbackground=color, highlightcolor=color)

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set(self, value: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
