"""Tests for CLI commands."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pause_monitor.cli import main
from pause_monitor.config import AlertsConfig, Config, RetentionConfig, SamplingConfig
from pause_monitor.storage import create_event, finalize_event, init_database


def create_test_event(
    conn,
    start_time: datetime,
    duration_seconds: float = 2.5,
    peak_stress: int = 15,
    peak_tier: int = 2,
) -> int:
    """Create a test event using the new API."""
    from datetime import timedelta

    event_id = create_event(conn, start_time)
    finalize_event(
        conn,
        event_id,
        end_timestamp=start_time + timedelta(seconds=duration_seconds),
        peak_stress=peak_stress,
        peak_tier=peak_tier,
    )
    return event_id


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

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
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

    def test_events_show_specific_event(self, runner: CliRunner, tmp_path: Path) -> None:
        """events <id> shows a specific event."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=3.5,
            peak_stress=50,
            peak_tier=2,
        )
        # Add notes via update
        conn.execute("UPDATE events SET notes = ? WHERE id = ?", ("Test pause event", event_id))
        conn.commit()
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "show", str(event_id)])

        assert result.exit_code == 0
        assert f"Event #{event_id}" in result.output
        assert "Duration: 3.5s" in result.output
        assert "Peak Stress: 50/100" in result.output
        assert "Test pause event" in result.output

    def test_events_nonexistent_id(self, runner: CliRunner, tmp_path: Path) -> None:
        """events <id> with non-existent ID shows error."""
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
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert multiple test events
        import sqlite3

        conn = sqlite3.connect(db_path)
        for i in range(5):
            create_test_event(
                conn,
                start_time=datetime(2026, 1, 20, 14, 30 + i, 0),
                duration_seconds=1.0 + i,
                peak_stress=15,
                peak_tier=2,
            )
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
        from datetime import timedelta

        from pause_monitor.storage import create_event, finalize_event, get_connection

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test events
        conn = get_connection(db_path)
        for i in range(3):
            start = datetime.now() - timedelta(hours=i)
            event_id = create_event(conn, start)
            finalize_event(
                conn,
                event_id,
                end_timestamp=start + timedelta(seconds=30 + i * 10),
                peak_stress=20 + i * 10,
                peak_tier=2,
            )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Events: 3" in result.output
        assert "Time range:" in result.output
        assert "Peak stress - Min:" in result.output
        assert "Tier 2 (elevated):" in result.output

    def test_history_json_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format json outputs JSON array."""
        import json
        from datetime import timedelta

        from pause_monitor.storage import create_event, finalize_event, get_connection

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        conn = get_connection(db_path)
        start = datetime.now() - timedelta(hours=1)
        event_id = create_event(conn, start)
        finalize_event(
            conn,
            event_id,
            end_timestamp=start + timedelta(seconds=45),
            peak_stress=35,
            peak_tier=2,
        )
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
        assert "start" in data[0]
        assert "peak_stress" in data[0]
        assert data[0]["peak_stress"] == 35
        assert data[0]["peak_tier"] == 2

    def test_history_csv_format(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --format csv outputs CSV with header."""
        from datetime import timedelta

        from pause_monitor.storage import create_event, finalize_event, get_connection

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        conn = get_connection(db_path)
        start = datetime.now() - timedelta(hours=1)
        event_id = create_event(conn, start)
        finalize_event(
            conn,
            event_id,
            end_timestamp=start + timedelta(seconds=30),
            peak_stress=25,
            peak_tier=2,
        )
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
        assert "start" in lines[0]
        assert "peak_stress" in lines[0]
        assert len(lines) == 2  # header + 1 data row

    def test_history_tier_breakdown(self, runner: CliRunner, tmp_path: Path) -> None:
        """history shows tier breakdown with tier 2 and tier 3 events."""
        from datetime import timedelta

        from pause_monitor.storage import create_event, finalize_event, get_connection

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert events with different tiers
        conn = get_connection(db_path)
        # 2 tier 2 events
        for i in range(2):
            start = datetime.now() - timedelta(hours=i)
            event_id = create_event(conn, start)
            finalize_event(
                conn, event_id, start + timedelta(seconds=30), peak_stress=25, peak_tier=2
            )
        # 1 tier 3 event
        start = datetime.now() - timedelta(hours=3)
        event_id = create_event(conn, start)
        finalize_event(conn, event_id, start + timedelta(seconds=60), peak_stress=55, peak_tier=3)
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Tier 2 (elevated): 2 events" in result.output
        assert "Tier 3 (critical): 1 events" in result.output

    def test_history_hours_option(self, runner: CliRunner, tmp_path: Path) -> None:
        """history --hours limits time range."""
        from datetime import timedelta

        from pause_monitor.storage import create_event, finalize_event, get_connection

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert event from 2 hours ago (outside --hours 1 range)
        conn = get_connection(db_path)
        start = datetime.now() - timedelta(hours=2)
        event_id = create_event(conn, start)
        finalize_event(conn, event_id, start + timedelta(seconds=30), peak_stress=25, peak_tier=2)
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
        assert "Would prune reviewed/dismissed events older than 90 days" in result.output

    def test_prune_dry_run_with_override(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune --dry-run with overrides shows custom values."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune", "--dry-run", "--events-days", "14"])

        assert result.exit_code == 0
        assert "Would prune reviewed/dismissed events older than 14 days" in result.output

    def test_prune_deletes_old_events(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune deletes old reviewed events."""
        import sqlite3
        import time

        from pause_monitor.storage import update_event_status

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert old event (100 days ago), marked as reviewed
        conn = sqlite3.connect(db_path)
        old_time = datetime.fromtimestamp(time.time() - 100 * 86400)
        event_id = create_test_event(
            conn,
            start_time=old_time,
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        update_event_status(conn, event_id, "reviewed")
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["prune", "--force"])

        assert result.exit_code == 0
        assert "Deleted 1 events" in result.output

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

    def test_prune_events_days_override(self, runner: CliRunner, tmp_path: Path) -> None:
        """prune --events-days overrides config value."""
        import sqlite3
        import time

        from pause_monitor.storage import update_event_status

        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert event 10 days ago, marked as reviewed
        conn = sqlite3.connect(db_path)
        old_time = datetime.fromtimestamp(time.time() - 10 * 86400)
        event_id = create_test_event(
            conn,
            start_time=old_time,
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        update_event_status(conn, event_id, "reviewed")
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.db_path = db_path
            mock_config.retention.events_days = 90
            mock_load.return_value = mock_config
            # Override to 7 days, so 10-day-old event will be deleted
            result = runner.invoke(main, ["prune", "--events-days", "7", "--force"])

        assert result.exit_code == 0
        assert "Deleted 1 events" in result.output

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
        assert "Delete reviewed/dismissed events older than 90 days?" in result.output
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
        assert "elevation_threshold = 30" in result.output
        assert "critical_threshold = 60" in result.output
        assert "[retention]" in result.output
        assert "samples_days = 30" in result.output
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
                elevation_threshold=40,
                critical_threshold=70,
            ),
            retention=RetentionConfig(samples_days=14, events_days=60),
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
        assert "elevation_threshold = 40" in result.output
        assert "critical_threshold = 70" in result.output
        assert "samples_days = 14" in result.output
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

    def test_config_edit_opens_existing(self, runner: CliRunner, tmp_path: Path) -> None:
        """config edit opens existing config without creating new."""
        config_path = tmp_path / "config.toml"

        # Create existing config
        existing_config = Config()
        existing_config.save(config_path)

        with (
            patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {"EDITOR": "nano"}),
        ):
            result = runner.invoke(main, ["config", "edit"])

        assert result.exit_code == 0
        assert "Created default config" not in result.output
        mock_run.assert_called_once_with(["nano", str(config_path)])

    def test_config_edit_uses_default_editor(self, runner: CliRunner, tmp_path: Path) -> None:
        """config edit uses nano when EDITOR is not set."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        with (
            patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {}, clear=True),
        ):
            # Ensure EDITOR is not set
            import os

            os.environ.pop("EDITOR", None)
            result = runner.invoke(main, ["config", "edit"])

        assert result.exit_code == 0
        mock_run.assert_called_once_with(["nano", str(config_path)])

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

    def test_config_reset_without_confirmation(self, runner: CliRunner, tmp_path: Path) -> None:
        """config reset aborts without --yes flag."""
        config_path = tmp_path / "config.toml"

        # Create custom config first
        custom_config = Config(
            sampling=SamplingConfig(normal_interval=99),
        )
        custom_config.save(config_path)

        with patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)):
            result = runner.invoke(main, ["config", "reset"])

        assert result.exit_code == 1
        assert "Aborted" in result.output

        # Verify config was NOT reset
        unchanged_config = Config.load(config_path)
        assert unchanged_config.sampling.normal_interval == 99

    def test_config_reset_interactive_yes(self, runner: CliRunner, tmp_path: Path) -> None:
        """config reset resets when user types 'y' interactively."""
        config_path = tmp_path / "config.toml"

        custom_config = Config(sampling=SamplingConfig(normal_interval=99))
        custom_config.save(config_path)

        with (
            patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(tmp_path)),
        ):
            result = runner.invoke(main, ["config", "reset"], input="y\n")

        assert result.exit_code == 0
        assert "Config reset to defaults" in result.output

    def test_config_reset_interactive_no(self, runner: CliRunner, tmp_path: Path) -> None:
        """config reset aborts when user types 'n' interactively."""
        config_path = tmp_path / "config.toml"

        custom_config = Config(sampling=SamplingConfig(normal_interval=99))
        custom_config.save(config_path)

        with patch.object(Config, "config_path", new_callable=lambda: _make_path_prop(config_path)):
            result = runner.invoke(main, ["config", "reset"], input="n\n")

        assert result.exit_code == 1
        assert "Aborted" in result.output


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
        assert "<string>-m</string>" in content
        assert "<string>pause_monitor.cli</string>" in content
        assert "<string>daemon</string>" in content
        assert "<key>RunAtLoad</key>" in content
        assert "<key>KeepAlive</key>" in content
        assert "<key>ProcessType</key>" in content
        assert "<string>Background</string>" in content

        # Verify launchctl bootstrap was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootstrap"
        assert call_args[2] == "gui/501"
        assert str(plist_path) in call_args[3]

    def test_install_system_wide(self, runner: CliRunner, tmp_path: Path) -> None:
        """install --system creates plist in /Library/LaunchDaemons."""
        # Can't write to /Library/LaunchDaemons in tests, so mock Path operations
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.exists", return_value=False),
            patch("subprocess.run") as mock_run,
            patch("os.getuid", return_value=0),  # Pretend to be root
        ):
            result = runner.invoke(main, ["install", "--system"])

        assert result.exit_code == 0
        assert "Created" in result.output

        # Verify launchctl bootstrap was called with "system" target
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootstrap"
        assert call_args[2] == "system"

    def test_install_creates_directory(self, runner: CliRunner, tmp_path: Path) -> None:
        """install creates LaunchAgents directory if it doesn't exist."""
        # Don't pre-create the directory
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        assert not plist_dir.exists()

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert plist_dir.exists()

    def test_install_already_loaded(self, runner: CliRunner, tmp_path: Path) -> None:
        """install handles already-loaded service gracefully."""
        from subprocess import CalledProcessError

        error = CalledProcessError(
            returncode=125,
            cmd=["launchctl", "bootstrap"],
            stderr=b"service already loaded",
        )

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run", side_effect=error),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert "Service was already installed" in result.output

    def test_install_already_loaded_capitalized(self, runner: CliRunner, tmp_path: Path) -> None:
        """install handles 'Already Loaded' (capitalized) message."""
        from subprocess import CalledProcessError

        error = CalledProcessError(
            returncode=125,
            cmd=["launchctl", "bootstrap"],
            stderr=b"Already loaded",
        )

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run", side_effect=error),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        # The check uses .lower() on decoded stderr, so "Already loaded" -> "already loaded"
        assert "Service was already installed" in result.output

    def test_install_bootstrap_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """install shows warning on bootstrap failure."""
        from subprocess import CalledProcessError

        error = CalledProcessError(
            returncode=1,
            cmd=["launchctl", "bootstrap"],
            stderr=b"some other error message",
        )

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run", side_effect=error),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert "Warning: Could not start service" in result.output
        assert "some other error message" in result.output

    def test_install_shows_status_instructions(self, runner: CliRunner, tmp_path: Path) -> None:
        """install shows helpful status and log commands."""
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert "launchctl print gui/501/com.pause-monitor.daemon" in result.output
        assert "tail -f ~/.local/share/pause-monitor/daemon.log" in result.output

    def test_install_plist_uses_current_python(self, runner: CliRunner, tmp_path: Path) -> None:
        """install uses sys.executable for Python path in plist."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
            patch("sys.executable", "/custom/python/path"),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0

        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        content = plist_path.read_text()
        assert "<string>/custom/python/path</string>" in content

    def test_install_plist_log_paths(self, runner: CliRunner, tmp_path: Path) -> None:
        """install configures correct log paths in plist."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0

        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        content = plist_path.read_text()
        # Check that log paths use the mocked home directory
        expected_log_path = f"{tmp_path}/.local/share/pause-monitor/daemon.log"
        assert f"<string>{expected_log_path}</string>" in content

    def test_install_system_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """install --system fails without root privileges."""
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),  # Non-root user
        ):
            result = runner.invoke(main, ["install", "--system"])

        assert result.exit_code == 1
        assert "Error: --system requires root privileges" in result.output
        assert "sudo" in result.output

    def test_install_existing_plist_prompts(self, runner: CliRunner, tmp_path: Path) -> None:
        """install prompts when plist already exists."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("existing content")

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            # User declines to overwrite
            result = runner.invoke(main, ["install"], input="n\n")

        assert result.exit_code == 0
        assert "already exists" in result.output
        assert "Overwrite?" in result.output
        # Plist should still have original content
        assert plist_path.read_text() == "existing content"

    def test_install_existing_plist_confirms_overwrite(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """install overwrites plist when user confirms."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("existing content")

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            # User confirms overwrite
            result = runner.invoke(main, ["install"], input="y\n")

        assert result.exit_code == 0
        assert "Created" in result.output
        # Plist should have new content
        assert "existing content" not in plist_path.read_text()
        assert "<key>Label</key>" in plist_path.read_text()

    def test_install_force_skips_prompt(self, runner: CliRunner, tmp_path: Path) -> None:
        """install --force overwrites plist without prompting."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("existing content")

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            result = runner.invoke(main, ["install", "--force"])

        assert result.exit_code == 0
        assert "Overwrite?" not in result.output
        assert "Created" in result.output
        # Plist should have new content
        assert "<key>Label</key>" in plist_path.read_text()

    def test_install_creates_log_directory(self, runner: CliRunner, tmp_path: Path) -> None:
        """install creates log directory if it doesn't exist."""
        log_dir = tmp_path / ".local" / "share" / "pause-monitor"
        assert not log_dir.exists()

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
        ):
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert log_dir.exists()


class TestEventsStatusManagement:
    """Tests for events status management commands."""

    def test_events_list_shows_status(self, runner: CliRunner, tmp_path: Path) -> None:
        """Events list shows status column."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        assert "unreviewed" in result.output or "reviewed" in result.output

    def test_events_filter_by_status(self, runner: CliRunner, tmp_path: Path) -> None:
        """Events can be filtered by status."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test events with different statuses
        import sqlite3

        from pause_monitor.storage import update_event_status

        conn = sqlite3.connect(db_path)

        # Create two events
        create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        event2_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 15, 30, 0),
            duration_seconds=3.0,
            peak_stress=15,
            peak_tier=2,
        )

        # Mark one as reviewed
        update_event_status(conn, event2_id, "reviewed")
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "--status", "unreviewed"])

        assert result.exit_code == 0
        # Verify filter worked: only unreviewed event (2.5s duration) shown
        assert "2.5s" in result.output
        assert "3.0s" not in result.output  # reviewed event should be filtered out

    def test_events_mark_reviewed(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command changes event status."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "mark", str(event_id), "--reviewed"])

        assert result.exit_code == 0
        assert "reviewed" in result.output.lower()

    def test_events_mark_with_notes(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command can add notes."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(
                main,
                ["events", "mark", str(event_id), "--reviewed", "--notes", "Chrome memory leak"],
            )

        assert result.exit_code == 0

    def test_events_mark_pinned(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command can pin events."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "mark", str(event_id), "--pinned"])

        assert result.exit_code == 0
        assert "pinned" in result.output.lower()

    def test_events_mark_dismissed(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command can dismiss events."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "mark", str(event_id), "--dismissed"])

        assert result.exit_code == 0
        assert "dismissed" in result.output.lower()

    def test_events_mark_multiple_flags_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command errors when multiple status flags given."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(
                main, ["events", "mark", str(event_id), "--reviewed", "--pinned"]
            )

        assert result.exit_code == 1
        assert "only one status flag" in result.output.lower()

    def test_events_mark_no_flags_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command errors when no flags given."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "mark", str(event_id)])

        assert result.exit_code == 1
        assert "specify" in result.output.lower()

    def test_events_mark_nonexistent_event(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command errors for nonexistent event."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events", "mark", "99999", "--reviewed"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_events_mark_notes_only(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mark command can add notes without changing status."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        event_id = create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(
                main, ["events", "mark", str(event_id), "--notes", "Just adding notes"]
            )

        assert result.exit_code == 0
        assert "notes" in result.output.lower()

    def test_events_show_status_icon(self, runner: CliRunner, tmp_path: Path) -> None:
        """Events list shows status icons."""
        db_path = tmp_path / "data.db"
        init_database(db_path)

        # Insert test event
        import sqlite3

        conn = sqlite3.connect(db_path)
        create_test_event(
            conn,
            start_time=datetime(2026, 1, 20, 14, 30, 0),
            duration_seconds=2.5,
            peak_stress=15,
            peak_tier=2,
        )
        conn.close()

        with patch("pause_monitor.config.Config.load") as mock_load:
            mock_config = MagicMock(spec=Config)
            mock_config.db_path = db_path
            mock_load.return_value = mock_config
            result = runner.invoke(main, ["events"])

        assert result.exit_code == 0
        # Should show status icon for unreviewed
        # The icon is "●" for unreviewed
        assert "●" in result.output or "[unreviewed]" in result.output


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
        assert "Service stopped" in result.output or "Warning" in result.output
        assert f"Removed {plist_path}" in result.output
        assert "Uninstall complete" in result.output
        assert not plist_path.exists()

        # Verify launchctl bootout was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootout"
        assert call_args[2] == "gui/501/com.pause-monitor.daemon"

    def test_uninstall_not_installed(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall shows message when service was not installed."""
        # Create config directories that would be prompted for deletion
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

    def test_uninstall_system_wide(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall --system removes plist from /Library/LaunchDaemons."""
        # Create config directories that would be prompted for deletion
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.unlink"),
            patch("subprocess.run") as mock_run,
            patch("os.getuid", return_value=0),  # Pretend to be root
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--system", "--keep-data"])

        assert result.exit_code == 0

        # Verify launchctl bootout was called with "system" target
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "launchctl"
        assert call_args[1] == "bootout"
        assert call_args[2] == "system/com.pause-monitor.daemon"

    def test_uninstall_system_requires_root(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall --system fails without root privileges."""
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),  # Non-root user
        ):
            result = runner.invoke(main, ["uninstall", "--system"])

        assert result.exit_code == 1
        assert "Error: --system requires root privileges" in result.output
        assert "sudo" in result.output

    def test_uninstall_service_not_running(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall handles 'No such process' error gracefully."""
        from subprocess import CalledProcessError

        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create config directories that would be prompted for deletion
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        error = CalledProcessError(
            returncode=3,
            cmd=["launchctl", "bootout"],
            stderr=b"No such process",
        )

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run", side_effect=error),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--keep-data"])

        assert result.exit_code == 0
        # Should NOT show warning for "No such process"
        assert "Warning" not in result.output
        assert f"Removed {plist_path}" in result.output
        assert not plist_path.exists()

    def test_uninstall_bootout_other_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall shows warning on bootout failure (other than No such process)."""
        from subprocess import CalledProcessError

        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create config directories that would be prompted for deletion
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        error = CalledProcessError(
            returncode=1,
            cmd=["launchctl", "bootout"],
            stderr=b"some other error",
        )

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run", side_effect=error),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--keep-data"])

        assert result.exit_code == 0
        assert "Warning: Could not stop service" in result.output
        assert "some other error" in result.output
        # Should still remove plist
        assert f"Removed {plist_path}" in result.output

    def test_uninstall_prompts_for_data_deletion(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall prompts for data directory deletion."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create data and config directories
        config_dir = tmp_path / ".config" / "pause-monitor"
        config_dir.mkdir(parents=True)
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"
        data_dir.mkdir(parents=True)

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            # Decline both prompts
            result = runner.invoke(main, ["uninstall"], input="n\nn\n")

        assert result.exit_code == 0
        assert f"Delete data directory {data_dir}?" in result.output
        # Data dir should still exist
        assert data_dir.exists()
        assert config_dir.exists()

    def test_uninstall_deletes_data_when_confirmed(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall deletes data when user confirms."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create data and config directories
        config_dir = tmp_path / ".config" / "pause-monitor"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("test")
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"
        data_dir.mkdir(parents=True)
        (data_dir / "data.db").write_text("test")

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            # Confirm both prompts
            result = runner.invoke(main, ["uninstall"], input="y\ny\n")

        assert result.exit_code == 0
        assert f"Removed {data_dir}" in result.output
        assert f"Removed {config_dir}" in result.output
        assert not data_dir.exists()
        assert not config_dir.exists()

    def test_uninstall_force_skips_prompts(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall --force deletes data without prompting."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create data and config directories
        config_dir = tmp_path / ".config" / "pause-monitor"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("test")
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"
        data_dir.mkdir(parents=True)
        (data_dir / "data.db").write_text("test")

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--force"])

        assert result.exit_code == 0
        # Should NOT prompt
        assert "Delete data directory" not in result.output
        assert "Delete config directory" not in result.output
        # Should delete
        assert f"Removed {data_dir}" in result.output
        assert f"Removed {config_dir}" in result.output
        assert not data_dir.exists()
        assert not config_dir.exists()

    def test_uninstall_keep_data_skips_deletion(self, runner: CliRunner, tmp_path: Path) -> None:
        """uninstall --keep-data preserves data and config directories."""
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "com.pause-monitor.daemon.plist"
        plist_path.write_text("<plist>test</plist>")

        # Create data and config directories
        config_dir = tmp_path / ".config" / "pause-monitor"
        config_dir.mkdir(parents=True)
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"
        data_dir.mkdir(parents=True)

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch("subprocess.run"),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--keep-data"])

        assert result.exit_code == 0
        # Should NOT prompt or delete
        assert "Delete data directory" not in result.output
        assert data_dir.exists()
        assert config_dir.exists()

    def test_uninstall_handles_nonexistent_data_dirs(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """uninstall handles case where data/config dirs don't exist."""
        # Create config directories that don't exist
        config_dir = tmp_path / ".config" / "pause-monitor"
        data_dir = tmp_path / ".local" / "share" / "pause-monitor"

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("os.getuid", return_value=501),
            patch.object(Config, "config_dir", new_callable=lambda: _make_path_prop(config_dir)),
            patch.object(Config, "data_dir", new_callable=lambda: _make_path_prop(data_dir)),
        ):
            result = runner.invoke(main, ["uninstall", "--force"])

        assert result.exit_code == 0
        # Should NOT try to delete nonexistent dirs
        assert f"Removed {data_dir}" not in result.output
        assert f"Removed {config_dir}" not in result.output
        assert "Uninstall complete" in result.output
