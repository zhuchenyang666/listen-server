"""开机自启：登录 Windows 后运行 `python -m listen_server --autostart`，启动标记了自启的服务。"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from listen_server.config import APP_DIR
from listen_server.models import ServiceConfig
from listen_server.store import load_services

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "listen-server"
LOG_FILE = APP_DIR / "autostart.log"


def launcher_command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" -m listen_server --autostart'


def sync(services: list[ServiceConfig]) -> None:
    """有任一服务开启自启时写入注册表启动项，否则删除。"""
    if sys.platform != "win32":
        return
    import winreg

    enabled = any(s.autostart for s in services)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launcher_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def _log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")


def run_autostart() -> None:
    from listen_server.manager import ServiceError, ServiceManager

    time.sleep(5)  # 等待登录后网络与磁盘就绪
    services, errors = load_services()
    for error in errors:
        _log(f"数据问题: {error}")
    manager = ServiceManager()
    running = {st.name for st in manager.collect(services, False) if st.alive}
    for svc in services:
        if not svc.autostart:
            continue
        if svc.name in running:
            _log(f"{svc.name}: 已在运行，跳过")
            continue
        try:
            manager.start(svc)
            _log(f"{svc.name}: 已启动")
        except ServiceError as e:
            _log(f"{svc.name}: 启动失败 {e}")
