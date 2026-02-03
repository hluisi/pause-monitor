"""Centralized console logging with Rich formatting.

This module provides:
1. Icon vocabulary (Icon class namespace)
2. Level-based styling
3. Core log functions (log, info, warn, error)
4. Domain-specific helpers (daemon_started, rogue_enter, heartbeat, etc.)
5. Structlog configuration (configure, get_structlog)

Console output uses Rich markup for colors. JSON file output via structlog
remains separate (machine-parseable, no colors).
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

if TYPE_CHECKING:
    from rogue_hunter.config import Config

# Rich console for colorful human-readable output
_console = Console(highlight=False)

# Module-level config reference for score_color (set by configure())
_config: "Config | None" = None


# ─────────────────────────────────────────────────────────────────────────────
# Icons
# ─────────────────────────────────────────────────────────────────────────────


class Icon:
    """Icon vocabulary for console output.

    Use via autocomplete: Icon.<TAB> to see all available icons.
    """

    OK = "[bold green]✓[/]"
    FAIL = "[bold red]✗[/]"
    WAIT = "⏳"
    CAPTURE = "📸"
    PRUNE = "🧹"
    SAVE = "💾"
    HEARTBEAT = "[magenta]♡[/]"
    ROGUE_ENTER = "[bright_green]▲[/]"
    ROGUE_EXIT = "[bright_red]▼[/]"
    SIGNAL = "⚡"
    CONNECTED = "[green]⬤[/]"
    DISCONNECTED = "[red]⬤[/]"


# ─────────────────────────────────────────────────────────────────────────────
# Level Styles
# ─────────────────────────────────────────────────────────────────────────────

_LEVEL_STYLES = {
    "info": "[bright_blue]\\[info][/]",
    "warn": "[yellow]\\[warn][/]",
    "error": "[bold red]\\[err][/] ",
}


# ─────────────────────────────────────────────────────────────────────────────
# Core Functions
# ─────────────────────────────────────────────────────────────────────────────


def log(level: str, msg: str, icon: str = "") -> None:
    """Print a log message with timestamp and level.

    Args:
        level: Log level (info, warn, error)
        msg: Message to print (can include Rich markup)
        icon: Optional icon to show after level (e.g., Icon.OK)
    """
    ts = datetime.now().strftime("%H:%M:%S")
    lvl = _LEVEL_STYLES.get(level, f"[{level}]")
    icon_part = f" {icon}" if icon else ""
    _console.print(f"[dim]{ts}[/] {lvl}{icon_part} {msg}")


def info(msg: str, icon: str = "") -> None:
    """Log an info message."""
    log("info", msg, icon)


def warn(msg: str, icon: str = "") -> None:
    """Log a warning message."""
    log("warn", msg, icon)


def error(msg: str, icon: str = "") -> None:
    """Log an error message."""
    log("error", msg, icon)


# ─────────────────────────────────────────────────────────────────────────────
# Styling Helpers
# ─────────────────────────────────────────────────────────────────────────────


def score_color(score: int) -> str:
    """Return Rich color name based on score severity.

    Requires configure() to have been called first.

    Raises:
        RuntimeError: If configure() hasn't been called.
    """
    if _config is None:
        raise RuntimeError("score_color() called before configure()")

    bands = _config.bands
    if score >= bands.high:
        return "bright_red"
    elif score >= bands.elevated:
        return "bright_yellow"
    return "green"


# ─────────────────────────────────────────────────────────────────────────────
# Domain Helpers
# ─────────────────────────────────────────────────────────────────────────────


def daemon_started() -> None:
    """Log daemon startup complete."""
    info("Daemon started", Icon.OK)


def daemon_stopping() -> None:
    """Log daemon shutdown initiated."""
    info("Daemon stopping...", Icon.WAIT)


def daemon_stopped() -> None:
    """Log daemon shutdown complete."""
    info("Daemon stopped", Icon.OK)


def signal_received(name: str) -> None:
    """Log signal received."""
    info(f"Received [bold]{name}[/]", Icon.SIGNAL)


def rogue_enter(cmd: str, pid: int, score: int, metrics: str) -> None:
    """Log process entered rogue tracking."""
    cmd_display = cmd[:28] + ".." if len(cmd) > 28 else cmd
    sc = score_color(score)
    info(
        f"[cyan]{cmd_display}[/] [dim]({pid})[/] score [{sc}]{score}[/] [dim]— {metrics}[/]",
        Icon.ROGUE_ENTER,
    )


def rogue_exit(cmd: str, pid: int) -> None:
    """Log process exited rogue tracking."""
    cmd_display = cmd[:28] + ".." if len(cmd) > 28 else cmd
    info(f"[cyan]{cmd_display}[/] [dim]({pid})[/] no longer rogue", Icon.ROGUE_EXIT)


def heartbeat(
    avg_score: int,
    max_score: int,
    tracked_count: int,
    buffer_size: int,
    buffer_capacity: int,
    client_count: int,
    rss_mb: float,
    db_size_mb: float,
) -> None:
    """Log periodic heartbeat stats."""
    avg_c = score_color(avg_score)
    max_c = score_color(max_score)
    info(
        f"score [{avg_c}]{avg_score}[/]–[{max_c}]{max_score}[/], "
        f"[cyan]{tracked_count}[/] tracked, "
        f"[dim]{buffer_size}/{buffer_capacity} buffer, "
        f"{client_count} clients, "
        f"{round(rss_mb, 1)}MB RSS, {round(db_size_mb, 1)}MB DB[/]",
        Icon.HEARTBEAT,
    )


def client_connected(count: int) -> None:
    """Log TUI client connected."""
    suffix = "s" if count != 1 else ""
    info(f"TUI connected [dim]({count} client{suffix})[/]", Icon.CONNECTED)


def client_disconnected(remaining: int) -> None:
    """Log TUI client disconnected."""
    info(f"TUI disconnected [dim]({remaining} remaining)[/]", Icon.DISCONNECTED)


def forensics_debounced(elapsed: float, cooldown: float) -> None:
    """Log forensics capture skipped due to debounce."""
    info(f"Forensics debounced [dim]({round(elapsed, 1)}s < {cooldown}s)[/]", Icon.WAIT)


def forensics_captured(event_id: int, capture_id: int) -> None:
    """Log forensics capture complete."""
    info(f"Forensics captured [dim](event {event_id}, #{capture_id})[/]", Icon.CAPTURE)


def forensics_skipped(reason: str) -> None:
    """Log forensics skipped."""
    warn(f"Forensics skipped — {reason}")


def socket_listening(path: str) -> None:
    """Log socket server ready."""
    info(f"Socket listening on [cyan]{path}[/]")


def socket_stopped() -> None:
    """Log socket server stopped."""
    info("Socket server stopped")


def auto_prune_started() -> None:
    """Log auto-prune started."""
    info("[dim]Auto-pruning...[/]", Icon.PRUNE)


def auto_prune_complete(events_deleted: int, snapshots_deleted: int = 0) -> None:
    """Log auto-prune complete."""
    parts = [f"{events_deleted} events"]
    if snapshots_deleted > 0:
        parts.append(f"{snapshots_deleted} snapshots")
    info(f"[dim]Pruned {', '.join(parts)}[/]")


def machine_snapshot_saved(process_count: int, max_score: int) -> None:
    """Log machine snapshot saved."""
    info(f"[dim]Snapshot saved: {process_count} processes, max score {max_score}[/]", Icon.SAVE)


def sample_failed(error_msg: str) -> None:
    """Log sample collection failed."""
    error(f"Sample failed: {error_msg}", Icon.FAIL)


def main_loop_cancelled() -> None:
    """Log main loop cancelled."""
    info("Main loop cancelled")


def already_running(pid: int | None = None) -> None:
    """Log daemon already running error."""
    if pid:
        error(f"Another daemon already running [dim](PID {pid})[/]", Icon.FAIL)
    else:
        error("Another daemon already running", Icon.FAIL)


def database_status(status: str, stale_closed: int) -> None:
    """Log database initialization status."""
    if stale_closed > 0:
        info(f"Database {status}, closed [cyan]{stale_closed}[/] stale events")
    else:
        info(f"Database {status}")


def config_created(path: str) -> None:
    """Log config file created."""
    info(f"Created config at [cyan]{path}[/]")


def version_info(name: str, version: str) -> None:
    """Log version info."""
    info(f"[bold cyan]{name}[/] v{version}")


def config_summary(buffer_size: int, tracking_threshold: int) -> None:
    """Log config summary."""
    info(f"Config: buffer=[cyan]{buffer_size}[/], track≥[cyan]{tracking_threshold}[/]")


def bands_summary(medium: int, elevated: int, high: int, critical: int) -> None:
    """Log band thresholds."""
    info(
        f"Bands: [green]{medium}[/]/[yellow]{elevated}[/]/"
        f"[bright_yellow]{high}[/]/[bright_red]{critical}[/] "
        f"[dim](med/elev/high/crit)[/]"
    )


def qos_set(qos_name: str) -> None:
    """Log QoS class set."""
    info(f"QoS: [cyan]{qos_name}[/]")


def priority_set(level: str) -> None:
    """Log priority set."""
    info(f"Priority: [cyan]{level}[/]")


def priority_default() -> None:
    """Log default priority (no elevation)."""
    info("Priority: [dim]default[/]")


def tailspin_enabled() -> None:
    """Log tailspin enabled."""
    info("Tailspin [green]enabled[/] [dim](was disabled)[/]")


def tailspin_disabled() -> None:
    """Log tailspin disabled."""
    info("Tailspin [dim]disabled[/]")


def tailspin_not_found() -> None:
    """Log tailspin not found."""
    warn("Tailspin not found — forensics will fail")


def tailspin_check_failed(error_msg: str) -> None:
    """Log tailspin check failed."""
    warn(f"Tailspin check failed: {error_msg}")


def tailspin_disable_failed(error_msg: str) -> None:
    """Log tailspin disable failed."""
    warn(f"Failed to disable tailspin: {error_msg}")


def caffeinate_not_found() -> None:
    """Log caffeinate not found."""
    warn("Caffeinate not found")


def pid_file_invalid() -> None:
    """Log PID file invalid."""
    warn("PID file invalid")


def daemon_already_running(pid: int) -> None:
    """Log daemon already running."""
    warn(f"Daemon already running [dim](PID {pid})[/]")


def stale_pid_file(pid: int, actual_process: str) -> None:
    """Log stale PID file (different process)."""
    info(f"[dim]Stale PID file — PID {pid} is {actual_process}[/]")


def stale_pid_not_found(pid: int) -> None:
    """Log stale PID file (process not found)."""
    info(f"[dim]Stale PID file — PID {pid} not found[/]")


def pid_verify_failed(pid: int) -> None:
    """Log PID verification failed (access denied)."""
    warn(f"Can't verify PID {pid} — assuming running")


def invalid_client_message() -> None:
    """Log invalid client message received."""
    warn("Invalid client message")


# ─────────────────────────────────────────────────────────────────────────────
# Structlog Configuration
# ─────────────────────────────────────────────────────────────────────────────


def _add_source(source: str) -> structlog.types.Processor:
    """Create a processor that adds a source field to log events."""

    def processor(
        logger: structlog.types.WrappedLogger,
        method_name: str,
        event_dict: structlog.types.EventDict,
    ) -> structlog.types.EventDict:
        event_dict["source"] = source
        return event_dict

    return processor


def configure(config: Config) -> None:
    """Configure structlog with dual output: console + JSON file.

    Console output uses human-readable format with colors.
    File output uses JSON Lines format for machine parsing.
    Both use local time to match sample timestamps.

    Args:
        config: Application config with paths
    """
    global _config
    _config = config

    # Ensure state directory exists for log file
    config.state_dir.mkdir(parents=True, exist_ok=True)

    # Set up rotating file handler for JSON output
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_path,
        maxBytes=config.system.log_max_bytes,
        backupCount=config.system.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    # Configure stdlib logging for file output
    # structlog will use this for JSON output via ProcessorFormatter
    stdlib_root = logging.getLogger()
    stdlib_root.setLevel(logging.INFO)

    # Clear any existing handlers
    stdlib_root.handlers.clear()

    # Add file handler with JSON formatter
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso", utc=False, key="ts"),
                structlog.processors.add_log_level,
                _add_source("daemon"),
                structlog.processors.format_exc_info,
            ],
        )
    )
    stdlib_root.addHandler(file_handler)

    # Configure structlog with console output (primary) and stdlib passthrough (for file)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
            structlog.processors.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Console output is handled by Rich (see log functions above)
    # structlog only writes to JSON file for machine parsing


def get_structlog() -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance.

    Returns a logger for structured JSON file output. Use this for
    machine-parseable events that should go to the log file.

    For human-readable console output, use the log/info/warn/error
    functions or domain helpers instead.
    """
    return structlog.get_logger()
