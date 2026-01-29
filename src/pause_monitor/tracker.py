# src/pause_monitor/tracker.py
"""Per-process band tracking."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

import structlog
from termcolor import colored

from pause_monitor.collector import ProcessScore
from pause_monitor.config import BandsConfig
from pause_monitor.storage import (
    close_process_event,
    create_process_event,
    get_open_events,
    insert_process_snapshot,
    update_process_event_peak,
)

log = structlog.get_logger()

# Snapshot types
SNAPSHOT_ENTRY = "entry"
SNAPSHOT_EXIT = "exit"
SNAPSHOT_CHECKPOINT = "checkpoint"


@dataclass
class TrackedProcess:
    """In-memory state for a tracked process."""

    event_id: int
    pid: int
    command: str
    peak_score: int
    last_checkpoint: float = 0.0  # Timestamp of last checkpoint snapshot


class ProcessTracker:
    """Tracks per-process band state and manages event lifecycle."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        bands: BandsConfig,
        boot_time: int,
    ) -> None:
        self.conn = conn
        self.bands = bands
        self.boot_time = boot_time
        self.tracked: dict[int, TrackedProcess] = {}
        self._restore_open_events()

    def _restore_open_events(self) -> None:
        """Restore tracking state from open events in DB."""
        for event in get_open_events(self.conn, self.boot_time):
            self.tracked[event["pid"]] = TrackedProcess(
                event_id=event["id"],
                pid=event["pid"],
                command=event["command"],
                peak_score=event["peak_score"],
            )

    def update(self, scores: list[ProcessScore]) -> None:
        """Update tracking with new scores."""
        current_pids = {s.pid for s in scores}
        threshold = self.bands.tracking_threshold

        # Close events for PIDs no longer present (process exited or dropped from top-N)
        for pid in list(self.tracked.keys()):
            if pid not in current_pids:
                # Use most recent score's timestamp for consistency, or current time
                # if no scores provided (e.g., empty update during shutdown)
                exit_time = scores[0].captured_at if scores else time.time()
                # No exit snapshot: we don't have final process state for disappeared PIDs
                self._close_event(pid, exit_time, exit_score=None)

        # Process each score
        for score in scores:
            in_bad_state = score.score >= threshold

            if score.pid in self.tracked:
                # Already tracking — update peak or close
                tracked = self.tracked[score.pid]
                if in_bad_state:
                    if score.score > tracked.peak_score:
                        self._update_peak(score)
                    # Checkpoint: periodic snapshot while in bad state
                    checkpoint_interval = self.bands.checkpoint_interval
                    if score.captured_at - tracked.last_checkpoint >= checkpoint_interval:
                        self._insert_checkpoint(score, tracked)
                else:
                    # Score dropped below threshold — capture exit state
                    self._close_event(score.pid, score.captured_at, exit_score=score)
            else:
                # Not tracking — maybe start
                if in_bad_state:
                    self._open_event(score)

    def _open_event(self, score: ProcessScore) -> None:
        """Create new event for process entering bad state."""
        snapshot_json = json.dumps(score.to_dict())
        band = self.bands.get_band(score.score)

        event_id = create_process_event(
            self.conn,
            pid=score.pid,
            command=score.command,
            boot_time=self.boot_time,
            entry_time=score.captured_at,
            entry_band=band,
            peak_score=score.score,
            peak_band=band,
            peak_snapshot=snapshot_json,
        )

        insert_process_snapshot(self.conn, event_id, SNAPSHOT_ENTRY, snapshot_json)

        self.tracked[score.pid] = TrackedProcess(
            event_id=event_id,
            pid=score.pid,
            command=score.command,
            peak_score=score.score,
            last_checkpoint=score.captured_at,  # Start checkpoint timer from entry
        )

        msg = (
            f"tracking_started: {colored(score.command, 'cyan')} "
            f"{colored(f'({score.score})', 'yellow')} "
            f"{colored(f'pid={score.pid}', 'dark_grey')} "
            f"{colored(f'[{band}]', 'magenta')}"
        )
        log.info(msg)

    def _close_event(
        self,
        pid: int,
        exit_time: float,
        exit_score: ProcessScore | None = None,
    ) -> None:
        """Close event for process exiting bad state.

        Args:
            pid: Process ID to close event for.
            exit_time: Timestamp when process exited bad state.
            exit_score: Final process score if available. None when PID disappeared
                       (process exited or dropped from top-N selection) and we don't
                       have its final state.
        """
        if pid not in self.tracked:
            return

        tracked = self.tracked.pop(pid)

        # Insert exit snapshot if we have the score (only when score dropped below threshold)
        if exit_score is not None:
            snapshot_json = json.dumps(exit_score.to_dict())
            insert_process_snapshot(self.conn, tracked.event_id, SNAPSHOT_EXIT, snapshot_json)

        close_process_event(self.conn, tracked.event_id, exit_time)

        # Log with reason for closure
        reason = "score_dropped" if exit_score is not None else "process_gone"
        exit_score_val = exit_score.score if exit_score else None
        score_info = f"{exit_score_val}→0" if exit_score_val else f"peak={tracked.peak_score}"
        msg = (
            f"tracking_ended: {colored(tracked.command, 'cyan')} "
            f"{colored(f'({score_info})', 'yellow')} "
            f"{colored(f'pid={pid}', 'dark_grey')} "
            f"{colored(f'[{reason}]', 'magenta')}"
        )
        log.info(msg)

    def _update_peak(self, score: ProcessScore) -> None:
        """Update peak for tracked process."""
        tracked = self.tracked[score.pid]
        old_score = tracked.peak_score
        old_band = self.bands.get_band(old_score)
        tracked.peak_score = score.score

        snapshot_json = json.dumps(score.to_dict())
        band = self.bands.get_band(score.score)

        # Log band transitions (escalations)
        if band != old_band:
            msg = (
                f"band_changed: {colored(score.command, 'cyan')} "
                f"{colored(f'({old_score}→{score.score})', 'yellow')} "
                f"{colored(f'pid={score.pid}', 'dark_grey')} "
                f"{colored(f'[{old_band}→{band}]', 'magenta')}"
            )
            log.info(msg)

        update_process_event_peak(
            self.conn,
            tracked.event_id,
            peak_score=score.score,
            peak_band=band,
            peak_snapshot=snapshot_json,
        )

        log.debug(
            f"tracking_peak: {colored(score.command, 'cyan')} "
            f"{colored(f'({old_score}→{score.score})', 'yellow')} "
            f"{colored(f'pid={score.pid}', 'dark_grey')}"
        )

    def _insert_checkpoint(self, score: ProcessScore, tracked: TrackedProcess) -> None:
        """Insert periodic checkpoint snapshot for a tracked process."""
        snapshot_json = json.dumps(score.to_dict())
        insert_process_snapshot(
            self.conn,
            tracked.event_id,
            SNAPSHOT_CHECKPOINT,
            snapshot_json,
        )
        tracked.last_checkpoint = score.captured_at

        log.debug(
            f"tracking_checkpoint: {colored(score.command, 'cyan')} "
            f"{colored(f'({score.score})', 'yellow')} "
            f"{colored(f'pid={score.pid}', 'dark_grey')}"
        )
