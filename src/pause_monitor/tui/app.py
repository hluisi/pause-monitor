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

from pause_monitor.boottime import get_boot_time
from pause_monitor.config import Config
from pause_monitor.formatting import format_duration, format_duration_verbose
from pause_monitor.socket_client import SocketClient
from pause_monitor.storage import get_process_event_detail, get_process_events

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

    def __init__(
        self, elevated_threshold: int = 50, critical_threshold: int = 75, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._score = 0
        self._connected = False
        self._elevated_threshold = elevated_threshold
        self._critical_threshold = critical_threshold

    def on_mount(self) -> None:
        """Show initial disconnected state."""
        self.set_disconnected()

    def update_score(self, score: int) -> None:
        """Update gauge with max process score."""
        self._score = score
        self._connected = True
        self.remove_class("disconnected")
        self.update(f"Score: {score:3d}/100 {'█' * (score // 5)}{'░' * (20 - score // 5)}")

        # Update styling based on score thresholds
        self.remove_class("elevated", "critical")
        if score >= self._critical_threshold:
            self.add_class("critical")
        elif score >= self._elevated_threshold:
            self.add_class("elevated")

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self._connected = False
        self.remove_class("elevated", "critical")
        self.add_class("disconnected")
        self.update("Score: ---/100  (not connected)")


class SampleInfoPanel(Static):
    """Panel showing current sample info (max score, process count, etc.)."""

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
        self._max_score = 0
        self._process_count = 0
        self._sample_count = 0

    def on_mount(self) -> None:
        """Show initial disconnected state."""
        self.set_disconnected()

    def update_info(self, max_score: int, process_count: int, sample_count: int) -> None:
        """Update displayed sample info."""
        self._max_score = max_score
        self._process_count = process_count
        self._sample_count = sample_count
        self.remove_class("disconnected")

        lines = [
            f"Max Score: {max_score}",
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

            for p in rogues:
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
    """Table showing recent process events."""

    DEFAULT_CSS = """
    EventsTable {
        height: 100%;
        border: solid $primary;
    }
    """

    def on_mount(self) -> None:
        """Set up table columns for process events."""
        self.add_column("Command", width=15)
        self.add_column("Band", width=10)
        self.add_column("Duration", width=10)
        self.add_column("Score", width=6)


class EventDetailScreen(Screen):
    """Screen showing details of a single process event."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
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

    EventDetailScreen .snapshot {
        margin-left: 2;
        color: $text-muted;
        height: auto;
        max-height: 15;
    }
    """

    def __init__(self, event_id: int, conn: sqlite3.Connection):
        super().__init__()
        self.event_id = event_id
        self._conn = conn
        self._event: dict | None = None

    def compose(self) -> ComposeResult:
        """Build the detail view layout."""
        import json
        from datetime import datetime

        self._event = get_process_event_detail(self._conn, self.event_id)
        if not self._event:
            yield Vertical(
                Label("Event not found", classes="title"),
            )
            return

        event = self._event
        entry_time = datetime.fromtimestamp(event["entry_time"])
        exit_time = datetime.fromtimestamp(event["exit_time"]) if event["exit_time"] else None

        # Calculate duration
        duration_str = format_duration_verbose(event["entry_time"], event["exit_time"])
        if exit_time:
            end_time_str = exit_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            end_time_str = "(ongoing)"

        # Parse peak snapshot
        snapshot_lines: list[str] = []
        if event["peak_snapshot"]:
            try:
                snapshot = json.loads(event["peak_snapshot"])
                for key, val in snapshot.items():
                    snapshot_lines.append(f"  {key}: {val}")
            except json.JSONDecodeError:
                snapshot_lines.append(f"  {event['peak_snapshot']}")

        yield Vertical(
            Label(f"Process Event #{event['id']}", classes="title"),
            Label(f"Command: {event['command']}"),
            Label(f"PID: {event['pid']}"),
            Label(f"Entry: {entry_time.strftime('%Y-%m-%d %H:%M:%S')} ({event['entry_band']})"),
            Label(f"Exit: {end_time_str}"),
            Label(f"Duration: {duration_str}"),
            Label(f"Peak Band: {event['peak_band']}"),
            Label(f"Peak Score: {event['peak_score']}"),
            Label("Peak Snapshot:", classes="section"),
            VerticalScroll(
                *(
                    [Label(line) for line in snapshot_lines]
                    if snapshot_lines
                    else [Label("  (none)")]
                ),
                classes="snapshot",
            ),
        )


class EventsScreen(Screen):
    """Full-screen process events list with selection."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("enter", "select_event", "View"),
        Binding("o", "filter_open", "Open Only"),
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
        self._open_only: bool = False
        self._events: list[dict] = []

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
        table.add_column("Command", width=20)
        table.add_column("PID", width=8)
        table.add_column("Peak Band", width=10)
        table.add_column("Duration", width=10)
        table.add_column("Score", width=6)
        self._refresh_events()

    def _refresh_events(self) -> None:
        """Refresh the events list from database."""
        import time

        boot_time = get_boot_time()

        if self._open_only:
            # Use get_open_events for open-only filter
            from pause_monitor.storage import get_open_events

            self._events = get_open_events(self._conn, boot_time)
        else:
            # Get all events from current boot
            self._events = get_process_events(self._conn, boot_time=boot_time, limit=100)

        table = self.query_one("#events-list", DataTable)
        table.clear()

        now = time.time()
        for event in self._events:
            duration_str = format_duration(event["entry_time"], event.get("exit_time"), now=now)

            table.add_row(
                str(event["id"]),
                event["command"][:20],
                str(event["pid"]),
                event["peak_band"],
                duration_str,
                str(event["peak_score"]),
                key=str(event["id"]),
            )

        # Update filter label
        filter_label = self.query_one("#filter-label", Static)
        if self._open_only:
            filter_label.update("Filter: Open events only")
        else:
            filter_label.update("Filter: All events (current boot)")

    def _get_selected_event_id(self) -> int | None:
        """Get the ID of the currently selected event."""
        table = self.query_one("#events-list", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._events):
            return self._events[table.cursor_row]["id"]
        return None

    def action_select_event(self) -> None:
        """View selected event details."""
        event_id = self._get_selected_event_id()
        if event_id:
            self.app.push_screen(EventDetailScreen(event_id, self._conn))

    def action_filter_open(self) -> None:
        """Filter to show only open events."""
        self._open_only = True
        self._refresh_events()

    def action_filter_all(self) -> None:
        """Show all events from current boot."""
        self._open_only = False
        self._refresh_events()


class HistoryScreen(Screen):
    """Full-screen historical process events across all boots."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("1", "filter_24h", "24h"),
        Binding("7", "filter_7d", "7 days"),
        Binding("3", "filter_30d", "30 days"),
        Binding("a", "filter_all", "All time"),
    ]

    DEFAULT_CSS = """
    HistoryScreen {
        layout: vertical;
    }

    HistoryScreen > Container {
        height: 100%;
    }

    HistoryScreen DataTable {
        height: 1fr;
    }

    HistoryScreen .summary-bar {
        height: auto;
        min-height: 3;
        background: $surface;
        padding: 1;
        border-bottom: solid $primary;
    }

    HistoryScreen .filter-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    # Time ranges in seconds
    TIME_RANGES = {
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
        "30d": 30 * 24 * 3600,
        "all": None,
    }

    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self._conn = conn
        self._time_range: str = "24h"
        self._events: list[dict] = []

    def compose(self) -> ComposeResult:
        """Build the history layout."""
        yield Header()
        yield Container(
            Static("Loading...", id="summary", classes="summary-bar"),
            Static("Filter: Last 24 hours", id="filter-label", classes="filter-bar"),
            DataTable(id="history-list"),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set up the history table."""
        table = self.query_one("#history-list", DataTable)
        table.cursor_type = "row"
        table.add_column("ID", width=5)
        table.add_column("Command", width=20)
        table.add_column("PID", width=8)
        table.add_column("Peak", width=10)
        table.add_column("Duration", width=10)
        table.add_column("Score", width=6)
        table.add_column("When", width=18)
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Refresh the history list from database."""
        import time

        # Calculate time cutoff
        cutoff_seconds = self.TIME_RANGES[self._time_range]
        time_cutoff = time.time() - cutoff_seconds if cutoff_seconds else None

        self._events = get_process_events(
            self._conn, boot_time=None, time_cutoff=time_cutoff, limit=500
        )

        table = self.query_one("#history-list", DataTable)
        table.clear()

        now = time.time()
        for event in self._events:
            duration_str = format_duration(event["entry_time"], event.get("exit_time"), now=now)

            # Format when the event started
            from datetime import datetime

            entry_dt = datetime.fromtimestamp(event["entry_time"])
            when_str = entry_dt.strftime("%m-%d %H:%M")

            table.add_row(
                str(event["id"]),
                event["command"][:20],
                str(event["pid"]),
                event["peak_band"],
                duration_str,
                str(event["peak_score"]),
                when_str,
                key=str(event["id"]),
            )

        # Update summary
        self._update_summary()

        # Update filter label
        filter_labels = {
            "24h": "Last 24 hours",
            "7d": "Last 7 days",
            "30d": "Last 30 days",
            "all": "All time",
        }
        filter_label = self.query_one("#filter-label", Static)
        filter_label.update(f"Filter: {filter_labels[self._time_range]}")

    def _update_summary(self) -> None:
        """Update the summary statistics."""
        summary = self.query_one("#summary", Static)

        if not self._events:
            summary.update("No events found")
            return

        # Calculate stats
        peak_scores = [e["peak_score"] for e in self._events]
        avg_score = sum(peak_scores) / len(peak_scores)

        # Band breakdown
        band_counts: dict[str, int] = {}
        for event in self._events:
            band = event["peak_band"]
            band_counts[band] = band_counts.get(band, 0) + 1

        band_str = "  ".join(f"{b}: {c}" for b, c in sorted(band_counts.items()))

        # Total tracked time
        total_duration = 0.0
        for event in self._events:
            if event["exit_time"]:
                total_duration += event["exit_time"] - event["entry_time"]

        duration_mins = total_duration / 60

        lines = [
            f"Events: {len(self._events)}  |  Avg: {avg_score:.0f}  |  Time: {duration_mins:.1f}m",
            f"Bands: {band_str}" if band_str else "",
        ]
        summary.update("\n".join(line for line in lines if line))

    def action_filter_24h(self) -> None:
        """Filter to last 24 hours."""
        self._time_range = "24h"
        self._refresh_history()

    def action_filter_7d(self) -> None:
        """Filter to last 7 days."""
        self._time_range = "7d"
        self._refresh_history()

    def action_filter_30d(self) -> None:
        """Filter to last 30 days."""
        self._time_range = "30d"
        self._refresh_history()

    def action_filter_all(self) -> None:
        """Show all historical events."""
        self._time_range = "all"
        self._refresh_history()


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
            sample_count = data.get("sample_count", 0)
            # For initial_state, get rogues from the last sample if available
            samples = data.get("samples", [])
            rogues = samples[-1].get("rogues", []) if samples else []
            process_count = samples[-1].get("process_count", 0) if samples else 0
        else:  # sample message
            max_score = data.get("max_score", 0)
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
            sample_info.update_info(max_score, process_count, sample_count)
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

        yield StressGauge(
            elevated_threshold=self.config.bands.tracking_threshold,
            critical_threshold=self.config.bands.forensics_threshold,
            id="stress-gauge",
        )
        yield SampleInfoPanel(id="sample-info")
        yield EventsTable(id="events")
        yield ProcessesPanel(id="processes")

        yield Footer()

    def _refresh_events(self) -> None:
        """Refresh events table from database."""
        import time

        if not self._conn:
            return

        # Get recent process events from current boot
        boot_time = get_boot_time()
        events = get_process_events(self._conn, boot_time=boot_time, limit=10)

        try:
            events_table = self.query_one("#events", EventsTable)
            events_table.clear()
            now = time.time()
            for event in events:
                duration_str = format_duration(event["entry_time"], event.get("exit_time"), now=now)

                events_table.add_row(
                    event["command"][:15],
                    event["peak_band"],
                    duration_str,
                    str(event["peak_score"]),
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
        if not self._conn:
            self.notify("Database not connected", severity="error")
            return
        self.push_screen(HistoryScreen(self._conn))


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = PauseMonitorApp(config)
    app.run()
