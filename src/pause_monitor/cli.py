"""CLI commands for pause-monitor."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Track down intermittent macOS system pauses."""
    pass


@main.command()
def daemon() -> None:
    """Run the background sampler."""
    import asyncio

    from pause_monitor.daemon import run_daemon

    asyncio.run(run_daemon())


@main.command()
def tui() -> None:
    """Launch interactive dashboard."""
    from pause_monitor.config import Config
    from pause_monitor.tui import run_tui

    config = Config.load()
    run_tui(config)


@main.command()
def status() -> None:
    """Quick health check."""
    from datetime import datetime, timedelta

    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, get_events, get_recent_samples

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # Get latest sample
        samples = get_recent_samples(conn, limit=1)

        if not samples:
            click.echo("No samples collected yet.")
            return

        latest = samples[0]
        age = (datetime.now() - latest.timestamp).total_seconds()

        # Check if daemon is running
        daemon_status = "running" if age < 30 else "stopped"

        click.echo(f"Daemon: {daemon_status}")
        click.echo(f"Last sample: {int(age)}s ago")
        click.echo(f"Stress: {latest.stress.total}/100")
        click.echo(
            f"  Load: {latest.stress.load}, Memory: {latest.stress.memory}, "
            f"Thermal: {latest.stress.thermal}, Latency: {latest.stress.latency}, "
            f"I/O: {latest.stress.io}"
        )

        # Get recent events
        events = get_events(
            conn,
            start=datetime.now() - timedelta(days=1),
            limit=3,
        )

        if events:
            click.echo(f"\nRecent events (last 24h): {len(events)}")
            for event in events:
                click.echo(
                    f"  - {event.timestamp.strftime('%H:%M:%S')}: {event.duration:.1f}s pause"
                )
    finally:
        conn.close()


@main.group(invoke_without_command=True)
@click.option("--limit", "-n", default=20, help="Number of events to show")
@click.option(
    "--status",
    type=click.Choice(["unreviewed", "reviewed", "pinned", "dismissed"]),
    help="Filter by status",
)
@click.pass_context
def events(ctx, limit: int, status: str | None) -> None:
    """List pause events.

    Without subcommand, lists recent events.
    Use 'events show <id>' to view event details.
    Use 'events mark <id>' to change event status.
    """
    # Store config in context for subcommands
    from pause_monitor.config import Config

    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()

    # If a subcommand was invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    from pause_monitor.storage import get_connection, get_events

    config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # List events
        event_list = get_events(conn, limit=limit, status=status)

        if not event_list:
            click.echo("No events recorded.")
            return

        # Status icons for visual distinction
        status_icons = {
            "unreviewed": "●",
            "reviewed": "○",
            "pinned": "◆",
            "dismissed": "◇",
        }

        click.echo(
            f"{'':3}{'ID':>5}  {'Time':20}  {'Duration':>10}  "
            f"{'Stress':>7}  {'Status':12}  Culprits"
        )
        click.echo("-" * 85)

        for event in event_list:
            icon = status_icons.get(event.status, "?")
            culprits_str = ", ".join(event.culprits[:2]) if event.culprits else "-"
            click.echo(
                f"{icon:3}{event.id:>5}  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S'):20}  "
                f"{event.duration:>8.1f}s  {event.stress.total:>6}/100  "
                f"{event.status:12}  {culprits_str}"
            )
    finally:
        conn.close()


@events.command("show")
@click.argument("event_id", type=int)
@click.pass_context
def events_show(ctx, event_id: int) -> None:
    """Show details of a specific event."""
    from pause_monitor.storage import get_connection, get_event_by_id

    config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Error: Database not found", err=True)
        raise SystemExit(1)

    conn = get_connection(config.db_path)
    try:
        event = get_event_by_id(conn, event_id)
        if not event:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        click.echo(f"Event #{event.id}")
        click.echo(f"Time: {event.timestamp}")
        click.echo(f"Duration: {event.duration:.1f}s")
        click.echo(f"Status: {event.status}")
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
    finally:
        conn.close()


@events.command("mark")
@click.argument("event_id", type=int)
@click.option("--reviewed", is_flag=True, help="Mark as reviewed")
@click.option("--pinned", is_flag=True, help="Pin event (protected from pruning)")
@click.option("--dismissed", is_flag=True, help="Dismiss event (eligible for pruning)")
@click.option("--notes", help="Add notes to event")
@click.pass_context
def events_mark(
    ctx, event_id: int, reviewed: bool, pinned: bool, dismissed: bool, notes: str | None
) -> None:
    """Change event status."""
    from pause_monitor.storage import get_connection, get_event_by_id, update_event_status

    # Determine status
    status_flags = sum([reviewed, pinned, dismissed])
    if status_flags > 1:
        click.echo("Error: Only one status flag allowed", err=True)
        raise SystemExit(1)

    status = None
    if reviewed:
        status = "reviewed"
    elif pinned:
        status = "pinned"
    elif dismissed:
        status = "dismissed"

    if not status and not notes:
        click.echo("Error: Specify --reviewed, --pinned, --dismissed, or --notes", err=True)
        raise SystemExit(1)

    config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Error: Database not found", err=True)
        raise SystemExit(1)

    conn = get_connection(config.db_path)
    try:
        event = get_event_by_id(conn, event_id)
        if not event:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        if status:
            update_event_status(conn, event_id, status, notes)
            click.echo(f"Event {event_id} marked as {status}")
        elif notes:
            update_event_status(conn, event_id, event.status, notes)
            click.echo(f"Notes added to event {event_id}")
    finally:
        conn.close()


@main.command()
@click.option("--hours", "-H", default=24, help="Hours of history to show")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def history(hours: int, fmt: str) -> None:
    """Query historical data."""
    import json
    from datetime import datetime, timedelta

    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, get_recent_samples

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # Get samples from time range
        # Note: get_recent_samples returns newest first, so we get more than needed
        # and filter by time
        cutoff = datetime.now() - timedelta(hours=hours)
        samples = get_recent_samples(conn, limit=hours * 720)  # ~1 sample/5s max
        samples = [s for s in samples if s.timestamp >= cutoff]

        if not samples:
            click.echo(f"No samples in the last {hours} hour{'s' if hours != 1 else ''}.")
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
            click.echo(
                f"Stress - Min: {min(stresses)}, Max: {max(stresses)}, "
                f"Avg: {sum(stresses) / len(stresses):.1f}"
            )

            # High stress periods
            high_stress = [s for s in samples if s.stress.total >= 30]
            if high_stress:
                click.echo(f"\nHigh stress periods: {len(high_stress)} samples")
                click.echo(f"  ({len(high_stress) / len(samples) * 100:.1f}% of time)")
    finally:
        conn.close()


@main.command()
@click.option("--samples-days", default=None, type=int, help="Override sample retention days")
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def prune(samples_days: int | None, events_days: int | None, dry_run: bool, force: bool) -> None:
    """Delete old data per retention policy."""
    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, prune_old_data

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

    if not force:
        click.confirm(
            f"Delete samples > {samples_days} days and events > {events_days} days?",
            abort=True,
        )

    conn = get_connection(config.db_path)
    try:
        samples_deleted, events_deleted = prune_old_data(
            conn,
            samples_days=samples_days,
            events_days=events_days,
        )
    finally:
        conn.close()

    click.echo(f"Deleted {samples_deleted} samples, {events_deleted} events")


@main.group()
def config() -> None:
    """Manage configuration."""
    pass


@config.command("show")
def config_show() -> None:
    """Display current configuration."""
    from pause_monitor.config import Config

    cfg = Config.load()

    click.echo(f"Config file: {cfg.config_path}")
    click.echo(f"Exists: {cfg.config_path.exists()}")
    click.echo()
    click.echo("[sampling]")
    click.echo(f"  normal_interval = {cfg.sampling.normal_interval}")
    click.echo(f"  elevated_interval = {cfg.sampling.elevated_interval}")
    click.echo(f"  elevation_threshold = {cfg.sampling.elevation_threshold}")
    click.echo(f"  critical_threshold = {cfg.sampling.critical_threshold}")
    click.echo()
    click.echo("[retention]")
    click.echo(f"  samples_days = {cfg.retention.samples_days}")
    click.echo(f"  events_days = {cfg.retention.events_days}")
    click.echo()
    click.echo("[alerts]")
    click.echo(f"  enabled = {cfg.alerts.enabled}")
    click.echo(f"  sound = {cfg.alerts.sound}")
    click.echo()
    click.echo(f"learning_mode = {cfg.learning_mode}")


@config.command("edit")
def config_edit() -> None:
    """Open config file in editor."""
    import os
    import subprocess

    from pause_monitor.config import Config

    cfg = Config.load()

    # Create config if it doesn't exist
    if not cfg.config_path.exists():
        cfg.save()
        click.echo(f"Created default config at {cfg.config_path}")

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(cfg.config_path)])


@config.command("reset")
@click.confirmation_option(prompt="Reset config to defaults?")
def config_reset() -> None:
    """Reset configuration to defaults."""
    from pause_monitor.config import Config

    cfg = Config()
    cfg.save()
    click.echo(f"Config reset to defaults at {cfg.config_path}")


@main.command()
@click.option("--system", "system_wide", is_flag=True, help="Install system-wide (requires root)")
@click.option("--force", is_flag=True, help="Overwrite existing plist without prompting")
def install(system_wide: bool, force: bool) -> None:
    """Set up launchd service."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    label = "com.pause-monitor.daemon"

    # Check root for system-wide install
    if system_wide and os.getuid() != 0:
        click.echo("Error: --system requires root privileges. Use sudo.", err=True)
        raise SystemExit(1)

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"

    plist_path = plist_dir / f"{label}.plist"

    # Check for existing plist
    if plist_path.exists() and not force:
        if not click.confirm(f"Plist already exists at {plist_path}. Overwrite?"):
            return

    # Create log directory if needed
    log_dir = Path.home() / ".local" / "share" / "pause-monitor"
    log_dir.mkdir(parents=True, exist_ok=True)

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
        click.echo("Service installed and started")
    except subprocess.CalledProcessError as e:
        # May already be loaded - check stderr for known messages
        stderr_text = e.stderr.decode()
        stderr_lower = stderr_text.lower()
        if "already loaded" in stderr_lower or "service already loaded" in stderr_lower:
            click.echo("Service was already installed")
        else:
            click.echo(f"Warning: Could not start service: {stderr_text}")

    click.echo(f"\nTo check status: launchctl print {service_target}/{label}")
    click.echo("To view logs: tail -f ~/.local/share/pause-monitor/daemon.log")


@main.command()
@click.option("--system", "system_wide", is_flag=True, help="Uninstall system-wide (requires root)")
@click.option("--keep-data", is_flag=True, help="Keep database and config files")
@click.option("--force", is_flag=True, help="Skip confirmation prompts")
def uninstall(system_wide: bool, keep_data: bool, force: bool) -> None:
    """Remove launchd service."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    label = "com.pause-monitor.daemon"

    # Check root for system-wide uninstall
    if system_wide and os.getuid() != 0:
        click.echo("Error: --system requires root privileges. Use sudo.", err=True)
        raise SystemExit(1)

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"

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
            # "No such process" is fine - service may not be running
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
            if force or click.confirm(f"Delete data directory {config.data_dir}?"):
                shutil.rmtree(config.data_dir)
                click.echo(f"Removed {config.data_dir}")

        if config.config_dir.exists():
            if force or click.confirm(f"Delete config directory {config.config_dir}?"):
                shutil.rmtree(config.config_dir)
                click.echo(f"Removed {config.config_dir}")

    click.echo("Uninstall complete")


if __name__ == "__main__":
    main()
