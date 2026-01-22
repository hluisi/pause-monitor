# Part 6: Interface

> **Navigation:** [Index](./index.md) | [Prev: Daemon](./05-daemon.md) | **Current** | [Next: Integration](./07-integration.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 10-12 (TUI Dashboard + CLI Commands + Install/Uninstall)
**Tasks:** 25-32
**Dependencies:** Part 2 (storage.py), Part 5 (daemon.py)

---

## Phase 10: TUI Dashboard

### Task 25: TUI Main Application

**Files:**
- Create: `src/pause_monitor/tui/__init__.py`
- Create: `src/pause_monitor/tui/app.py`

**Step 1: Create TUI package structure**

Create `src/pause_monitor/tui/__init__.py`:

```python
"""TUI dashboard for pause-monitor."""

from pause_monitor.tui.app import PauseMonitorApp

__all__ = ["PauseMonitorApp"]
```

**Step 2: Create main TUI application**

Create `src/pause_monitor/tui/app.py`:

```python
"""Main TUI application."""

import sqlite3

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, ProgressBar

from pause_monitor.config import Config


class StressGauge(Static):
    """Visual stress level gauge."""

    DEFAULT_CSS = """
    StressGauge {
        height: 3;
        border: solid green;
        padding: 0 1;
    }

    StressGauge.elevated {
        border: solid yellow;
    }

    StressGauge.critical {
        border: solid red;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._stress = 0

    def update_stress(self, stress: int) -> None:
        """Update the displayed stress value."""
        self._stress = stress
        self.update(f"Stress: {stress:3d}/100 {'█' * (stress // 5)}{'░' * (20 - stress // 5)}")

        # Update styling based on level
        self.remove_class("elevated", "critical")
        if stress >= 60:
            self.add_class("critical")
        elif stress >= 30:
            self.add_class("elevated")


class MetricsPanel(Static):
    """Panel showing current system metrics."""

    DEFAULT_CSS = """
    MetricsPanel {
        height: 8;
        border: solid $primary;
        padding: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._metrics = {}

    def update_metrics(self, metrics: dict) -> None:
        """Update displayed metrics."""
        self._metrics = metrics
        lines = [
            f"CPU: {metrics.get('cpu_pct', 0):.1f}%",
            f"Load: {metrics.get('load_avg', 0):.2f}",
            f"Memory: {metrics.get('mem_available', 0) / 1e9:.1f} GB free",
            f"Freq: {metrics.get('cpu_freq', 0)} MHz",
            f"Throttled: {'Yes' if metrics.get('throttled') else 'No'}",
        ]
        self.update("\n".join(lines))


class EventsTable(DataTable):
    """Table showing recent pause events."""

    DEFAULT_CSS = """
    EventsTable {
        height: 10;
    }
    """

    def on_mount(self) -> None:
        """Set up table columns."""
        self.add_column("Time", width=20)
        self.add_column("Duration", width=10)
        self.add_column("Stress", width=8)
        self.add_column("Culprits", width=30)


class PauseMonitorApp(App):
    """Main TUI application for pause-monitor."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 3;
        grid-gutter: 1;
    }

    #stress-gauge {
        column-span: 2;
    }

    #metrics {
        row-span: 1;
    }

    #breakdown {
        row-span: 1;
    }

    #events {
        column-span: 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("e", "show_events", "Events"),
        ("h", "show_history", "History"),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()
        self._conn: sqlite3.Connection | None = None

    def on_mount(self) -> None:
        """Initialize on startup."""
        from pause_monitor.storage import get_connection

        self.title = "pause-monitor"
        self.sub_title = "System Health Monitor"

        # Connect to database (read-only for TUI)
        self._conn = get_connection(self.config.db_path)

        # Start periodic refresh
        self.set_interval(1.0, self._refresh_data)

    def on_unmount(self) -> None:
        """Cleanup on shutdown."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def compose(self) -> ComposeResult:
        """Create the TUI layout."""
        yield Header()

        yield StressGauge(id="stress-gauge")
        yield MetricsPanel(id="metrics")
        yield Static("Stress Breakdown", id="breakdown")
        yield EventsTable(id="events")

        yield Footer()

    def _refresh_data(self) -> None:
        """Refresh displayed data from database."""
        if not self._conn:
            return

        from pause_monitor.storage import get_recent_samples

        samples = get_recent_samples(self._conn, limit=1)
        if samples:
            sample = samples[0]
            stress_gauge = self.query_one("#stress-gauge", StressGauge)
            stress_gauge.update_stress(sample.stress.total)

    def action_refresh(self) -> None:
        """Manual refresh."""
        self._refresh_data()

    def action_show_events(self) -> None:
        """Show events view."""
        self.notify("Events view not yet implemented")

    def action_show_history(self) -> None:
        """Show history view."""
        self.notify("History view not yet implemented")


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = PauseMonitorApp(config)
    app.run()
```

**Step 3: Wire CLI tui command**

Replace the tui command in `src/pause_monitor/cli.py`:

```python
@main.command()
def tui():
    """Launch interactive dashboard."""
    from pause_monitor.tui import PauseMonitorApp
    from pause_monitor.config import Config

    config = Config.load()
    app = PauseMonitorApp(config)
    app.run()
```

**Step 4: Run TUI smoke test**

Run: `uv run pause-monitor tui --help`
Expected: Help text displays

**Step 5: Commit**

```bash
git add src/pause_monitor/tui/ src/pause_monitor/cli.py
git commit -m "feat(tui): add basic TUI dashboard structure"
```

---

## Phase 11: CLI Commands

### Task 26: Status Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement status command**

Replace the status command in `src/pause_monitor/cli.py`:

```python
@main.command()
def status():
    """Quick health check."""
    import sqlite3
    from datetime import datetime, timedelta
    from pause_monitor.config import Config
    from pause_monitor.storage import get_recent_samples, get_events

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    # Get latest sample
    samples = get_recent_samples(conn, limit=1)

    if not samples:
        click.echo("No samples collected yet.")
        conn.close()
        return

    latest = samples[0]
    age = (datetime.now() - latest.timestamp).total_seconds()

    # Check if daemon is running
    daemon_status = "running" if age < 30 else "stopped"

    click.echo(f"Daemon: {daemon_status}")
    click.echo(f"Last sample: {int(age)}s ago")
    click.echo(f"Stress: {latest.stress.total}/100")
    click.echo(f"  Load: {latest.stress.load}, Memory: {latest.stress.memory}, "
               f"Thermal: {latest.stress.thermal}, Latency: {latest.stress.latency}, "
               f"I/O: {latest.stress.io}")

    # Get recent events
    events = get_events(
        conn,
        start=datetime.now() - timedelta(days=1),
        limit=5,
    )

    if events:
        click.echo(f"\nRecent events (last 24h): {len(events)}")
        for event in events[:3]:
            click.echo(f"  - {event.timestamp.strftime('%H:%M:%S')}: "
                       f"{event.duration:.1f}s pause")

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement status command"
```

---

### Task 27: Events Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement events command**

Replace the events command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.argument("event_id", required=False, type=int)
@click.option("--limit", "-n", default=20, help="Number of events to show")
def events(event_id, limit):
    """List or inspect pause events."""
    import sqlite3
    from pause_monitor.config import Config
    from pause_monitor.storage import get_events, get_event_by_id

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    if event_id:
        # Show single event details
        event = get_event_by_id(conn, event_id)
        if not event:
            click.echo(f"Event {event_id} not found.")
            conn.close()
            return

        click.echo(f"Event #{event.id}")
        click.echo(f"Time: {event.timestamp}")
        click.echo(f"Duration: {event.duration:.1f}s")
        click.echo(f"Stress: {event.stress.total}/100")
        click.echo(f"  Load: {event.stress.load}")
        click.echo(f"  Memory: {event.stress.memory}")
        click.echo(f"  Thermal: {event.stress.thermal}")
        click.echo(f"  Latency: {event.stress.latency}")
        click.echo(f"  I/O: {event.stress.io}")

        if event.culprits:
            click.echo(f"Culprits: {', '.join(event.culprits)}")

        if event.event_dir:
            click.echo(f"Forensics: {event.event_dir}")

        if event.notes:
            click.echo(f"Notes: {event.notes}")
    else:
        # List events
        event_list = get_events(conn, limit=limit)

        if not event_list:
            click.echo("No events recorded.")
            conn.close()
            return

        click.echo(f"{'ID':>5}  {'Time':20}  {'Duration':>10}  {'Stress':>7}  Culprits")
        click.echo("-" * 70)

        for event in event_list:
            culprits_str = ", ".join(event.culprits[:2]) if event.culprits else "-"
            click.echo(
                f"{event.id:>5}  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S'):20}  "
                f"{event.duration:>8.1f}s  {event.stress.total:>6}/100  {culprits_str}"
            )

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement events command"
```

---

### Task 28: History Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement history command**

Replace the history command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--hours", "-h", default=24, help="Hours of history to show")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def history(hours, fmt):
    """Query historical data."""
    import sqlite3
    import json
    from datetime import datetime, timedelta
    from pause_monitor.config import Config
    from pause_monitor.storage import get_recent_samples

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    # Get samples from time range
    # Note: get_recent_samples returns newest first, so we get more than needed
    # and filter by time
    cutoff = datetime.now() - timedelta(hours=hours)
    samples = get_recent_samples(conn, limit=hours * 720)  # ~1 sample/5s max
    samples = [s for s in samples if s.timestamp >= cutoff]

    if not samples:
        click.echo(f"No samples in the last {hours} hours.")
        conn.close()
        return

    if fmt == "json":
        data = [
            {
                "timestamp": s.timestamp.isoformat(),
                "stress": s.stress.total,
                "cpu_pct": s.cpu_pct,
                "load_avg": s.load_avg,
            }
            for s in samples
        ]
        click.echo(json.dumps(data, indent=2))
    elif fmt == "csv":
        click.echo("timestamp,stress,cpu_pct,load_avg")
        for s in samples:
            click.echo(f"{s.timestamp.isoformat()},{s.stress.total},{s.cpu_pct},{s.load_avg}")
    else:
        # Summary stats
        stresses = [s.stress.total for s in samples]
        click.echo(f"Samples: {len(samples)}")
        click.echo(f"Time range: {samples[-1].timestamp} to {samples[0].timestamp}")
        click.echo(f"Stress - Min: {min(stresses)}, Max: {max(stresses)}, "
                   f"Avg: {sum(stresses)/len(stresses):.1f}")

        # High stress periods
        high_stress = [s for s in samples if s.stress.total >= 30]
        if high_stress:
            click.echo(f"\nHigh stress periods: {len(high_stress)} samples")
            click.echo(f"  ({len(high_stress) / len(samples) * 100:.1f}% of time)")

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement history command"
```

---

### Task 29: Config Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement config command**

Add a new config command group to `src/pause_monitor/cli.py`:

```python
@main.group()
def config():
    """Manage configuration."""
    pass


@config.command("show")
def config_show():
    """Display current configuration."""
    from pause_monitor.config import Config

    config = Config.load()

    click.echo(f"Config file: {config.config_path}")
    click.echo(f"Exists: {config.config_path.exists()}")
    click.echo()
    click.echo("[sampling]")
    click.echo(f"  normal_interval = {config.sampling.normal_interval}")
    click.echo(f"  elevated_interval = {config.sampling.elevated_interval}")
    click.echo(f"  elevation_threshold = {config.sampling.elevation_threshold}")
    click.echo(f"  critical_threshold = {config.sampling.critical_threshold}")
    click.echo()
    click.echo("[retention]")
    click.echo(f"  samples_days = {config.retention.samples_days}")
    click.echo(f"  events_days = {config.retention.events_days}")
    click.echo()
    click.echo("[alerts]")
    click.echo(f"  enabled = {config.alerts.enabled}")
    click.echo(f"  sound = {config.alerts.sound}")
    click.echo()
    click.echo(f"learning_mode = {config.learning_mode}")


@config.command("edit")
def config_edit():
    """Open config file in editor."""
    import subprocess
    import os
    from pause_monitor.config import Config

    config = Config.load()

    # Create config if it doesn't exist
    if not config.config_path.exists():
        config.save()
        click.echo(f"Created default config at {config.config_path}")

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(config.config_path)])


@config.command("reset")
@click.confirmation_option(prompt="Reset config to defaults?")
def config_reset():
    """Reset configuration to defaults."""
    from pause_monitor.config import Config

    config = Config()
    config.save()
    click.echo(f"Config reset to defaults at {config.config_path}")
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement config command group"
```

---

### Task 30: Prune Command

**Files:**
- Modify: `src/pause_monitor/cli.py`
- Modify: `src/pause_monitor/storage.py`

**Step 1: Add prune function to storage**

Add to `src/pause_monitor/storage.py`:

```python
def prune_old_data(
    conn: sqlite3.Connection,
    samples_days: int = 30,
    events_days: int = 90,
) -> tuple[int, int]:
    """Delete old samples and events.

    Args:
        conn: Database connection
        samples_days: Delete samples older than this
        events_days: Delete events older than this

    Returns:
        Tuple of (samples_deleted, events_deleted)
    """
    cutoff_samples = time.time() - (samples_days * 86400)
    cutoff_events = time.time() - (events_days * 86400)

    # Delete old process samples first (foreign key)
    conn.execute(
        """
        DELETE FROM process_samples
        WHERE sample_id IN (SELECT id FROM samples WHERE timestamp < ?)
        """,
        (cutoff_samples,),
    )

    # Delete old samples
    cursor = conn.execute(
        "DELETE FROM samples WHERE timestamp < ?",
        (cutoff_samples,),
    )
    samples_deleted = cursor.rowcount

    # Delete old events
    cursor = conn.execute(
        "DELETE FROM events WHERE timestamp < ?",
        (cutoff_events,),
    )
    events_deleted = cursor.rowcount

    conn.commit()

    log.info(
        "prune_complete",
        samples_deleted=samples_deleted,
        events_deleted=events_deleted,
    )

    return samples_deleted, events_deleted
```

**Step 2: Add prune command to CLI**

Add to `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--samples-days", default=None, type=int, help="Override sample retention days")
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
def prune(samples_days, events_days, dry_run):
    """Delete old data per retention policy."""
    import sqlite3
    from pause_monitor.config import Config
    from pause_monitor.storage import prune_old_data

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found.")
        return

    samples_days = samples_days or config.retention.samples_days
    events_days = events_days or config.retention.events_days

    if dry_run:
        click.echo(f"Would prune samples older than {samples_days} days")
        click.echo(f"Would prune events older than {events_days} days")
        return

    conn = sqlite3.connect(config.db_path)
    samples_deleted, events_deleted = prune_old_data(
        conn,
        samples_days=samples_days,
        events_days=events_days,
    )
    conn.close()

    click.echo(f"Deleted {samples_deleted} samples, {events_deleted} events")
```

**Step 3: Commit**

```bash
git add src/pause_monitor/storage.py src/pause_monitor/cli.py
git commit -m "feat(cli): implement prune command with retention policy"
```

---

## Phase 12: Install/Uninstall

### Task 31: Install Command (launchd)

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement install command with modern launchctl syntax**

Replace the install command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--user", is_flag=True, default=True, help="Install for current user (default)")
@click.option("--system", "system_wide", is_flag=True, help="Install system-wide (requires root)")
def install(user, system_wide):
    """Set up launchd service."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
        label = "com.pause-monitor.daemon"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"
        label = "com.pause-monitor.daemon"

    plist_path = plist_dir / f"{label}.plist"

    # Get Python path
    python_path = sys.executable

    # Create plist content
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>pause_monitor.cli</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.local/share/pause-monitor/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.local/share/pause-monitor/daemon.log</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LegacyTimers</key>
    <true/>
</dict>
</plist>
"""

    # Create directory if needed
    plist_dir.mkdir(parents=True, exist_ok=True)

    # Write plist
    plist_path.write_text(plist_content)
    click.echo(f"Created {plist_path}")

    # Bootstrap the service (modern launchctl syntax)
    try:
        subprocess.run(
            ["launchctl", "bootstrap", service_target, str(plist_path)],
            check=True,
            capture_output=True,
        )
        click.echo(f"Service installed and started")
    except subprocess.CalledProcessError as e:
        # May already be loaded
        if b"already loaded" in e.stderr or b"service already loaded" in e.stderr.lower():
            click.echo("Service was already installed")
        else:
            click.echo(f"Warning: Could not start service: {e.stderr.decode()}")

    click.echo(f"\nTo check status: launchctl print {service_target}/{label}")
    click.echo(f"To view logs: tail -f ~/.local/share/pause-monitor/daemon.log")
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement install with modern launchctl bootstrap"
```

---

### Task 32: Uninstall Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement uninstall command**

Replace the uninstall command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--user", is_flag=True, default=True, help="Uninstall user service (default)")
@click.option("--system", "system_wide", is_flag=True, help="Uninstall system service")
@click.option("--keep-data", is_flag=True, help="Keep database and config files")
def uninstall(user, system_wide, keep_data):
    """Remove launchd service."""
    import subprocess
    import shutil
    from pathlib import Path

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
        label = "com.pause-monitor.daemon"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"
        label = "com.pause-monitor.daemon"

    plist_path = plist_dir / f"{label}.plist"

    # Bootout the service (modern launchctl syntax)
    if plist_path.exists():
        try:
            subprocess.run(
                ["launchctl", "bootout", f"{service_target}/{label}"],
                check=True,
                capture_output=True,
            )
            click.echo("Service stopped")
        except subprocess.CalledProcessError as e:
            if b"No such process" not in e.stderr:
                click.echo(f"Warning: Could not stop service: {e.stderr.decode()}")

        # Remove plist
        plist_path.unlink()
        click.echo(f"Removed {plist_path}")
    else:
        click.echo("Service was not installed")

    # Optionally remove data
    if not keep_data:
        from pause_monitor.config import Config
        config = Config()

        if config.data_dir.exists():
            if click.confirm(f"Delete data directory {config.data_dir}?"):
                shutil.rmtree(config.data_dir)
                click.echo(f"Removed {config.data_dir}")

        if config.config_dir.exists():
            if click.confirm(f"Delete config directory {config.config_dir}?"):
                shutil.rmtree(config.config_dir)
                click.echo(f"Removed {config.config_dir}")

    click.echo("Uninstall complete")
```

**Step 2: Add import for os module at top of cli.py**

Add to imports in `src/pause_monitor/cli.py`:

```python
import os
```

**Step 3: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement uninstall with modern launchctl bootout"
```

---


---

> **Next:** [Part 7: Integration](./07-integration.md)
