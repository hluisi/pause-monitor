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
    from pause_monitor.tui import PauseMonitorApp

    config = Config.load()
    app = PauseMonitorApp(config)
    app.run()


@main.command()
def status():
    """Quick health check."""
    click.echo("Status not yet implemented")


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
