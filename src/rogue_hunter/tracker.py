# src/rogue_hunter/tracker.py
"""Per-process band tracking."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from rogue_hunter.collector import ProcessScore
from rogue_hunter.config import BandsConfig
from rogue_hunter.storage import (
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
    peak_snapshot_id: int
    last_checkpoint: float = 0.0  # Timestamp of last checkpoint snapshot


class ProcessTracker:
    """Tracks per-process band state and manages event lifecycle."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        bands: BandsConfig,
        boot_time: int,
        on_forensics_trigger: Callable[[int, str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize process tracker.

        Args:
            conn: Database connection
            bands: Band threshold configuration
            boot_time: System boot time for identifying this boot session
            on_forensics_trigger: Optional async callback for forensics capture.
                                  Called with (event_id, trigger_reason) when a process
                                  enters high/critical band or escalates into it.
        """
        self.conn = conn
        self.bands = bands
        self.boot_time = boot_time
        self.tracked: dict[int, TrackedProcess] = {}
        self._on_forensics_trigger = on_forensics_trigger
        self._restore_open_events()

    def _restore_open_events(self) -> None:
        """Restore tracking state from open events in DB."""
        for event in get_open_events(self.conn, self.boot_time):
            self.tracked[event["pid"]] = TrackedProcess(
                event_id=event["id"],
                pid=event["pid"],
                command=event["command"],
                peak_score=event["peak_score"],
                peak_snapshot_id=event["peak_snapshot_id"],
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
            in_bad_state = score.score.current >= threshold

            if score.pid in self.tracked:
                # Already tracking — update peak or close
                tracked = self.tracked[score.pid]
                if in_bad_state:
                    if score.score.current > tracked.peak_score:
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
        import asyncio

        band = score.band.current

        # Create event (peak_snapshot_id starts NULL)
        event_id = create_process_event(
            self.conn,
            pid=score.pid,
            command=score.command,
            boot_time=self.boot_time,
            entry_time=score.captured_at,
            entry_band=band,
            peak_score=score.score.current,
            peak_band=band,
        )

        # Insert entry snapshot and set as peak
        snapshot_id = insert_process_snapshot(self.conn, event_id, SNAPSHOT_ENTRY, score)
        update_process_event_peak(
            self.conn,
            event_id,
            peak_score=score.score.current,
            peak_band=band,
            peak_snapshot_id=snapshot_id,
        )

        self.tracked[score.pid] = TrackedProcess(
            event_id=event_id,
            pid=score.pid,
            command=score.command,
            peak_score=score.score.current,
            peak_snapshot_id=snapshot_id,
            last_checkpoint=score.captured_at,  # Start checkpoint timer from entry
        )

        log.info(
            "tracking_started",
            command=score.command,
            score=score.score.current,
            pid=score.pid,
            band=band,
        )

        # Trigger forensics if entering high or critical band
        if band in ("high", "critical") and self._on_forensics_trigger:
            asyncio.create_task(self._on_forensics_trigger(event_id, f"band_entry_{band}"))

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
            insert_process_snapshot(self.conn, tracked.event_id, SNAPSHOT_EXIT, exit_score)

        close_process_event(self.conn, tracked.event_id, exit_time)

        # Log with reason for closure
        reason = "score_dropped" if exit_score is not None else "process_gone"
        exit_score_val = exit_score.score.current if exit_score else None
        log.info(
            "tracking_ended",
            command=tracked.command,
            exit_score=exit_score_val,
            peak_score=tracked.peak_score,
            pid=pid,
            reason=reason,
        )

    def _update_peak(self, score: ProcessScore) -> None:
        """Update peak for tracked process."""
        import asyncio

        tracked = self.tracked[score.pid]
        old_score = tracked.peak_score
        old_band = self.bands.get_band(old_score)
        tracked.peak_score = score.score.current

        band = score.band.current

        # Log band transitions (escalations)
        if band != old_band:
            log.info(
                "band_changed",
                command=score.command,
                old_score=old_score,
                new_score=score.score.current,
                pid=score.pid,
                old_band=old_band,
                new_band=band,
            )

            # Trigger forensics on escalation INTO high/critical (from lower band)
            if band in ("high", "critical") and old_band not in ("high", "critical"):
                if self._on_forensics_trigger:
                    asyncio.create_task(
                        self._on_forensics_trigger(tracked.event_id, f"peak_escalation_{band}")
                    )

        # Insert checkpoint snapshot as new peak
        snapshot_id = insert_process_snapshot(
            self.conn, tracked.event_id, SNAPSHOT_CHECKPOINT, score
        )
        tracked.peak_snapshot_id = snapshot_id

        update_process_event_peak(
            self.conn,
            tracked.event_id,
            peak_score=score.score.current,
            peak_band=band,
            peak_snapshot_id=snapshot_id,
        )

        log.debug(
            "tracking_peak",
            command=score.command,
            old_score=old_score,
            new_score=score.score.current,
            pid=score.pid,
        )

    def _insert_checkpoint(self, score: ProcessScore, tracked: TrackedProcess) -> None:
        """Insert periodic checkpoint snapshot for a tracked process.

        Note: This is for periodic checkpoints only, NOT peak updates.
        The snapshot is recorded but doesn't update peak_snapshot_id.
        """
        insert_process_snapshot(self.conn, tracked.event_id, SNAPSHOT_CHECKPOINT, score)
        tracked.last_checkpoint = score.captured_at

        log.debug(
            "tracking_checkpoint",
            command=score.command,
            score=score.score.current,
            pid=score.pid,
        )
