from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from listen_server.ui.theme import (
    ROW_EVEN_BG,
    ROW_HOVER_BG,
    ROW_HOVER_FG,
    ROW_ODD_BG,
    TEXT,
    TOOLTIP_BG,
    TOOLTIP_BORDER,
)

HOVER_TAG = "hover"
EVEN_TAG = "even"
ODD_TAG = "odd"


class Tooltip:
    def __init__(self, master: tk.Misc, wraplength: int) -> None:
        self.master = master
        self.wraplength = wraplength
        self.win: tk.Toplevel | None = None
        self.label: tk.Label | None = None

    def show(self, text: str, x_root: int, y_root: int) -> None:
        if self.win is None:
            self.win = tk.Toplevel(self.master)
            self.win.withdraw()
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.label = tk.Label(
                self.win, justify="left", background=TOOLTIP_BG, foreground=TEXT,
                font="TkDefaultFont", wraplength=self.wraplength, padx=8, pady=4,
                highlightthickness=1, highlightbackground=TOOLTIP_BORDER, borderwidth=0,
            )
            self.label.pack()
        self.label.configure(text=text)
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = min(x_root + 14, self.win.winfo_screenwidth() - w - 4)
        y = y_root + 20
        if y + h > self.win.winfo_screenheight():
            y = y_root - h - 8
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.deiconify()
        self.win.lift()

    def hide(self) -> None:
        if self.win is not None:
            self.win.withdraw()


class TreeEffects:
    """Treeview 的斑马纹、行悬浮高亮、单元格悬浮提示。

    ttk 中先创建的 tag 优先级更高，因此必须在其他 tag_configure 之前实例化。
    """

    def __init__(self, tree: ttk.Treeview, tooltip_delay: int = 500, wraplength: int = 600) -> None:
        self.tree = tree
        self.tooltip_delay = tooltip_delay
        self.tooltip = Tooltip(tree, wraplength)
        self._hover_row: str | None = None
        self._cell: tuple[str, str] | None = None
        self._after_id: str | None = None
        self.on_hover_change: Callable[[], None] | None = None

        tree.tag_configure(HOVER_TAG, background=ROW_HOVER_BG, foreground=ROW_HOVER_FG)
        tree.tag_configure(ODD_TAG, background=ROW_ODD_BG)
        tree.tag_configure(EVEN_TAG, background=ROW_EVEN_BG)

        tree.bind("<Motion>", self._on_motion, add="+")
        tree.bind("<Leave>", self._on_leave, add="+")
        tree.bind("<ButtonPress>", lambda _: self._reset_tooltip(), add="+")
        tree.bind("<MouseWheel>", self._on_scroll, add="+")
        tree.bind("<KeyPress>", lambda _: self._reset_tooltip(), add="+")

    @property
    def hover_row(self) -> str | None:
        return self._hover_row

    @staticmethod
    def zebra_tag(index: int) -> str:
        return ODD_TAG if index % 2 else EVEN_TAG

    def reapply(self) -> None:
        """表格重新设置 tags 后调用，恢复悬浮高亮。"""
        row = self._hover_row
        if row and self.tree.exists(row):
            self._add_tag(row)
        else:
            self._hover_row = None

    # ---------- 行悬浮 ----------

    def _add_tag(self, iid: str) -> None:
        tags = tuple(self.tree.item(iid, "tags"))
        if HOVER_TAG not in tags:
            self.tree.item(iid, tags=(*tags, HOVER_TAG))

    def _remove_tag(self, iid: str) -> None:
        if self.tree.exists(iid):
            tags = tuple(t for t in self.tree.item(iid, "tags") if t != HOVER_TAG)
            self.tree.item(iid, tags=tags)

    def _set_hover(self, row: str | None) -> None:
        if row == self._hover_row:
            return
        if self._hover_row:
            self._remove_tag(self._hover_row)
        self._hover_row = row
        if row:
            self._add_tag(row)
        if self.on_hover_change is not None:
            self.on_hover_change()

    # ---------- 事件 ----------

    def _locate(self, x: int, y: int) -> tuple[str | None, str | None]:
        if self.tree.identify_region(x, y) not in ("cell", "tree"):
            return None, None
        return self.tree.identify_row(y) or None, self.tree.identify_column(x) or None

    def _on_motion(self, event: tk.Event) -> None:
        self._update(event.x, event.y)

    def _update(self, x: int, y: int) -> None:
        row, col = self._locate(x, y)
        self._set_hover(row)
        cell = (row, col) if row and col else None
        if cell != self._cell:
            self._reset_tooltip()
            self._cell = cell
            if cell:
                self._after_id = self.tree.after(self.tooltip_delay, self._show_tooltip)

    def _pointer_inside(self) -> bool:
        tree = self.tree
        px, py = tree.winfo_pointerxy()
        x, y = px - tree.winfo_rootx(), py - tree.winfo_rooty()
        return 0 <= x < tree.winfo_width() and 0 <= y < tree.winfo_height()

    def _on_leave(self, _event: tk.Event) -> None:
        # 鼠标移到叠加的表格线上也会触发 Leave，此时仍视为在表格内
        if self._pointer_inside():
            return
        self._set_hover(None)
        self._reset_tooltip()
        self._cell = None

    def _on_scroll(self, _event: tk.Event) -> None:
        self._reset_tooltip()
        self._cell = None

        def refresh() -> None:
            tree = self.tree
            px, py = tree.winfo_pointerxy()
            self._update(px - tree.winfo_rootx(), py - tree.winfo_rooty())

        self.tree.after(30, refresh)

    # ---------- 悬浮提示 ----------

    def _reset_tooltip(self) -> None:
        if self._after_id:
            self.tree.after_cancel(self._after_id)
            self._after_id = None
        self.tooltip.hide()

    def _show_tooltip(self) -> None:
        self._after_id = None
        if not self._cell:
            return
        row, col = self._cell
        if not self.tree.exists(row):
            return
        text = str(self.tree.set(row, col)).strip()
        if not text or text == "-":
            return
        self.tooltip.show(text, *self.tree.winfo_pointerxy())
