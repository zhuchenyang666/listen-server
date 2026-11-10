"""在状态列前叠加彩色圆点（ttk.Treeview 无法给单元格的一部分上色）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import ttk

from listen_server.ui.grid_lines import forward_events
from listen_server.ui.table_effects import ODD_TAG, TreeEffects
from listen_server.ui.theme import (
    ROW_EVEN_BG,
    ROW_HOVER_BG,
    ROW_ODD_BG,
    ROW_SELECTED_BG,
)

DOT_LEADER = "\u2003\u2003"  # 状态文字前留出圆点的位置（两个全角空格）


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def circle_image(master: tk.Misc, size: int, color: str, bg: str) -> tk.PhotoImage:
    """抗锯齿圆形：4x4 超采样计算覆盖率，与背景色混合。"""
    fg_rgb, bg_rgb = _hex(color), _hex(bg)
    img = tk.PhotoImage(master=master, width=size, height=size)
    r = size / 2
    samples = [(i + 0.5) / 4 for i in range(4)]
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            inside = sum(
                1 for sy in samples for sx in samples
                if (px + sx - r) ** 2 + (py + sy - r) ** 2 <= r * r
            )
            a = inside / 16
            r_, g_, b_ = (round(f * a + b * (1 - a)) for f, b in zip(fg_rgb, bg_rgb, strict=True))
            row.append(f"#{r_:02x}{g_:02x}{b_:02x}")
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    return img


class StatusDots:
    def __init__(self, tree: ttk.Treeview, column: str, effects: TreeEffects,
                 color_of: Callable[[str], str | None]) -> None:
        self.tree = tree
        self.column = column
        self.effects = effects
        self.color_of = color_of
        self._labels: list[tk.Label] = []
        self._images: dict[tuple[str, str, int], tk.PhotoImage] = {}
        self._pending = False
        tree.bind("<Configure>", self.schedule, add="+")
        tree.bind("<<TreeviewSelect>>", self.schedule, add="+")
        effects.on_hover_change = self.schedule

    def schedule(self, *_: object) -> None:
        if not self._pending:
            self._pending = True
            self.tree.after_idle(self.redraw)

    def _image(self, color: str, bg: str, size: int) -> tk.PhotoImage:
        key = (color, bg, size)
        if key not in self._images:
            self._images[key] = circle_image(self.tree, size, color, bg)
        return self._images[key]

    def _row_bg(self, iid: str) -> str:
        if iid in self.tree.selection():
            return ROW_SELECTED_BG
        if iid == self.effects.hover_row:
            return ROW_HOVER_BG
        return ROW_ODD_BG if ODD_TAG in self.tree.item(iid, "tags") else ROW_EVEN_BG

    def redraw(self) -> None:
        self._pending = False
        tree = self.tree
        if not tree.winfo_exists():
            return
        em = tkfont.nametofont("TkDefaultFont").measure("\u2003")
        size = max(6, round(em * 0.62))
        used = 0
        for iid in tree.get_children():
            color = self.color_of(iid)
            box = tree.bbox(iid, self.column)
            if not color or not box:
                continue
            x, y, w, h = box
            if w < em * 2:
                continue
            while len(self._labels) <= used:
                label = tk.Label(tree, borderwidth=0, highlightthickness=0, padx=0, pady=0)
                forward_events(label, tree)
                self._labels.append(label)
            label = self._labels[used]
            label.configure(image=self._image(color, self._row_bg(iid), size))
            label.place(x=x + round(em * 0.55), y=y + (h - size) // 2, width=size, height=size)
            used += 1
        for label in self._labels[used:]:
            label.place_forget()
