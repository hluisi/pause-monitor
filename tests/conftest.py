"""Shared test fixtures for rogue-hunter."""

import time
from pathlib import Path

import pytest

from rogue_hunter.collector import ProcessScore
from rogue_hunter.storage import init_database


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(tmp_db: Path) -> Path:
    """Create an initialized database with schema."""
    init_database(tmp_db)
    return tmp_db


def make_process_score(
    pid: int = 123,
    command: str = "test_cmd",
    score: int = 50,
    cpu: float = 25.0,
    state: str = "running",
    mem: int = 100,
    mem_peak: int = 150,
    pageins: int = 0,
    pageins_rate: float = 0.0,
    faults: int = 0,
    faults_rate: float = 0.0,
    disk_io: int = 0,
    disk_io_rate: float = 0.0,
    csw: int = 100,
    csw_rate: float = 0.0,
    syscalls: int = 50,
    syscalls_rate: float = 0.0,
    threads: int = 4,
    mach_msgs: int = 0,
    mach_msgs_rate: float = 0.0,
    instructions: int = 0,
    cycles: int = 0,
    ipc: float = 0.0,
    energy: int = 0,
    energy_rate: float = 0.0,
    wakeups: int = 0,
    wakeups_rate: float = 0.0,
    runnable_time: int = 0,
    runnable_time_rate: float = 0.0,
    qos_interactive: int = 0,
    qos_interactive_rate: float = 0.0,
    gpu_time: int = 0,
    gpu_time_rate: float = 0.0,
    zombie_children: int = 0,
    priority: int = 31,
    band: str = "elevated",
    blocking_score: float = 0.0,
    contention_score: float = 0.0,
    pressure_score: float = 0.0,
    efficiency_score: float = 0.0,
    dominant_category: str = "blocking",
    dominant_metrics: list[str] | None = None,
    captured_at: float | None = None,
) -> ProcessScore:
    """Create a ProcessScore for testing."""
    cap_time = captured_at or time.time()
    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=cap_time,
        # CPU
        cpu=cpu,
        # Memory
        mem=mem,
        mem_peak=mem_peak,
        pageins=pageins,
        pageins_rate=pageins_rate,
        faults=faults,
        faults_rate=faults_rate,
        # Disk I/O
        disk_io=disk_io,
        disk_io_rate=disk_io_rate,
        # Activity
        csw=csw,
        csw_rate=csw_rate,
        syscalls=syscalls,
        syscalls_rate=syscalls_rate,
        threads=threads,
        mach_msgs=mach_msgs,
        mach_msgs_rate=mach_msgs_rate,
        # Efficiency
        instructions=instructions,
        cycles=cycles,
        ipc=ipc,
        # Power
        energy=energy,
        energy_rate=energy_rate,
        wakeups=wakeups,
        wakeups_rate=wakeups_rate,
        # Contention
        runnable_time=runnable_time,
        runnable_time_rate=runnable_time_rate,
        qos_interactive=qos_interactive,
        qos_interactive_rate=qos_interactive_rate,
        # GPU
        gpu_time=gpu_time,
        gpu_time_rate=gpu_time_rate,
        # Zombie children
        zombie_children=zombie_children,
        # State
        state=state,
        priority=priority,
        # Scoring
        score=score,
        band=band,
        blocking_score=blocking_score,
        contention_score=contention_score,
        pressure_score=pressure_score,
        efficiency_score=efficiency_score,
        dominant_category=dominant_category,
        dominant_metrics=dominant_metrics or [],
    )
