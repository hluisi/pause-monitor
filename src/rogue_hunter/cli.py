"""CLI commands for rogue-hunter."""

from pathlib import Path

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

    from rogue_hunter.daemon import run_daemon

    asyncio.run(run_daemon())


@main.command()
def tui() -> None:
    """Launch interactive dashboard."""
    from rogue_hunter.config import Config
    from rogue_hunter.tui import run_tui

    config = Config.load()
    run_tui(config)


@main.command()
def status() -> None:
    """Quick health check."""
    import time

    from rogue_hunter.boottime import get_boot_time
    from rogue_hunter.config import Config
    from rogue_hunter.storage import DatabaseNotAvailable, get_open_events, require_database

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

    from rogue_hunter.boottime import get_boot_time
    from rogue_hunter.config import Config
    from rogue_hunter.storage import (
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

            from rogue_hunter.formatting import format_duration

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
@click.option("--forensics", "-f", is_flag=True, help="Show forensic capture details")
@click.option("--threads", "-t", is_flag=True, help="Show spindump thread states")
@click.option("--logs", "-l", is_flag=True, help="Show system log entries")
@click.pass_context
def events_show(ctx, event_id: int, forensics: bool, threads: bool, logs: bool) -> None:
    """Show details of a specific process event."""
    import json
    from datetime import datetime

    from rogue_hunter.storage import (
        get_buffer_context,
        get_forensic_captures,
        get_log_entries,
        get_process_event_detail,
        get_process_snapshots,
        get_spindump_processes,
        get_spindump_threads,
        require_database,
    )

    config = ctx.obj["config"]

    with require_database(config.db_path, exit_on_missing=True) as conn:
        event = get_process_event_detail(conn, event_id)

        if not event:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        from rogue_hunter.formatting import format_duration_verbose

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
            snapshot = event["peak_snapshot"]
            for key, val in snapshot.items():
                click.echo(f"  {key}: {val}")

        # Show all snapshots
        snapshots = get_process_snapshots(conn, event_id)
        if snapshots:
            click.echo(f"\nSnapshots: {len(snapshots)}")
            for snap in snapshots:
                dominant = snap.get("dominant_resource", "unknown")
                disprop = snap.get("disproportionality", 0.0)
                score_val = snap["score"]
                cpu_val = snap["cpu"]
                mem_val = snap["mem"]
                click.echo(
                    f"  [{snap['snapshot_type']}] score={score_val} "
                    f"cpu={cpu_val:.1f} mem={mem_val} [dominant: {dominant} ({disprop:.1%})]"
                )

        # Show forensic captures
        captures = get_forensic_captures(conn, event_id)
        if captures:
            click.echo(f"\nForensic Captures: {len(captures)}")
            for cap in captures:
                cap_time = datetime.fromtimestamp(cap["captured_at"])
                click.echo(f"\n  [{cap['trigger']}] at {cap_time.strftime('%H:%M:%S')}")
                click.echo(f"    Tailspin: {cap['tailspin_status'] or 'pending'}")
                click.echo(f"    Logs: {cap['logs_status'] or 'pending'}")

                if forensics:
                    # Show buffer context
                    context = get_buffer_context(conn, cap["id"])
                    if context:
                        click.echo(
                            f"    Buffer: {context['sample_count']} samples, "
                            f"peak {context['peak_score']}"
                        )
                        try:
                            culprits = json.loads(context["culprits"])
                            for culprit in culprits[:5]:
                                dominant = culprit.get("dominant_resource", "unknown")
                                disprop = culprit.get("disproportionality", 0.0)
                                score_val = culprit.get("score", 0)
                                click.echo(
                                    f"      - {culprit['command']} ({score_val}) "
                                    f"[dominant: {dominant} ({disprop:.1%})]"
                                )
                        except json.JSONDecodeError:
                            pass

                if threads:
                    # Show spindump thread states
                    procs = get_spindump_processes(conn, cap["id"])
                    if procs:
                        click.echo(f"    Spindump Processes: {len(procs)}")
                        for proc in procs[:10]:
                            footprint = (
                                f"{proc['footprint_mb']:.1f}MB" if proc["footprint_mb"] else "?"
                            )
                            click.echo(f"      {proc['name']} [{proc['pid']}] ({footprint})")
                            proc_threads = get_spindump_threads(conn, proc["id"])
                            for t in proc_threads[:5]:
                                state = t["state"] or "unknown"
                                name = t["thread_name"] or "unnamed"
                                click.echo(f"        Thread {t['thread_id']}: {state} ({name})")

                if logs:
                    # Show log entries
                    entries = get_log_entries(conn, cap["id"], limit=20)
                    if entries:
                        click.echo(f"    Log Entries: {len(entries)}")
                        for entry in entries:
                            subsys = entry["subsystem"] or "system"
                            msg = entry["event_message"][:80]
                            click.echo(f"      [{entry['timestamp'][:19]}] {subsys}: {msg}")
        elif forensics or threads or logs:
            click.echo("\nNo forensic captures for this event.")


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

    from rogue_hunter.config import Config
    from rogue_hunter.storage import DatabaseNotAvailable, get_process_events, require_database

    config = Config.load()

    try:
        with require_database(config.db_path) as conn:
            # Get events from time range
            cutoff = time.time() - (hours * 3600)
            events = get_process_events(conn, time_cutoff=cutoff, limit=1000)

            if not events:
                click.echo(f"No events in the last {hours} hour{'s' if hours != 1 else ''}.")
                return

            from rogue_hunter.formatting import calculate_duration

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
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def prune(events_days: int | None, dry_run: bool, force: bool) -> None:
    """Delete old closed process events.

    Prunes closed process_events older than events_days.
    """
    from rogue_hunter.config import Config
    from rogue_hunter.storage import get_connection, prune_old_data

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'rogue-hunter daemon' first.")
        return

    events_days = events_days or config.retention.events_days

    if dry_run:
        click.echo(f"Would prune closed events older than {events_days} days")
        return

    if not force:
        click.confirm(
            f"Delete closed events older than {events_days} days?",
            abort=True,
        )

    conn = get_connection(config.db_path)
    try:
        events_deleted = prune_old_data(conn, events_days=events_days)
    finally:
        conn.close()

    click.echo(f"Deleted {events_deleted} events")


@main.group()
def config() -> None:
    """Manage configuration."""
    pass


@config.command("show")
def config_show() -> None:
    """Display current configuration."""
    from rogue_hunter.config import Config

    cfg = Config.load()

    click.echo(f"Config file: {cfg.config_path}")
    click.echo(f"Exists: {cfg.config_path.exists()}")
    click.echo()
    click.echo("[retention]")
    click.echo(f"  events_days = {cfg.retention.events_days}")
    click.echo()
    click.echo("[system]")
    click.echo(f"  ring_buffer_size = {cfg.system.ring_buffer_size}")
    click.echo()
    click.echo("[bands]")
    click.echo(f"  medium = {cfg.bands.medium}")
    click.echo(f"  elevated = {cfg.bands.elevated}")
    click.echo(f"  high = {cfg.bands.high}")
    click.echo(f"  critical = {cfg.bands.critical}")
    click.echo(f"  tracking_band = {cfg.bands.tracking_band}")
    click.echo(f"  forensics_band = {cfg.bands.forensics_band}")


@config.command("edit")
def config_edit() -> None:
    """Open config file in editor."""
    import os
    import subprocess

    from rogue_hunter.config import Config

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
    from rogue_hunter.config import Config

    cfg = Config()
    cfg.save()
    click.echo(f"Config reset to defaults at {cfg.config_path}")


def _setup_sudoers(username: str, runtime_dir: Path) -> None:
    """Create sudoers rule for tailspin save.

    Creates /etc/sudoers.d/rogue-hunter with a narrow rule allowing
    tailspin to write only to the configured runtime directory.

    Args:
        username: The user to grant sudo access to
        runtime_dir: Directory for tailspin captures (from config.runtime_dir)

    Raises:
        RuntimeError: If the sudoers rule is invalid
    """
    import os
    import subprocess

    sudoers_path = Path("/etc/sudoers.d/rogue-hunter")

    # Narrow rule: only allow tailspin save to the runtime directory
    rule = f"{username} ALL = (root) NOPASSWD: /usr/bin/tailspin save -o {runtime_dir}/*\n"

    # Write with correct permissions (must be done atomically)
    sudoers_path.write_text(rule)
    os.chmod(sudoers_path, 0o440)
    os.chown(sudoers_path, 0, 0)  # root:wheel (wheel is gid 0 on macOS)

    # Validate with visudo
    result = subprocess.run(
        ["/usr/sbin/visudo", "-c", "-f", str(sudoers_path)],
        capture_output=True,
    )
    if result.returncode != 0:
        # Invalid syntax - remove and raise
        sudoers_path.unlink()
        raise RuntimeError(f"Invalid sudoers syntax: {result.stderr.decode()}")


# =============================================================================
# perms command group - Permissions for forensics (sudoers + tailspin)
# =============================================================================


@main.group()
def perms() -> None:
    """Manage permissions for forensics capture."""
    pass


@perms.command("install")
def perms_install() -> None:
    """Set up sudoers rule and enable tailspin.

    Must be run with sudo. This allows the daemon to capture
    forensic data without requiring the daemon itself to run as root.
    """
    import os
    import subprocess

    from rogue_hunter.config import Config

    if os.getuid() != 0:
        click.echo("Error: requires root privileges. Use sudo.", err=True)
        raise SystemExit(1)

    username = os.environ.get("SUDO_USER")
    if not username:
        click.echo("Error: Could not determine user. Run with sudo, not as root.", err=True)
        raise SystemExit(1)

    # Load config to get runtime_dir
    cfg = Config.load()

    # Set up sudoers for tailspin
    click.echo("Setting up sudoers rule for tailspin...")
    _setup_sudoers(username, cfg.runtime_dir)
    click.echo(f"  Created /etc/sudoers.d/rogue-hunter (allows writes to {cfg.runtime_dir})")

    # Enable tailspin
    click.echo("Enabling tailspin...")
    subprocess.run(["/usr/bin/tailspin", "enable"], check=True)
    click.echo("  tailspin enabled")

    click.echo("\nPermissions configured. Forensics capture is now available.")


@perms.command("uninstall")
def perms_uninstall() -> None:
    """Remove sudoers rule and disable tailspin.

    Must be run with sudo.
    """
    import os
    import subprocess

    if os.getuid() != 0:
        click.echo("Error: requires root privileges. Use sudo.", err=True)
        raise SystemExit(1)

    # Remove sudoers rule
    sudoers_path = Path("/etc/sudoers.d/rogue-hunter")
    if sudoers_path.exists():
        sudoers_path.unlink()
        click.echo("Removed /etc/sudoers.d/rogue-hunter")
    else:
        click.echo("Sudoers rule was not installed")

    # Disable tailspin
    click.echo("Disabling tailspin...")
    subprocess.run(["/usr/bin/tailspin", "disable"], check=True)
    click.echo("  tailspin disabled")

    click.echo("\nPermissions removed.")


@perms.command("status")
def perms_status() -> None:
    """Check if permissions are configured."""
    import subprocess
    from pathlib import Path

    sudoers_path = Path("/etc/sudoers.d/rogue-hunter")
    sudoers_ok = sudoers_path.exists()

    # Check tailspin status
    result = subprocess.run(
        ["/usr/bin/tailspin", "stat"],
        capture_output=True,
        text=True,
    )
    tailspin_enabled = "oncore is enabled" in result.stdout.lower()

    click.echo(f"Sudoers rule: {'installed' if sudoers_ok else 'not installed'}")
    click.echo(f"Tailspin:     {'enabled' if tailspin_enabled else 'disabled'}")

    if sudoers_ok and tailspin_enabled:
        click.echo("\nForensics capture is available.")
    else:
        click.echo("\nRun 'sudo rogue-hunter perms install' to enable forensics.")


# =============================================================================
# service command group - LaunchAgent/LaunchDaemon management
# =============================================================================


@main.group()
def service() -> None:
    """Manage the background daemon service."""
    pass


def _get_service_paths(system_wide: bool, username: str) -> tuple:
    """Get launchd paths for service management.

    Returns (plist_dir, plist_path, service_target, label)
    """
    import pwd
    from pathlib import Path

    label = "com.rogue-hunter.daemon"

    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
    else:
        user_uid = pwd.getpwnam(username).pw_uid
        plist_dir = Path(f"/Users/{username}/Library/LaunchAgents")
        service_target = f"gui/{user_uid}"

    plist_path = plist_dir / f"{label}.plist"
    return plist_dir, plist_path, service_target, label


@service.command("install")
@click.option("--system", "system_wide", is_flag=True, help="Install system-wide (requires root)")
@click.option("--force", is_flag=True, help="Overwrite existing plist without prompting")
def service_install(system_wide: bool, force: bool) -> None:
    """Install the daemon as a launchd service.

    Creates a LaunchAgent (user) or LaunchDaemon (system) that
    starts automatically and restarts if it crashes.
    """
    import os
    import pwd
    import subprocess
    import sys
    from pathlib import Path

    # System-wide requires root
    if system_wide and os.getuid() != 0:
        click.echo("Error: system-wide install requires root. Use sudo.", err=True)
        raise SystemExit(1)

    # Determine username
    if os.getuid() == 0:
        username = os.environ.get("SUDO_USER")
        if not username:
            click.echo("Error: Could not determine user. Run with sudo, not as root.", err=True)
            raise SystemExit(1)
    else:
        username = os.environ.get("USER")

    plist_dir, plist_path, service_target, label = _get_service_paths(system_wide, username)

    # Check for existing plist
    if plist_path.exists() and not force:
        if not click.confirm(f"Plist already exists at {plist_path}. Overwrite?"):
            click.echo("Aborted.")
            return

    # Create log directory if needed
    user_info = pwd.getpwnam(username)
    log_dir = Path(f"/Users/{username}/.local/share/rogue-hunter")
    log_dir.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        os.chown(log_dir, user_info.pw_uid, user_info.pw_gid)

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
        <string>rogue_hunter.cli</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/{username}/.local/share/rogue-hunter/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/{username}/.local/share/rogue-hunter/daemon.log</string>
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

    # Bootstrap the service
    try:
        subprocess.run(
            ["launchctl", "bootstrap", service_target, str(plist_path)],
            check=True,
            capture_output=True,
        )
        click.echo("Service installed and started")
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr.decode()
        stderr_lower = stderr_text.lower()
        if "already loaded" in stderr_lower or "service already loaded" in stderr_lower:
            click.echo("Service was already installed")
        else:
            click.echo(f"Warning: Could not start service: {stderr_text}")

    click.echo("\nTo check status: rogue-hunter service status")
    click.echo(f"To view logs: tail -f /Users/{username}/.local/share/rogue-hunter/daemon.log")


@service.command("uninstall")
@click.option("--system", "system_wide", is_flag=True, help="Uninstall system-wide service")
@click.option("--keep-data", is_flag=True, help="Keep database and config files")
@click.option("--force", is_flag=True, help="Skip confirmation prompts")
def service_uninstall(system_wide: bool, keep_data: bool, force: bool) -> None:
    """Remove the launchd service.

    Stops the daemon and removes the LaunchAgent/LaunchDaemon plist.
    """
    import os
    import shutil
    import subprocess

    # System-wide requires root
    if system_wide and os.getuid() != 0:
        click.echo("Error: system-wide uninstall requires root. Use sudo.", err=True)
        raise SystemExit(1)

    # Determine username
    if os.getuid() == 0:
        username = os.environ.get("SUDO_USER")
        if not username:
            click.echo("Error: Could not determine user. Run with sudo, not as root.", err=True)
            raise SystemExit(1)
    else:
        username = os.environ.get("USER")

    plist_dir, plist_path, service_target, label = _get_service_paths(system_wide, username)

    # Bootout the service
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
        from rogue_hunter.config import Config

        config = Config()

        if config.data_dir.exists():
            if force or click.confirm(f"Delete data directory {config.data_dir}?"):
                shutil.rmtree(config.data_dir)
                click.echo(f"Removed {config.data_dir}")

        if config.config_dir.exists():
            if force or click.confirm(f"Delete config directory {config.config_dir}?"):
                shutil.rmtree(config.config_dir)
                click.echo(f"Removed {config.config_dir}")

    click.echo("\nService uninstalled.")


@service.command("status")
@click.option("--system", "system_wide", is_flag=True, help="Check system-wide service")
def service_status(system_wide: bool) -> None:
    """Check if the launchd service is installed and running."""
    import os
    import subprocess

    # Determine username
    username = os.environ.get("USER")
    if os.getuid() == 0:
        username = os.environ.get("SUDO_USER", username)

    plist_dir, plist_path, service_target, label = _get_service_paths(system_wide, username)

    plist_exists = plist_path.exists()
    click.echo(f"Plist: {'installed' if plist_exists else 'not installed'}")
    if plist_exists:
        click.echo(f"  Path: {plist_path}")

    # Check if service is loaded/running
    result = subprocess.run(
        ["launchctl", "print", f"{service_target}/{label}"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Parse state from output
        state = "unknown"
        pid = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("state ="):
                state = line.split("=")[1].strip()
            elif line.startswith("pid ="):
                pid = line.split("=")[1].strip()

        click.echo(f"Service: {state}")
        if pid:
            click.echo(f"  PID: {pid}")
    else:
        click.echo("Service: not loaded")


if __name__ == "__main__":
    main()
