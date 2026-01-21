"""Main TUI application."""

import sqlite3

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

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
        self._metrics: dict = {}

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
