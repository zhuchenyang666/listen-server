"""应用图标：任务栏、主窗口与所有子窗口左上角统一使用 resources/static/server.png。"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ICON_RELATIVE = Path("resources") / "static" / "server.png"

if getattr(sys, "frozen", False):
    # 打包后资源解压在 sys._MEIPASS 下（由 run_build.py 通过 --add-data 打入 exe）
    ICON_PATH = Path(sys._MEIPASS) / ICON_RELATIVE
else:
    # src/listen_server/ui/icon.py -> 项目根目录
    ICON_PATH = Path(__file__).resolve().parents[3] / ICON_RELATIVE
APP_USER_MODEL_ID = "PigZhu.ListenServer"


def set_app_id() -> None:
    """必须在创建窗口前调用：否则 Windows 任务栏把程序归到 python.exe，显示 Python 图标。"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply_icon(root: tk.Tk) -> None:
    """设为默认图标，之后创建的所有 Toplevel 子窗口自动继承。"""
    if not ICON_PATH.is_file():
        return
    try:
        image = tk.PhotoImage(master=root, file=str(ICON_PATH))
    except tk.TclError:
        return
    root.iconphoto(True, image)
    root._app_icon = image  # 保持引用，避免被回收后图标消失
