"""Main TUI application."""

import sqlite3
from typing import Any

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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._metrics: dict[str, Any] = {}

    def update_metrics(self, metrics: dict[str, Any]) -> None:
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
        from pause_monitor.storage import get_connection, get_schema_version

        self.title = "pause-monitor"
        self.sub_title = "System Health Monitor"

        # Check if database exists and has schema
        if not self.config.db_path.exists():
            self.notify(
                "Database not found. Run 'pause-monitor daemon' first.",
                severity="error",
                timeout=10,
            )
            return

        # Connect to database
        self._conn = get_connection(self.config.db_path)

        # Verify schema is initialized
        if get_schema_version(self._conn) == 0:
            self._conn.close()
            self._conn = None
            self.notify(
                "Database not initialized. Run 'pause-monitor daemon' first.",
                severity="error",
                timeout=10,
            )
            return

        # Start periodic refresh
        self.set_interval(1.0, self._refresh_data)
        # Load initial data
        self._refresh_data()

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

        from pause_monitor.storage import get_events, get_recent_samples

        samples = get_recent_samples(self._conn, limit=1)
        if samples:
            sample = samples[0]
            # Update stress gauge
            stress_gauge = self.query_one("#stress-gauge", StressGauge)
            stress_gauge.update_stress(sample.stress.total)

            # Update metrics panel
            metrics_panel = self.query_one("#metrics", MetricsPanel)
            metrics_panel.update_metrics(
                {
                    "cpu_pct": sample.cpu_pct or 0,
                    "load_avg": sample.load_avg or 0,
                    "mem_available": sample.mem_available or 0,
                    "cpu_freq": sample.cpu_freq or 0,
                    "throttled": sample.throttled or False,
                }
            )

            # Update stress breakdown
            breakdown = self.query_one("#breakdown", Static)
            breakdown.update(
                f"Load: {sample.stress.load:3d}  Memory: {sample.stress.memory:3d}\n"
                f"Thermal: {sample.stress.thermal:3d}  Latency: {sample.stress.latency:3d}\n"
                f"I/O: {sample.stress.io:3d}"
            )

        # Update events table
        events = get_events(self._conn, limit=10)
        events_table = self.query_one("#events", EventsTable)
        events_table.clear()
        for event in events:
            culprits_str = ", ".join(event.culprits[:3]) if event.culprits else "-"
            if len(event.culprits) > 3:
                culprits_str += f" (+{len(event.culprits) - 3})"
            events_table.add_row(
                event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                f"{event.duration:.1f}s",
                str(event.stress.total),
                culprits_str,
            )

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
