"""Shared test fixtures for pause-monitor."""

from pathlib import Path

import pytest

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
