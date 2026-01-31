"""Shared test fixtures for rogue-hunter."""

import time
from pathlib import Path

import pytest

from rogue_hunter.collector import MetricValue, MetricValueStr, ProcessScore
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


def make_metric(
    value: float | int,
    low: float | int | None = None,
    high: float | int | None = None,
) -> MetricValue:
    """Create a MetricValue for testing with optional low/high."""
    return MetricValue(
        current=value,
        low=low if low is not None else value,
        high=high if high is not None else value,
    )


def make_metric_str(value: str, low: str | None = None, high: str | None = None) -> MetricValueStr:
    """Create a MetricValueStr for testing with optional low/high."""
    return MetricValueStr(
        current=value,
        low=low if low is not None else value,
        high=high if high is not None else value,
    )


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
    """Create a ProcessScore for testing.

    All MetricValue fields are created with low=high=current.
    """
    cap_time = captured_at or time.time()
    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=cap_time,
        # CPU
        cpu=make_metric(cpu),
        # Memory
        mem=make_metric(mem),
        mem_peak=mem_peak,
        pageins=make_metric(pageins),
        pageins_rate=make_metric(pageins_rate),
        faults=make_metric(faults),
        faults_rate=make_metric(faults_rate),
        # Disk I/O
        disk_io=make_metric(disk_io),
        disk_io_rate=make_metric(disk_io_rate),
        # Activity
        csw=make_metric(csw),
        csw_rate=make_metric(csw_rate),
        syscalls=make_metric(syscalls),
        syscalls_rate=make_metric(syscalls_rate),
        threads=make_metric(threads),
        mach_msgs=make_metric(mach_msgs),
        mach_msgs_rate=make_metric(mach_msgs_rate),
        # Efficiency
        instructions=make_metric(instructions),
        cycles=make_metric(cycles),
        ipc=make_metric(ipc),
        # Power
        energy=make_metric(energy),
        energy_rate=make_metric(energy_rate),
        wakeups=make_metric(wakeups),
        wakeups_rate=make_metric(wakeups_rate),
        # Contention
        runnable_time=make_metric(runnable_time),
        runnable_time_rate=make_metric(runnable_time_rate),
        qos_interactive=make_metric(qos_interactive),
        qos_interactive_rate=make_metric(qos_interactive_rate),
        # State
        state=make_metric_str(state),
        priority=make_metric(priority),
        # Scoring
        score=make_metric(score),
        band=make_metric_str(band),
        blocking_score=make_metric(blocking_score),
        contention_score=make_metric(contention_score),
        pressure_score=make_metric(pressure_score),
        efficiency_score=make_metric(efficiency_score),
        dominant_category=dominant_category,
        dominant_metrics=dominant_metrics or [],
    )
