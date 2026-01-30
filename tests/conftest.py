"""Shared test fixtures for pause-monitor."""

import time
from pathlib import Path

import pytest

from pause_monitor.collector import ProcessScore
from pause_monitor.storage import init_database


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
    cmprs: int = 0,
    pageins: int = 0,
    csw: int = 100,
    sysbsd: int = 50,
    threads: int = 4,
    categories: frozenset[str] | None = None,
    captured_at: float | None = None,
) -> ProcessScore:
    """Create a ProcessScore for testing."""
    return ProcessScore(
        pid=pid,
        command=command,
        cpu=cpu,
        state=state,
        mem=mem,
        cmprs=cmprs,
        pageins=pageins,
        csw=csw,
        sysbsd=sysbsd,
        threads=threads,
        score=score,
        categories=categories or frozenset({"cpu"}),
        captured_at=captured_at or time.time(),
    )
