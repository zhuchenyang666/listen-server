from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from listen_server import autostart, store
from listen_server.config import FONT_SIZE, REFRESH_INTERVAL_MS, STOP_TIMEOUT
from listen_server.manager import ServiceError, ServiceManager, log_file
from listen_server.models import ServiceStatus, State
from listen_server.ui.grid_lines import HeadingLabels, TreeGridLines
from listen_server.ui.icon import apply_icon, set_app_id
from listen_server.ui.status_dots import DOT_LEADER, StatusDots
from listen_server.ui.table_effects import TreeEffects
from listen_server.ui.theme import (
    HEADING_BG,
    SolidEntry,
    cursor_monitor,
    enable_dpi_awareness,
    setup_theme,
    window_dpi,
)

# (列 id, 标题, 宽度, 对齐, 是否拉伸)
COLUMNS = (
    ("seq", "序号", 46, "center", False),
    ("state", "状态", 88, "w", False),
    ("name", "服务名", 150, "w", False),
    ("type", "类型", 64, "center", False),
    ("port", "端口", 96, "center", False),
    ("pid", "PID", 64, "center", False),
    ("cpu", "CPU", 62, "e", False),
    ("mem", "内存", 62, "e", False),
    ("disk", "磁盘", 62, "e", False),
    ("uptime", "运行时长", 86, "center", False),
    ("description", "备注", 120, "w", False),
    ("cwd", "目录", 200, "w", True),
)
USAGE_COLUMNS = ("cpu", "mem", "disk")

STATE_ORDER = {s: i for i, s in enumerate(State)}
DOT_RUNNING = "#16a34a"
DOT_STOPPED = "#dc2626"
UNREGISTERED_FG = "#5a5a5a"


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 0.05:
        return "0%"
    if value < 10:
        return f"{value:.1f}%"
    return f"{value:.0f}%"


def format_uptime(started_at: float | None) -> str:
    if not started_at:
        return "-"
    seconds = max(0, int(time.time() - started_at))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def open_path(path: str | Path) -> None:
    path = str(path)
    if sys.platform == "win32":
        try:
            os.startfile(path)
        except OSError:
            if os.path.isfile(path):
                subprocess.Popen(["notepad.exe", path])
            else:
                raise
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class MainWindow:
    def __init__(self, root: tk.Tk, dpi: int) -> None:
        self.root = root
        self.dpi = dpi
        self.scale = dpi / 96
        self.manager = ServiceManager()
        self.errors: list[str] = []
        self.font_factor = FONT_SIZE / 9
        self.statuses: dict[str, ServiceStatus] = {}
        self.busy: set[str] = set()
        self.detail_windows: dict[str, tk.Toplevel] = {}
        self.sort_col = "port"
        self.sort_reverse = False
        self.last_refresh: float | None = None
        self.message = ""

        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._refreshing = False
        self._refresh_pending = False
        self._autostart_synced = False

        self.show_unregistered = tk.BooleanVar(value=False)
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._render())

        self._build()
        self.root.after(100, self._process_queue)
        self.refresh()
        self._schedule_auto_refresh()

    def px(self, value: int) -> int:
        return int(value * self.scale)

    def text_px(self, value: int) -> int:
        """随字号放大的像素宽度，用于列宽和窗口尺寸。"""
        return int(value * self.scale * self.font_factor)

    # ---------- 界面构建 ----------

    def _build(self) -> None:
        bar = ttk.Frame(self.root, padding=(self.px(8), self.px(6), self.px(8), self.px(4)))
        bar.pack(fill="x")

        def button(text: str, command: Callable[[], None], style: str = "TButton") -> ttk.Button:
            btn = ttk.Button(bar, text=text, command=command, style=style)
            btn.pack(side="left", padx=(0, self.px(4)))
            return btn

        def separator() -> None:
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=self.px(6))

        button("＋ 添加", self.add_service, style="Accent.TButton")
        separator()
        self.btn_start = button("▶ 启动", self.start_selected)
        self.btn_stop = button("■ 停止", self.stop_selected)
        self.btn_restart = button("⟳ 重启", self.restart_selected)
        separator()
        self.btn_log = button("日志", self.open_log_selected)
        self.btn_dir = button("目录", self.open_dir_selected)
        separator()
        button("↻ 刷新", self.refresh)

        search_bar = ttk.Frame(self.root, padding=(self.px(8), 0, self.px(8), self.px(6)))
        search_bar.pack(fill="x")
        ttk.Checkbutton(
            search_bar, text="显示未登记端口", variable=self.show_unregistered, command=self.refresh
        ).pack(side="right", padx=(self.px(12), 0))
        search_entry = SolidEntry(search_bar, self.search, pad=(self.px(6), self.px(3)),
                                  placeholder="搜索任意字段：服务名 / 端口 / PID / 类型 / 状态 / 备注 / 目录")
        search_entry.pack(side="left", fill="x", expand=True)

        table = ttk.Frame(self.root, padding=(self.px(8), 0))
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended"
        )
        for col, _, width, anchor, stretch in COLUMNS:
            if col != "seq":
                self.tree.heading(col, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=self.text_px(width), minwidth=self.text_px(40),
                             anchor=anchor, stretch=stretch)
        self.heading_labels = HeadingLabels(self.tree, USAGE_COLUMNS, HEADING_BG)
        self._update_headings()
        self.effects = TreeEffects(self.tree, wraplength=self.text_px(480))
        self.tree.tag_configure(State.UNREGISTERED.name, foreground=UNREGISTERED_FG)

        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.grid_lines = TreeGridLines(self.tree)
        self.status_dots = StatusDots(self.tree, "state", self.effects, self._dot_color)

        def on_scroll(setter: Callable[[str, str], None]) -> Callable[[str, str], None]:
            def wrapped(first: str, last: str) -> None:
                setter(first, last)
                self.grid_lines.schedule()
                self.status_dots.schedule()
                self.heading_labels.schedule()

            return wrapped

        self.tree.configure(yscrollcommand=on_scroll(vsb.set), xscrollcommand=on_scroll(hsb.set))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", lambda _: self._update_buttons())
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda _: self.stop_selected())
        self.root.bind("<Configure>", self._check_dpi, add="+")
        self.root.bind("<F5>", lambda _: self.refresh())
        self.root.bind("<Control-f>", lambda _: search_entry.focus_set())

        status = ttk.Frame(self.root, padding=(self.px(8), self.px(4)))
        status.pack(fill="x")
        self.status_label = ttk.Label(status, text="正在扫描…")
        self.status_label.pack(side="left")
        self.error_label = ttk.Label(status, text="", foreground="#b00020", cursor="hand2")
        self.error_label.pack(side="right")
        self.error_label.bind("<Button-1>", lambda _: self.show_errors())

        self.menu = tk.Menu(self.root, tearoff=0)

    def _check_dpi(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        dpi = window_dpi(self.root)
        if not dpi or dpi == self.dpi:
            return
        self.dpi = dpi
        self.scale = dpi / 96
        setup_theme(self.root, FONT_SIZE, dpi)
        for col, _, width, *_ in COLUMNS:
            self.tree.column(col, width=self.text_px(width), minwidth=self.text_px(40))
        self.effects.tooltip.wraplength = self.text_px(480)
        self.grid_lines.schedule()
        self.status_dots.schedule()
        self.heading_labels.schedule()

    def _dot_color(self, iid: str) -> str | None:
        st = self.statuses.get(iid)
        if st is None or iid in self.busy:
            return None
        return DOT_RUNNING if st.state in (State.RUNNING, State.UNREGISTERED) else DOT_STOPPED

    def _update_headings(self) -> None:
        stats = self.manager.system_stats
        totals = {"cpu": stats.cpu, "mem": stats.mem, "disk": stats.disk}
        for col, title, *_ in COLUMNS:
            arrow = ""
            if col == self.sort_col:
                arrow = " ▼" if self.sort_reverse else " ▲"
            if col in USAGE_COLUMNS:
                self.tree.heading(col, text="")
                self.heading_labels.set_text(col, f"{totals[col]:.0f}%\n{title}{arrow}")
            else:
                self.tree.heading(col, text=title + arrow)

    # ---------- 刷新 ----------

    def _process_queue(self) -> None:
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _schedule_auto_refresh(self) -> None:
        def tick() -> None:
            self.refresh()
            self._schedule_auto_refresh()

        self.root.after(REFRESH_INTERVAL_MS, tick)

    def refresh(self) -> None:
        if self._refreshing:
            self._refresh_pending = True
            return
        self._refreshing = True
        show = self.show_unregistered.get()
        threading.Thread(target=self._refresh_worker, args=(show,), daemon=True).start()

    def _refresh_worker(self, show_unregistered: bool) -> None:
        try:
            services, errors = store.load_services()
            statuses = self.manager.collect(services, show_unregistered)
            result = (errors, statuses)
        except Exception as e:
            result = ([f"刷新失败：{type(e).__name__}: {e}"], None)
        self._ui_queue.put(lambda: self._on_refreshed(*result))

    def _on_refreshed(self, errors: list[str], statuses: list[ServiceStatus] | None) -> None:
        self._refreshing = False
        self.errors = errors
        if statuses is not None:
            self.statuses = {s.key: s for s in statuses}
            self.last_refresh = time.time()
            if not self._autostart_synced:
                self._autostart_synced = True
                self._sync_autostart()
            self._update_headings()
        self._render()
        for key, win in list(self.detail_windows.items()):
            win.update_status(self.statuses.get(key))
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()

    # ---------- 表格渲染 ----------

    def _match(self, st: ServiceStatus, keyword: str) -> bool:
        if not keyword:
            return True
        return any(keyword in str(cell).lower() for cell in self._row_values(st)[1:])

    def _sort_key(self, st: ServiceStatus):
        col = self.sort_col
        if col == "port":
            primary = st.port or 65536  # 没有端口的排在最后
        elif col == "pid":
            primary = st.pid or 0
        elif col == "state":
            primary = STATE_ORDER[st.state]
        elif col == "uptime":
            primary = -(st.started_at or 0)
        elif col in USAGE_COLUMNS:
            primary = getattr(st, col) or 0.0
        else:
            primary = str(getattr(st, col, "")).lower()
        return primary, st.port or 65536, st.name

    def _row_values(self, st: ServiceStatus, seq: int = 0) -> tuple:
        state = f"{DOT_LEADER}处理中…" if st.key in self.busy else f"{DOT_LEADER}{st.state}"
        uptime = format_uptime(st.started_at) if st.alive else "-"
        return (seq, state, st.name, st.type, st.ports_text, st.pid or "-",
                format_percent(st.cpu), format_percent(st.mem), format_percent(st.disk),
                uptime, st.description, st.cwd)

    def _render(self) -> None:
        keyword = self.search.get().strip().lower()
        matched = [s for s in self.statuses.values() if self._match(s, keyword)]
        rows = [
            st
            for group in (True, False)
            for st in sorted(
                (s for s in matched if s.registered is group),
                key=self._sort_key, reverse=self.sort_reverse,
            )
        ]
        existing = set(self.tree.get_children())
        wanted = set()
        for index, st in enumerate(rows):
            values = self._row_values(st, index + 1)
            tags = (st.state.name, TreeEffects.zebra_tag(index))
            if st.key in existing:
                self.tree.item(st.key, values=values, tags=tags)
            else:
                self.tree.insert("", "end", iid=st.key, values=values, tags=tags)
            self.tree.move(st.key, "", index)
            wanted.add(st.key)
        for iid in existing - wanted:
            self.tree.delete(iid)
        self.effects.reapply()
        self.grid_lines.schedule()
        self.status_dots.schedule()
        self._update_status_bar()
        self._update_buttons()

    def _sort_by(self, col: str) -> None:
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            # 占用类列默认从高到低，与任务管理器一致
            self.sort_col, self.sort_reverse = col, col in USAGE_COLUMNS
        self._update_headings()
        self._render()

    def _update_status_bar(self) -> None:
        registered = [s for s in self.statuses.values() if s.registered]
        running = sum(1 for s in registered if s.state is State.RUNNING)
        parts = [f"共 {len(registered)} 个服务，运行 {running} 个"]
        if self.show_unregistered.get():
            unregistered = len(self.statuses) - len(registered)
            parts.append(f"未登记端口 {unregistered} 个")
        if self.last_refresh:
            parts.append(f"上次刷新 {time.strftime('%H:%M:%S', time.localtime(self.last_refresh))}")
        if not registered:
            parts.append("尚未登记服务，点击「＋ 添加」注册一个服务")
        if self.message:
            parts.append(self.message)
        self.status_label.configure(text="  │  ".join(parts))
        self.error_label.configure(
            text=f"⚠ {len(self.errors)} 个问题（点击查看）" if self.errors else ""
        )

    def set_message(self, text: str) -> None:
        self.message = f"{time.strftime('%H:%M:%S')} {text}"
        self._update_status_bar()

    # ---------- 选择与按钮状态 ----------

    def selected(self) -> list[ServiceStatus]:
        return [self.statuses[i] for i in self.tree.selection() if i in self.statuses]

    def _can_start(self, st: ServiceStatus) -> bool:
        return st.registered and not st.alive and st.key not in self.busy

    def _can_stop(self, st: ServiceStatus) -> bool:
        return st.alive and st.key not in self.busy

    def _can_restart(self, st: ServiceStatus) -> bool:
        return st.registered and st.key not in self.busy

    def _update_buttons(self) -> None:
        sel = self.selected()
        single = sel[0] if len(sel) == 1 else None
        rules = (
            (self.btn_start, any(map(self._can_start, sel))),
            (self.btn_stop, any(map(self._can_stop, sel))),
            (self.btn_restart, any(map(self._can_restart, sel))),
            (self.btn_log, single is not None and single.registered),
            (self.btn_dir, single is not None and bool(single.cwd)),
        )
        for btn, enabled in rules:
            btn.state(["!disabled"] if enabled else ["disabled"])

    # ---------- 操作 ----------

    def _run_action(self, st: ServiceStatus, label: str, fn: Callable[[], None]) -> None:
        if st.key in self.busy:
            return
        self.busy.add(st.key)
        self._render()

        def worker() -> None:
            error = None
            try:
                fn()
            except ServiceError as e:
                error = str(e)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
            self._ui_queue.put(lambda: self._on_action_done(st, label, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_action_done(self, st: ServiceStatus, label: str, error: str | None) -> None:
        self.busy.discard(st.key)
        if error:
            self.set_message(f"{st.name} {label}失败")
            messagebox.showerror(f"{label}失败", f"{st.name}：{error}", parent=self.root)
        else:
            self.set_message(f"{st.name} {label}完成")
        self.refresh()

    def start(self, items: list[ServiceStatus]) -> None:
        for st in filter(self._can_start, items):
            self._run_action(st, "启动", lambda st=st: self.manager.start(st.config))

    def stop(self, items: list[ServiceStatus]) -> None:
        items = [s for s in items if self._can_stop(s)]
        if not items:
            return
        lines = "\n".join(f"  • {s.name}（端口 {s.ports_text}，PID {s.pid or '-'}）" for s in items)
        warning = ""
        if any(not s.registered for s in items):
            warning = "\n\n注意：包含未登记的进程，结束后无法通过 listen-server 再次启动。"
        if not messagebox.askyesno("确认停止", f"确定停止以下服务？\n\n{lines}{warning}",
                                   parent=self.root):
            return
        timeout = STOP_TIMEOUT
        for st in items:
            self._run_action(st, "停止", lambda st=st: self.manager.stop(st, timeout))

    def restart(self, items: list[ServiceStatus]) -> None:
        timeout = STOP_TIMEOUT
        for st in filter(self._can_restart, items):
            self._run_action(st, "重启", lambda st=st: self.manager.restart(st, timeout))

    # ---------- 登记服务 ----------

    def registered_names(self) -> set[str]:
        return {s.name for s in self.statuses.values() if s.registered}

    def add_service(self) -> None:
        from listen_server.ui.service_dialog import ServiceDialog

        ServiceDialog(self, parent=self.root).show()

    def edit_service(self, st: ServiceStatus, parent: tk.Misc | None = None) -> None:
        from listen_server.ui.service_dialog import ServiceDialog

        if st.config is None:
            return
        ServiceDialog(self, st.config.spec, parent=parent or self.root).show()

    def save_service(self, spec: dict, old_name: str | None, start: bool) -> str | None:
        """保存登记信息，返回错误信息；成功返回 None。"""
        try:
            config = store.spec_to_config(spec)
            store.upsert_spec(spec, old_name)
        except (OSError, ValueError) as e:
            return str(e)
        self._sync_autostart()
        self.set_message(f"已{'修改' if old_name else '添加'}服务 {config.name}")
        if start:
            st = ServiceStatus(key=f"svc:{config.name}", name=config.name, type=config.type or "-",
                               state=State.STOPPED, config=config)
            self._run_action(st, "启动", lambda: self.manager.start(config))
        else:
            self.refresh()
        return None

    def delete_service(self, st: ServiceStatus) -> None:
        if st.config is None:
            return
        note = "\n\n服务正在运行，删除登记不会停止它。" if st.alive else ""
        if not messagebox.askyesno("删除服务", f"确定删除服务「{st.name}」的登记？{note}",
                                   parent=self.root):
            return
        try:
            store.delete_spec(st.name)
        except (OSError, ValueError) as e:
            messagebox.showerror("删除失败", str(e), parent=self.root)
            return
        self.statuses.pop(st.key, None)
        self._sync_autostart()
        self.set_message(f"已删除服务 {st.name}")
        self.refresh()

    def _sync_autostart(self) -> None:
        try:
            autostart.sync(store.load_services()[0])
        except OSError as e:
            self.set_message(f"设置开机自启失败：{e}")

    def start_selected(self) -> None:
        self.start(self.selected())

    def stop_selected(self) -> None:
        self.stop(self.selected())

    def restart_selected(self) -> None:
        self.restart(self.selected())

    def _open(self, path: str | Path) -> None:
        try:
            open_path(path)
        except OSError as e:
            messagebox.showerror("打开失败", f"{path}\n\n{e}", parent=self.root)

    def open_log(self, st: ServiceStatus) -> None:
        path = log_file(st.name)
        if not path.exists():
            messagebox.showinfo("日志", f"{st.name} 还没有日志。\n只有通过 listen-server 启动的服务才会记录日志。",
                                parent=self.root)
            return
        self._open(path)

    def open_log_selected(self) -> None:
        sel = self.selected()
        if len(sel) == 1 and sel[0].registered:
            self.open_log(sel[0])

    def open_dir_selected(self) -> None:
        sel = self.selected()
        if len(sel) == 1 and sel[0].cwd:
            self._open(sel[0].cwd)

    def show_errors(self) -> None:
        if not self.errors:
            return
        text = "\n\n".join(self.errors[:30])
        if len(self.errors) > 30:
            text += f"\n\n…… 另有 {len(self.errors) - 30} 条"
        messagebox.showwarning("问题", text, parent=self.root)

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_message(f"已复制：{text}")

    # ---------- 鼠标事件 ----------

    def _on_double_click(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if iid in self.statuses:
            self.open_detail(iid)

    def open_detail(self, key: str) -> None:
        from listen_server.ui.detail_window import DetailWindow

        win = DetailWindow(self, key)
        self.detail_windows[key] = win
        win.show()

    def _on_right_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        sel = self.selected()
        if not sel:
            return
        st = sel[0]
        single = len(sel) == 1

        def state(enabled: bool) -> str:
            return "normal" if enabled else "disabled"

        m = self.menu
        m.delete(0, "end")
        m.add_command(label="启动", command=self.start_selected,
                      state=state(any(map(self._can_start, sel))))
        m.add_command(label="停止", command=self.stop_selected,
                      state=state(any(map(self._can_stop, sel))))
        m.add_command(label="重启", command=self.restart_selected,
                      state=state(any(map(self._can_restart, sel))))
        m.add_separator()
        editable = single and st.registered
        m.add_command(label="编辑", command=lambda: self.edit_service(st), state=state(editable))
        m.add_command(label="删除", command=lambda: self.delete_service(st), state=state(editable))
        m.add_separator()
        m.add_command(label="详情", command=lambda: self.open_detail(st.key), state=state(single))
        m.add_command(label="打开日志", command=lambda: self.open_log(st),
                      state=state(single and st.registered))
        m.add_command(label="打开目录", command=lambda: self._open(st.cwd),
                      state=state(single and bool(st.cwd)))
        if st.ports:
            for port in st.ports:
                url = f"http://localhost:{port}"
                m.add_command(label=f"在浏览器打开 {url}", command=lambda u=url: webbrowser.open(u),
                              state=state(single))
        else:
            m.add_command(label="在浏览器打开（未检测到监听端口）", state="disabled")
        m.add_separator()
        m.add_command(label="复制端口", command=lambda: self._copy(st.ports_text),
                      state=state(single and bool(st.ports)))
        m.add_command(label="复制 PID", command=lambda: self._copy(str(st.pid)),
                      state=state(single and st.pid is not None))
        m.add_command(label="复制启动命令", command=lambda: self._copy(st.command),
                      state=state(single and bool(st.command)))
        m.tk_popup(event.x_root, event.y_root)


def run() -> None:
    enable_dpi_awareness()
    set_app_id()
    root = tk.Tk()
    root.withdraw()
    root.title("listen-server")
    apply_icon(root)

    dpi, (left, top, right, bottom) = cursor_monitor(root)
    setup_theme(root, FONT_SIZE, dpi)
    scale = dpi / 96 * FONT_SIZE / 9
    width = min(int(1120 * scale), int((right - left) * 0.92))
    height = min(int(520 * scale), int((bottom - top) * 0.85))
    x = left + (right - left - width) // 2
    y = top + (bottom - top - height) // 3
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(min(width, int(640 * scale)), min(height, int(320 * scale)))

    def report_error(exc, val, tb) -> None:
        traceback.print_exception(exc, val, tb)
        messagebox.showerror("程序错误", f"{exc.__name__}: {val}", parent=root)

    root.report_callback_exception = report_error
    MainWindow(root, dpi)
    root.deiconify()
    root.mainloop()
