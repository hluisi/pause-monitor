"""Main TUI application."""

import asyncio
import logging
import sqlite3
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from pause_monitor.config import Config
from pause_monitor.socket_client import SocketClient

log = logging.getLogger(__name__)


class StressGauge(Static):
    """Visual stress level gauge showing max process score."""

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

    StressGauge.disconnected {
        border: solid $error;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._score = 0
        self._connected = False

    def on_mount(self) -> None:
        """Show initial disconnected state."""
        self.set_disconnected()

    def update_score(self, score: int) -> None:
        """Update gauge with max process score."""
        self._score = score
        self._connected = True
        self.remove_class("disconnected")
        self.update(f"Score: {score:3d}/100 {'█' * (score // 5)}{'░' * (20 - score // 5)}")

        # Update styling based on level
        self.remove_class("elevated", "critical")
        if score >= 60:
            self.add_class("critical")
        elif score >= 30:
            self.add_class("elevated")

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self._connected = False
        self.remove_class("elevated", "critical")
        self.add_class("disconnected")
        self.update("Score: ---/100  (not connected)")


class SampleInfoPanel(Static):
    """Panel showing current sample info (tier, process count, etc.)."""

    DEFAULT_CSS = """
    SampleInfoPanel {
        height: auto;
        min-height: 4;
        border: solid $primary;
        padding: 1;
    }

    SampleInfoPanel.disconnected {
        border: solid $error;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tier = 1
        self._process_count = 0
        self._sample_count = 0

    def on_mount(self) -> None:
        """Show initial disconnected state."""
        self.set_disconnected()

    def update_info(self, tier: int, process_count: int, sample_count: int) -> None:
        """Update displayed sample info."""
        self._tier = tier
        self._process_count = process_count
        self._sample_count = sample_count
        self.remove_class("disconnected")

        tier_labels = {1: "Normal", 2: "Elevated", 3: "Critical"}
        lines = [
            f"Tier: {tier} ({tier_labels.get(tier, 'Unknown')})",
            f"Processes: {process_count}",
            f"Samples: {sample_count}",
        ]
        self.update("\n".join(lines))

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.add_class("disconnected")
        self.update("(not connected)\n\nStart daemon with:\nsudo pause-monitor daemon")


class ProcessesPanel(Static):
    """Panel showing top rogue processes by score."""

    DEFAULT_CSS = """
    ProcessesPanel {
        height: 100%;
        border: solid $primary;
        padding: 0;
    }

    ProcessesPanel.disconnected {
        border: solid $error;
    }

    ProcessesPanel DataTable {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        """Create rogue process table."""
        yield DataTable(id="rogue-processes")

    def on_mount(self) -> None:
        """Set up table with columns."""
        self._table = self.query_one("#rogue-processes", DataTable)

        # Configure table: Command, Score, CPU%, Mem, Pageins, State
        self._table.add_columns("Command", "Score", "CPU%", "Mem", "Pageins", "State")
        self._table.show_header = True
        self._table.cursor_type = "none"

        self.set_disconnected()

    def update_rogues(self, rogues: list[dict]) -> None:
        """Update with rogue process list."""
        self.remove_class("disconnected")

        if self._table:
            self._table.clear()

            for p in rogues[:10]:  # Show top 10
                self._table.add_row(
                    p.get("command", "?")[:20],
                    str(p.get("score", 0)),
                    f"{p.get('cpu', 0):.1f}%",
                    self._format_bytes(p.get("mem", 0)),
                    str(p.get("pageins", 0)),
                    p.get("state", "?")[:8],
                )

    def _format_bytes(self, bytes_val: int) -> str:
        """Format bytes as human-readable string."""
        if bytes_val < 1024:
            return f"{bytes_val}B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f}K"
        elif bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f}M"
        else:
            return f"{bytes_val / (1024 * 1024 * 1024):.1f}G"

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.add_class("disconnected")
        if self._table:
            self._table.clear()
            self._table.add_row("(not connected)", "---", "---", "---", "---", "---")


class EventsTable(DataTable):
    """Table showing recent pause events."""

    DEFAULT_CSS = """
    EventsTable {
        height: 100%;
        border: solid $primary;
    }
    """

    def on_mount(self) -> None:
        """Set up table columns."""
        self.add_column("Status", width=3)
        self.add_column("Time", width=20)
        self.add_column("Duration", width=10)
        self.add_column("Stress", width=8)


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
        from pause_monitor.storage import get_event_by_id, get_process_samples

        self._event = get_event_by_id(self._conn, self.event_id)
        if not self._event:
            yield Vertical(
                Label("Event not found", classes="title"),
            )
            return

        event = self._event
        status_icon = STATUS_ICONS.get(event.status, "?")

        # Calculate duration
        if event.end_timestamp:
            duration = (event.end_timestamp - event.start_timestamp).total_seconds()
            duration_str = f"{duration:.1f}s"
            end_time_str = event.end_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        else:
            duration_str = "ongoing"
            end_time_str = "(ongoing)"

        # Get rogue processes from stored samples
        samples = get_process_samples(self._conn, self.event_id)
        rogues: list[str] = []
        if samples:
            # Collect unique rogues across all samples, sorted by max score
            rogue_map: dict[tuple[int, str], int] = {}  # (pid, command) -> max_score
            for sample in samples:
                for rogue in sample.data.rogues:
                    key = (rogue.pid, rogue.command)
                    if key not in rogue_map or rogue.score > rogue_map[key]:
                        rogue_map[key] = rogue.score
            # Sort by score descending, take top 10
            sorted_rogues = sorted(rogue_map.items(), key=lambda x: x[1], reverse=True)[:10]
            rogues = [f"{cmd} (PID {pid}, score {score})" for (pid, cmd), score in sorted_rogues]

        yield Vertical(
            Label(f"Event #{event.id} {status_icon}", classes="title"),
            Label(f"Start: {event.start_timestamp.strftime('%Y-%m-%d %H:%M:%S')}"),
            Label(f"End: {end_time_str}"),
            Label(f"Duration: {duration_str}"),
            Label(f"Status: {event.status}"),
            Label(f"Peak Tier: {event.peak_tier or '-'}"),
            Label(f"Peak Score: {event.peak_stress or '-'}"),
            Label("Top Rogues:", classes="section"),
            VerticalScroll(
                *([Label(f"  {r}") for r in rogues] if rogues else [Label("  (none)")]),
                classes="culprit-list",
            ),
            Label("Notes:", classes="section"),
            Label(f"  {event.notes or '(none)'}", classes="notes"),
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
        self._refresh_events()

    def _refresh_events(self) -> None:
        """Refresh the events list from database."""
        from pause_monitor.storage import get_events

        self._events = get_events(self._conn, limit=100, status=self._filter_status)

        table = self.query_one("#events-list", DataTable)
        table.clear()

        for event in self._events:
            status_icon = STATUS_ICONS.get(event.status, "?")
            # Calculate duration from timestamps
            if event.end_timestamp:
                duration = (event.end_timestamp - event.start_timestamp).total_seconds()
                duration_str = f"{duration:.1f}s"
            else:
                duration_str = "ongoing"
            # Use peak_stress instead of stress.total
            stress_str = str(event.peak_stress) if event.peak_stress else "-"

            table.add_row(
                str(event.id),
                status_icon,
                event.start_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                duration_str,
                stress_str,
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

    /* Row 1: score gauge spans full width */
    #stress-gauge {
        column-span: 2;
    }

    /* Row 2: sample info (left) + events (right) */
    #sample-info {
        height: auto;
        min-height: 4;
    }

    #events {
        height: auto;
        min-height: 8;
    }

    /* Row 3: processes spans full width */
    #processes {
        column-span: 2;
        height: 1fr;
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
        self._socket_client: SocketClient | None = None
        self._use_socket: bool = False
        self._socket_read_task: asyncio.Task | None = None

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

        # Start async socket connection attempt
        asyncio.create_task(self._try_socket_connect())

        # Load events from database (events only, not samples)
        self._refresh_events()
        # Refresh events periodically (new events may be added by daemon)
        self.set_interval(5.0, self._refresh_events)

    def on_unmount(self) -> None:
        """Cleanup on shutdown."""
        # Cancel socket read task
        if self._socket_read_task and not self._socket_read_task.done():
            self._socket_read_task.cancel()

        # Schedule async socket cleanup
        if self._socket_client:
            asyncio.create_task(self._socket_client.disconnect())

        if self._conn:
            self._conn.close()
            self._conn = None

    async def _try_socket_connect(self) -> None:
        """Try to connect to daemon via socket for real-time data."""
        self._socket_client = SocketClient(socket_path=self.config.socket_path)

        try:
            await self._socket_client.connect()
            self._use_socket = True
            self.sub_title = "System Health Monitor (live)"
            log.info("tui_socket_connected path=%s", self.config.socket_path)
            # Start reading messages
            self._socket_read_task = asyncio.create_task(self._read_socket_loop())
        except FileNotFoundError:
            self._set_disconnected("socket not found - daemon not running")
            self.notify(
                "Daemon not running. Start with: sudo pause-monitor daemon",
                severity="warning",
            )
        except PermissionError as e:
            self._set_disconnected(f"permission denied: {e}")
            self.notify(
                f"Socket permission denied: {e}",
                severity="error",
            )
        except Exception as e:
            self._set_disconnected(f"{type(e).__name__}: {e}")
            self.notify(
                f"Socket connection failed: {e}",
                severity="error",
            )

    async def _read_socket_loop(self) -> None:
        """Read messages from socket and update UI."""
        try:
            while self._use_socket and self._socket_client:
                data = await self._socket_client.read_message()
                self._handle_socket_data(data)
        except ConnectionError as e:
            self._set_disconnected(f"connection lost: {e}")
            self.notify("Lost connection to daemon", severity="warning")
        except asyncio.CancelledError:
            pass  # Normal shutdown
        except Exception as e:
            self._set_disconnected(f"{type(e).__name__}: {e}")
            self.notify(f"Socket error: {e}", severity="error")  # Normal shutdown

    def _set_disconnected(self, error: str | None = None) -> None:
        """Update UI to show disconnected state."""
        self._use_socket = False
        self.sub_title = "System Health Monitor (disconnected)"

        if error:
            log.warning("tui_socket_disconnected error=%s", error)
        else:
            log.warning("tui_socket_disconnected")

        # Update widgets to show disconnected state (may not exist if app not mounted)
        try:
            self.query_one("#stress-gauge", StressGauge).set_disconnected()
        except Exception:
            pass

        try:
            self.query_one("#sample-info", SampleInfoPanel).set_disconnected()
        except Exception:
            pass

        try:
            self.query_one("#processes", ProcessesPanel).set_disconnected()
        except Exception:
            pass

    def _handle_socket_data(self, data: dict[str, Any]) -> None:
        """Handle real-time data from daemon socket."""
        msg_type = data.get("type", "sample")

        # Extract max_score based on message type
        if msg_type == "initial_state":
            max_score = data.get("max_score", 0)
            tier = data.get("tier", 1)
            sample_count = data.get("sample_count", 0)
            # For initial_state, get rogues from the last sample if available
            samples = data.get("samples", [])
            rogues = samples[-1].get("rogues", []) if samples else []
            process_count = samples[-1].get("process_count", 0) if samples else 0
        else:  # sample message
            max_score = data.get("max_score", 0)
            tier = data.get("tier", 1)
            sample_count = data.get("sample_count", 0)
            rogues = data.get("rogues", [])
            process_count = data.get("process_count", 0)

        # Update score gauge
        try:
            stress_gauge = self.query_one("#stress-gauge", StressGauge)
            stress_gauge.update_score(max_score)
        except NoMatches:
            pass  # Widget not mounted yet
        except Exception:
            log.exception("Failed to update score gauge")

        # Update sample info panel
        try:
            sample_info = self.query_one("#sample-info", SampleInfoPanel)
            sample_info.update_info(tier, process_count, sample_count)
        except NoMatches:
            pass  # Widget not mounted yet
        except Exception:
            log.exception("Failed to update sample info panel")

        # Update processes panel with rogues
        try:
            processes_panel = self.query_one("#processes", ProcessesPanel)
            processes_panel.update_rogues(rogues)
        except NoMatches:
            pass  # Widget not mounted yet
        except Exception:
            log.exception("Failed to update processes panel")

    def compose(self) -> ComposeResult:
        """Create the TUI layout."""
        yield Header()

        yield StressGauge(id="stress-gauge")
        yield SampleInfoPanel(id="sample-info")
        yield EventsTable(id="events")
        yield ProcessesPanel(id="processes")

        yield Footer()

    def _refresh_events(self) -> None:
        """Refresh events table from database."""
        if not self._conn:
            return

        from pause_monitor.storage import get_events

        # Update events table
        events = get_events(self._conn, limit=10)
        try:
            events_table = self.query_one("#events", EventsTable)
            events_table.clear()
            for event in events:
                status_icon = STATUS_ICONS.get(event.status, "?")
                # Calculate duration from timestamps
                if event.end_timestamp:
                    duration = (event.end_timestamp - event.start_timestamp).total_seconds()
                    duration_str = f"{duration:.1f}s"
                else:
                    duration_str = "ongoing"
                # Use peak_stress instead of stress.total
                stress_str = str(event.peak_stress) if event.peak_stress else "-"
                events_table.add_row(
                    status_icon,
                    event.start_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    duration_str,
                    stress_str,
                )
        except NoMatches:
            pass

    def action_refresh(self) -> None:
        """Manual refresh of events."""
        self._refresh_events()

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
