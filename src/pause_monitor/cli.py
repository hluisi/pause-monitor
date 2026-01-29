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
    import time

    from pause_monitor.boottime import get_boot_time
    from pause_monitor.config import Config
    from pause_monitor.storage import DatabaseNotAvailable, get_open_events, require_database

    config = Config.load()

    # Check daemon status via socket file
    daemon_running = config.socket_path.exists()
    click.echo(f"Daemon: {'running' if daemon_running else 'stopped'}")

    try:
        with require_database(config.db_path) as conn:
            boot_time = get_boot_time()
            open_events = get_open_events(conn, boot_time)

            if not open_events:
                click.echo("No active process tracking.")
                return

            click.echo(f"\nActive tracked processes: {len(open_events)}")
            for event in open_events:
                duration = time.time() - event["entry_time"]
                duration_str = f"{duration:.0f}s"
                click.echo(
                    f"  - {event['command']} (PID {event['pid']}): "
                    f"{duration_str} in {event['peak_band']} (score {event['peak_score']})"
                )
    except DatabaseNotAvailable:
        return


@main.group(invoke_without_command=True)
@click.option("--limit", "-n", default=20, help="Number of events to show")
@click.option("--open", "open_only", is_flag=True, help="Show only open events")
@click.pass_context
def events(ctx, limit: int, open_only: bool) -> None:
    """List process events.

    Shows per-process band tracking events from the current boot.
    Use 'events show <id>' to view event details.
    """
    import time

    from pause_monitor.boottime import get_boot_time
    from pause_monitor.config import Config
    from pause_monitor.storage import (
        DatabaseNotAvailable,
        get_open_events,
        get_process_events,
        require_database,
    )

    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()

    # If a subcommand was invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    config = ctx.obj["config"]

    try:
        with require_database(config.db_path) as conn:
            boot_time = get_boot_time()

            if open_only:
                # Only show open events
                events_list = get_open_events(conn, boot_time)
            else:
                # Show all events from current boot (both open and closed)
                events_list = get_process_events(conn, boot_time=boot_time, limit=limit)

            if not events_list:
                click.echo("No events recorded.")
                return

            click.echo(
                f"{'ID':>5}  {'Command':20}  {'PID':>7}  {'Duration':>10}  "
                f"{'Peak Band':>10}  {'Score':>6}"
            )
            click.echo("-" * 75)

            from pause_monitor.formatting import format_duration

            now = time.time()
            for event in events_list:
                duration_str = format_duration(event["entry_time"], event.get("exit_time"), now=now)

                click.echo(
                    f"{event['id']:>5}  {event['command'][:20]:20}  {event['pid']:>7}  "
                    f"{duration_str:>10}  {event['peak_band']:>10}  {event['peak_score']:>6}"
                )
    except DatabaseNotAvailable:
        return


@events.command("show")
@click.argument("event_id", type=int)
@click.pass_context
def events_show(ctx, event_id: int) -> None:
    """Show details of a specific process event."""
    import json
    from datetime import datetime

    from pause_monitor.storage import get_process_event_detail, require_database

    config = ctx.obj["config"]

    with require_database(config.db_path, exit_on_missing=True) as conn:
        event = get_process_event_detail(conn, event_id)

        if not event:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        from pause_monitor.formatting import format_duration_verbose

        entry_time = datetime.fromtimestamp(event["entry_time"])
        exit_time = datetime.fromtimestamp(event["exit_time"]) if event["exit_time"] else None
        duration_str = format_duration_verbose(event["entry_time"], event["exit_time"])

        click.echo(f"Process Event #{event['id']}")
        click.echo(f"Command: {event['command']}")
        click.echo(f"PID: {event['pid']}")
        click.echo(f"Entry: {entry_time} ({event['entry_band']} band)")
        if exit_time:
            click.echo(f"Exit: {exit_time}")
        click.echo(f"Duration: {duration_str}")
        click.echo(f"Peak Band: {event['peak_band']}")
        click.echo(f"Peak Score: {event['peak_score']}")

        # Show peak snapshot
        if event["peak_snapshot"]:
            click.echo("\nPeak Snapshot:")
            try:
                snapshot = json.loads(event["peak_snapshot"])
                for key, val in snapshot.items():
                    click.echo(f"  {key}: {val}")
            except json.JSONDecodeError:
                click.echo(f"  {event['peak_snapshot']}")

        # Show any snapshots
        snapshots = conn.execute(
            "SELECT snapshot_type, snapshot FROM process_snapshots WHERE event_id = ?",
            (event_id,),
        ).fetchall()

        if snapshots:
            click.echo(f"\nSnapshots: {len(snapshots)}")
            for stype, sdata in snapshots:
                click.echo(f"  [{stype}] {sdata[:100]}...")


@main.command()
@click.option("--hours", "-H", default=24, help="Hours of history to show")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def history(hours: int, fmt: str) -> None:
    """Query historical process events.

    Shows per-process band tracking history.
    """
    import json
    import time
    from datetime import datetime

    from pause_monitor.config import Config
    from pause_monitor.storage import DatabaseNotAvailable, get_process_events, require_database

    config = Config.load()

    try:
        with require_database(config.db_path) as conn:
            # Get events from time range
            cutoff = time.time() - (hours * 3600)
            events = get_process_events(conn, time_cutoff=cutoff, limit=1000)

            if not events:
                click.echo(f"No events in the last {hours} hour{'s' if hours != 1 else ''}.")
                return

            from pause_monitor.formatting import calculate_duration

            if fmt == "json":
                data = []
                for event in events:
                    entry_time = datetime.fromtimestamp(event["entry_time"])
                    exit_time = (
                        datetime.fromtimestamp(event["exit_time"]) if event["exit_time"] else None
                    )
                    duration = calculate_duration(event["entry_time"], event["exit_time"])
                    data.append(
                        {
                            "id": event["id"],
                            "pid": event["pid"],
                            "command": event["command"],
                            "entry": entry_time.isoformat(),
                            "exit": exit_time.isoformat() if exit_time else None,
                            "duration_sec": duration,
                            "entry_band": event["entry_band"],
                            "peak_band": event["peak_band"],
                            "peak_score": event["peak_score"],
                        }
                    )
                click.echo(json.dumps(data, indent=2))
            elif fmt == "csv":
                click.echo("id,pid,command,entry,exit,duration_sec,entry_band,peak_band,peak_score")
                for event in events:
                    entry_time = datetime.fromtimestamp(event["entry_time"])
                    exit_time = (
                        datetime.fromtimestamp(event["exit_time"]) if event["exit_time"] else None
                    )
                    dur = calculate_duration(event["entry_time"], event["exit_time"])
                    duration = f"{dur:.1f}" if dur is not None else ""
                    click.echo(
                        f"{event['id']},{event['pid']},{event['command']},{entry_time.isoformat()},"
                        f"{exit_time.isoformat() if exit_time else ''},"
                        f"{duration},{event['entry_band']},{event['peak_band']},{event['peak_score']}"
                    )
            else:
                # Summary stats
                click.echo(f"Events: {len(events)}")
                first_time = datetime.fromtimestamp(events[-1]["entry_time"])
                last_time = datetime.fromtimestamp(events[0]["entry_time"])
                click.echo(
                    f"Time range: {first_time.strftime('%Y-%m-%d %H:%M')} "
                    f"to {last_time.strftime('%Y-%m-%d %H:%M')}"
                )

                # Peak score stats
                peak_scores = [event["peak_score"] for event in events]
                click.echo(
                    f"Peak scores - Min: {min(peak_scores)}, Max: {max(peak_scores)}, "
                    f"Avg: {sum(peak_scores) / len(peak_scores):.1f}"
                )

                # Band breakdown
                band_counts: dict[str, int] = {}
                for event in events:
                    band = event["peak_band"]
                    band_counts[band] = band_counts.get(band, 0) + 1

                click.echo("\nPeak band breakdown:")
                for band in ["low", "medium", "elevated", "high", "critical"]:
                    if band in band_counts:
                        click.echo(f"  {band}: {band_counts[band]} events")

                # Total tracked time
                total_duration = 0.0
                for event in events:
                    if event["exit_time"]:
                        total_duration += event["exit_time"] - event["entry_time"]
                if total_duration > 0:
                    mins = total_duration / 60
                    click.echo(f"\nTotal tracked time: {total_duration:.0f}s ({mins:.1f}m)")
    except DatabaseNotAvailable:
        return


@main.command()
@click.option("--samples-days", default=None, type=int, help="Override sample retention days")
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def prune(samples_days: int | None, events_days: int | None, dry_run: bool, force: bool) -> None:
    """Delete old samples and closed process events.

    Prunes process_sample_records older than samples_days.
    Prunes closed process_events older than events_days.
    """
    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, prune_old_data

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    samples_days = samples_days or config.retention.samples_days
    events_days = events_days or config.retention.events_days

    if dry_run:
        click.echo(f"Would prune samples older than {samples_days} days")
        click.echo(f"Would prune closed events older than {events_days} days")
        return

    if not force:
        click.confirm(
            f"Delete samples older than {samples_days} days and "
            f"closed events older than {events_days} days?",
            abort=True,
        )

    conn = get_connection(config.db_path)
    try:
        samples_deleted, events_deleted = prune_old_data(
            conn, samples_days=samples_days, events_days=events_days
        )
    finally:
        conn.close()

    click.echo(f"Deleted {samples_deleted} samples and {events_deleted} events")


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
    click.echo()
    click.echo("[bands]")
    click.echo(f"  low = {cfg.bands.low}")
    click.echo(f"  medium = {cfg.bands.medium}")
    click.echo(f"  elevated = {cfg.bands.elevated}")
    click.echo(f"  high = {cfg.bands.high}")
    click.echo(f"  critical = {cfg.bands.critical}")
    click.echo(f"  tracking_band = {cfg.bands.tracking_band}")
    click.echo(f"  forensics_band = {cfg.bands.forensics_band}")
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
