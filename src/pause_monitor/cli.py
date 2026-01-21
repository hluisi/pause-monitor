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
def status():
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
                click.echo(f"  - {event.timestamp.strftime('%H:%M:%S')}: {event.duration:.1f}s pause")
    finally:
        conn.close()


@main.command()
@click.argument("event_id", required=False)
def events(event_id):
    """List or inspect pause events."""
    if event_id:
        click.echo(f"Event {event_id} not yet implemented")
    else:
        click.echo("Events list not yet implemented")


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
