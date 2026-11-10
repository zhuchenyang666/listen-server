"""把 listen-server 打包成单个 exe。

用法（项目根目录）：
    uv run python run_build.py

输出（dist/ 与 build/ 已在 .gitignore 中，不纳入版本控制）：
    dist/listen-server/
    ├── listen-server.exe   # 单文件 exe，不生成 _internal 文件夹
    └── config/             # 运行数据：services.json、state.json、logs/、autostart.log
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
NAME = "listen-server"
ENTRY = ROOT / "src" / "listen_server" / "__main__.py"
ICON_PNG = ROOT / "resources" / "static" / "server.png"

BUILD_DIR = ROOT / "build"
RELEASE_DIR = ROOT / "dist" / NAME
EXE_PATH = RELEASE_DIR / f"{NAME}.exe"
CONFIG_DIR = RELEASE_DIR / "config"


def png_to_ico(png: Path, ico: Path) -> None:
    """生成内嵌 PNG 的 .ico（Windows Vista 起支持），无需 Pillow。"""
    data = png.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG 文件：{png}")
    width, height = struct.unpack(">II", data[16:24])
    header = struct.pack("<HHH", 0, 1, 1)  # reserved, type=icon, count
    entry = struct.pack(
        "<BBBBHHII",
        width if width < 256 else 0,
        height if height < 256 else 0,
        0, 0,           # 调色板颜色数、保留
        1, 32,          # planes、每像素位数
        len(data),
        len(header) + 16,
    )
    ico.parent.mkdir(parents=True, exist_ok=True)
    ico.write_bytes(header + entry + data)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("仅支持在 Windows 上打包")

    ico = BUILD_DIR / f"{NAME}.ico"
    png_to_ico(ICON_PNG, ico)

    # 只替换 exe，保留已有的 config/（避免重新打包时丢失已登记的服务）
    if EXE_PATH.exists():
        EXE_PATH.unlink()

    PyInstaller.__main__.run([
        str(ENTRY),
        "--name", NAME,
        "--onefile",            # 单个 exe，不产生 _internal 目录
        "--windowed",           # 不显示控制台窗口
        "--noconfirm",
        "--clean",
        "--paths", str(ROOT / "src"),
        "--icon", str(ico),
        "--add-data", f"{ICON_PNG}{os.pathsep}resources/static",
        "--distpath", str(RELEASE_DIR),
        "--workpath", str(BUILD_DIR / "pyinstaller"),
        "--specpath", str(BUILD_DIR),
    ])

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    internal = RELEASE_DIR / "_internal"
    if internal.exists():
        shutil.rmtree(internal)

    size_mb = EXE_PATH.stat().st_size / 1024 / 1024
    print()
    print("打包完成：")
    print(f"  exe     : {EXE_PATH}  ({size_mb:.1f} MB)")
    print(f"  配置目录: {CONFIG_DIR}")


if __name__ == "__main__":
    main()
