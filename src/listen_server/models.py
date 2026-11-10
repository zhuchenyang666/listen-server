from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class State(StrEnum):
    RUNNING = "运行中"
    STOPPED = "已停止"
    FAILED = "异常退出"
    UNREGISTERED = "未登记"


@dataclass
class SystemStats:
    cpu: float = 0.0
    mem: float = 0.0
    disk: float = 0.0


class LaunchMode(StrEnum):
    BACKGROUND = "background"
    TERMINAL = "terminal"

    @property
    def label(self) -> str:
        return "无终端启动" if self is LaunchMode.BACKGROUND else "终端启动"


@dataclass
class ServiceConfig:
    name: str
    command: str
    cwd: Path
    type: str | None = None
    file: str = ""  # 程序文件（jar / exe / 脚本），用于识别外部启动的同一服务
    runtime: str = ""
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""
    mode: LaunchMode = LaunchMode.BACKGROUND
    autostart: bool = False
    spec: dict | None = None  # 添加服务时填写的原始表单数据，编辑时回填


@dataclass
class ServiceStatus:
    key: str
    name: str
    type: str
    state: State
    ports: list[int] = field(default_factory=list)  # 进程树实际监听的端口
    pid: int | None = None
    root_pids: list[int] = field(default_factory=list)  # 停止时结束这些进程树
    bind: str = ""
    started_at: float | None = None
    description: str = ""
    cwd: str = ""
    command: str = ""
    process_name: str = ""
    exit_code: int | None = None
    cpu: float | None = None   # 占全部 CPU 的百分比
    mem: float | None = None   # 占物理内存的百分比
    disk: float | None = None  # 占磁盘活动时间的百分比（按 I/O 吞吐量份额估算）
    config: ServiceConfig | None = None

    @property
    def registered(self) -> bool:
        return self.config is not None

    @property
    def alive(self) -> bool:
        return self.state in (State.RUNNING, State.UNREGISTERED)

    @property
    def port(self) -> int | None:
        return self.ports[0] if self.ports else None

    @property
    def ports_text(self) -> str:
        return ", ".join(map(str, self.ports)) if self.ports else "-"
