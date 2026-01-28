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
    from pause_monitor.storage import get_connection, get_open_events

    config = Config.load()

    # Check daemon status via socket file
    daemon_running = config.socket_path.exists()
    click.echo(f"Daemon: {'running' if daemon_running else 'stopped'}")

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
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
    finally:
        conn.close()


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
    from pause_monitor.storage import get_connection, get_open_events

    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()

    # If a subcommand was invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        boot_time = get_boot_time()

        if open_only:
            # Only show open events
            events_list = get_open_events(conn, boot_time)
        else:
            # Show all events from current boot (both open and closed)
            cursor = conn.execute(
                """SELECT id, pid, command, entry_time, exit_time, entry_band, peak_band, peak_score
                   FROM process_events
                   WHERE boot_time = ?
                   ORDER BY entry_time DESC
                   LIMIT ?""",
                (boot_time, limit),
            )
            events_list = [
                {
                    "id": r[0],
                    "pid": r[1],
                    "command": r[2],
                    "entry_time": r[3],
                    "exit_time": r[4],
                    "entry_band": r[5],
                    "peak_band": r[6],
                    "peak_score": r[7],
                }
                for r in cursor.fetchall()
            ]

        if not events_list:
            click.echo("No events recorded.")
            return

        click.echo(
            f"{'ID':>5}  {'Command':20}  {'PID':>7}  {'Duration':>10}  "
            f"{'Peak Band':>10}  {'Score':>6}"
        )
        click.echo("-" * 75)

        now = time.time()
        for event in events_list:
            if event.get("exit_time"):
                duration = event["exit_time"] - event["entry_time"]
                duration_str = f"{duration:.1f}s"
            else:
                duration = now - event["entry_time"]
                duration_str = f"{duration:.0f}s*"  # * means ongoing

            click.echo(
                f"{event['id']:>5}  {event['command'][:20]:20}  {event['pid']:>7}  "
                f"{duration_str:>10}  {event['peak_band']:>10}  {event['peak_score']:>6}"
            )
    finally:
        conn.close()


@events.command("show")
@click.argument("event_id", type=int)
@click.pass_context
def events_show(ctx, event_id: int) -> None:
    """Show details of a specific process event."""
    import json
    from datetime import datetime

    from pause_monitor.storage import get_connection

    config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Error: Database not found", err=True)
        raise SystemExit(1)

    conn = get_connection(config.db_path)
    try:
        row = conn.execute(
            """SELECT id, pid, command, boot_time, entry_time, exit_time,
                      entry_band, peak_band, peak_score, peak_snapshot
               FROM process_events WHERE id = ?""",
            (event_id,),
        ).fetchone()

        if not row:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        entry_time = datetime.fromtimestamp(row[4])
        exit_time = datetime.fromtimestamp(row[5]) if row[5] else None

        if exit_time:
            duration = (exit_time - entry_time).total_seconds()
            duration_str = f"{duration:.1f}s"
        else:
            duration_str = "ongoing"

        click.echo(f"Process Event #{row[0]}")
        click.echo(f"Command: {row[2]}")
        click.echo(f"PID: {row[1]}")
        click.echo(f"Entry: {entry_time} ({row[6]} band)")
        if exit_time:
            click.echo(f"Exit: {exit_time}")
        click.echo(f"Duration: {duration_str}")
        click.echo(f"Peak Band: {row[7]}")
        click.echo(f"Peak Score: {row[8]}")

        # Show peak snapshot
        if row[9]:
            click.echo("\nPeak Snapshot:")
            try:
                snapshot = json.loads(row[9])
                for key, val in snapshot.items():
                    click.echo(f"  {key}: {val}")
            except json.JSONDecodeError:
                click.echo(f"  {row[9]}")

        # Show any snapshots
        snapshots = conn.execute(
            "SELECT snapshot_type, snapshot FROM process_snapshots WHERE event_id = ?",
            (event_id,),
        ).fetchall()

        if snapshots:
            click.echo(f"\nSnapshots: {len(snapshots)}")
            for stype, sdata in snapshots:
                click.echo(f"  [{stype}] {sdata[:100]}...")
    finally:
        conn.close()


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
    from pause_monitor.storage import get_connection

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # Get events from time range
        cutoff = time.time() - (hours * 3600)
        cursor = conn.execute(
            """SELECT id, pid, command, entry_time, exit_time, entry_band, peak_band, peak_score
               FROM process_events
               WHERE entry_time >= ?
               ORDER BY entry_time DESC
               LIMIT 1000""",
            (cutoff,),
        )
        events = cursor.fetchall()

        if not events:
            click.echo(f"No events in the last {hours} hour{'s' if hours != 1 else ''}.")
            return

        if fmt == "json":
            data = []
            for row in events:
                entry_time = datetime.fromtimestamp(row[3])
                exit_time = datetime.fromtimestamp(row[4]) if row[4] else None
                duration = (row[4] - row[3]) if row[4] else None
                data.append(
                    {
                        "id": row[0],
                        "pid": row[1],
                        "command": row[2],
                        "entry": entry_time.isoformat(),
                        "exit": exit_time.isoformat() if exit_time else None,
                        "duration_sec": duration,
                        "entry_band": row[5],
                        "peak_band": row[6],
                        "peak_score": row[7],
                    }
                )
            click.echo(json.dumps(data, indent=2))
        elif fmt == "csv":
            click.echo("id,pid,command,entry,exit,duration_sec,entry_band,peak_band,peak_score")
            for row in events:
                entry_time = datetime.fromtimestamp(row[3])
                exit_time = datetime.fromtimestamp(row[4]) if row[4] else None
                duration = f"{row[4] - row[3]:.1f}" if row[4] else ""
                click.echo(
                    f"{row[0]},{row[1]},{row[2]},{entry_time.isoformat()},"
                    f"{exit_time.isoformat() if exit_time else ''},"
                    f"{duration},{row[5]},{row[6]},{row[7]}"
                )
        else:
            # Summary stats
            click.echo(f"Events: {len(events)}")
            first_time = datetime.fromtimestamp(events[-1][3])
            last_time = datetime.fromtimestamp(events[0][3])
            click.echo(
                f"Time range: {first_time.strftime('%Y-%m-%d %H:%M')} "
                f"to {last_time.strftime('%Y-%m-%d %H:%M')}"
            )

            # Peak score stats
            peak_scores = [row[7] for row in events]
            click.echo(
                f"Peak scores - Min: {min(peak_scores)}, Max: {max(peak_scores)}, "
                f"Avg: {sum(peak_scores) / len(peak_scores):.1f}"
            )

            # Band breakdown
            band_counts: dict[str, int] = {}
            for row in events:
                band = row[6]  # peak_band
                band_counts[band] = band_counts.get(band, 0) + 1

            click.echo("\nPeak band breakdown:")
            for band in ["low", "medium", "elevated", "high", "critical"]:
                if band in band_counts:
                    click.echo(f"  {band}: {band_counts[band]} events")

            # Total tracked time
            total_duration = 0.0
            for row in events:
                if row[4]:  # exit_time
                    total_duration += row[4] - row[3]
            if total_duration > 0:
                mins = total_duration / 60
                click.echo(f"\nTotal tracked time: {total_duration:.0f}s ({mins:.1f}m)")
    finally:
        conn.close()


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
        click.echo("Database not found.")
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
