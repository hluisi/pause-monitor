"""Real-time monitoring dashboard for pause-monitor.

Philosophy: TUI = Real-time window into daemon state. Nothing more.
- Display what the daemon sends via socket — no contrived data
- CLI is for investigation; TUI is for "what's happening now"
- Single-screen dashboard — no page switching for real-time monitoring
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Grid, Horizontal, ScrollableContainer
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Label, Static

from pause_monitor.config import Config
from pause_monitor.socket_client import SocketClient

# Score band thresholds (matches daemon's tracking threshold)
TRACKING_THRESHOLD = 40


def get_tier_name(score: int) -> str:
    """Convert score to tier name."""
    if score >= 80:
        return "CRITICAL"
    elif score >= TRACKING_THRESHOLD:
        return "ELEVATED"
    return "NORMAL"


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return f"{bytes_val}B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.0f}K"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f}M"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f}G"


def format_count(val: int | float | str | None) -> str:
    """Format large counts with k/M suffix.

    Handles int, float, or string input robustly.
    """
    if val is None:
        return "0"
    try:
        num = int(float(val))  # Handle "123.4" strings too
    except (ValueError, TypeError):
        return "?"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1000:
        return f"{num / 1000:.1f}k"
    return str(num)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60)}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins}m"


def extract_time(timestamp_str: str) -> str:
    """Extract HH:MM:SS from various timestamp formats.

    Handles:
    - ISO format: 2026-01-29T15:10:45.123456
    - Space format: 2026-01-29 15:10:45
    - Time only: 15:10:45
    """
    if not timestamp_str:
        return datetime.now().strftime("%H:%M:%S")

    # Handle ISO format with T separator
    if "T" in timestamp_str:
        time_part = timestamp_str.split("T")[-1]
        # Remove microseconds if present
        if "." in time_part:
            time_part = time_part.split(".")[0]
        return time_part[:8]

    # Handle space separator
    if " " in timestamp_str:
        time_part = timestamp_str.split(" ")[-1]
        return time_part[:8]

    # Already just time
    return timestamp_str[:8]


class HeaderBar(Static):
    """Header showing stress gauge, sparkline, and system stats."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 4;
        padding: 0 1;
        border: solid green;
    }

    HeaderBar.elevated {
        border: solid yellow;
    }

    HeaderBar.critical {
        border: solid red;
    }

    HeaderBar.disconnected {
        border: solid $error;
    }

    HeaderBar Horizontal {
        height: 1;
        width: 100%;
    }

    HeaderBar #gauge-left {
        width: auto;
    }

    HeaderBar #gauge-right {
        width: 1fr;
        text-align: right;
    }

    HeaderBar #sparkline {
        width: 1fr;
        height: 1;
        text-align: right;
    }
    """

    # Default buffer size, will be resized to match actual width
    DEFAULT_SPARKLINE_SIZE = 60
    # Unicode block characters for sparkline (8 levels)
    BARS = " ▁▂▃▄▅▆▇█"

    score: reactive[int] = reactive(0)
    connected: reactive[bool] = reactive(False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timestamp = ""
        self._process_count = 0
        self._sample_count = 0
        self._sparkline_data: list[int] = []
        self._sparkline_size = self.DEFAULT_SPARKLINE_SIZE
        self._start_time = time.time()

    def compose(self) -> ComposeResult:
        """Create header layout."""
        yield Horizontal(
            Label("", id="gauge-left"),
            Label("", id="gauge-right"),
        )
        yield Label("", id="sparkline")

    def on_resize(self) -> None:
        """Resize sparkline buffer to fill available width."""
        try:
            label = self.query_one("#sparkline", Label)
            new_width = label.size.width
            if new_width > 0 and new_width != self._sparkline_size:
                self._sparkline_size = new_width
                # Trim data if buffer shrunk
                if len(self._sparkline_data) > new_width:
                    self._sparkline_data = self._sparkline_data[-new_width:]
        except NoMatches:
            pass

    def _render_sparkline(self) -> str:
        """Render sparkline as Unicode block characters."""
        if not self._sparkline_data:
            return ""

        # Scale values 0-100 to bar indices 0-8
        chars = []
        for val in self._sparkline_data:
            bar_idx = int(val / 100 * 8)
            bar_idx = max(0, min(8, bar_idx))
            chars.append(self.BARS[bar_idx])

        return "".join(chars)

    def watch_score(self, score: int) -> None:
        """Update gauge when score changes."""
        self._update_gauge()
        self._update_border_class()

    def watch_connected(self, connected: bool) -> None:
        """Update display when connection state changes."""
        if not connected:
            self.add_class("disconnected")
            self.remove_class("elevated", "critical")
        else:
            self.remove_class("disconnected")
            self._update_border_class()
        self._update_gauge()

    def _update_border_class(self) -> None:
        """Update border color based on score."""
        self.remove_class("elevated", "critical", "disconnected")
        if not self.connected:
            self.add_class("disconnected")
        elif self.score >= 80:
            self.add_class("critical")
        elif self.score >= TRACKING_THRESHOLD:
            self.add_class("elevated")

    def _update_gauge(self) -> None:
        """Update the gauge line display."""
        try:
            gauge_left = self.query_one("#gauge-left", Label)
            gauge_right = self.query_one("#gauge-right", Label)
        except NoMatches:
            return

        if not self.connected:
            gauge_left.update("STRESS ░░░░░░░░░░░░░░░░░░░░ ---/100   DISCONNECTED")
            gauge_right.update("Run: pause-monitor daemon")
            return

        filled = self.score // 5
        bar = "█" * filled + "░" * (20 - filled)
        tier = get_tier_name(self.score)
        uptime = format_duration(time.time() - self._start_time)
        gauge_left.update(
            f"STRESS {bar} {self.score:3d}/100   {tier}   {self._timestamp} ({uptime})"
        )
        gauge_right.update(f"{self._process_count} procs   #{self._sample_count}")

    def update_from_sample(
        self,
        score: int,
        process_count: int,
        sample_count: int,
        timestamp: str,
    ) -> None:
        """Update header from a sample."""
        self._timestamp = timestamp
        self._process_count = process_count
        self._sample_count = sample_count
        self.connected = True

        # Append to sparkline buffer and trim to current width
        self._sparkline_data.append(score)
        if len(self._sparkline_data) > self._sparkline_size:
            self._sparkline_data = self._sparkline_data[-self._sparkline_size :]

        # Update sparkline label with our own rendering
        try:
            sparkline_label = self.query_one("#sparkline", Label)
            sparkline_label.update(self._render_sparkline())
        except NoMatches:
            pass

        # Update score and always refresh gauge
        self.score = score
        self._update_gauge()

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.connected = False


class ProcessTable(Static):
    """Table showing rogue processes with full metrics.

    Uses CSS Grid layout for proper proportional column widths.
    Includes decay: processes stay visible (dimmed) for 10s after leaving rogues.
    """

    DEFAULT_CSS = """
    ProcessTable {
        height: 1fr;
        border: solid $primary;
    }

    ProcessTable.disconnected {
        border: solid $error;
    }

    ProcessTable ScrollableContainer {
        width: 100%;
        height: 100%;
    }

    ProcessTable #process-grid {
        width: 100%;
        height: auto;
        layout: grid;
        grid-size: 9;
        /* Column widths: trend, process(flex), score, cpu, mem, pgin, csw, state, why(flex) */
        grid-columns: 3 1fr 7 6 6 6 8 10 2fr;
    }

    ProcessTable .header {
        text-style: bold;
        background: $surface;
        height: 1;
    }

    ProcessTable .cell {
        height: 1;
    }

    ProcessTable .decayed {
        text-style: dim;
    }

    /* Score-based row colors (5 bands) */
    ProcessTable .critical {
        color: $error;
    }

    ProcessTable .high {
        color: orange;
    }

    ProcessTable .elevated {
        color: $warning;
    }

    ProcessTable .medium {
        color: $text;
    }

    ProcessTable .low {
        color: $success;
    }
    """

    DECAY_SECONDS = 10.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._grid: Grid | None = None
        self._prev_scores: dict[int, int] = {}
        self._cached_rogues: dict[int, dict] = {}
        self._last_seen: dict[int, float] = {}

    def compose(self) -> ComposeResult:
        """Create the process table using CSS Grid."""
        with ScrollableContainer():
            with Grid(id="process-grid"):
                # Header row
                yield Label("", classes="header")
                yield Label("Process", classes="header")
                yield Label("Score", classes="header")
                yield Label("CPU%", classes="header")
                yield Label("Mem", classes="header")
                yield Label("Pgin", classes="header")
                yield Label("CSW", classes="header")
                yield Label("State", classes="header")
                yield Label("Why", classes="header")

    def on_mount(self) -> None:
        """Get grid reference."""
        self._grid = self.query_one("#process-grid", Grid)
        self.set_disconnected()

    def _clear_data_rows(self) -> None:
        """Remove all data rows, keeping header."""
        if not self._grid:
            return
        children = list(self._grid.children)
        for child in children[9:]:
            child.remove()

    def _get_score_class(self, score: int) -> str:
        """Get CSS class based on score band (5 bands)."""
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "elevated"
        elif score >= 20:
            return "medium"
        return "low"

    def _add_row(
        self,
        trend: str,
        command: str,
        score_val: int,
        cpu: str,
        mem: str,
        pgin: str,
        csw: str,
        state: str,
        why: str,
        decayed: bool = False,
    ) -> None:
        """Add a data row to the grid."""
        if not self._grid:
            return

        score_class = self._get_score_class(score_val)
        base_class = f"cell {score_class}"
        if decayed:
            base_class += " decayed"

        self._grid.mount(Label(trend, classes=base_class))
        self._grid.mount(Label(command, classes=base_class))
        self._grid.mount(Label(str(score_val), classes=base_class))
        self._grid.mount(Label(cpu, classes=base_class))
        self._grid.mount(Label(mem, classes=base_class))
        self._grid.mount(Label(pgin, classes=base_class))
        self._grid.mount(Label(csw, classes=base_class))
        self._grid.mount(Label(state, classes=base_class))
        self._grid.mount(Label(why, classes=base_class))

    def update_rogues(self, rogues: list[dict], now: float) -> None:
        """Update with rogue process list.

        Rogues contain ProcessScore data serialized as dicts with MetricValue
        fields (each has current/low/high).
        """
        self.remove_class("disconnected")
        if not self._grid:
            return

        # Update cache with current rogues
        current_pids: set[int] = set()
        for rogue in rogues:
            pid = rogue.get("pid")
            if pid is not None:
                current_pids.add(pid)
                self._cached_rogues[pid] = rogue.copy()
                self._last_seen[pid] = now

        # Build display list: current + decayed
        display_list: list[tuple[dict, bool]] = []

        for rogue in rogues:
            display_list.append((rogue, False))

        for pid, cached in list(self._cached_rogues.items()):
            if pid not in current_pids:
                age = now - self._last_seen.get(pid, 0)
                if age < self.DECAY_SECONDS:
                    display_list.append((cached, True))
                else:
                    del self._cached_rogues[pid]
                    self._last_seen.pop(pid, None)

        # Sort by score (MetricValue dict: {"current": x, "low": y, "high": z})
        display_list.sort(
            key=lambda x: x[0]["score"]["current"],
            reverse=True,
        )

        self._clear_data_rows()

        for rogue, is_decayed in display_list:
            pid = rogue.get("pid", 0)
            score = rogue["score"]["current"]

            prev_score = self._prev_scores.get(pid, score)

            if is_decayed:
                trend = "○"
            elif score > prev_score:
                trend = "▲"
            elif score < prev_score:
                trend = "▽"
            else:
                trend = "●"

            self._prev_scores[pid] = score

            categories = rogue.get("categories", [])
            if isinstance(categories, (list, tuple, set, frozenset)):
                why = ",".join(sorted(categories))
            else:
                why = str(categories)

            # Extract .current from MetricValue dicts
            cpu = rogue["cpu"]["current"]
            mem = rogue["mem"]["current"]
            pageins = rogue["pageins"]["current"]
            csw = rogue["csw"]["current"]
            state = rogue["state"]["current"]

            self._add_row(
                trend,
                str(rogue.get("command", "?")),
                score,  # Pass int for color coding
                f"{cpu:.1f}",
                format_bytes(mem),
                format_count(pageins),
                format_count(csw),
                str(state),
                why,
                decayed=is_decayed,
            )

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.add_class("disconnected")
        self._clear_data_rows()
        self._add_row("", "(not connected)", 0, "---", "---", "---", "---", "---", "")


@dataclass
class DisplayTrackedProcess:
    """A process being tracked in the TUI display.

    Note: This is distinct from tracker.py's TrackedProcess which manages
    database event lifecycle. This class is purely for TUI display state.
    """

    command: str
    entry_time: float
    peak_score: int
    peak_categories: list[str] = field(default_factory=list)
    exit_time: float | None = None
    exit_reason: str = ""

    @property
    def duration(self) -> float:
        """Duration in seconds."""
        end = self.exit_time if self.exit_time else time.time()
        return end - self.entry_time

    @property
    def is_active(self) -> bool:
        """Whether still being tracked."""
        return self.exit_time is None


class TrackedEventsPanel(Static):
    """Panel showing tracked processes - active and historical.

    Deduplicates by command name: shows ONE entry per process (highest peak).
    Active processes shown first, then history sorted by peak score.
    """

    DEFAULT_CSS = """
    TrackedEventsPanel {
        height: 100%;
        border: solid $primary;
        padding: 0 1;
    }

    TrackedEventsPanel DataTable {
        width: 100%;
        height: 1fr;
    }

    TrackedEventsPanel #tracked-title {
        height: 1;
        background: $surface;
    }
    """

    MAX_HISTORY = 15

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None
        # command -> DisplayTrackedProcess for active tracking (by command name, not PID)
        self._active: dict[str, DisplayTrackedProcess] = {}
        # command -> DisplayTrackedProcess for history (one entry per command, highest peak)
        self._history: dict[str, DisplayTrackedProcess] = {}
        # Track PIDs currently above threshold
        self._tracked_pids: set[int] = set()

    def compose(self) -> ComposeResult:
        """Create the panel."""
        yield Static("TRACKED PROCESSES", id="tracked-title")
        yield DataTable(id="tracked-table")

    def on_mount(self) -> None:
        """Set up table."""
        self._table = self.query_one("#tracked-table", DataTable)
        self._table.add_column("Time", width=8)
        self._table.add_column("Process")
        self._table.add_column("Peak", width=4)
        self._table.add_column("Dur", width=6)
        self._table.add_column("Why")
        self._table.add_column("Status", width=8)
        self._table.show_header = True
        self._table.cursor_type = "none"

    def _extract_score(self, rogue: dict) -> int:
        """Extract current score value from rogue dict.

        Score is always a MetricValue dict: {"current": x, "low": y, "high": z}
        """
        return rogue["score"]["current"]

    def update_tracking(self, rogues: list[dict], now: float) -> None:
        """Update tracking based on current rogues.

        Tracks by COMMAND NAME (not PID) to deduplicate.
        """
        # Build current state: command -> best rogue for processes above threshold
        current_above: dict[str, dict] = {}
        current_pids: set[int] = set()

        for r in rogues:
            score = self._extract_score(r)
            if score >= TRACKING_THRESHOLD:
                cmd = r.get("command", "?")
                current_pids.add(r.get("pid", 0))
                # Keep the highest scoring entry per command
                if cmd in current_above:
                    existing_score = self._extract_score(current_above[cmd])
                else:
                    existing_score = 0
                if cmd not in current_above or score > existing_score:
                    current_above[cmd] = r

        # Check for new/updated active entries
        for cmd, rogue in current_above.items():
            score = self._extract_score(rogue)
            categories = rogue.get("categories", [])
            if isinstance(categories, (set, frozenset)):
                categories = list(categories)

            if cmd not in self._active:
                # New tracking entry
                self._active[cmd] = DisplayTrackedProcess(
                    command=cmd,
                    entry_time=now,
                    peak_score=score,
                    peak_categories=categories,
                )
            else:
                # Update peak if higher
                tracked = self._active[cmd]
                if score > tracked.peak_score:
                    tracked.peak_score = score
                    tracked.peak_categories = categories

        # Check for exits (commands that were active but no longer above threshold)
        for cmd in list(self._active.keys()):
            if cmd not in current_above:
                tracked = self._active.pop(cmd)
                tracked.exit_time = now
                tracked.exit_reason = "dropped"

                # Add to history, keeping only highest peak per command
                existing = self._history.get(cmd)
                if existing is None or tracked.peak_score > existing.peak_score:
                    self._history[cmd] = tracked

        # Limit history size (keep highest peaks)
        if len(self._history) > self.MAX_HISTORY:
            sorted_history = sorted(
                self._history.items(),
                key=lambda x: x[1].peak_score,
                reverse=True,
            )
            self._history = dict(sorted_history[: self.MAX_HISTORY])

        self._tracked_pids = current_pids
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Refresh the table display."""
        if not self._table:
            return

        self._table.clear()

        # Show active tracking first (sorted by peak score desc)
        active_sorted = sorted(
            self._active.values(),
            key=lambda t: t.peak_score,
            reverse=True,
        )
        for tracked in active_sorted:
            time_str = datetime.fromtimestamp(tracked.entry_time).strftime("%H:%M:%S")
            why = ",".join(sorted(tracked.peak_categories)) if tracked.peak_categories else ""
            duration = format_duration(tracked.duration)

            self._table.add_row(
                time_str,
                tracked.command[:15],
                str(tracked.peak_score),
                duration,
                why,
                "[green]active[/]",
            )

        # Show history (sorted by peak score desc)
        history_sorted = sorted(
            self._history.values(),
            key=lambda t: t.peak_score,
            reverse=True,
        )
        for tracked in history_sorted:
            time_str = datetime.fromtimestamp(tracked.entry_time).strftime("%H:%M:%S")
            why = ",".join(sorted(tracked.peak_categories)) if tracked.peak_categories else ""
            duration = format_duration(tracked.duration)

            self._table.add_row(
                time_str,
                tracked.command[:15],
                str(tracked.peak_score),
                duration,
                why,
                "[dim]ended[/]",
            )


class ActivityLog(Static):
    """Activity log showing tier transitions."""

    DEFAULT_CSS = """
    ActivityLog {
        height: 100%;
        border: solid $primary;
        padding: 0 1;
    }

    ActivityLog #activity-title {
        height: 1;
        background: $surface;
    }

    ActivityLog #log-container {
        height: 1fr;
    }

    ActivityLog .entry {
        height: 1;
    }

    ActivityLog .entry-high {
        color: red;
    }

    ActivityLog .entry-elevated {
        color: yellow;
    }

    ActivityLog .entry-normal {
        color: green;
    }
    """

    MAX_ENTRIES = 15

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries: list[tuple[str, str, str]] = []
        self._prev_tier = "NORMAL"

    def compose(self) -> ComposeResult:
        """Create log container."""
        yield Static("SYSTEM ACTIVITY", id="activity-title")
        yield Container(id="log-container")

    def on_mount(self) -> None:
        """Initial state."""
        self._add_entry("Waiting for connection...", "normal")

    def _add_entry(self, message: str, level: str = "normal") -> None:
        """Add a log entry."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._entries.append((timestamp, message, level))
        if len(self._entries) > self.MAX_ENTRIES:
            self._entries = self._entries[-self.MAX_ENTRIES :]
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Refresh the log display."""
        try:
            container = self.query_one("#log-container", Container)
        except NoMatches:
            return

        container.remove_children()
        for timestamp, message, level in self._entries:
            label = Label(f"{timestamp}  {message}", classes=f"entry entry-{level}")
            container.mount(label)

    def check_transitions(self, score: int) -> None:
        """Check for tier transitions."""
        current_tier = get_tier_name(score)
        if current_tier != self._prev_tier:
            if current_tier == "CRITICAL":
                self._add_entry(f"● System → CRITICAL (score: {score})", "high")
            elif current_tier == "ELEVATED":
                self._add_entry(f"● System → ELEVATED (score: {score})", "elevated")
            else:
                self._add_entry(f"○ System → NORMAL (score: {score})", "normal")
            self._prev_tier = current_tier

    def connected(self) -> None:
        """Called when daemon connects."""
        self._entries.clear()
        self._add_entry("Connected to daemon", "normal")


class PauseMonitorApp(App):
    """Real-time monitoring dashboard for pause-monitor."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 4;
    }

    #main-area {
        height: 1fr;
    }

    #bottom-panels {
        height: 12;
    }

    #bottom-panels > * {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    # Reconnect backoff settings
    _RECONNECT_INITIAL_DELAY = 1.0  # Start with 1 second
    _RECONNECT_MAX_DELAY = 30.0  # Cap at 30 seconds
    _RECONNECT_MULTIPLIER = 2.0  # Double each time

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()
        self._socket_client: SocketClient | None = None
        self._use_socket: bool = False
        self._socket_read_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._stopping: bool = False

    def compose(self) -> ComposeResult:
        """Create the TUI layout."""
        yield HeaderBar(id="header")
        yield ProcessTable(id="main-area")
        yield Horizontal(
            ActivityLog(id="activity"),
            TrackedEventsPanel(id="tracked"),
            id="bottom-panels",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize on startup."""
        self.title = "pause-monitor"
        self.sub_title = "Real-time Dashboard"
        asyncio.create_task(self._initial_connect())

    def on_unmount(self) -> None:
        """Cleanup on shutdown."""
        self._stopping = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._socket_read_task and not self._socket_read_task.done():
            self._socket_read_task.cancel()
        if self._socket_client:
            asyncio.create_task(self._socket_client.disconnect())

    async def _try_socket_connect(self, show_notification: bool = True) -> bool:
        """Try to connect to daemon via socket.

        Args:
            show_notification: Whether to show notification on failure

        Returns:
            True if connected successfully, False otherwise
        """
        if self._socket_client is None:
            self._socket_client = SocketClient(socket_path=self.config.socket_path)

        try:
            await self._socket_client.connect()
            self._use_socket = True
            self.sub_title = "Real-time Dashboard (live)"
            # Log connection to daemon's log file
            try:
                await self._socket_client.send_message(
                    {
                        "type": "log",
                        "level": "info",
                        "event": "tui_connected",
                        "path": str(self.config.socket_path),
                    }
                )
            except Exception:
                pass  # Connection logging is best-effort
            try:
                self.query_one("#activity", ActivityLog).connected()
            except Exception:
                pass
            self._socket_read_task = asyncio.create_task(self._read_socket_loop())
            return True
        except FileNotFoundError:
            self._set_disconnected("socket not found", start_reconnect=False)
            if show_notification:
                self.notify(
                    "Daemon not running. Start with: pause-monitor daemon",
                    severity="warning",
                )
            return False
        except PermissionError as e:
            self._set_disconnected(f"permission denied: {e}", start_reconnect=False)
            if show_notification:
                self.notify(f"Socket permission denied: {e}", severity="error")
            return False
        except Exception as e:
            self._set_disconnected(f"{type(e).__name__}: {e}", start_reconnect=False)
            if show_notification:
                self.notify(f"Socket connection failed: {e}", severity="error")
            return False

    async def _initial_connect(self) -> None:
        """Initial connection attempt with notification, then start reconnect if needed."""
        connected = await self._try_socket_connect(show_notification=True)
        if not connected:
            # Start reconnect loop for initial connection failures
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect with exponential backoff.

        Backoff schedule: 1s → 2s → 4s → 8s → 16s → 30s (capped)
        """
        delay = self._RECONNECT_INITIAL_DELAY

        while not self._stopping:
            self.sub_title = f"Real-time Dashboard (reconnecting in {delay:.0f}s...)"

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if self._stopping:
                return

            # Disconnect existing client if any
            if self._socket_client:
                try:
                    await self._socket_client.disconnect()
                except Exception:
                    pass
                self._socket_client = None

            # Try to connect
            self.sub_title = "Real-time Dashboard (reconnecting...)"
            connected = await self._try_socket_connect(show_notification=False)

            if connected:
                self.notify("Reconnected to daemon", severity="information")
                return  # Success! Exit reconnect loop

            # Increase delay with exponential backoff
            delay = min(delay * self._RECONNECT_MULTIPLIER, self._RECONNECT_MAX_DELAY)

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
            pass
        except Exception as e:
            self._set_disconnected(f"{type(e).__name__}: {e}")
            self.notify(f"Socket error: {e}", severity="error")

    def _set_disconnected(self, error: str | None = None, start_reconnect: bool = True) -> None:
        """Update UI to show disconnected state and optionally start reconnection.

        Args:
            error: Optional error message (unused, kept for API compatibility)
            start_reconnect: Whether to start auto-reconnect loop (default True)
        """
        self._use_socket = False
        self.sub_title = "Real-time Dashboard (disconnected)"
        try:
            self.query_one("#header", HeaderBar).set_disconnected()
        except Exception:
            pass
        try:
            self.query_one("#main-area", ProcessTable).set_disconnected()
        except Exception:
            pass

        # Start reconnect loop if not already running and not shutting down
        if start_reconnect and not self._stopping:
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _handle_socket_data(self, data: dict[str, Any]) -> None:
        """Handle messages from daemon socket."""
        msg_type = data.get("type", "sample")

        # Ignore initial_state — TUI builds sparkline from streaming samples
        if msg_type == "initial_state":
            return

        # Regular sample message
        now = time.time()

        max_score = data.get("max_score", 0)
        sample_count = data.get("sample_count", 0)
        rogues = data.get("rogues", [])
        process_count = data.get("process_count", 0)
        raw_timestamp = data.get("timestamp", "")
        timestamp_str = extract_time(raw_timestamp)

        try:
            self.query_one("#header", HeaderBar).update_from_sample(
                max_score, process_count, sample_count, timestamp_str
            )
        except NoMatches:
            pass

        # Update process table
        try:
            self.query_one("#main-area", ProcessTable).update_rogues(rogues, now)
        except NoMatches:
            pass

        # Check tier transitions
        try:
            self.query_one("#activity", ActivityLog).check_transitions(max_score)
        except NoMatches:
            pass

        # Update tracked events panel
        try:
            self.query_one("#tracked", TrackedEventsPanel).update_tracking(rogues, now)
        except NoMatches:
            pass


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = PauseMonitorApp(config)
    app.run()
