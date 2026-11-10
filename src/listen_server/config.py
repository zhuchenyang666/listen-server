from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(os.environ.get("LISTEN_SERVER_HOME") or Path.home() / ".listen-server")
STATE_FILE = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"

FONT_SIZE = 10
REFRESH_INTERVAL_MS = 3000
STOP_TIMEOUT = 10.0
