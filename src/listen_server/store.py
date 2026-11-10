"""通过界面登记的服务，保存在 services.json 中。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from listen_server.config import APP_DIR
from listen_server.models import LaunchMode, ServiceConfig

SERVICES_FILE = APP_DIR / "services.json"
SOURCE_LABEL = "用户添加"


class ServiceType:
    def __init__(self, key: str, label: str, *, runtime: str = "", file: str = "",
                 filetypes: tuple[tuple[str, str], ...] = (), jvm: bool = False,
                 custom: bool = False) -> None:
        self.key = key
        self.label = label
        self.runtime = runtime          # 默认运行环境，空表示不需要
        self.file = file                # "required" / "optional" / ""
        self.filetypes = filetypes
        self.jvm = jvm
        self.custom = custom

    @property
    def display(self) -> str:
        return f"{self.key}  —  {self.label}"


SERVICE_TYPES: dict[str, ServiceType] = {t.key: t for t in (
    ServiceType("jar", "Java 可执行 JAR（Spring Boot 等）", runtime="java", file="required",
                filetypes=(("JAR 文件", "*.jar"),), jvm=True),
    ServiceType("java", "Java 命令（如 -cp lib/* com.demo.Main）", runtime="java", jvm=True),
    ServiceType("python", "Python 脚本或模块（FastAPI / Flask / Django）", runtime="python",
                file="optional", filetypes=(("Python 脚本", "*.py"),)),
    ServiceType("node", "Node.js 脚本", runtime="node", file="optional",
                filetypes=(("JavaScript", "*.js *.mjs *.cjs"),)),
    ServiceType("npm", "npm / pnpm / yarn 脚本（如 run dev）", runtime="npm"),
    ServiceType("exe", "可执行程序（.exe）", file="required", filetypes=(("可执行程序", "*.exe"),)),
    ServiceType("bat", "批处理脚本（.bat / .cmd）", file="required",
                filetypes=(("批处理", "*.bat *.cmd"),)),
    ServiceType("powershell", "PowerShell 脚本（.ps1）", runtime="powershell", file="required",
                filetypes=(("PowerShell 脚本", "*.ps1"),)),
    ServiceType("custom", "自定义命令", custom=True),
)}


def _quote(value: str) -> str:
    value = value.strip()
    if value and " " in value and not value.startswith('"'):
        return f'"{value}"'
    return value


def build_command(spec: dict[str, Any]) -> str:
    stype = SERVICE_TYPES.get(spec.get("type", ""), SERVICE_TYPES["custom"])
    if stype.custom:
        return str(spec.get("command", "")).strip()

    runtime = _quote(str(spec.get("runtime") or stype.runtime))
    file = _quote(str(spec.get("file", "")))
    jvm = str(spec.get("jvm_args", "")).strip()
    args = str(spec.get("args", "")).strip()

    match stype.key:
        case "jar":
            parts = [runtime, jvm, "-jar", file, args]
        case "java":
            parts = [runtime, jvm, args]
        case "powershell":
            parts = [runtime, "-NoProfile -ExecutionPolicy Bypass -File", file, args]
        case "exe" | "bat":
            parts = [file, args]
        case _:
            parts = [runtime, file, args]
    return " ".join(p for p in parts if p)


def spec_to_config(spec: dict[str, Any]) -> ServiceConfig:
    name = str(spec.get("name", "")).strip()
    if not name:
        raise ValueError("服务名称不能为空")
    command = build_command(spec)
    if not command:
        raise ValueError("启动命令为空")
    cwd = str(spec.get("cwd", "")).strip()
    if not cwd:
        raise ValueError("工作目录不能为空")
    env = spec.get("env") or {}
    stype = SERVICE_TYPES.get(spec.get("type", ""), SERVICE_TYPES["custom"])
    return ServiceConfig(
        name=name,
        command=command,
        cwd=Path(cwd),
        type=spec.get("type") or None,
        file=str(spec.get("file", "")).strip(),
        runtime=str(spec.get("runtime") or stype.runtime),
        description=str(spec.get("description", "")),
        env={str(k): str(v) for k, v in env.items()},
        source=SOURCE_LABEL,
        mode=LaunchMode(spec.get("mode", LaunchMode.BACKGROUND)),
        autostart=bool(spec.get("autostart", False)),
        spec=dict(spec),
    )


def load_specs() -> list[dict[str, Any]]:
    try:
        data = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise ValueError(f"{SERVICES_FILE}: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{SERVICES_FILE}: 格式错误，应为数组")
    return [s for s in data if isinstance(s, dict)]


def load_services() -> tuple[list[ServiceConfig], list[str]]:
    """读取所有登记的服务，返回 (服务列表, 问题列表)。"""
    try:
        specs = load_specs()
    except ValueError as e:
        return [], [str(e)]
    services: list[ServiceConfig] = []
    errors: list[str] = []
    names: set[str] = set()
    for spec in specs:
        try:
            svc = spec_to_config(spec)
        except (TypeError, ValueError) as e:
            errors.append(f"服务「{spec.get('name', '?')}」数据有误：{e}")
            continue
        if svc.name in names:
            errors.append(f"服务名重复「{svc.name}」，已忽略")
            continue
        names.add(svc.name)
        services.append(svc)
    return services, errors


def save_specs(specs: list[dict[str, Any]]) -> None:
    SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SERVICES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SERVICES_FILE)


def upsert_spec(spec: dict[str, Any], old_name: str | None = None) -> None:
    specs = load_specs()
    key = old_name or spec["name"]
    for i, s in enumerate(specs):
        if s.get("name") == key:
            specs[i] = spec
            break
    else:
        specs.append(spec)
    save_specs(specs)


def delete_spec(name: str) -> None:
    save_specs([s for s in load_specs() if s.get("name") != name])
