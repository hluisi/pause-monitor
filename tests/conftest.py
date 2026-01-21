"""Shared test fixtures for pause-monitor."""

from pathlib import Path

import pytest

from pause_monitor.storage import init_database
from pause_monitor.stress import StressBreakdown


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(tmp_db: Path) -> Path:
    """Create an initialized database with schema."""
    init_database(tmp_db)
    return tmp_db


def create_test_stress() -> StressBreakdown:
    """Create a StressBreakdown for testing."""
    return StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)


@pytest.fixture
def sample_stress() -> StressBreakdown:
    """Fixture for a sample StressBreakdown."""
    return create_test_stress()
