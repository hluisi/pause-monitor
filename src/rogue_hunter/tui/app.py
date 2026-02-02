"""Real-time monitoring dashboard for rogue-hunter.

Philosophy: TUI = Real-time window into daemon state. Nothing more.
- Display what the daemon sends via socket — no contrived data
- CLI is for investigation; TUI is for "what's happening now"
- Single-screen dashboard — no page switching for real-time monitoring
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Label, RichLog, Static

from rogue_hunter.config import Config
from rogue_hunter.socket_client import SocketClient
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
    """Format a resource share value compactly.

    Args:
        value: The share value (multiple of fair share, e.g., 10.5 = 10.5× fair share).

    Returns:
        Formatted string like "10x", "1.5x", "0.8x".
    """
    if value >= 100:
        return f"{int(value)}x"
    elif value >= 10:
        return f"{value:.0f}x"
    elif value >= 1:
        return f"{value:.1f}x"
    else:
        return f"{value:.2f}x"


def format_dominant_info(dominant_resource: str, disproportionality: float) -> str:
    """Format dominant resource info for display.

    Args:
        dominant_resource: The resource type (cpu, gpu, memory, disk, wakeups).
        disproportionality: The disproportionality multiplier.

    Returns:
        Formatted string like "CPU 10.5x" or "MEM 1.5x".
    """
    disprop_str = format_share(disproportionality)

    # Labels for resources
    resource_labels = {
        "cpu": "CPU",
        "gpu": "GPU",
        "memory": "MEM",
        "disk": "DISK",
        "wakeups": "WAKE",
    }
    label = resource_labels.get(dominant_resource, dominant_resource.upper())
    return f"{label} {disprop_str}"


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
        height: 5;
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
        height: 2;
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
            max_value=100,
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

    def _update_gauge(self) -> None:
        """Update the gauge line display."""
        try:
            gauge_left = self.query_one("#gauge-left", Label)
            gauge_right = self.query_one("#gauge-right", Label)
        except NoMatches:
            return

        if not self.connected:
            gauge_left.update("STRESS ░░░░░░░░░░░░░░░░░░░░ ---/100   DISCONNECTED")
            gauge_right.update("Run: rogue-hunter daemon")
            return

        filled = self.score // 5
        bar = "█" * filled + "░" * (20 - filled)
        bands = self.app.config.bands
        tier = get_tier_name(self.score, bands.elevated, bands.critical)
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
    Includes decay: processes stay visible (dimmed) for 10s after leaving rogues.
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

    DECAY_SECONDS = 10.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._table: DataTable | None = None
        self._prev_scores: dict[int, int] = {}
        self._cached_rogues: dict[int, dict] = {}
        self._last_seen: dict[int, float] = {}

    def compose(self) -> ComposeResult:
        """Create the process table using DataTable."""
        yield DataTable(id="process-table", zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        """Set up table columns."""
        self.border_title = "TOP PROCESSES"
        self._table = self.query_one("#process-table", DataTable)
        self._table.add_columns(
            "", "PID", "Process", "Score", "CPU", "GPU", "MEM", "DISK", "WAKE", "State", "Dominant"
        )
        self.set_disconnected()

    def _get_band_style(self, score: int, decayed: bool) -> str:
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

        return f"{style} dim" if decayed else style

    def _get_trend_style(self, trend: str, decayed: bool) -> str:
        """Get style for trend indicator based on config colors."""
        if decayed:
            return self.app.config.tui.colors.trends.decayed

        trends = self.app.config.tui.colors.trends
        trend_colors = {
            "▲": trends.worsening,
            "▽": trends.improving,
            "●": trends.stable,
            "○": trends.decayed,
        }
        return trend_colors.get(trend, "")

    def _get_state_style(self, state: str, decayed: bool) -> str:
        """Get style for process state based on config colors."""
        if decayed:
            return "dim"

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
        cpu_share: float,
        gpu_share: float,
        mem_share: float,
        disk_share: float,
        wakeups_share: float,
        state: str,
        dominant_resource: str,
        disproportionality: float,
        decayed: bool = False,
    ) -> list[Text]:
        """Build styled row cells using Rich Text objects.

        Color scheme:
        - Trend: Own colors based on direction
        - PID: Muted color (not competing with process name)
        - Process name: Band color based on score severity
        - Score: Bold band color
        - Resource shares: Band color (shows what's driving the score)
        - State: Own colors based on process state severity
        - Dominant: Shows highest weighted resource
        """
        colors = self.app.config.tui.colors

        # Get styles for each element type
        band_style = self._get_band_style(score_val, decayed)
        trend_style = self._get_trend_style(trend, decayed)
        state_style = self._get_state_style(state, decayed)
        pid_style = "dim" if decayed else colors.pid.default
        score_style = f"bold {band_style}" if band_style else "bold"

        # Build dominant display using format_dominant_info
        dominant_display = format_dominant_info(dominant_resource, disproportionality)

        return [
            Text(trend, style=trend_style),
            Text(pid, style=pid_style),
            Text(command, style=band_style),
            Text(str(score_val), style=score_style),
            Text(format_share(cpu_share), style=band_style),
            Text(format_share(gpu_share), style=band_style),
            Text(format_share(mem_share), style=band_style),
            Text(format_share(disk_share), style=band_style),
            Text(format_share(wakeups_share), style=band_style),
            Text(state, style=state_style),
            Text(dominant_display, style=band_style),
        ]

    def update_rogues(self, rogues: list[dict], now: float) -> None:
        """Update with rogue process list.

        Rogues contain ProcessScore data serialized as dicts.
        """
        self.remove_class("disconnected")
        if not self._table:
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

        # Sort by score (plain int value)
        display_list.sort(
            key=lambda x: x[0]["score"],
            reverse=True,
        )

        self._table.clear()

        for rogue, is_decayed in display_list:
            pid = rogue.get("pid", 0)
            score = rogue["score"]

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

            # Extract resource-based scoring fields
            cpu_share = rogue.get("cpu_share", 0.0)
            gpu_share = rogue.get("gpu_share", 0.0)
            mem_share = rogue.get("mem_share", 0.0)
            disk_share = rogue.get("disk_share", 0.0)
            wakeups_share = rogue.get("wakeups_share", 0.0)
            dominant_resource = rogue.get("dominant_resource", "cpu")
            disproportionality = rogue.get("disproportionality", 0.0)
            state = rogue["state"]

            self._table.add_row(
                *self._make_row(
                    trend,
                    str(pid),
                    str(rogue.get("command", "?")),
                    score,
                    cpu_share,
                    gpu_share,
                    mem_share,
                    disk_share,
                    wakeups_share,
                    str(state),
                    dominant_resource,
                    disproportionality,
                    decayed=is_decayed,
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
                0.0,  # cpu_share
                0.0,  # gpu_share
                0.0,  # mem_share
                0.0,  # disk_share
                0.0,  # wakeups_share
                "---",  # state
                "cpu",  # dominant_resource
                0.0,  # disproportionality
                decayed=False,
            )
            self._table.add_row(*row)


@dataclass
class DisplayTrackedProcess:
    """A process being tracked in the TUI display.

    Note: This is distinct from tracker.py's TrackedProcess which manages
    database event lifecycle. This class is purely for TUI display state.
    """

    command: str
    entry_time: float
    peak_score: int
    dominant_resource: str = "cpu"  # cpu, gpu, memory, disk, wakeups
    disproportionality: float = 0.0  # How much this process dominates the resource
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
        border-title-align: left;
    }

    TrackedEventsPanel DataTable {
        width: 100%;
        height: 100%;
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
        yield DataTable(id="tracked-table", zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        """Set up table."""
        self.border_title = "TRACKED"
        self._table = self.query_one("#tracked-table", DataTable)
        self._table.add_column("Time", width=8)
        self._table.add_column("Process")
        self._table.add_column("Peak", width=4)
        self._table.add_column("Dur", width=6)
        self._table.add_column("Dominant", width=10)  # Resource and disproportionality
        self._table.add_column("Status", width=8)
        self._table.show_header = True

    def _extract_score(self, rogue: dict) -> int:
        """Extract score value from rogue dict."""
        return rogue["score"]

    def update_tracking(self, rogues: list[dict], now: float) -> None:
        """Update tracking based on current rogues.

        Tracks by COMMAND NAME (not PID) to deduplicate.
        """
        # Build current state: command -> best rogue for processes above threshold
        current_above: dict[str, dict] = {}
        current_pids: set[int] = set()
        tracking_threshold = self.app.config.bands.elevated

        for r in rogues:
            score = self._extract_score(r)
            if score >= tracking_threshold:
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
            dominant_resource = rogue.get("dominant_resource", "cpu")
            disproportionality = rogue.get("disproportionality", 0.0)

            if cmd not in self._active:
                # New tracking entry
                self._active[cmd] = DisplayTrackedProcess(
                    command=cmd,
                    entry_time=now,
                    peak_score=score,
                    dominant_resource=dominant_resource,
                    disproportionality=disproportionality,
                )
            else:
                # Update peak if higher
                tracked = self._active[cmd]
                if score > tracked.peak_score:
                    tracked.peak_score = score
                    tracked.dominant_resource = dominant_resource
                    tracked.disproportionality = disproportionality

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

        # Get status colors from config
        status_colors = self.app.config.tui.colors.status

        # Show active tracking first (sorted by peak score desc)
        active_sorted = sorted(
            self._active.values(),
            key=lambda t: t.peak_score,
            reverse=True,
        )
        for tracked in active_sorted:
            time_str = datetime.fromtimestamp(tracked.entry_time).strftime("%H:%M:%S")
            dominant_display = format_dominant_info(
                tracked.dominant_resource, tracked.disproportionality
            )
            duration = format_duration(tracked.duration)

            self._table.add_row(
                time_str,
                tracked.command[:15],
                str(tracked.peak_score),
                duration,
                dominant_display,
                f"[{status_colors.active}]active[/]",
            )

        # Show history (sorted by peak score desc)
        history_sorted = sorted(
            self._history.values(),
            key=lambda t: t.peak_score,
            reverse=True,
        )
        for tracked in history_sorted:
            time_str = datetime.fromtimestamp(tracked.entry_time).strftime("%H:%M:%S")
            dominant_display = format_dominant_info(
                tracked.dominant_resource, tracked.disproportionality
            )
            duration = format_duration(tracked.duration)

            self._table.add_row(
                time_str,
                tracked.command[:15],
                str(tracked.peak_score),
                duration,
                dominant_display,
                f"[{status_colors.ended}]ended[/]",
            )


class ActivityLog(Static):
    """Activity log showing tier transitions using RichLog for auto-scroll."""

    DEFAULT_CSS = """
    ActivityLog {
        height: 100%;
        border: solid $primary;
        border-title-align: left;
    }

    ActivityLog RichLog {
        width: 100%;
        height: 100%;
    }
    """

    MAX_ENTRIES = 15

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._prev_tier = "NORMAL"

    def compose(self) -> ComposeResult:
        """Create log using RichLog for auto-scroll and auto-prune."""
        yield RichLog(id="activity-log", markup=True, max_lines=self.MAX_ENTRIES)

    def on_mount(self) -> None:
        """Set border title and initial state."""
        self.border_title = "ACTIVITY"
        self._add_entry("Waiting for connection...", "normal")

    def _add_entry(self, message: str, level: str = "normal") -> None:
        """Add a log entry with colored timestamp using config colors."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        colors = self.app.config.tui.colors
        # Use band colors for severity, but border "normal" color for healthy state
        # (band "low" may be empty since healthy processes don't need color)
        color = {
            "high": colors.bands.critical,
            "elevated": colors.bands.elevated,
            "normal": colors.borders.normal,  # Use border green for healthy
        }.get(level, "white")
        try:
            log = self.query_one("#activity-log", RichLog)
            log.write(f"[{color}]{timestamp}  {message}[/{color}]")
        except NoMatches:
            pass

    def check_transitions(self, score: int) -> None:
        """Check for tier transitions."""
        bands = self.app.config.bands
        current_tier = get_tier_name(score, bands.elevated, bands.critical)
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
        try:
            log = self.query_one("#activity-log", RichLog)
            log.clear()
        except NoMatches:
            pass
        self._add_entry("Connected to daemon", "normal")


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
            ActivityLog(id="activity"),
            TrackedEventsPanel(id="tracked"),
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

        Backoff schedule: 1s → 2s → 4s → 8s → 16s → 30s (capped)
        """
        delay = self._RECONNECT_INITIAL_DELAY

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
            delay = min(delay * self._RECONNECT_MULTIPLIER, self._RECONNECT_MAX_DELAY)

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
    app = RogueHunterApp(config)
    app.run()
