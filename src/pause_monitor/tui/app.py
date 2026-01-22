"""Main TUI application."""

import sqlite3
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

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
        self.add_column("Status", width=3)
        self.add_column("Time", width=20)
        self.add_column("Duration", width=10)
        self.add_column("Stress", width=8)
        self.add_column("Culprits", width=30)


# Status icons for event display
STATUS_ICONS = {
    "unreviewed": "○",
    "reviewed": "✓",
    "pinned": "📌",
    "dismissed": "✗",
}


class EventDetailScreen(Screen):
    """Screen showing details of a single event."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("r", "mark_reviewed", "Reviewed"),
        Binding("p", "mark_pinned", "Pin"),
        Binding("d", "mark_dismissed", "Dismiss"),
    ]

    DEFAULT_CSS = """
    EventDetailScreen {
        align: center middle;
    }

    EventDetailScreen > Vertical {
        width: 80%;
        height: 80%;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    EventDetailScreen .title {
        text-style: bold;
        margin-bottom: 1;
    }

    EventDetailScreen .section {
        margin-top: 1;
        text-style: bold;
        color: $text-muted;
    }

    EventDetailScreen .culprit-list {
        margin-left: 2;
        height: auto;
        max-height: 10;
    }

    EventDetailScreen .notes {
        margin-left: 2;
        color: $text-muted;
    }
    """

    def __init__(self, event_id: int, conn: sqlite3.Connection):
        super().__init__()
        self.event_id = event_id
        self._conn = conn
        self._event: Any = None

    def compose(self) -> ComposeResult:
        """Build the detail view layout."""
        from pause_monitor.storage import get_event_by_id

        self._event = get_event_by_id(self._conn, self.event_id)
        if not self._event:
            yield Vertical(
                Label("Event not found", classes="title"),
            )
            return

        event = self._event
        status_icon = STATUS_ICONS.get(event.status, "?")

        yield Vertical(
            Label(f"Event #{event.id} {status_icon}", classes="title"),
            Label(f"Time: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"),
            Label(f"Duration: {event.duration:.2f}s"),
            Label(f"Status: {event.status}"),
            Label("Stress Breakdown:", classes="section"),
            Label(
                f"  Total: {event.stress.total}  "
                f"Load: {event.stress.load}  Memory: {event.stress.memory}  "
                f"Thermal: {event.stress.thermal}  Latency: {event.stress.latency}  "
                f"I/O: {event.stress.io}"
            ),
            Label("Culprits:", classes="section"),
            VerticalScroll(
                *[Label(f"  • {c}") for c in event.culprits] if event.culprits else [Label("  (none)")],
                classes="culprit-list",
            ),
            Label("Notes:", classes="section"),
            Label(f"  {event.notes or '(none)'}", classes="notes"),
            Label(f"Event Dir: {event.event_dir or '(none)'}"),
        )

    def _update_status(self, new_status: str) -> None:
        """Update event status and refresh."""
        if not self._conn:
            self.app.notify("Database connection lost", severity="error")
            return

        from pause_monitor.storage import update_event_status

        try:
            update_event_status(self._conn, self.event_id, new_status)
            self.app.pop_screen()
            self.app.notify(f"Event #{self.event_id} marked as {new_status}")
        except Exception as e:
            self.app.notify(f"Failed to update status: {e}", severity="error")

    def action_mark_reviewed(self) -> None:
        """Mark event as reviewed."""
        self._update_status("reviewed")

    def action_mark_pinned(self) -> None:
        """Mark event as pinned."""
        self._update_status("pinned")

    def action_mark_dismissed(self) -> None:
        """Mark event as dismissed."""
        self._update_status("dismissed")


class EventsScreen(Screen):
    """Full-screen events list with filtering and selection."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("enter", "select_event", "View"),
        Binding("r", "mark_reviewed", "Reviewed"),
        Binding("p", "mark_pinned", "Pin"),
        Binding("d", "mark_dismissed", "Dismiss"),
        Binding("u", "filter_unreviewed", "Unreviewed"),
        Binding("a", "filter_all", "All"),
    ]

    DEFAULT_CSS = """
    EventsScreen {
        layout: vertical;
    }

    EventsScreen > Container {
        height: 100%;
    }

    EventsScreen DataTable {
        height: 1fr;
    }

    EventsScreen .filter-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self._conn = conn
        self._filter_status: str | None = None
        self._events: list = []

    def compose(self) -> ComposeResult:
        """Build the events list layout."""
        yield Header()
        yield Container(
            Static("Filter: All events", id="filter-label", classes="filter-bar"),
            DataTable(id="events-list"),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set up the events table."""
        table = self.query_one("#events-list", DataTable)
        table.cursor_type = "row"
        table.add_column("ID", width=5)
        table.add_column("Status", width=3)
        table.add_column("Time", width=20)
        table.add_column("Duration", width=10)
        table.add_column("Stress", width=8)
        table.add_column("Culprits", width=40)
        self._refresh_events()

    def _refresh_events(self) -> None:
        """Refresh the events list from database."""
        from pause_monitor.storage import get_events

        self._events = get_events(self._conn, limit=100, status=self._filter_status)

        table = self.query_one("#events-list", DataTable)
        table.clear()

        for event in self._events:
            status_icon = STATUS_ICONS.get(event.status, "?")
            culprits_str = ", ".join(event.culprits[:3]) if event.culprits else "-"
            if event.culprits and len(event.culprits) > 3:
                culprits_str += f" (+{len(event.culprits) - 3})"

            table.add_row(
                str(event.id),
                status_icon,
                event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                f"{event.duration:.1f}s",
                str(event.stress.total),
                culprits_str,
                key=str(event.id),
            )

        # Update filter label
        filter_label = self.query_one("#filter-label", Static)
        if self._filter_status:
            filter_label.update(f"Filter: {self._filter_status}")
        else:
            filter_label.update("Filter: All events")

    def _get_selected_event_id(self) -> int | None:
        """Get the ID of the currently selected event."""
        table = self.query_one("#events-list", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._events):
            return self._events[table.cursor_row].id
        return None

    def action_select_event(self) -> None:
        """View selected event details."""
        event_id = self._get_selected_event_id()
        if event_id:
            self.app.push_screen(EventDetailScreen(event_id, self._conn))

    def _update_selected_status(self, new_status: str) -> None:
        """Update selected event's status."""
        if not self._conn:
            self.notify("Database connection lost", severity="error")
            return

        from pause_monitor.storage import update_event_status

        event_id = self._get_selected_event_id()
        if event_id:
            try:
                update_event_status(self._conn, event_id, new_status)
                self._refresh_events()
                self.notify(f"Event #{event_id} marked as {new_status}")
            except Exception as e:
                self.notify(f"Failed to update status: {e}", severity="error")

    def action_mark_reviewed(self) -> None:
        """Mark selected event as reviewed."""
        self._update_selected_status("reviewed")

    def action_mark_pinned(self) -> None:
        """Mark selected event as pinned."""
        self._update_selected_status("pinned")

    def action_mark_dismissed(self) -> None:
        """Mark selected event as dismissed."""
        self._update_selected_status("dismissed")

    def action_filter_unreviewed(self) -> None:
        """Filter to show only unreviewed events."""
        self._filter_status = "unreviewed"
        self._refresh_events()

    def action_filter_all(self) -> None:
        """Show all events."""
        self._filter_status = None
        self._refresh_events()


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
            status_icon = STATUS_ICONS.get(event.status, "?")
            culprits_str = ", ".join(event.culprits[:3]) if event.culprits else "-"
            if len(event.culprits) > 3:
                culprits_str += f" (+{len(event.culprits) - 3})"
            events_table.add_row(
                status_icon,
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
        if not self._conn:
            self.notify("Database not connected", severity="error")
            return
        self.push_screen(EventsScreen(self._conn))

    def action_show_history(self) -> None:
        """Show history view."""
        self.notify("History view not yet implemented")


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = PauseMonitorApp(config)
    app.run()
