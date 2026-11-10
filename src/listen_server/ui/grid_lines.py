from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from listen_server.ui.theme import HEADING_BORDER

GRID_COLOR = "#d4d4d4"

FORWARDED_EVENTS = (
    "<Button-1>", "<Double-1>", "<Button-3>", "<MouseWheel>",
    "<B1-Motion>", "<ButtonRelease-1>", "<Motion>",
)


def forward_events(overlay: tk.Widget, tree: ttk.Treeview) -> None:
    """把叠加在表格上的控件收到的鼠标事件转交给表格。"""

    def forward(event: tk.Event, sequence: str) -> None:
        options = {
            "x": event.x + overlay.winfo_x(),
            "y": event.y + overlay.winfo_y(),
            "rootx": event.x_root,
            "rooty": event.y_root,
        }
        if sequence == "<MouseWheel>":
            options["delta"] = event.delta
        if sequence == "<Button-1>":
            tree.focus_set()
        tree.event_generate(sequence, **options)

    for sequence in FORWARDED_EVENTS:
        overlay.bind(sequence, lambda e, s=sequence: forward(e, s))


def heading_height(tree: ttk.Treeview) -> int:
    """表头底部的 y 坐标（不含），没有表头时返回 0。"""
    if "headings" not in str(tree.cget("show")):
        return 0
    bottom = 0
    for y in range(200):
        if tree.identify_region(20, y) in ("heading", "separator"):
            bottom = y + 1
        elif bottom:
            break
    return bottom


class HeadingLabels:
    """在指定列的表头上叠加可多行显示的标签（ttk 表头文字只绘制一行）。"""

    def __init__(self, tree: ttk.Treeview, columns: tuple[str, ...], background: str) -> None:
        self.tree = tree
        self.columns = columns
        self.labels: dict[str, tk.Label] = {}
        for col in columns:
            label = tk.Label(tree, background=background, font="TkHeadingFont",
                             justify="center", borderwidth=0, padx=0, pady=0)
            forward_events(label, tree)
            self.labels[col] = label
        self._pending = False
        for sequence in ("<Configure>", "<B1-Motion>", "<ButtonRelease-1>"):
            tree.bind(sequence, self.schedule, add="+")

    def set_text(self, col: str, text: str) -> None:
        self.labels[col].configure(text=text)

    def set_background(self, color: str) -> None:
        for label in self.labels.values():
            label.configure(background=color)

    def schedule(self, *_: object) -> None:
        if not self._pending:
            self._pending = True
            self.tree.after_idle(self.redraw)

    def redraw(self) -> None:
        self._pending = False
        tree = self.tree
        if not tree.winfo_exists():
            return
        head = heading_height(tree)
        columns = tree["displaycolumns"]
        if columns in ("#all", ("#all",)):
            columns = tree["columns"]
        offset = int(sum(int(tree.column(c, "width")) for c in columns) * float(tree.xview()[0]))
        x = -offset
        for col in columns:
            width = int(tree.column(col, "width"))
            label = self.labels.get(col)
            if label is not None:
                if head and x + width > 0 and x < tree.winfo_width():
                    label.place(x=x + 1, y=2, width=max(1, width - 2), height=max(1, head - 4))
                else:
                    label.place_forget()
            x += width


class TreeGridLines:
    """在 ttk.Treeview 上叠加 1px 线条作为表头和表格的横向、竖向内边框（ttk 原生不支持）。"""

    def __init__(self, tree: ttk.Treeview, color: str = GRID_COLOR,
                 heading_color: str = HEADING_BORDER) -> None:
        self.tree = tree
        self.color = color
        self.heading_color = heading_color
        self._rows: list[tk.Frame] = []
        self._columns: list[tk.Frame] = []
        self._heading_columns: list[tk.Frame] = []
        self._heading_line: tk.Frame | None = None
        self._pending = False
        for sequence in ("<Configure>", "<KeyRelease>", "<B1-Motion>", "<ButtonRelease-1>"):
            tree.bind(sequence, self.schedule, add="+")

    def wrap_scroll(self, setter: Callable[[str, str], None]) -> Callable[[str, str], None]:
        def wrapped(first: str, last: str) -> None:
            setter(first, last)
            self.schedule()

        return wrapped

    def schedule(self, *_: object) -> None:
        if not self._pending:
            self._pending = True
            self.tree.after_idle(self.redraw)

    def _new_line(self, color: str) -> tk.Frame:
        line = tk.Frame(self.tree, background=color, borderwidth=0, highlightthickness=0)
        forward_events(line, self.tree)
        return line

    def redraw(self) -> None:
        self._pending = False
        tree = self.tree
        if not tree.winfo_exists():
            return

        head = heading_height(tree)
        if self._heading_line is None:
            self._heading_line = self._new_line(self.heading_color)
        if head:
            self._heading_line.place(x=0, y=head - 1, relwidth=1, height=1)
        else:
            self._heading_line.place_forget()

        height = tree.winfo_height()
        width = tree.winfo_width()
        columns = tree["displaycolumns"]
        if columns in ("#all", ("#all",)):
            columns = tree["columns"]
        widths = [int(tree.column(c, "width")) for c in columns]
        x = -int(sum(widths) * float(tree.xview()[0]))
        used_body = used_head = 0
        for w in widths[:-1]:
            x += w
            if not 0 < x < width:
                continue
            if head:
                while len(self._heading_columns) <= used_head:
                    self._heading_columns.append(self._new_line(self.heading_color))
                self._heading_columns[used_head].place(x=x - 1, y=0, width=1, height=head)
                used_head += 1
            while len(self._columns) <= used_body:
                self._columns.append(self._new_line(self.color))
            self._columns[used_body].place(x=x - 1, y=head, width=1, height=max(1, height - head))
            used_body += 1
        for line in self._heading_columns[used_head:]:
            line.place_forget()
        for line in self._columns[used_body:]:
            line.place_forget()

        used = 0
        for iid in tree.get_children():
            box = tree.bbox(iid)
            if not box:
                continue
            bottom = box[1] + box[3] - 1
            if head <= bottom < height:
                while len(self._rows) <= used:
                    self._rows.append(self._new_line(self.color))
                self._rows[used].place(x=0, y=bottom, relwidth=1, height=1)
                used += 1
        for line in self._rows[used:]:
            line.place_forget()
