"""Real-time monitoring dashboard for rogue-hunter.

Philosophy: TUI = Real-time window into daemon state. Nothing more.
- Display what the daemon sends via socket — no contrived data
- CLI is for investigation; TUI is for "what's happening now"
- Single-screen dashboard — no page switching for real-time monitoring
"""

import asyncio
import time
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Label, Static

from rogue_hunter.config import Config
from rogue_hunter.socket_client import SocketClient
from rogue_hunter.storage import (
    get_connection,
    get_forensic_captures,
    get_process_events,
)
from rogue_hunter.tui.sparkline import (
    GradientColor,
    Sparkline,
    SparklineDirection,
    SparklineOrientation,
)


def get_tier_name(score: int, elevated: int, critical: int) -> str:
    """Convert score to tier name using config thresholds."""
    if score >= critical:
        return "CRITICAL"
    elif score >= elevated:
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


def format_rate(bytes_per_sec: float) -> str:
    """Format bytes/sec as human-readable rate."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f}B"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.0f}K"
    elif bytes_per_sec < 1024 * 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f}M"
    else:
        return f"{bytes_per_sec / (1024 * 1024 * 1024):.1f}G"


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


def format_share(value: float) -> str:
    """Format a resource share value with 2 decimal precision.

    Args:
        value: The share value (multiple of fair share, e.g., 10.5 = 10.5× fair share).

    Returns:
        Formatted string like "12.34x".
    """
    return f"{value:.2f}x"


def format_bytes_precise(bytes_val: int | float) -> str:
    """Format bytes as human-readable string with 2 decimal precision."""
    val = float(bytes_val)
    if val < 1024:
        return f"{val:.2f}B"
    elif val < 1024 * 1024:
        return f"{val / 1024:.2f}K"
    elif val < 1024 * 1024 * 1024:
        return f"{val / (1024 * 1024):.2f}M"
    else:
        return f"{val / (1024 * 1024 * 1024):.2f}G"


def format_cpu_column(cpu: float) -> str:
    """Format CPU column: raw percentage, right-justified.

    Example: "  45.23%"
    """
    return f"{cpu:>7.2f}%"


def format_gpu_column(gpu_rate: float) -> str:
    """Format GPU column: ms/s, right-justified.

    Example: "   2.15ms"
    """
    return f"{gpu_rate:>7.2f}ms"


def format_mem_column(mem: int) -> str:
    """Format memory column: human-readable bytes, right-justified.

    Example: "  1.23G"
    """
    return format_bytes_precise(mem).rjust(7)


def format_disk_column(disk_rate: float) -> str:
    """Format disk column: human-readable rate, right-justified.

    Example: " 15.67M/s"
    """
    return f"{format_bytes_precise(disk_rate)}/s".rjust(9)


def format_wake_column(wake_rate: float) -> str:
    """Format wakeups column: rate/s, right-justified.

    Example: "   50.00/s"
    """
    return f"{wake_rate:>7.2f}/s"


def format_dominant_info(dominant_resource: str, disproportionality: float) -> str:
    """Format dominant resource info for display, right-justified.

    Args:
        dominant_resource: The resource type (cpu, gpu, memory, disk, wakeups).
        disproportionality: The disproportionality multiplier.

    Returns:
        Formatted string like "CPU  10.50x" right-justified to 12 chars.
    """
    # Labels for resources (all 4 chars for alignment)
    resource_labels = {
        "cpu": "CPU",
        "gpu": "GPU",
        "memory": "MEM",
        "disk": "DISK",
        "wakeups": "WAKE",
    }
    label = resource_labels.get(dominant_resource, dominant_resource.upper()[:4])
    # Format: "LABEL" (4 chars) + " " + value (7 chars with 2 decimals) + "x"
    return f"{label:>4} {disproportionality:>7.2f}x"


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
        height: 6;
        padding: 0 1;
        border: solid green;
        border-title-align: left;
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

    HeaderBar Sparkline {
        width: 1fr;
        height: 3;
    }
    """

    score: reactive[int] = reactive(0)
    connected: reactive[bool] = reactive(False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timestamp = ""
        self._process_count = 0
        self._sample_count = 0
        self._start_time = time.time()
        self._gradient: GradientColor | None = None

    def compose(self) -> ComposeResult:
        """Create header layout."""
        yield Horizontal(
            Label("", id="gauge-left"),
            Label("", id="gauge-right"),
        )
        # Sparkline is created in on_mount where we have access to config

    def on_mount(self) -> None:
        """Set border title and create sparkline with config values."""
        self.border_title = "STRESS"

        # Create sparkline with config values
        sp_config = self.app.config.tui.sparkline

        # Map config strings to enums
        orientation_map = {
            "normal": SparklineOrientation.NORMAL,
            "inverted": SparklineOrientation.INVERTED,
            "mirrored": SparklineOrientation.MIRRORED,
        }
        orientation = orientation_map.get(sp_config.orientation, SparklineOrientation.NORMAL)

        direction_map = {
            "rtl": SparklineDirection.RTL,
            "ltr": SparklineDirection.LTR,
        }
        direction = direction_map.get(sp_config.direction, SparklineDirection.RTL)

        sparkline = Sparkline(
            height=sp_config.height,
            min_value=sp_config.min_value,
            max_value=sp_config.max_value,
            orientation=orientation,
            direction=direction,
            color_func=self._get_sparkline_color,
            id="sparkline",
        )
        self.mount(sparkline)

    def _get_sparkline_color(self, value: float) -> str:
        """Map score to color using smooth gradient between band colors.

        Args:
            value: The stress score (0-100).

        Returns:
            Hex color string interpolated between band colors.
        """
        # Lazy initialization of gradient (needs self.app which isn't available in __init__)
        if self._gradient is None:
            bands = self.app.config.bands
            colors = self.app.config.tui.colors.bands
            self._gradient = GradientColor(
                [
                    (0, colors.low),
                    (bands.medium, colors.medium),
                    (bands.elevated, colors.elevated),
                    (bands.high, colors.high),
                    (bands.critical, colors.critical),
                ]
            )
        return self._gradient(value)

    def watch_score(self, score: int) -> None:
        """Update gauge when score changes."""
        self._update_gauge()
        self._update_border_color()

    def watch_connected(self, connected: bool) -> None:
        """Update display when connection state changes."""
        self._update_border_color()
        self._update_gauge()

    def _update_border_color(self) -> None:
        """Update border color based on score using config colors."""
        bands = self.app.config.bands
        borders = self.app.config.tui.colors.borders

        if not self.connected:
            color = borders.disconnected
        elif self.score >= bands.critical:
            color = borders.critical
        elif self.score >= bands.elevated:
            color = borders.elevated
        else:
            color = borders.normal

        self.styles.border = ("solid", color)

    def _render_stress_bar(self, score: int, width: int = 30) -> Text:
        """Render a colored stress bar with gradient and overflow indicator.

        The bar scales to the critical threshold (not 100). Scores above critical
        show an overflow indicator. Uses partial block characters for smooth fill
        and gradient coloring matching the sparkline.

        Args:
            score: Current stress score.
            width: Bar width in characters.

        Returns:
            Rich Text object with styled bar.
        """
        bands = self.app.config.bands
        critical = bands.critical

        # Partial block characters for sub-character precision (8 levels)
        # Index 0 = empty, 1-7 = partial fills, 8 = full
        blocks = " ▏▎▍▌▋▊▉█"

        # Calculate fill level (0.0 to 1.0+, can exceed 1.0 for overflow)
        fill_ratio = score / critical if critical > 0 else 0
        # Clamp for bar rendering (overflow shown separately)
        clamped_ratio = min(1.0, fill_ratio)

        # Total sub-character units
        total_units = width * 8
        filled_units = int(clamped_ratio * total_units)

        result = Text()

        # Ensure gradient is initialized
        if self._gradient is None:
            colors = self.app.config.tui.colors.bands
            self._gradient = GradientColor(
                [
                    (0, colors.low),
                    (bands.medium, colors.medium),
                    (bands.elevated, colors.elevated),
                    (bands.high, colors.high),
                    (bands.critical, colors.critical),
                ]
            )

        # Render each character position
        for i in range(width):
            char_start_unit = i * 8
            char_end_unit = (i + 1) * 8

            if filled_units >= char_end_unit:
                # Fully filled character
                char = blocks[8]
            elif filled_units <= char_start_unit:
                # Empty character
                char = "░"
            else:
                # Partially filled
                partial_units = filled_units - char_start_unit
                char = blocks[partial_units]

            # Calculate what score this position represents (for coloring)
            position_score = (i / width) * critical
            color = self._gradient(position_score)

            if filled_units > char_start_unit:
                result.append(char, style=color)
            else:
                result.append(char, style="dim")

        # Add overflow indicator when score exceeds critical
        if fill_ratio > 1.0:
            result.append(">>>", style="bold red")

        return result

    def _update_gauge(self) -> None:
        """Update the gauge line display."""
        try:
            gauge_left = self.query_one("#gauge-left", Label)
            gauge_right = self.query_one("#gauge-right", Label)
        except NoMatches:
            return

        bands = self.app.config.bands

        if not self.connected:
            empty_bar = Text("░" * 30, style="dim")
            gauge_text = Text("STRESS ")
            gauge_text.append_text(empty_bar)
            gauge_text.append(f" ---/{bands.critical}   DISCONNECTED")
            gauge_left.update(gauge_text)
            gauge_right.update("Run: rogue-hunter daemon")
            return

        tier = get_tier_name(self.score, bands.elevated, bands.critical)
        uptime = format_duration(time.time() - self._start_time)

        # Build the gauge line with colored bar
        gauge_text = Text("STRESS ")
        gauge_text.append_text(self._render_stress_bar(self.score))
        gauge_text.append(
            f" {self.score:3d}/{bands.critical}   {tier}   {self._timestamp} ({uptime})"
        )

        gauge_left.update(gauge_text)
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

        # Append to sparkline - it handles buffer management internally
        try:
            self.query_one("#sparkline", Sparkline).append(score)
        except NoMatches:
            pass

        # Update score and always refresh gauge
        self.score = score
        self._update_gauge()

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.connected = False


class ProcessTable(Static):
    """Table showing rogue processes with resource-based scoring.

    Uses DataTable with Rich Text objects for row-level styling.
    Shows dominant resource and disproportionality for each process.
    Displays exactly what's in the current sample — no decay logic.
    """

    DEFAULT_CSS = """
    ProcessTable {
        height: 1fr;
        border: solid $primary;
        border-title-align: left;
    }

    ProcessTable.disconnected {
        border: solid $error;
    }

    ProcessTable DataTable {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None
        self._prev_scores: dict[int, int] = {}

    def compose(self) -> ComposeResult:
        """Create the process table using DataTable."""
        yield DataTable(id="process-table", zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        """Set up table columns."""
        self.border_title = "TOP PROCESSES"
        self._table = self.query_one("#process-table", DataTable)
        # Centered headers for alignment
        self._table.add_columns(
            "",
            Text("PID", justify="center"),
            Text("Process", justify="center"),
            Text("Score", justify="center"),
            Text("CPU", justify="center"),
            Text("GPU", justify="center"),
            Text("MEM", justify="center"),
            Text("DISK", justify="center"),
            Text("WAKE", justify="center"),
            Text("State", justify="center"),
            Text("Dominant", justify="center"),
        )
        self.set_disconnected()

    def _get_band_style(self, score: int) -> str:
        """Map score to Rich style string using config colors."""
        bands = self.app.config.bands
        colors = self.app.config.tui.colors.bands

        if score >= bands.critical:
            style = colors.critical
        elif score >= bands.high:
            style = colors.high
        elif score >= bands.elevated:
            style = colors.elevated
        elif score >= bands.medium:
            style = colors.medium
        else:
            style = colors.low

        # Add bold for high-severity bands
        if score >= bands.elevated and style:
            style = f"bold {style}"

        return style

    def _get_trend_style(self, trend: str) -> str:
        """Get style for trend indicator based on config colors."""
        trends = self.app.config.tui.colors.trends
        trend_colors = {
            "▲": trends.worsening,
            "▽": trends.improving,
            "●": trends.stable,
        }
        return trend_colors.get(trend, "")

    def _get_state_style(self, state: str) -> str:
        """Get style for process state based on config colors."""
        state_colors = self.app.config.tui.colors.process_state
        state_map = {
            "running": state_colors.running,
            "sleeping": state_colors.sleeping,
            "idle": state_colors.idle,
            "stopped": state_colors.stopped,
            "zombie": state_colors.zombie,
            "stuck": state_colors.stuck,
            "unknown": state_colors.unknown,
        }
        return state_map.get(state, state_colors.unknown)

    def _make_row(
        self,
        trend: str,
        pid: str,
        command: str,
        score_val: int,
        cpu: float,
        gpu_rate: float,
        mem: int,
        disk_rate: float,
        wake_rate: float,
        state: str,
        dominant_resource: str,
        disproportionality: float,
    ) -> list[Text]:
        """Build styled row cells using Rich Text objects.

        Color scheme:
        - Trend: Own colors based on direction
        - PID: Muted color (not competing with process name)
        - Process name: Band color based on score severity
        - Score: Bold band color
        - Resource columns: Each resource has its own Dracula theme color
        - State: Own colors based on process state severity
        - Dominant: Colored by dominant resource type
        """
        colors = self.app.config.tui.colors

        # Get styles for each element type
        band_style = self._get_band_style(score_val)
        trend_style = self._get_trend_style(trend)
        state_style = self._get_state_style(state)
        pid_style = colors.pid.default
        score_style = f"bold {band_style}" if band_style else "bold"

        # Resource column colors - each gets its own Dracula theme color
        cpu_style = "#8be9fd"  # Dracula cyan
        gpu_style = "#bd93f9"  # Dracula purple
        mem_style = "#f1fa8c"  # Dracula yellow
        disk_style = "#ffb86c"  # Dracula orange
        wake_style = "#2ee8bb"  # Aqua (green-leaning)

        # Dominant column color based on which resource is dominant
        dominant_colors = {
            "cpu": cpu_style,
            "gpu": gpu_style,
            "memory": mem_style,
            "disk": disk_style,
            "wakeups": wake_style,
        }
        dominant_style = dominant_colors.get(dominant_resource, band_style)

        # Build dominant display using format_dominant_info
        dominant_display = format_dominant_info(dominant_resource, disproportionality)

        return [
            Text(trend, style=trend_style),
            Text(str(pid).rjust(6), style=pid_style),
            Text(command, style=band_style),
            Text(str(score_val).rjust(3), style=score_style),
            Text(format_cpu_column(cpu), style=cpu_style),
            Text(format_gpu_column(gpu_rate), style=gpu_style),
            Text(format_mem_column(mem), style=mem_style),
            Text(format_disk_column(disk_rate), style=disk_style),
            Text(format_wake_column(wake_rate), style=wake_style),
            Text(state, style=state_style),
            Text(dominant_display, style=dominant_style),
        ]

    def update_rogues(self, rogues: list[dict]) -> None:
        """Update with rogue process list.

        Rogues contain ProcessScore data serialized as dicts.
        Displays exactly what's in the current sample — no decay logic.
        """
        self.remove_class("disconnected")
        if not self._table:
            return

        # Sort by score descending
        sorted_rogues = sorted(rogues, key=lambda x: x["score"], reverse=True)

        self._table.clear()

        for rogue in sorted_rogues:
            pid = rogue.get("pid", 0)
            score = rogue["score"]

            # Trend based on previous score
            prev_score = self._prev_scores.get(pid, score)
            if score > prev_score:
                trend = "▲"
            elif score < prev_score:
                trend = "▽"
            else:
                trend = "●"

            self._prev_scores[pid] = score

            # Extract raw metrics
            cpu = rogue.get("cpu", 0.0)
            gpu_rate = rogue.get("gpu_time_rate", 0.0)
            mem = rogue.get("mem", 0)
            disk_rate = rogue.get("disk_io_rate", 0.0)
            wake_rate = rogue.get("wakeups_rate", 0.0)

            dominant_resource = rogue.get("dominant_resource", "cpu")
            disproportionality = rogue.get("disproportionality", 0.0)
            state = rogue["state"]

            self._table.add_row(
                *self._make_row(
                    trend,
                    str(pid),
                    str(rogue.get("command", "?")),
                    score,
                    cpu,
                    gpu_rate,
                    mem,
                    disk_rate,
                    wake_rate,
                    str(state),
                    dominant_resource,
                    disproportionality,
                )
            )

    def set_disconnected(self) -> None:
        """Show disconnected state."""
        self.add_class("disconnected")
        if self._table:
            self._table.clear()
            row = self._make_row(
                "",  # trend
                "",  # pid
                "(not connected)",  # command
                0,  # score
                0.0,  # cpu
                0.0,  # gpu_rate
                0,  # mem
                0.0,  # disk_rate
                0.0,  # wake_rate
                "---",  # state
                "cpu",  # dominant_resource
                0.0,  # disproportionality
            )
            self._table.add_row(*row)


class RecentlyCalmPanel(Static):
    """Panel showing processes that recently dropped out of the rogues list.

    Tracks processes that were in the rogues list but aren't anymore.
    Shows them dimmed for decay_seconds before removing.
    Displays: PID, Process, Score (frozen at last-seen values).
    """

    DEFAULT_CSS = """
    RecentlyCalmPanel {
        width: 25%;
        height: 100%;
        border: solid $primary;
        border-title-align: left;
    }

    RecentlyCalmPanel DataTable {
        width: 100%;
        height: 100%;
        scrollbar-size: 0 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None
        self._cached_rogues: dict[int, dict] = {}
        self._last_seen: dict[int, float] = {}
        self._current_pids: set[int] = set()

    def compose(self) -> ComposeResult:
        """Create the panel."""
        yield DataTable(id="calm-table", zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        """Set up table columns."""
        self.border_title = "RECENTLY ROGUE"
        self._table = self.query_one("#calm-table", DataTable)
        self._table.add_columns(
            Text("PID", justify="center"),
            Text("Process", justify="center"),
            Text("Score", justify="center"),
        )

    def _get_band_style(self, score: int) -> str:
        """Map score to dimmed Rich style string."""
        bands = self.app.config.bands
        colors = self.app.config.tui.colors.bands

        if score >= bands.critical:
            style = colors.critical
        elif score >= bands.high:
            style = colors.high
        elif score >= bands.elevated:
            style = colors.elevated
        elif score >= bands.medium:
            style = colors.medium
        else:
            style = colors.low

        return f"{style} dim"

    def update_rogues(self, rogues: list[dict], now: float) -> None:
        """Update with current rogues to detect what dropped out.

        Args:
            rogues: Current rogues list from daemon.
            now: Current timestamp for decay timing.
        """
        if not self._table:
            return

        # Track current PIDs
        current_pids = {r.get("pid") for r in rogues if r.get("pid") is not None}

        # Cache any new rogues (so we have their data when they drop)
        for rogue in rogues:
            pid = rogue.get("pid")
            if pid is not None:
                self._cached_rogues[pid] = rogue.copy()
                self._last_seen[pid] = now

        # Find processes that dropped out (were cached but not in current)
        decay_seconds = self.app.config.tui.decay_seconds
        dropped: list[tuple[int, dict]] = []

        for pid, cached in list(self._cached_rogues.items()):
            if pid not in current_pids:
                age = now - self._last_seen.get(pid, 0)
                if age < decay_seconds:
                    dropped.append((pid, cached))
                else:
                    # Expired — remove from cache
                    del self._cached_rogues[pid]
                    self._last_seen.pop(pid, None)

        # Sort by score descending
        dropped.sort(key=lambda x: x[1].get("score", 0), reverse=True)

        # Rebuild table
        self._table.clear()

        for pid, cached in dropped:
            score = cached.get("score", 0)
            command = str(cached.get("command", "?"))
            style = self._get_band_style(score)

            self._table.add_row(
                Text(str(pid).rjust(6), style="dim"),
                Text(command, style=style),
                Text(str(score).rjust(3), style=style),
            )

        self._current_pids = current_pids


class EventHistoryPanel(Static):
    """Panel showing process events from the database.

    Displays recent tracking events with forensics indicators.
    Reads directly from the database for persistence across reconnects.
    """

    DEFAULT_CSS = """
    EventHistoryPanel {
        height: 100%;
        border: solid $primary;
        border-title-align: left;
    }

    EventHistoryPanel DataTable {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None
        self._db_conn: Any | None = None
        self._boot_time: int = 0
        # Cache forensics status: event_id -> bool (has captures)
        self._forensics_cache: dict[int, bool] = {}

    def compose(self) -> ComposeResult:
        """Create the panel."""
        yield DataTable(id="events-table", zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        """Set up table and database connection."""
        self.border_title = "EVENT HISTORY"
        self._table = self.query_one("#events-table", DataTable)
        self._table.add_column("Time", width=8)
        self._table.add_column("Process", width=15)
        self._table.add_column("Peak", width=4)
        self._table.add_column("Band", width=8)
        self._table.add_column("Dur", width=7)
        self._table.add_column("Status", width=10)
        self._table.add_column("📸", width=2)  # Forensics indicator
        self._table.show_header = True

        # Open read-only database connection
        db_path = self.app.config.db_path
        if db_path.exists():
            self._db_conn = get_connection(db_path)
            # Get boot time from daemon_state if available
            try:
                row = self._db_conn.execute(
                    "SELECT value FROM daemon_state WHERE key = 'boot_time'"
                ).fetchone()
                if row:
                    self._boot_time = int(row[0])
            except Exception:
                pass
            self.refresh_from_db()

    def on_unmount(self) -> None:
        """Close database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None

    def _has_forensics(self, event_id: int) -> bool:
        """Check if event has forensic captures (cached)."""
        if event_id in self._forensics_cache:
            return self._forensics_cache[event_id]

        if not self._db_conn:
            return False

        captures = get_forensic_captures(self._db_conn, event_id)
        has_captures = len(captures) > 0
        self._forensics_cache[event_id] = has_captures
        return has_captures

    def _get_band_color(self, band: str) -> str:
        """Get color for a band from config."""
        colors = self.app.config.tui.colors.bands
        return {
            "low": colors.low,
            "medium": colors.medium,
            "elevated": colors.elevated,
            "high": colors.high,
            "critical": colors.critical,
        }.get(band, "white")

    def refresh_from_db(self) -> None:
        """Refresh display from database."""
        if not self._table or not self._db_conn:
            return

        self._table.clear()

        # Get config values
        status_colors = self.app.config.tui.colors.status
        max_events = self.app.config.tui.tracked_max_history

        # Get recent events (includes both open and closed)
        events = get_process_events(
            self._db_conn,
            boot_time=self._boot_time if self._boot_time else None,
            limit=max_events,
        )

        # Sort by: tracking first, then by peak score descending
        # exit_time is None for tracking (False sorts before True)
        events.sort(key=lambda e: (e.get("exit_time") is not None, -e.get("peak_score", 0)))

        now = time.time()

        for event in events:
            entry_time = event.get("entry_time", 0)
            exit_time = event.get("exit_time")
            peak_score = event.get("peak_score", 0)
            peak_band = event.get("peak_band", "low")
            command = event.get("command", "?")
            event_id = event.get("id", 0)

            # Format time
            time_str = datetime.fromtimestamp(entry_time).strftime("%H:%M:%S")

            # Calculate duration
            if exit_time:
                duration = exit_time - entry_time
            else:
                duration = now - entry_time
            dur_str = format_duration(duration)

            # Determine status
            is_open = exit_time is None
            if is_open:
                status_text = f"[{status_colors.active}]tracking[/]"
            else:
                status_text = f"[{status_colors.ended}]ended[/]"

            # Get band color for peak
            band_color = self._get_band_color(peak_band)

            # Format peak with band color
            peak_text = f"[{band_color}]{peak_score}[/]"

            # Format band with color
            band_text = f"[{band_color}]{peak_band}[/]"

            # Forensics indicator
            has_forensics = self._has_forensics(event_id)
            forensics_text = "✓" if has_forensics else ""

            self._table.add_row(
                time_str,
                command[:15],
                Text.from_markup(peak_text),
                Text.from_markup(band_text),
                dur_str,
                Text.from_markup(status_text),
                forensics_text,
            )


class RogueHunterApp(App):
    """Real-time monitoring dashboard for rogue-hunter."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 5;
    }

    #main-area {
        height: 1fr;
    }

    #bottom-panels {
        height: 12;
        layout: horizontal;
    }

    #recently-calm {
        width: 1fr;
    }

    #event-history {
        width: 2fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()
        # Create config file with defaults if it doesn't exist
        if not self.config.config_path.exists():
            self.config.save()
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
            RecentlyCalmPanel(id="recently-calm"),
            EventHistoryPanel(id="event-history"),
            id="bottom-panels",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize on startup."""
        self.title = "rogue-hunter"
        self.sub_title = "Real-time Dashboard"
        asyncio.create_task(self._initial_connect())

    def on_unmount(self) -> None:
        """Cleanup on shutdown."""
        self._stopping = True

        # Close socket first to unblock any pending readline()
        if self._socket_client:
            self._socket_client.close()

        # Now cancel tasks (they should exit quickly since socket is closed)
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._socket_read_task and not self._socket_read_task.done():
            self._socket_read_task.cancel()

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
            except ConnectionError:
                pass  # Connection logging is best-effort
            # Refresh event history from database on connect
            try:
                self.query_one("#event-history", EventHistoryPanel).refresh_from_db()
            except (NoMatches, ScreenStackError):
                pass
            self._socket_read_task = asyncio.create_task(self._read_socket_loop())
            return True
        except FileNotFoundError:
            self._set_disconnected("socket not found", start_reconnect=False)
            if show_notification:
                self.notify(
                    "Daemon not running. Start with: rogue-hunter daemon",
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

        Backoff schedule uses config values (default: 1s → 2s → 4s → 8s → 16s → 30s capped)
        """
        tui_config = self.config.tui
        delay = tui_config.reconnect_initial_delay

        while not self._stopping:
            self.sub_title = f"Real-time Dashboard (reconnecting in {delay:.0f}s...)"

            # Sleep in 1-second chunks to stay responsive to shutdown
            remaining = delay
            while remaining > 0 and not self._stopping:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    return
                remaining -= 1.0

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
            delay = min(
                delay * tui_config.reconnect_multiplier,
                tui_config.reconnect_max_delay,
            )

    async def _read_socket_loop(self) -> None:
        """Read messages from socket and update UI."""
        try:
            while self._use_socket and self._socket_client and not self._stopping:
                try:
                    data = await self._socket_client.read_message(timeout=1.0)
                    self._handle_socket_data(data)
                except TimeoutError:
                    continue  # Check loop conditions and retry
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
        except (NoMatches, ScreenStackError):
            pass
        try:
            self.query_one("#main-area", ProcessTable).set_disconnected()
        except (NoMatches, ScreenStackError):
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

        # Update process table (pure current sample)
        try:
            self.query_one("#main-area", ProcessTable).update_rogues(rogues)
        except NoMatches:
            pass

        # Update recently calm panel (tracks what dropped out)
        try:
            self.query_one("#recently-calm", RecentlyCalmPanel).update_rogues(rogues, now)
        except NoMatches:
            pass

        # Refresh event history from database periodically (every 10 samples ≈ 3 seconds)
        if sample_count % 10 == 0:
            try:
                self.query_one("#event-history", EventHistoryPanel).refresh_from_db()
            except NoMatches:
                pass


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = RogueHunterApp(config)
    app.run()
