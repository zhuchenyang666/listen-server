from __future__ import annotations

import os
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from listen_server.models import LaunchMode
from listen_server.store import SERVICE_TYPES, build_command
from listen_server.ui.modal import place_over, show_modal
from listen_server.ui.theme import SolidEntry, SolidText

if TYPE_CHECKING:
    from listen_server.ui.main_window import MainWindow

ARGS_EXAMPLES = {
    "jar": "如 --spring.profiles.active=dev",
    "java": "如 -cp lib/* com.demo.Main",
    "python": "如 -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
    "node": "如 --env=dev",
    "npm": "如 run dev",
}

FILE_HINTS = {
    "required": "必填",
    "optional": "可选；留空时只执行「运行环境 + 启动参数」，如 python -m uvicorn …",
}


class ServiceDialog(tk.Toplevel):
    def __init__(self, main: MainWindow, spec: dict[str, Any] | None = None, *,
                 parent: tk.Misc) -> None:
        super().__init__(parent)
        self.main = main
        self.parent = parent
        self.old_name = spec.get("name") if spec else None
        self.withdraw()
        self.title("编辑服务" if spec else "添加服务")
        self.resizable(True, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _: self.destroy())

        px = main.px
        spec = spec or {}
        type_key = spec.get("type", "jar")
        if type_key not in SERVICE_TYPES:
            type_key = "custom"

        self.v_type = tk.StringVar(value=SERVICE_TYPES[type_key].display)
        self.v_name = tk.StringVar(value=spec.get("name", ""))
        self.v_mode = tk.StringVar(value=spec.get("mode", LaunchMode.BACKGROUND))
        self.v_file = tk.StringVar(value=spec.get("file", ""))
        self.v_runtime = tk.StringVar(value=spec.get("runtime", ""))
        self.v_jvm = tk.StringVar(value=spec.get("jvm_args", ""))
        self.v_args = tk.StringVar(value=spec.get("args", ""))
        self.v_command = tk.StringVar(value=spec.get("command", ""))
        self.v_cwd = tk.StringVar(value=spec.get("cwd", ""))
        self.v_desc = tk.StringVar(value=spec.get("description", ""))
        self.v_autostart = tk.BooleanVar(value=bool(spec.get("autostart", False)))
        self.v_preview = tk.StringVar()

        body = ttk.Frame(self, padding=(px(16), px(14), px(16), px(6)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        self.body = body
        self.rows: dict[str, list[tk.Widget]] = {}
        self._row = 0

        def entry(var: tk.StringVar, **kw: Any) -> SolidEntry:
            return SolidEntry(body, var, pad=(px(6), px(3)), **kw)

        combo = ttk.Combobox(body, textvariable=self.v_type, state="readonly",
                             values=[t.display for t in SERVICE_TYPES.values()])
        combo.bind("<<ComboboxSelected>>", lambda _: self._on_type_changed())
        self._add_row("type", "服务类型", combo)

        self.e_name = entry(self.v_name, placeholder="如 order-service，全局唯一")
        self._add_row("name", "服务名称", self.e_name)

        modes = ttk.Frame(body)
        ttk.Radiobutton(modes, text="无终端启动（后台运行，输出写入日志）", value=LaunchMode.BACKGROUND,
                        variable=self.v_mode).pack(side="left")
        ttk.Radiobutton(modes, text="终端启动（弹出终端窗口显示输出）", value=LaunchMode.TERMINAL,
                        variable=self.v_mode).pack(side="left", padx=(px(16), 0))
        self._add_row("mode", "启动方式", modes)

        self.e_file = entry(self.v_file)
        self.file_hint = self._add_row("file", "程序文件", self.e_file,
                                       button=("浏览…", self._browse_file), hint=" ")

        self.e_runtime = entry(self.v_runtime)
        self.runtime_hint = self._add_row("runtime", "运行环境", self.e_runtime,
                                          button=("浏览…", self._browse_runtime), hint=" ")

        self._add_row("jvm", "JVM 参数", entry(self.v_jvm, placeholder="如 -Xms256m -Xmx512m"))

        self.e_args = entry(self.v_args)
        self._add_row("args", "启动参数", self.e_args,
                      hint="端口由服务自身的配置决定，启动后表格中自动显示实际监听的端口")

        self._add_row("command", "启动命令",
                      entry(self.v_command, placeholder="如 nginx.exe -p D:/nginx 或 go run ."),
                      hint="完整命令行；端口由服务自身的配置决定")

        self._add_row("cwd", "工作目录", entry(self.v_cwd, placeholder="留空时使用程序文件所在目录"),
                      button=("浏览…", self._browse_cwd))

        self.t_env = SolidText(body, height=3, pad=(px(6), px(3)))
        self.t_env.set("\n".join(f"{k}={v}" for k, v in (spec.get("env") or {}).items()))
        self.t_env.text.bind("<KeyRelease>", lambda _: self._update_preview(), add="+")
        self._add_row("env", "环境变量", self.t_env, hint="每行一个 KEY=VALUE，可留空")

        self._add_row("desc", "备注", entry(self.v_desc))

        self._add_row("autostart", "开机自启", ttk.Checkbutton(
            body, text="登录 Windows 后自动启动此服务", variable=self.v_autostart))

        self._add_row("preview", "命令预览", entry(self.v_preview, readonly=True))

        buttons = ttk.Frame(self, padding=(px(16), px(8), px(16), px(14)))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存并启动", style="Accent.TButton",
                   command=lambda: self._save(start=True)).pack(side="right")
        ttk.Button(buttons, text="保存", command=lambda: self._save(start=False)).pack(
            side="right", padx=px(8))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")

        for var in (self.v_file, self.v_runtime, self.v_jvm, self.v_args, self.v_command):
            var.trace_add("write", lambda *_: self._update_preview())

        self._on_type_changed()

    def show(self) -> None:
        """模态显示，关闭前阻塞。"""
        self.update_idletasks()
        width = max(self.main.text_px(620), self.winfo_reqwidth())
        place_over(self, self.parent, width, self.winfo_reqheight())
        self.after_idle(self.e_name.focus_set)
        show_modal(self, self.parent)

    # ---------- 布局 ----------

    def _add_row(self, key: str, label: str, widget: tk.Widget, *,
                 button: tuple[str, Any] | None = None, hint: str | None = None) -> ttk.Label | None:
        px = self.main.px
        row = self._row
        lbl = ttk.Label(self.body, text=f"{label}:")
        lbl.grid(row=row, column=0, sticky="w", pady=(px(5), 0), padx=(0, px(10)))
        widget.grid(row=row, column=1, sticky="ew", pady=(px(5), 0))
        widgets: list[tk.Widget] = [lbl, widget]
        if button:
            btn = ttk.Button(self.body, text=button[0], command=button[1])
            btn.grid(row=row, column=2, sticky="w", padx=(px(8), 0), pady=(px(5), 0))
            widgets.append(btn)
        hint_label = None
        if hint is not None:
            hint_label = ttk.Label(self.body, text=hint, style="Hint.TLabel")
            hint_label.grid(row=row + 1, column=1, columnspan=2, sticky="w")
            widgets.append(hint_label)
            self._row += 1
        self._row += 1
        self.rows[key] = widgets
        return hint_label

    def _show_row(self, key: str, visible: bool) -> None:
        for w in self.rows[key]:
            if visible:
                w.grid()
            else:
                w.grid_remove()

    # ---------- 交互 ----------

    @property
    def stype(self):
        key = self.v_type.get().split()[0]
        return SERVICE_TYPES.get(key, SERVICE_TYPES["custom"])

    def _on_type_changed(self) -> None:
        t = self.stype
        self._show_row("file", bool(t.file))
        self._show_row("runtime", bool(t.runtime))
        self._show_row("jvm", t.jvm)
        self._show_row("args", not t.custom)
        self._show_row("command", t.custom)
        if t.file:
            self.file_hint.configure(text=FILE_HINTS[t.file])
        if t.runtime:
            self.runtime_hint.configure(
                text=f"留空使用 PATH 中的 {t.runtime}；也可以选择指定版本的可执行文件")
            self.e_runtime.set_placeholder(t.runtime)
        self.e_args.set_placeholder(ARGS_EXAMPLES.get(t.key, "启动参数，可留空"))
        self.e_file.set_placeholder("选择 " + " / ".join(p for _, p in t.filetypes))
        self._update_preview()
        if self.winfo_viewable():
            self.update_idletasks()
            width = max(self.winfo_width(), self.winfo_reqwidth())
            place_over(self, self.parent, width, self.winfo_reqheight())

    def _browse_file(self) -> None:
        t = self.stype
        path = filedialog.askopenfilename(
            parent=self, title="选择程序文件",
            filetypes=[*t.filetypes, ("所有文件", "*.*")],
            initialdir=self._initial_dir(),
        )
        if not path:
            return
        path = os.path.normpath(path)
        self.v_file.set(path)
        if not self.v_cwd.get().strip():
            self.v_cwd.set(str(Path(path).parent))
        if not self.v_name.get().strip():
            self.v_name.set(Path(path).stem)

    def _browse_runtime(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择运行环境可执行文件",
            filetypes=[("可执行程序", "*.exe *.cmd *.bat"), ("所有文件", "*.*")],
            initialdir=self._initial_dir(),
        )
        if path:
            self.v_runtime.set(os.path.normpath(path))

    def _browse_cwd(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择工作目录",
                                       initialdir=self._initial_dir())
        if path:
            self.v_cwd.set(os.path.normpath(path))

    def _initial_dir(self) -> str:
        for value in (self.v_cwd.get(), self.v_file.get()):
            p = Path(value.strip()) if value.strip() else None
            if p and p.exists():
                return str(p if p.is_dir() else p.parent)
        return str(Path.home())

    def _collect(self) -> dict[str, Any]:
        t = self.stype
        spec: dict[str, Any] = {
            "name": self.v_name.get().strip(),
            "type": t.key,
            "mode": self.v_mode.get(),
            "cwd": self.v_cwd.get().strip(),
            "description": self.v_desc.get().strip(),
            "autostart": self.v_autostart.get(),
        }
        if t.custom:
            spec["command"] = self.v_command.get().strip()
        else:
            spec["args"] = self.v_args.get().strip()
        if t.file:
            spec["file"] = self.v_file.get().strip()
        if t.runtime:
            spec["runtime"] = self.v_runtime.get().strip()
        if t.jvm:
            spec["jvm_args"] = self.v_jvm.get().strip()
        return spec

    def _update_preview(self) -> None:
        self.v_preview.set(build_command(self._collect()))

    def _parse_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for i, line in enumerate(self.t_env.get().splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.strip():
                raise ValueError(f"环境变量第 {i} 行格式错误，应为 KEY=VALUE：{line}")
            env[key.strip()] = value.strip()
        return env

    def _validate(self, spec: dict[str, Any]) -> str | None:
        t = self.stype
        name = spec["name"]
        if not name:
            return "请填写服务名称"
        if name != self.old_name and name in self.main.registered_names():
            return f"服务名称「{name}」已存在"
        if self.old_name and name != self.old_name:
            old = self.main.statuses.get(f"svc:{self.old_name}")
            if old is not None and old.alive:
                return "服务正在运行，请先停止后再修改名称"

        if t.file:
            file = spec.get("file", "")
            if t.file == "required" and not file:
                return "请选择程序文件"
            if file and not Path(file).is_file():
                return f"程序文件不存在：{file}"
        runtime = spec.get("runtime", "")
        if runtime and (os.sep in runtime or "/" in runtime) and not Path(runtime).is_file():
            return f"运行环境不存在：{runtime}"
        if runtime == "" and t.runtime and shutil.which(t.runtime) is None:
            return f"在 PATH 中找不到 {t.runtime}，请在「运行环境」中选择可执行文件"
        if t.custom and not spec.get("command"):
            return "请填写启动命令"

        if not spec["cwd"] and spec.get("file"):
            spec["cwd"] = str(Path(spec["file"]).parent)
        if not spec["cwd"]:
            return "请选择工作目录"
        if not Path(spec["cwd"]).is_dir():
            return f"工作目录不存在：{spec['cwd']}"
        return None

    def _save(self, start: bool) -> None:
        spec = self._collect()
        try:
            spec["env"] = self._parse_env()
        except ValueError as e:
            messagebox.showerror("输入有误", str(e), parent=self)
            return
        error = self._validate(spec)
        if error:
            messagebox.showerror("输入有误", error, parent=self)
            return

        error = self.main.save_service(spec, self.old_name, start=start)
        if error:
            messagebox.showerror("保存失败", error, parent=self)
            return
        self.destroy()
