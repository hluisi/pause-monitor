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
    from pause_monitor.storage import get_connection, get_events

    config = Config.load()

    # Check daemon status via socket file
    daemon_running = config.socket_path.exists()
    click.echo(f"Daemon: {'running' if daemon_running else 'stopped'}")

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # Get recent events
        events = get_events(
            conn,
            start=datetime.now() - timedelta(days=1),
            limit=5,
        )

        if not events:
            click.echo("No events in the last 24 hours.")
            return

        click.echo(f"\nRecent events (last 24h): {len(events)}")
        for event in events:
            # Compute duration if event has ended
            if event.end_timestamp:
                duration = (event.end_timestamp - event.start_timestamp).total_seconds()
                duration_str = f"{duration:.1f}s"
            else:
                duration_str = "ongoing"

            tier_str = f"tier {event.peak_tier}" if event.peak_tier else "elevated"
            score_str = f"peak score {event.peak_stress}" if event.peak_stress else ""

            click.echo(
                f"  - {event.start_timestamp.strftime('%H:%M:%S')}: "
                f"{duration_str} {tier_str} {score_str}"
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
            f"{'Stress':>7}  {'Tier':>5}  {'Status':12}"
        )
        click.echo("-" * 75)

        for event in event_list:
            icon = status_icons.get(event.status, "?")
            # Calculate duration from timestamps
            if event.end_timestamp:
                duration = (event.end_timestamp - event.start_timestamp).total_seconds()
                duration_str = f"{duration:.1f}s"
            else:
                duration_str = "ongoing"
            stress_str = f"{event.peak_stress or 0}/100"
            tier_str = str(event.peak_tier or "-")
            click.echo(
                f"{icon:3}{event.id:>5}  {event.start_timestamp.strftime('%Y-%m-%d %H:%M:%S'):20}  "
                f"{duration_str:>10}  {stress_str:>7}  {tier_str:>5}  {event.status:12}"
            )
    finally:
        conn.close()


@events.command("show")
@click.argument("event_id", type=int)
@click.pass_context
def events_show(ctx, event_id: int) -> None:
    """Show details of a specific event."""
    from pause_monitor.storage import get_connection, get_event_by_id, get_process_samples

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

        # Calculate duration
        if event.end_timestamp:
            duration = (event.end_timestamp - event.start_timestamp).total_seconds()
            duration_str = f"{duration:.1f}s"
        else:
            duration_str = "ongoing"

        click.echo(f"Event #{event.id}")
        click.echo(f"Start: {event.start_timestamp}")
        if event.end_timestamp:
            click.echo(f"End: {event.end_timestamp}")
        click.echo(f"Duration: {duration_str}")
        click.echo(f"Status: {event.status}")
        click.echo(f"Peak Stress: {event.peak_stress or 0}/100")
        click.echo(f"Peak Tier: {event.peak_tier or '-'}")

        if event.notes:
            click.echo(f"Notes: {event.notes}")

        # Show process samples captured during this event
        samples = get_process_samples(conn, event_id)
        if samples:
            click.echo(f"\nSamples captured: {len(samples)}")

            for sample in samples:
                timestamp_str = sample.data.timestamp.strftime("%H:%M:%S")
                click.echo(
                    f"  {timestamp_str} | Tier {sample.tier} | Max Score: {sample.data.max_score}"
                )
                for rogue in sample.data.rogues:
                    categories_str = ", ".join(sorted(rogue.categories))
                    click.echo(f"    {rogue.command}: {rogue.score} ({categories_str})")
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
    """Query historical events.

    Note: Since Phase 6 redesign, continuous samples are no longer stored.
    This command shows escalation events (elevated/critical periods) instead.
    """
    import json
    from datetime import datetime, timedelta

    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, get_events

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        # Get events from time range
        cutoff = datetime.now() - timedelta(hours=hours)
        events = get_events(conn, start=cutoff, limit=1000)

        if not events:
            click.echo(f"No events in the last {hours} hour{'s' if hours != 1 else ''}.")
            return

        if fmt == "json":
            data = []
            for e in events:
                duration = None
                if e.end_timestamp:
                    duration = (e.end_timestamp - e.start_timestamp).total_seconds()
                data.append(
                    {
                        "id": e.id,
                        "start": e.start_timestamp.isoformat(),
                        "end": e.end_timestamp.isoformat() if e.end_timestamp else None,
                        "duration_sec": duration,
                        "peak_stress": e.peak_stress,
                        "peak_tier": e.peak_tier,
                        "status": e.status,
                    }
                )
            click.echo(json.dumps(data, indent=2))
        elif fmt == "csv":
            click.echo("id,start,end,duration_sec,peak_stress,peak_tier,status")
            for e in events:
                duration = ""
                if e.end_timestamp:
                    duration = f"{(e.end_timestamp - e.start_timestamp).total_seconds():.1f}"
                end = e.end_timestamp.isoformat() if e.end_timestamp else ""
                click.echo(
                    f"{e.id},{e.start_timestamp.isoformat()},{end},"
                    f"{duration},{e.peak_stress or ''},{e.peak_tier or ''},{e.status}"
                )
        else:
            # Summary stats
            click.echo(f"Events: {len(events)}")
            click.echo(
                f"Time range: {events[-1].start_timestamp.strftime('%Y-%m-%d %H:%M')} "
                f"to {events[0].start_timestamp.strftime('%Y-%m-%d %H:%M')}"
            )

            # Peak stress stats
            peak_stresses = [e.peak_stress for e in events if e.peak_stress]
            if peak_stresses:
                click.echo(
                    f"Peak stress - Min: {min(peak_stresses)}, Max: {max(peak_stresses)}, "
                    f"Avg: {sum(peak_stresses) / len(peak_stresses):.1f}"
                )

            # Tier breakdown
            tier2_count = sum(1 for e in events if e.peak_tier == 2)
            tier3_count = sum(1 for e in events if e.peak_tier == 3)
            click.echo(f"\nTier 2 (elevated): {tier2_count} events")
            click.echo(f"Tier 3 (critical): {tier3_count} events")

            # Total elevated time
            total_duration = 0.0
            for e in events:
                if e.end_timestamp:
                    total_duration += (e.end_timestamp - e.start_timestamp).total_seconds()
            if total_duration > 0:
                mins = total_duration / 60
                click.echo(f"\nTotal elevated time: {total_duration:.0f}s ({mins:.1f}m)")
    finally:
        conn.close()


@main.command()
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def prune(events_days: int | None, dry_run: bool, force: bool) -> None:
    """Delete old reviewed/dismissed events per retention policy.

    Only prunes events marked as 'reviewed' or 'dismissed'.
    Unreviewed and pinned events are never automatically deleted.
    """
    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, prune_old_data

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found.")
        return

    events_days = events_days or config.retention.events_days

    if dry_run:
        click.echo(f"Would prune reviewed/dismissed events older than {events_days} days")
        return

    if not force:
        click.confirm(
            f"Delete reviewed/dismissed events older than {events_days} days?",
            abort=True,
        )

    conn = get_connection(config.db_path)
    try:
        events_deleted = prune_old_data(conn, events_days=events_days)
    finally:
        conn.close()

    click.echo(f"Deleted {events_deleted} events (with their samples)")


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
    click.echo("[tiers]")
    click.echo(f"  elevated_threshold = {cfg.tiers.elevated_threshold}")
    click.echo(f"  critical_threshold = {cfg.tiers.critical_threshold}")
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
