from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import psutil

SYSTEM_PIDS = {0, 4}

SYSTEM_PROCESS_NAMES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "spoolsv.exe", "lsaiso.exe", "memory compression",
}


@dataclass
class ListenEntry:
    port: int
    pid: int | None
    hosts: list[str] = field(default_factory=list)

    @property
    def bind(self) -> str:
        return ", ".join(self.hosts)


@dataclass
class ProcInfo:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: str = ""
    cwd: str = ""
    create_time: float | None = None

    @property
    def is_system(self) -> bool:
        return self.pid in SYSTEM_PIDS or self.name.lower() in SYSTEM_PROCESS_NAMES


def _iter_listen_sockets():
    try:
        for c in psutil.net_connections(kind="inet"):
            yield c.status, c.laddr, c.pid
    except psutil.AccessDenied:
        for proc in psutil.process_iter():
            try:
                for c in proc.net_connections(kind="inet"):
                    yield c.status, c.laddr, proc.pid
            except psutil.Error:
                continue


def scan_listening() -> dict[int, ListenEntry]:
    """返回 {端口: 监听信息}，只统计 TCP LISTEN 状态的端口。"""
    result: dict[int, ListenEntry] = {}
    for status, laddr, pid in _iter_listen_sockets():
        if status != psutil.CONN_LISTEN or not laddr:
            continue
        entry = result.get(laddr.port)
        if entry is None:
            result[laddr.port] = ListenEntry(laddr.port, pid, [laddr.ip])
            continue
        if laddr.ip not in entry.hosts:
            entry.hosts.append(laddr.ip)
        if entry.pid is None:
            entry.pid = pid
    return result


def _safe(fn, default=""):
    try:
        return fn()
    except (psutil.Error, OSError):
        return default


def proc_info(pid: int) -> ProcInfo | None:
    try:
        p = psutil.Process(pid)
    except psutil.Error:
        return None
    with p.oneshot():
        cmdline = _safe(p.cmdline, [])
        return ProcInfo(
            pid=pid,
            name=_safe(p.name),
            exe=_safe(p.exe),
            cmdline=" ".join(cmdline) if cmdline else "",
            cwd=_safe(p.cwd),
            create_time=_safe(p.create_time, None),
        )


TYPE_PROCESS_NAMES = {
    "jar": {"java", "javaw"},
    "java": {"java", "javaw"},
    "python": {"python", "pythonw", "py", "uv"},
    "node": {"node"},
    "npm": {"node", "npm"},
    "bat": {"cmd"},
    "powershell": {"powershell", "pwsh"},
}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _stem(path: str) -> str:
    name = Path(path.strip().strip('"')).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


@dataclass
class Signature:
    """用于在系统进程中识别某个登记服务（包括在 IDE 等外部启动的情况）。"""
    file: str          # 程序文件规范化路径；为空时按工作目录 + 进程名识别
    file_name: str
    cwd: str
    names: set[str]


def service_signature(svc_type: str | None, file: str, runtime: str, cwd: str, command: str) -> Signature:
    names = set(TYPE_PROCESS_NAMES.get(svc_type or "", ()))
    if runtime:
        names.add(_stem(runtime))
    if svc_type == "exe" and file:
        names.add(_stem(file))
    if svc_type in (None, "", "custom") and command.strip():
        first = shlex.split(command, posix=False)[0] if command.strip() else ""
        names.add(_stem(first))
    file_norm = _norm(file)
    return Signature(file_norm, Path(file_norm).name if file_norm else "", _norm(cwd), names)


def match_service_processes(signatures: dict[str, Signature]) -> dict[str, list[int]]:
    """返回 {服务名: 匹配到的根进程 pid 列表}；父进程也匹配时只保留父进程。"""
    all_names = set().union(*(s.names for s in signatures.values())) if signatures else set()
    candidates: list[tuple[int, int, str, str, str]] = []
    for p in psutil.process_iter(["pid", "ppid", "name"]):
        name = _stem(p.info.get("name") or "")
        if name not in all_names:
            continue
        cmd = " ".join(_safe(p.cmdline, []) or [])
        cwd = _safe(p.cwd)
        candidates.append((p.pid, p.info.get("ppid") or 0, name,
                           os.path.normcase(cmd).replace("/", "\\"), _norm(cwd)))

    result: dict[str, list[int]] = {}
    for svc_name, sig in signatures.items():
        matched: dict[int, int] = {}
        for pid, ppid, name, cmd, cwd in candidates:
            if name not in sig.names:
                continue
            if sig.file:
                ok = sig.file in cmd or (cwd == sig.cwd and sig.file_name in cmd)
            else:
                ok = bool(sig.cwd) and cwd == sig.cwd
            if ok:
                matched[pid] = ppid
        result[svc_name] = sorted(pid for pid, ppid in matched.items() if ppid not in matched)
    return result


def detect_type(info: ProcInfo | None) -> str:
    if info is None or not info.name:
        return "-"
    name = info.name.lower().removesuffix(".exe")
    if name in ("java", "javaw"):
        return "java"
    if name.startswith("python") or name in ("uv", "uvicorn", "gunicorn", "hypercorn"):
        return "python"
    if name in ("node", "bun", "deno"):
        return "node"
    if name == "dotnet":
        return "dotnet"
    if info.cwd and (Path(info.cwd) / "go.mod").is_file():
        return "go"
    return name
