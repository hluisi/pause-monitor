"""CLI commands for pause-monitor."""

import click


@click.group()
@click.version_option()
def main():
    """Track down intermittent macOS system pauses."""
    pass


@main.command()
def daemon():
    """Run the background sampler."""
    import asyncio

    from pause_monitor.daemon import run_daemon

    asyncio.run(run_daemon())


@main.command()
def tui():
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


@main.command()
@click.argument("event_id", required=False, type=int)
@click.option("--limit", "-n", default=20, help="Number of events to show")
def events(event_id: int | None, limit: int) -> None:
    """List or inspect pause events."""
    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, get_event_by_id, get_events

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = get_connection(config.db_path)
    try:
        if event_id:
            # Show single event details
            event = get_event_by_id(conn, event_id)
            if not event:
                click.echo(f"Event {event_id} not found.")
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
                return

            click.echo(f"{'ID':>5}  {'Time':20}  {'Duration':>10}  {'Stress':>7}  Culprits")
            click.echo("-" * 70)

            for event in event_list:
                culprits_str = ", ".join(event.culprits[:2]) if event.culprits else "-"
                click.echo(
                    f"{event.id:>5}  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S'):20}  "
                    f"{event.duration:>8.1f}s  {event.stress.total:>6}/100  {culprits_str}"
                )
    finally:
        conn.close()


@main.command()
def history():
    """Query historical data."""
    click.echo("History not yet implemented")


@main.command()
def install():
    """Set up launchd service."""
    click.echo("Install not yet implemented")


@main.command()
def uninstall():
    """Remove launchd service."""
    click.echo("Uninstall not yet implemented")


if __name__ == "__main__":
    main()
