"""按进程树采样 CPU / 内存 / 磁盘占用，口径参考 Windows 任务管理器。"""

from __future__ import annotations

import ctypes
import sys
import time

import psutil

from listen_server.models import SystemStats

# CPU 与 I/O 是两次采样之间的差值，间隔太短（如连续触发的刷新）时结果会接近 0
MIN_SAMPLE_INTERVAL = 1.0

Metrics = tuple[float, int, float]  # (cpu 原始百分比, rss 字节, I/O 字节/秒)


class DiskActiveTime:
    """磁盘活动时间百分比，与任务管理器一致：100 - PhysicalDisk(_Total)\\% Idle Time（Windows PDH）。"""

    COUNTER = r"\PhysicalDisk(_Total)\% Idle Time"
    PDH_FMT_DOUBLE = 0x00000200

    def __init__(self) -> None:
        self._query = None
        self._counter = None
        if sys.platform != "win32":
            return
        try:
            pdh = ctypes.windll.pdh
            query, counter = ctypes.c_void_p(), ctypes.c_void_p()
            if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
                return
            if pdh.PdhAddEnglishCounterW(query, self.COUNTER, None, ctypes.byref(counter)) != 0:
                pdh.PdhCloseQuery(query)
                return
            pdh.PdhCollectQueryData(query)
            self._pdh, self._query, self._counter = pdh, query, counter
        except (AttributeError, OSError):
            self._query = None

    @property
    def available(self) -> bool:
        return self._query is not None

    def read(self) -> float | None:
        if self._query is None:
            return None

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", ctypes.c_ulong), ("_pad", ctypes.c_ulong),
                        ("doubleValue", ctypes.c_double)]

        value = Value()
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        if self._pdh.PdhGetFormattedCounterValue(
            self._counter, self.PDH_FMT_DOUBLE, None, ctypes.byref(value)
        ) != 0:
            return None
        return min(100.0, max(0.0, 100.0 - value.doubleValue))


class ResourceSampler:
    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._io_prev: dict[int, tuple[int, float]] = {}
        self._disk_prev: tuple[int, int, float] | None = None
        self._cpu_count = psutil.cpu_count() or 1
        self._mem_total = psutil.virtual_memory().total
        self._disk_rate = 0.0
        self._last_sample = 0.0
        self._fresh = True
        self._seen: set[int] = set()
        self._round: dict[int, Metrics | None] = {}
        self._last: dict[int, Metrics] = {}
        self.system = SystemStats()
        self._cpu_prev = psutil.cpu_times()
        self._disk_active = DiskActiveTime()

    def _system_cpu(self) -> float:
        # psutil.cpu_percent(None) 按线程记录上次采样，而每次刷新都在新线程中执行，只能自己算差值
        now = psutil.cpu_times()
        prev, self._cpu_prev = self._cpu_prev, now
        total = sum(now) - sum(prev)
        if total <= 0:
            return self.system.cpu
        idle = now.idle - prev.idle
        return min(100.0, max(0.0, (1 - idle / total) * 100))

    def begin(self) -> None:
        """每轮刷新开始时调用；距上次采样不足 MIN_SAMPLE_INTERVAL 时沿用上次结果。"""
        self._seen = set()
        self._round = {}
        now = time.monotonic()
        self._fresh = now - self._last_sample >= MIN_SAMPLE_INTERVAL
        if not self._fresh:
            return
        self._last_sample = now

        cpu = self._system_cpu()
        mem = psutil.virtual_memory().percent
        disk = 0.0
        try:
            counters = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            counters = None
        if counters is not None:
            total_bytes = counters.read_bytes + counters.write_bytes
            busy_ms = counters.read_time + counters.write_time
            if self._disk_prev is not None:
                prev_bytes, prev_busy, prev_t = self._disk_prev
                dt = max(now - prev_t, 1e-3)
                disk = min(100.0, max(0.0, (busy_ms - prev_busy) / (dt * 1000) * 100))
                self._disk_rate = max(0.0, (total_bytes - prev_bytes) / dt)
            self._disk_prev = (total_bytes, busy_ms, now)
        active = self._disk_active.read()
        if active is not None:
            disk = active
        self.system = SystemStats(cpu=cpu, mem=mem, disk=disk)

    def end(self) -> None:
        """每轮刷新结束时调用，清理已不再出现的进程。"""
        for pid in list(self._procs):
            if pid not in self._seen:
                self._procs.pop(pid, None)
                self._io_prev.pop(pid, None)
                self._last.pop(pid, None)

    def _proc(self, pid: int) -> psutil.Process | None:
        p = self._procs.get(pid)
        if p is not None and p.is_running():
            return p
        try:
            p = psutil.Process(pid)
            p.cpu_percent(None)
        except psutil.Error:
            return None
        self._procs[pid] = p
        return p

    def _metrics(self, pid: int) -> Metrics | None:
        """单个进程本轮的指标，同一轮内只采样一次。"""
        if pid in self._round:
            return self._round[pid]
        p = self._proc(pid)
        result = None
        if p is not None:
            self._seen.add(pid)
            if not self._fresh and pid in self._last:
                result = self._last[pid]
            else:
                result = self._measure(pid, p)
        self._round[pid] = result
        return result

    def _measure(self, pid: int, p: psutil.Process) -> Metrics | None:
        try:
            with p.oneshot():
                cpu = p.cpu_percent(None)
                rss = p.memory_info().rss
                io = p.io_counters()
        except psutil.Error:
            return None
        now = time.monotonic()
        total = io.read_bytes + io.write_bytes
        prev = self._io_prev.get(pid)
        rate = max(0, total - prev[0]) / max(now - prev[1], 1e-3) if prev else 0.0
        self._io_prev[pid] = (total, now)
        self._last[pid] = (cpu, rss, rate)
        return self._last[pid]

    def sample(self, pid: int) -> tuple[float, float, float] | None:
        """返回 pid 及其所有子进程合计的 (cpu%, 内存%, 磁盘%)。"""
        root = self._proc(pid)
        if root is None:
            return None
        try:
            pids = [pid] + [c.pid for c in root.children(recursive=True)]
        except psutil.Error:
            pids = [pid]

        cpu = 0.0
        rss = 0
        io_rate = 0.0
        for child_pid in pids:
            metrics = self._metrics(child_pid)
            if metrics is not None:
                cpu += metrics[0]
                rss += metrics[1]
                io_rate += metrics[2]

        disk = 0.0
        if self._disk_rate > 0:
            disk = self.system.disk * min(1.0, io_rate / self._disk_rate)
        return (
            min(100.0, cpu / self._cpu_count),
            rss / self._mem_total * 100,
            disk,
        )
