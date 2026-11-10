from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller 打包后为 True
FROZEN = bool(getattr(sys, "frozen", False))


def _default_app_dir() -> Path:
    # 打包后：exe 同级的 config 文件夹；源码运行：~/.listen-server
    if FROZEN:
        return Path(sys.executable).resolve().parent / "config"
    return Path.home() / ".listen-server"


APP_DIR = Path(os.environ.get("LISTEN_SERVER_HOME") or _default_app_dir())
STATE_FILE = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"

FONT_SIZE = 10
REFRESH_INTERVAL_MS = 3000
STOP_TIMEOUT = 10.0
