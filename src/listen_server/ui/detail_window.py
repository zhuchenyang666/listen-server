from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from listen_server.manager import log_file, read_log_tail
from listen_server.models import ServiceStatus
from listen_server.ui.modal import place_over, show_modal
from listen_server.ui.theme import SolidEntry

if TYPE_CHECKING:
    from listen_server.ui.main_window import MainWindow

FIELDS = (
    ("name", "服务名"),
    ("type", "类型"),
    ("state", "状态"),
    ("port", "端口"),
    ("bind", "监听地址"),
    ("url", "访问地址"),
    ("pid", "PID"),
    ("started", "启动时间"),
    ("mode", "启动方式"),
    ("cwd", "目录"),
    ("command", "命令"),
    ("env", "环境变量"),
    ("source", "登记来源"),
)

LOG_REFRESH_MS = 2000


class DetailWindow(tk.Toplevel):
    def __init__(self, main: MainWindow, key: str) -> None:
        super().__init__(main.root)
        self.withdraw()
        self.main = main
        self.key = key
        self.status: ServiceStatus | None = None
        self._last_log = None
        self._after_id: str | None = None

        px = main.px
        self.minsize(px(500), px(400))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _: self.close())

        info = ttk.Frame(self, padding=px(10))
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.StringVar] = {}
        for row, (field, label) in enumerate(FIELDS):
            ttk.Label(info, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=px(2))
            var = tk.StringVar(value="-")
            entry = SolidEntry(info, var, readonly=True, pad=(px(6), px(2)))
            entry.grid(row=row, column=1, sticky="ew", padx=(px(8), 0), pady=px(2))
            self.vars[field] = var

        log_frame = ttk.LabelFrame(self, text="最近日志（末尾 200 行，自动刷新）", padding=px(4))
        log_frame.pack(fill="both", expand=True, padx=px(10))
        self.log_text = tk.Text(log_frame, wrap="none", height=10, font="TkFixedFont",
                                state="disabled", background="#1e1e1e", foreground="#e8e8e8",
                                relief="solid", borderwidth=1, padx=px(6), pady=px(4))
        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        hsb = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(self, padding=px(10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="关闭", command=self.close).pack(side="right")
        self.btn_restart = ttk.Button(buttons, text="⟳ 重启", command=lambda: self._act(main.restart))
        self.btn_restart.pack(side="right", padx=px(4))
        self.btn_stop = ttk.Button(buttons, text="■ 停止", command=lambda: self._act(main.stop))
        self.btn_stop.pack(side="right")
        self.btn_start = ttk.Button(buttons, text="▶ 启动", command=lambda: self._act(main.start))
        self.btn_start.pack(side="right", padx=px(4))
        self.btn_log = ttk.Button(buttons, text="打开日志文件", command=self._open_log)
        self.btn_log.pack(side="left")
        self.btn_edit = ttk.Button(buttons, text="编辑", command=self._edit)
        self.btn_edit.pack(side="left", padx=px(4))

        self.update_status(main.statuses.get(key))
        self._poll_log()

    def _act(self, action) -> None:
        if self.status is not None:
            action([self.status])

    def show(self) -> None:
        self.update_idletasks()
        width = max(self.main.text_px(720), self.winfo_reqwidth())
        height = max(self.main.text_px(600), self.winfo_reqheight())
        place_over(self, self.main.root, width, height)
        show_modal(self, self.main.root)

    def _edit(self) -> None:
        if self.status is not None:
            self.main.edit_service(self.status, parent=self)

    def _open_log(self) -> None:
        if self.status is not None:
            self.main.open_log(self.status)

    def update_status(self, st: ServiceStatus | None) -> None:
        if not self.winfo_exists():
            return
        self.status = st
        if st is None:
            self.title("服务已不存在")
            self.vars["state"].set("服务已从配置中移除或端口已不再监听")
            for btn in (self.btn_start, self.btn_stop, self.btn_restart, self.btn_log, self.btn_edit):
                btn.state(["disabled"])
            return

        self.title(f"{st.name} - 详情")
        busy = st.key in self.main.busy
        state_text = "处理中…" if busy else str(st.state)
        if st.exit_code is not None:
            state_text += f"（退出码 {st.exit_code}）"
        if st.ports:
            port_text = st.ports_text
        else:
            port_text = "-（进程尚未监听端口）" if st.alive else "-（未运行）"
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.started_at)) \
            if st.started_at and st.alive else "-"
        cfg = st.config
        values = {
            "name": st.name,
            "type": st.type,
            "state": state_text,
            "port": port_text,
            "bind": st.bind or "-",
            "url": "  ".join(f"http://localhost:{p}" for p in st.ports) or "-",
            "pid": str(st.pid or "-"),
            "started": started,
            "mode": f"{cfg.mode.label}{'，开机自启' if cfg.autostart else ''}" if cfg else "-",
            "cwd": st.cwd or "-",
            "command": st.command or "-",
            "env": "  ".join(f"{k}={v}" for k, v in cfg.env.items()) if cfg and cfg.env else "-",
            "source": cfg.source if cfg else "未登记（由端口扫描发现）",
        }
        for field, value in values.items():
            self.vars[field].set(value)

        self.btn_start.state(["!disabled"] if self.main._can_start(st) else ["disabled"])
        self.btn_stop.state(["!disabled"] if self.main._can_stop(st) else ["disabled"])
        self.btn_restart.state(["!disabled"] if self.main._can_restart(st) else ["disabled"])
        self.btn_log.state(["!disabled"] if st.registered else ["disabled"])
        self.btn_edit.state(["!disabled"] if cfg is not None else ["disabled"])

    def _poll_log(self) -> None:
        st = self.status
        if st is None or not st.registered:
            text = "未登记的服务没有日志。" if st is not None else ""
        else:
            text = read_log_tail(log_file(st.name)) or "暂无日志（只有通过 listen-server 启动的服务才会记录日志）"
        if text != self._last_log:
            self._last_log = text
            at_bottom = self.log_text.yview()[1] >= 0.999
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", text)
            self.log_text.configure(state="disabled")
            if at_bottom:
                self.log_text.see("end")
        self._after_id = self.after(LOG_REFRESH_MS, self._poll_log)

    def close(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self.main.detail_windows.pop(self.key, None)
        self.destroy()
