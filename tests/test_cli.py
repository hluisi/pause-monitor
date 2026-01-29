"""Tests for CLI commands."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pause_monitor.cli import main
from pause_monitor.config import (
    AlertsConfig,
    BandsConfig,
    Config,
    RetentionConfig,
    SamplingConfig,
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
            peak_snapshot="{}",
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

        conn = sqlite3.connect(db_path)
        event_id = create_process_event(
            conn,
            pid=1234,
            command="test_proc",
            boot_time=1706000000,
            entry_time=time.time() - 60,
            entry_band="elevated",
            peak_score=75,
            peak_band="high",
            peak_snapshot='{"cpu": 80.0}',
        )
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
                peak_snapshot="{}",
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
                peak_snapshot="{}",
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
            peak_snapshot="{}",
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
            peak_snapshot="{}",
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
            peak_snapshot="{}",
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
        assert "[sampling]" in result.output
        assert "normal_interval = 5" in result.output
        assert "elevated_interval = 1" in result.output
        assert "[bands]" in result.output
        assert "low = 20" in result.output
        assert "critical = 100" in result.output
        assert "[retention]" in result.output
        assert "events_days = 90" in result.output
        assert "[alerts]" in result.output
        assert "enabled = True" in result.output
        assert "sound = True" in result.output
        assert "learning_mode = False" in result.output

    def test_config_show_custom_values(self, runner: CliRunner, tmp_path: Path) -> None:
        """config show displays custom values from config file."""
        config_path = tmp_path / "config.toml"

        # Create a custom config
        custom_config = Config(
            sampling=SamplingConfig(
                normal_interval=10,
                elevated_interval=2,
            ),
            bands=BandsConfig(
                low=15,
                medium=30,
                elevated=50,
                high=70,
                critical=90,
            ),
            retention=RetentionConfig(events_days=60),
            alerts=AlertsConfig(enabled=False, sound=False),
            learning_mode=True,
        )
        custom_config.save(config_path)

        with patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)):
            result = runner.invoke(main, ["config", "show"])

        assert result.exit_code == 0
        assert "Exists: True" in result.output
        assert "normal_interval = 10" in result.output
        assert "elevated_interval = 2" in result.output
        assert "low = 15" in result.output
        assert "critical = 90" in result.output
        assert "events_days = 60" in result.output
        assert "enabled = False" in result.output
        assert "sound = False" in result.output
        assert "learning_mode = True" in result.output

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
            sampling=SamplingConfig(normal_interval=99),
            learning_mode=True,
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
        assert reset_config.sampling.normal_interval == 5
        assert reset_config.learning_mode is False


class TestInstallCommand:
    """Tests for the install command."""

    def test_install_user_default(self, runner: CliRunner, tmp_path: Path) -> None:
        """install creates plist in ~/Library/LaunchAgents by default."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run") as mock_run,
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert "Created" in result.output

        # Verify plist was created
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        assert plist_path.exists()

        # Verify plist content
        content = plist_path.read_text()
        assert "<key>Label</key>" in content
        assert "<string>com.pause-monitor.daemon</string>" in content

        # Verify launchctl bootstrap was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootstrap"

    def test_install_system_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """install --system fails without root privileges."""
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),  # Non-root user
        ):
            result = runner.invoke(main, ["install", "--system"])

        assert result.exit_code == 1
        assert "Error: --system requires root privileges" in result.output


class TestUninstallCommand:
    """Tests for the uninstall command."""

    def test_uninstall_user_default(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall removes plist from ~/Library/LaunchAgents by default."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create config directories that would be prompted for deletion
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run") as mock_run,
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            # Use --keep-data to avoid prompts
            result = runner.invoke(main, ["uninstall", "--keep-data"])

        assert result.exit_code == 0
        assert f"Removed {plist_path}" in result.output
        assert "Uninstall complete" in result.output
        assert not plist_path.exists()

        # Verify launchctl bootout was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootout"

    def test_uninstall_not_installed(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall shows message when service was not installed."""
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--keep-data"])

        assert result.exit_code == 0
        assert "Service was not installed" in result.output
        assert "Uninstall complete" in result.output

    def test_uninstall_system_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall --system fails without root privileges."""
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),  # Non-root user
        ):
            result = runner.invoke(main, ["uninstall", "--system"])

        assert result.exit_code == 1
        assert "Error: --system requires root privileges" in result.output


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
            peak_snapshot="{}",
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
