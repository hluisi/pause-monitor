# src/pause_monitor/tracker.py
"""Per-process band tracking."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from pause_monitor.collector import ProcessScore
from pause_monitor.config import BandsConfig
from pause_monitor.storage import (
    close_process_event,
    create_process_event,
    get_open_events,
    insert_process_snapshot,
    update_process_event_peak,
)


@dataclass
class TrackedProcess:
    """In-memory state for a tracked process."""

    event_id: int
    pid: int
    peak_score: int


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
                peak_score=event["peak_score"],
            )

    def update(self, scores: list[ProcessScore]) -> None:
        """Update tracking with new scores."""
        current_pids = {s.pid for s in scores}
        threshold = self.bands.tracking_threshold

        # Close events for PIDs no longer present
        for pid in list(self.tracked.keys()):
            if pid not in current_pids:
                # Use current time for exit if no scores provided
                exit_time = time.time()
                self._close_event(pid, exit_time)

        # Process each score
        for score in scores:
            in_bad_state = score.score >= threshold

            if score.pid in self.tracked:
                # Already tracking — update peak or close
                tracked = self.tracked[score.pid]
                if in_bad_state:
                    if score.score > tracked.peak_score:
                        self._update_peak(score)
                else:
                    self._close_event(score.pid, score.captured_at)
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

        insert_process_snapshot(self.conn, event_id, "entry", snapshot_json)

        self.tracked[score.pid] = TrackedProcess(
            event_id=event_id,
            pid=score.pid,
            peak_score=score.score,
        )

    def _close_event(self, pid: int, exit_time: float) -> None:
        """Close event for process exiting bad state."""
        if pid not in self.tracked:
            return

        tracked = self.tracked.pop(pid)
        close_process_event(self.conn, tracked.event_id, exit_time)

    def _update_peak(self, score: ProcessScore) -> None:
        """Update peak for tracked process."""
        tracked = self.tracked[score.pid]
        tracked.peak_score = score.score

        snapshot_json = json.dumps(score.to_dict())
        band = self.bands.get_band(score.score)

        update_process_event_peak(
            self.conn,
            tracked.event_id,
            peak_score=score.score,
            peak_band=band,
            peak_snapshot=snapshot_json,
        )
