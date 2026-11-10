from __future__ import annotations

import json
import locale
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from listen_server.config import LOG_DIR, STATE_FILE
from listen_server.models import (
    LaunchMode,
    ServiceConfig,
    ServiceStatus,
    State,
    SystemStats,
)
from listen_server.resources import ResourceSampler
from listen_server.scanner import (
    ProcInfo,
    Signature,
    detect_type,
    match_service_processes,
    proc_info,
    scan_listening,
    service_signature,
)

_LOCALE_ENCODING = locale.getpreferredencoding(False)


class ServiceError(Exception):
    pass


def log_file(name: str) -> Path:
    return LOG_DIR / f"{re.sub(r'[\\/:*?\"<>|\s]', '_', name)}.log"


def _decode_line(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(_LOCALE_ENCODING, errors="replace")


def read_log_tail(path: Path, max_lines: int = 200, max_bytes: int = 256 * 1024) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            offset = max(0, size - max_bytes)
            f.seek(offset)
            data = f.read()
    except OSError:
        return ""
    if offset:
        data = data[data.find(b"\n") + 1:]
    lines = data.splitlines()[-max_lines:]
    return "\n".join(_decode_line(line).rstrip("\r") for line in lines)


def _popen_detached(args: str, flags: int, **kwargs: Any) -> subprocess.Popen:
    try:
        # 脱离启动器的 Job，避免关闭 listen-server 时服务被连带结束
        return subprocess.Popen(
            args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs
        )
    except OSError:
        return subprocess.Popen(args, creationflags=flags, **kwargs)


def _spawn(command: str, cwd: Path, env: dict[str, str], log) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "cwd": cwd, "env": env, "shell": True,
        "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
    }
    if os.name != "nt":
        return subprocess.Popen(command, start_new_session=True, **kwargs)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return _popen_detached(command, flags, **kwargs)


def _spawn_terminal(name: str, command: str, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    if os.name != "nt":
        raise ServiceError("终端启动目前仅支持 Windows")
    title = re.sub(r'[&|<>^"%]', "", name) or "listen-server"
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    # 不用 shell=True：它会强制隐藏窗口
    args = f'"{comspec}" /s /c "title {title} && {command}"'
    return _popen_detached(args, subprocess.CREATE_NEW_CONSOLE, cwd=cwd, env=env)


def build_env(svc: ServiceConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.update(svc.env)
    return env


def _tree_pids(root: psutil.Process) -> set[int]:
    try:
        return {root.pid} | {c.pid for c in root.children(recursive=True)}
    except psutil.Error:
        return {root.pid}


class ServiceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._exit_codes: dict[str, int] = {}
        self._state: dict[str, dict[str, Any]] = self._load_state()
        self.sampler = ResourceSampler()

    # ---------- 运行记录 ----------

    @staticmethod
    def _load_state() -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _alive_root(self, name: str) -> psutil.Process | None:
        """返回本工具启动的根进程；进程已退出则清理记录并保存退出码。"""
        rec = self._state.get(name)
        if not rec:
            return None
        proc = self._procs.get(name)
        if proc is not None and proc.poll() is not None:
            root = None
        else:
            try:
                root = psutil.Process(int(rec["pid"]))
                if abs(root.create_time() - float(rec.get("create_time", 0))) > 1:
                    root = None
            except (psutil.Error, KeyError, ValueError, TypeError):
                root = None
        if root is None:
            proc = self._procs.pop(name, None)
            if proc is not None and proc.returncode is not None:
                self._exit_codes[name] = proc.returncode
            self._state.pop(name, None)
            self._save_state()
        return root

    # ---------- 状态采集 ----------

    def _service_roots(self, services: list[ServiceConfig]) -> dict[str, list[psutil.Process]]:
        """每个服务的根进程：本工具启动的用记录的进程；否则在系统进程中按程序文件 / 工作目录识别。"""
        roots: dict[str, list[psutil.Process]] = {}
        signatures: dict[str, Signature] = {}
        with self._lock:
            for svc in services:
                root = self._alive_root(svc.name)
                if root is not None:
                    roots[svc.name] = [root]
                else:
                    signatures[svc.name] = service_signature(
                        svc.type, svc.file, svc.runtime, str(svc.cwd), svc.command)
        if signatures:
            for name, pids in match_service_processes(signatures).items():
                procs = []
                for pid in pids:
                    try:
                        procs.append(psutil.Process(pid))
                    except psutil.Error:
                        continue
                roots[name] = procs
        return roots

    def collect(self, services: list[ServiceConfig], show_unregistered: bool) -> list[ServiceStatus]:
        listening = scan_listening()
        pid_ports: dict[int, list[int]] = {}
        for port, entry in listening.items():
            if entry.pid:
                pid_ports.setdefault(entry.pid, []).append(port)
        roots_by_service = self._service_roots(services)

        result: list[ServiceStatus] = []
        claimed_ports: set[int] = set()
        for svc in services:
            st = ServiceStatus(
                key=f"svc:{svc.name}",
                name=svc.name,
                type=svc.type or "-",
                state=State.STOPPED,
                description=svc.description,
                cwd=str(svc.cwd),
                command=svc.command,
                config=svc,
            )
            roots = roots_by_service.get(svc.name, [])
            if roots:
                st.state = State.RUNNING
                st.root_pids = [r.pid for r in roots]
                pids: set[int] = set()
                for r in roots:
                    pids |= _tree_pids(r)
                st.ports = sorted(p for pid in pids for p in pid_ports.get(pid, []))
                claimed_ports.update(st.ports)
                if st.ports:
                    entry = listening[st.ports[0]]
                    st.pid, st.bind = entry.pid, entry.bind
                else:
                    st.pid = roots[0].pid
                with self._lock:
                    st.started_at = self._state.get(svc.name, {}).get("started_at")
                if st.started_at is None:
                    try:
                        st.started_at = roots[0].create_time()
                    except psutil.Error:
                        pass
            else:
                with self._lock:
                    code = self._exit_codes.get(svc.name)
                if code not in (None, 0):
                    st.state = State.FAILED
                    st.exit_code = code
            result.append(st)

        if show_unregistered:
            cache: dict[int, ProcInfo | None] = {}
            for port, entry in sorted(listening.items()):
                if port in claimed_ports or entry.pid in (None, 0, 4):
                    continue
                if entry.pid not in cache:
                    cache[entry.pid] = proc_info(entry.pid)
                pi = cache[entry.pid]
                if pi is not None and pi.is_system:
                    continue
                result.append(ServiceStatus(
                    key=f"port:{port}",
                    name=pi.name if pi else f"PID {entry.pid}",
                    type=detect_type(pi),
                    state=State.UNREGISTERED,
                    ports=[port],
                    pid=entry.pid,
                    root_pids=[entry.pid],
                    bind=entry.bind,
                    started_at=pi.create_time if pi else None,
                    cwd=pi.cwd if pi else "",
                    command=pi.cmdline if pi else "",
                    process_name=pi.name if pi else "",
                ))

        self.sampler.begin()
        for st in result:
            if st.alive and st.root_pids:
                usage = self.sampler.sample(st.root_pids[0])
                if usage is not None:
                    st.cpu, st.mem, st.disk = usage
        self.sampler.end()
        return result

    @property
    def system_stats(self) -> SystemStats:
        return self.sampler.system

    # ---------- 启动 / 停止 ----------

    def start(self, svc: ServiceConfig) -> None:
        with self._lock:
            root = self._alive_root(svc.name)
            if root is not None:
                raise ServiceError(f"已由 listen-server 启动，PID {root.pid}")

        if not svc.cwd.is_dir():
            raise ServiceError(f"工作目录不存在：{svc.cwd}")

        env = build_env(svc)
        command = svc.command
        path = log_file(svc.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as log:
            mode_note = "（终端启动，输出显示在终端窗口中）" if svc.mode is LaunchMode.TERMINAL else ""
            header = f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动{mode_note}: {command} =====\n"
            log.write(header.encode("utf-8"))
            log.flush()
            try:
                if svc.mode is LaunchMode.TERMINAL:
                    proc = _spawn_terminal(svc.name, command, svc.cwd, env)
                else:
                    proc = _spawn(command, svc.cwd, env, log)
            except OSError as e:
                raise ServiceError(f"启动失败：{e}") from e

        try:
            create_time = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            create_time = 0.0
        with self._lock:
            self._procs[svc.name] = proc
            self._exit_codes.pop(svc.name, None)
            self._state[svc.name] = {
                "pid": proc.pid,
                "create_time": create_time,
                "started_at": time.time(),
                "command": svc.command,
            }
            self._save_state()

    def stop(self, st: ServiceStatus, timeout: float) -> None:
        roots: list[psutil.Process] = []
        if st.config is not None:
            with self._lock:
                root = self._alive_root(st.name)
            if root is not None:
                roots.append(root)
        for pid in st.root_pids:
            if all(r.pid != pid for r in roots):
                try:
                    roots.append(psutil.Process(pid))
                except psutil.Error:
                    pass

        if not roots:
            self._forget(st)
            raise ServiceError("服务未在运行")

        for r in roots:
            _check_protected(r)

        targets: dict[int, psutil.Process] = {}
        for r in roots:
            try:
                for child in r.children(recursive=True):
                    targets[child.pid] = child
            except psutil.Error:
                pass
            targets[r.pid] = r
        _terminate(list(targets.values()), timeout)
        self._forget(st)

    def restart(self, st: ServiceStatus, timeout: float) -> None:
        if st.config is None:
            raise ServiceError("未登记的服务无法重启")
        if st.alive:
            self.stop(st, timeout)
        deadline = time.time() + timeout
        while any(p in scan_listening() for p in st.ports):
            if time.time() > deadline:
                raise ServiceError(f"端口 {st.ports_text} 在 {timeout:g} 秒内未释放")
            time.sleep(0.5)
        self.start(st.config)

    def _forget(self, st: ServiceStatus) -> None:
        if st.config is None:
            return
        with self._lock:
            proc = self._procs.pop(st.name, None)
            if proc is not None:
                proc.poll()
            self._exit_codes.pop(st.name, None)
            if self._state.pop(st.name, None) is not None:
                self._save_state()


def _check_protected(p: psutil.Process) -> None:
    pi = proc_info(p.pid)
    if p.pid == os.getpid() or (pi is not None and pi.is_system):
        name = pi.name if pi else ""
        raise ServiceError(f"拒绝结束受保护的进程：PID {p.pid} {name}")


def _terminate(procs: list[psutil.Process], timeout: float) -> None:
    denied: list[int] = []
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied.append(p.pid)
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied.append(p.pid)
    _, alive = psutil.wait_procs(alive, timeout=3)
    if denied:
        pids = ", ".join(str(pid) for pid in sorted(set(denied)))
        raise ServiceError(f"权限不足，无法结束进程 PID {pids}，请以管理员身份运行 listen-server")
    if alive:
        raise ServiceError(f"进程未能结束：PID {', '.join(str(p.pid) for p in alive)}")
