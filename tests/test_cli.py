"""Tests for CLI commands."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pause_monitor.cli import main
from pause_monitor.config import Config
from pause_monitor.storage import Event, init_database, insert_event
from pause_monitor.stress import StressBreakdown


@pytest.fixture
def runner() -> CliRunner:
    """Create a CLI test runner."""
    return CliRunner()


class TestEventsCommand:
    """Tests for the events command."""

    def test_events_no_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """events with no database shows helpful message."""
        # Point to a non-existent database
        mock_db_path = tmp_path / "nonexistent" / "data.db"

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = mock_db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "Database not found" in result.output
        assert "pause-monitor daemon" in result.output

    def test_events_empty_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """events with empty database shows 'No events recorded'."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "No events recorded" in result.output

    def test_events_listing(self, runner: CliRunner, tmp_path: Path) -> None:
        """events lists events when present."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test events
        import sqlite3

        conn = sqlite3.connect(db_path)
        stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
        event = Event(
            timestamp=datetime(2026, 1, 20, 14, 30, 0),
            duration=2.5,
            stress=stress,
            culprits=["codemeter", "WindowServer"],
            event_dir=None,
            notes=None,
        )
        insert_event(conn, event)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "ID" in result.output
        assert "Time" in result.output
        assert "Duration" in result.output
        assert "Stress" in result.output
        assert "2.5s" in result.output
        assert "codemeter" in result.output

    def test_events_show_specific_event(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """events <id> shows a specific event."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        stress = StressBreakdown(load=20, memory=10, thermal=5, latency=15, io=0)
        event = Event(
            timestamp=datetime(2026, 1, 20, 14, 30, 0),
            duration=3.5,
            stress=stress,
            culprits=["kernel_task"],
            event_dir="/path/to/forensics",
            notes="Test pause event",
        )
        event_id = insert_event(conn, event)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", str(event_id)])

        assert result.exit_code == 0
        assert f"Event #{event_id}" in result.output
        assert "Duration: 3.5s" in result.output
        assert "Stress: 50/100" in result.output
        assert "Load: 20" in result.output
        assert "Memory: 10" in result.output
        assert "Thermal: 5" in result.output
        assert "Latency: 15" in result.output
        assert "kernel_task" in result.output
        assert "/path/to/forensics" in result.output
        assert "Test pause event" in result.output

    def test_events_nonexistent_id(self, runner: CliRunner, tmp_path: Path) -> None:
        """events <id> with non-existent ID shows error."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "99999"])

        assert result.exit_code == 0
        assert "Event 99999 not found" in result.output

    def test_events_limit_option(self, runner: CliRunner, tmp_path: Path) -> None:
        """events --limit restricts number of events shown."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert multiple test events
        import sqlite3

        conn = sqlite3.connect(db_path)
        stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
        for i in range(5):
            event = Event(
                timestamp=datetime(2026, 1, 20, 14, 30 + i, 0),
                duration=1.0 + i,
                stress=stress,
                culprits=[],
                event_dir=None,
                notes=None,
            )
            insert_event(conn, event)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "-n", "2"])

        assert result.exit_code == 0
        # Count data rows (skip header and separator)
        lines = [line for line in result.output.strip().split("\n") if line.strip()]
        # Header line, separator line, 2 data lines
        data_lines = [
            line for line in lines if not line.startswith("ID") and "---" not in line
        ]
        assert len(data_lines) == 2


class TestHistoryCommand:
    """Tests for the history command."""

    def test_history_no_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """history with no database shows helpful message."""
        mock_db_path = tmp_path / "nonexistent" / "data.db"

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = mock_db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Database not found" in result.output
        assert "pause-monitor daemon" in result.output

    def test_history_empty_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """history with empty database shows 'No samples'."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "No samples in the last 24 hours" in result.output

    def test_history_table_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history with table format shows summary stats."""
        from pause_monitor.storage import Sample, get_connection, insert_sample

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test samples
        conn = get_connection(db_path)
        for i in range(5):
            stress = StressBreakdown(
                load=10 + i * 5, memory=5, thermal=0, latency=0, io=0
            )
            sample = Sample(
                timestamp=datetime.now(),
                interval=5.0,
                cpu_pct=25.0 + i,
                load_avg=1.5 + i * 0.2,
                mem_available=8000000000,
                swap_used=0,
                io_read=1000,
                io_write=500,
                net_sent=100,
                net_recv=200,
                cpu_temp=45.0,
                cpu_freq=2400,
                throttled=False,
                gpu_pct=0.0,
                stress=stress,
            )
            insert_sample(conn, sample)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Samples: 5" in result.output
        assert "Time range:" in result.output
        assert "Stress - Min:" in result.output
        assert "Max:" in result.output
        assert "Avg:" in result.output

    def test_history_json_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format json outputs JSON array."""
        import json

        from pause_monitor.storage import Sample, get_connection, insert_sample

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test sample
        conn = get_connection(db_path)
        stress = StressBreakdown(load=15, memory=5, thermal=0, latency=0, io=0)
        sample = Sample(
            timestamp=datetime.now(),
            interval=5.0,
            cpu_pct=30.0,
            load_avg=2.0,
            mem_available=8000000000,
            swap_used=0,
            io_read=1000,
            io_write=500,
            net_sent=100,
            net_recv=200,
            cpu_temp=45.0,
            cpu_freq=2400,
            throttled=False,
            gpu_pct=0.0,
            stress=stress,
        )
        insert_sample(conn, sample)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history", "-f", "json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert "timestamp" in data[0]
        assert "stress" in data[0]
        assert "cpu_pct" in data[0]
        assert "load_avg" in data[0]
        assert data[0]["stress"] == 20  # 15 + 5
        assert data[0]["cpu_pct"] == 30.0

    def test_history_csv_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format csv outputs CSV with header."""
        from pause_monitor.storage import Sample, get_connection, insert_sample

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test sample
        conn = get_connection(db_path)
        stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
        sample = Sample(
            timestamp=datetime.now(),
            interval=5.0,
            cpu_pct=25.0,
            load_avg=1.5,
            mem_available=8000000000,
            swap_used=0,
            io_read=1000,
            io_write=500,
            net_sent=100,
            net_recv=200,
            cpu_temp=45.0,
            cpu_freq=2400,
            throttled=False,
            gpu_pct=0.0,
            stress=stress,
        )
        insert_sample(conn, sample)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history", "-f", "csv"])

        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert lines[0] == "timestamp,stress,cpu_pct,load_avg"
        assert len(lines) == 2  # header + 1 data row
        # Verify data row has correct number of columns
        data_parts = lines[1].split(",")
        assert len(data_parts) == 4
        assert data_parts[1] == "15"  # stress total: 10 + 5
        assert data_parts[2] == "25.0"  # cpu_pct

    def test_history_high_stress_periods(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """history shows high stress period summary when present."""
        from pause_monitor.storage import Sample, get_connection, insert_sample

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert samples with varying stress (some >= 30)
        conn = get_connection(db_path)
        for stress_val in [10, 20, 35, 45, 25]:
            stress = StressBreakdown(load=stress_val, memory=0, thermal=0, latency=0, io=0)
            sample = Sample(
                timestamp=datetime.now(),
                interval=5.0,
                cpu_pct=25.0,
                load_avg=1.5,
                mem_available=8000000000,
                swap_used=0,
                io_read=1000,
                io_write=500,
                net_sent=100,
                net_recv=200,
                cpu_temp=45.0,
                cpu_freq=2400,
                throttled=False,
                gpu_pct=0.0,
                stress=stress,
            )
            insert_sample(conn, sample)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "High stress periods: 2 samples" in result.output
        assert "40.0% of time" in result.output

    def test_history_hours_option(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --hours limits time range."""
        from datetime import timedelta

        from pause_monitor.storage import Sample, get_connection, insert_sample

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert sample from 2 hours ago (outside --hours 1 range)
        conn = get_connection(db_path)
        stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
        old_sample = Sample(
            timestamp=datetime.now() - timedelta(hours=2),
            interval=5.0,
            cpu_pct=25.0,
            load_avg=1.5,
            mem_available=8000000000,
            swap_used=0,
            io_read=1000,
            io_write=500,
            net_sent=100,
            net_recv=200,
            cpu_temp=45.0,
            cpu_freq=2400,
            throttled=False,
            gpu_pct=0.0,
            stress=stress,
        )
        insert_sample(conn, old_sample)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history", "-h", "1"])

        assert result.exit_code == 0
        assert "No samples in the last 1 hours" in result.output
