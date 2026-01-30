"""Tests for CLI commands."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pause_monitor.cli import main
from pause_monitor.config import (
    BandsConfig,
    Config,
    RetentionConfig,
)
from pause_monitor.storage import create_process_event, init_database


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

        with (
            patch("pause_monitor.config.Config.load") as mock_load,
            patch("pause_monitor.boottime.get_boot_time", return_value=1706000000),
        ):
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "No events recorded" in result.output

    def test_events_listing(self, runner: CliRunner, tmp_path: Path) -> None:
        """events lists events when present."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        conn = sqlite3.connect(db_path)
        create_process_event(
            conn,
            pid=1234,
            command="test_proc",
            boot_time=1706000000,
            entry_time=time.time() - 60,
            entry_band="elevated",
            peak_score=50,
            peak_band="high",
        )
        conn.close()

        with (
            patch("pause_monitor.config.Config.load") as mock_load,
            patch("pause_monitor.boottime.get_boot_time", return_value=1706000000),
        ):
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "ID" in result.output
        assert "Command" in result.output
        assert "test_proc" in result.output
        assert "high" in result.output

    def test_events_show_specific_event(self, runner: CliRunner, tmp_path: Path) -> None:
        """events show <id> shows a specific event."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        from pause_monitor.storage import insert_process_snapshot, update_process_event_peak
        from tests.conftest import make_process_score

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        event_id = create_process_event(
            conn,
            pid=1234,
            command="test_proc",
            boot_time=1706000000,
            entry_time=time.time() - 60,
            entry_band="elevated",
            peak_score=75,
            peak_band="high",
        )
        # Insert a snapshot so peak_snapshot is available for display
        score = make_process_score(pid=1234, command="test_proc", score=75, cpu=80.0)
        snap_id = insert_process_snapshot(conn, event_id, "entry", score)
        update_process_event_peak(conn, event_id, 75, "high", snap_id)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "show", str(event_id)])

        assert result.exit_code == 0
        assert f"Process Event #{event_id}" in result.output
        assert "test_proc" in result.output
        assert "Peak Score: 75" in result.output

    def test_events_nonexistent_id(self, runner: CliRunner, tmp_path: Path) -> None:
        """events show <id> with non-existent ID shows error."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "show", "99999"])

        assert result.exit_code == 1
        assert "Error: Event 99999 not found" in result.output

    def test_events_limit_option(self, runner: CliRunner, tmp_path: Path) -> None:
        """events --limit restricts number of events shown."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert multiple test events
        conn = sqlite3.connect(db_path)
        for i in range(5):
            create_process_event(
                conn,
                pid=1000 + i,
                command=f"proc_{i}",
                boot_time=1706000000,
                entry_time=time.time() - (60 * i),
                entry_band="elevated",
                peak_score=50 + i,
                peak_band="high",
            )
        conn.close()

        with (
            patch("pause_monitor.config.Config.load") as mock_load,
            patch("pause_monitor.boottime.get_boot_time", return_value=1706000000),
        ):
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "-n", "2"])

        assert result.exit_code == 0
        # Count data rows (skip header and separator)
        lines = [line for line in result.output.strip().split("\n") if line.strip()]
        data_lines = [line for line in lines if not line.startswith("ID") and "---" not in line]
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
        """history with empty database shows 'No events'."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "No events in the last 24 hours" in result.output

    def test_history_table_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history with table format shows summary stats."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test events
        conn = sqlite3.connect(db_path)
        for i in range(3):
            entry = time.time() - (i * 3600)
            exit_time = entry + 30
            event_id = create_process_event(
                conn,
                pid=1000 + i,
                command=f"proc_{i}",
                boot_time=1706000000,
                entry_time=entry,
                entry_band="elevated",
                peak_score=50 + i * 10,
                peak_band="elevated",
            )
            # Close the event
            conn.execute(
                "UPDATE process_events SET exit_time = ? WHERE id = ?",
                (exit_time, event_id),
            )
            conn.commit()
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Events: 3" in result.output
        assert "Time range:" in result.output
        assert "Peak scores" in result.output

    def test_history_json_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format json outputs JSON array."""
        import json
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        conn = sqlite3.connect(db_path)
        entry = time.time() - 3600
        event_id = create_process_event(
            conn,
            pid=1234,
            command="test_proc",
            boot_time=1706000000,
            entry_time=entry,
            entry_band="elevated",
            peak_score=50,
            peak_band="elevated",
        )
        conn.execute("UPDATE process_events SET exit_time = ? WHERE id = ?", (entry + 45, event_id))
        conn.commit()
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
        assert "entry" in data[0]
        assert "peak_score" in data[0]
        assert data[0]["peak_score"] == 50

    def test_history_csv_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format csv outputs CSV with header."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        conn = sqlite3.connect(db_path)
        entry = time.time() - 3600
        event_id = create_process_event(
            conn,
            pid=1234,
            command="test_proc",
            boot_time=1706000000,
            entry_time=entry,
            entry_band="elevated",
            peak_score=50,
            peak_band="elevated",
        )
        conn.execute("UPDATE process_events SET exit_time = ? WHERE id = ?", (entry + 30, event_id))
        conn.commit()
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history", "-f", "csv"])

        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        # Header should have event fields
        assert "id" in lines[0]
        assert "entry" in lines[0]
        assert "peak_score" in lines[0]
        assert len(lines) == 2  # header + 1 data row

    def test_history_hours_option(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --hours limits time range."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert event from 2 hours ago (outside --hours 1 range)
        conn = sqlite3.connect(db_path)
        entry = time.time() - 2 * 3600
        create_process_event(
            conn,
            pid=1234,
            command="old_proc",
            boot_time=1706000000,
            entry_time=entry,
            entry_band="elevated",
            peak_score=50,
            peak_band="elevated",
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history", "-H", "1"])

        assert result.exit_code == 0
        assert "No events in the last 1 hour" in result.output


class TestPruneCommand:
    """Tests for the prune command."""

    def test_prune_no_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune with no database shows message."""
        mock_db_path = tmp_path / "nonexistent" / "data.db"

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = mock_db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune"])

        assert result.exit_code == 0
        assert "Database not found" in result.output

    def test_prune_dry_run(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune --dry-run shows what would be deleted."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune", "--dry-run"])

        assert result.exit_code == 0
        assert "Would prune closed events older than 90 days" in result.output

    def test_prune_with_nothing_to_delete(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune with no old data shows zeros."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune", "--force"])

        assert result.exit_code == 0
        assert "Deleted 0 events" in result.output

    def test_prune_requires_confirmation(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune without --force aborts without confirmation."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            # Don't confirm (default is 'n')
            result = runner.invoke(main, ["prune"])

        assert result.exit_code == 1
        assert "Aborted" in result.output

    def test_prune_interactive_confirmation(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune proceeds when user confirms interactively."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune"], input="y\n")

        assert result.exit_code == 0
        assert "Delete closed events older than 90 days" in result.output
        assert "Deleted 0 events" in result.output


def _make_path_prop(path: Path):
    """Create a property that returns a fixed path."""
    return property(lambda self: path)


class TestConfigCommand:
    """Tests for the config command group."""

    def test_config_show_defaults(self, runner: CliRunner, tmp_path: Path) -> None:
        """config show displays default values when no config file exists."""
        config_path = tmp_path / "config.toml"

        with patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)):
            result = runner.invoke(main, ["config", "show"])

        assert result.exit_code == 0
        assert "Config file:" in result.output
        assert "Exists: False" in result.output
        assert "[retention]" in result.output
        # Verify output contains actual default values from config dataclasses
        defaults = Config()
        assert f"events_days = {defaults.retention.events_days}" in result.output
        assert "[bands]" in result.output
        assert f"medium = {defaults.bands.medium}" in result.output
        assert f"critical = {defaults.bands.critical}" in result.output

    def test_config_show_custom_values(self, runner: CliRunner, tmp_path: Path) -> None:
        """config show displays custom values from config file."""
        config_path = tmp_path / "config.toml"

        # Create a custom config
        custom_config = Config(
            bands=BandsConfig(
                medium=15,
                elevated=30,
                high=50,
                critical=70,
            ),
            retention=RetentionConfig(events_days=60),
        )
        custom_config.save(config_path)

        with patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)):
            result = runner.invoke(main, ["config", "show"])

        assert result.exit_code == 0
        assert "Exists: True" in result.output
        assert "medium = 15" in result.output
        assert "critical = 70" in result.output
        assert "events_days = 60" in result.output

    def test_config_edit_creates_default(self, runner: CliRunner, tmp_path: Path) -> None:
        """config edit creates default config if it doesn't exist."""
        config_path = tmp_path / "config.toml"

        with (
            patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(tmp_path)),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {"EDITOR": "vim"}),
        ):
            result = runner.invoke(main, ["config", "edit"])

        assert result.exit_code == 0
        assert f"Created default config at {config_path}" in result.output
        assert config_path.exists()
        mock_run.assert_called_once_with(["vim", str(config_path)])

    def test_config_reset_with_confirmation(self, runner: CliRunner, tmp_path: Path) -> None:
        """config reset resets config when user confirms."""
        config_path = tmp_path / "config.toml"

        # Create custom config first
        custom_config = Config(
            retention=RetentionConfig(events_days=999),
        )
        custom_config.save(config_path)

        with (
            patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(tmp_path)),
        ):
            result = runner.invoke(main, ["config", "reset", "--yes"])

        assert result.exit_code == 0
        assert f"Config reset to defaults at {config_path}" in result.output

        # Verify defaults were written
        reset_config = Config.load(config_path)
        assert reset_config.retention.events_days == 90
        assert reset_config.bands.medium == 20


class TestInstallCommand:
    """Tests for the install command."""

    def test_install_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """install fails without root privileges."""
        with patch("os.getuid", return_value=501):  # Non-root user
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 1
        assert "requires root privileges" in result.output

    def test_install_requires_sudo_user(self, runner: CliRunner, tmp_path: Path) -> None:
        """install fails when run as root directly (not via sudo)."""
        with (
            patch("os.getuid", return_value=0),  # Root user
            patch.dict("os.environ", {"SUDO_USER": ""}, clear=False),
        ):
            # Clear SUDO_USER to simulate running as root directly
            import os

            original = os.environ.get("SUDO_USER")
            if "SUDO_USER" in os.environ:
                del os.environ["SUDO_USER"]
            try:
                result = runner.invoke(main, ["install"])
            finally:
                if original is not None:
                    os.environ["SUDO_USER"] = original

        assert result.exit_code == 1
        assert "Could not determine user" in result.output


class TestUninstallCommand:
    """Tests for the uninstall command."""

    def test_uninstall_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall fails without root privileges."""
        with patch("os.getuid", return_value=501):  # Non-root user
            result = runner.invoke(main, ["uninstall"])

        assert result.exit_code == 1
        assert "requires root privileges" in result.output

    def test_uninstall_requires_sudo_user(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall fails when run as root directly (not via sudo)."""
        with (
            patch("os.getuid", return_value=0),  # Root user
            patch.dict("os.environ", {"SUDO_USER": ""}, clear=False),
        ):
            # Clear SUDO_USER to simulate running as root directly
            import os

            original = os.environ.get("SUDO_USER")
            if "SUDO_USER" in os.environ:
                del os.environ["SUDO_USER"]
            try:
                result = runner.invoke(main, ["uninstall"])
            finally:
                if original is not None:
                    os.environ["SUDO_USER"] = original

        assert result.exit_code == 1
        assert "Could not determine user" in result.output


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_no_database(self, runner: CliRunner, tmp_path: Path) -> None:
        """status with no database shows message."""
        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = tmp_path / "nonexistent" / "data.db"
            mock_config.socket_path = tmp_path / "daemon.sock"
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "Daemon: stopped" in result.output
        assert "Database not found" in result.output

    def test_status_no_events(self, runner: CliRunner, tmp_path: Path) -> None:
        """status with no active events shows message."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with (
            patch("pause_monitor.config.Config.load") as mock_load,
            patch("pause_monitor.boottime.get_boot_time", return_value=1706000000),
        ):
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_config.socket_path = tmp_path / "daemon.sock"
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "No active process tracking" in result.output

    def test_status_with_active_events(self, runner: CliRunner, tmp_path: Path) -> None:
        """status shows active tracked processes."""
        import sqlite3

        db_path = tmp_path / "data.db"
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        create_process_event(
            conn,
            pid=1234,
            command="chrome",
            boot_time=1706000000,
            entry_time=time.time() - 30,
            entry_band="elevated",
            peak_score=65,
            peak_band="high",
        )
        conn.close()

        with (
            patch("pause_monitor.config.Config.load") as mock_load,
            patch("pause_monitor.boottime.get_boot_time", return_value=1706000000),
        ):
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_config.socket_path = tmp_path / "daemon.sock"
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "Active tracked processes: 1" in result.output
        assert "chrome" in result.output
        assert "PID 1234" in result.output
