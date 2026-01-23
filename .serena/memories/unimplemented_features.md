# Unimplemented Features and Stubs

> **Phase 6 COMPLETE (2026-01-22).** Tier-based event storage; SCHEMA_VERSION=6.

**Last audited:** 2026-01-23

## Completed (via Redesign)

- ~~Sentinel slow loop~~ - Replaced by Daemon powermetrics integration
- ~~TUI socket streaming~~ - Implemented via SocketServer/SocketClient
- ~~Complete 8-factor stress (including pageins)~~ - All factors now calculated from powermetrics
- ~~Process attribution~~ - Using powermetrics top_cpu_processes + top_pagein_processes
- ~~Ring Buffer~~ - `ringbuffer.py` stores last 30s of stress samples
- ~~Tier State Machine~~ - `sentinel.py:TierManager` manages tier transitions
- ~~Event Status Management~~ - CLI `events mark` command and TUI EventsScreen
- ~~Socket Server/Client~~ - Real-time data streaming to TUI
- ~~10Hz Main Loop~~ - Single 100ms loop driven by powermetrics stream

## Explicit Stubs

| Location | Current Behavior | Expected Behavior |
|----------|------------------|-------------------|
| `tui/app.py:843-845` `action_show_history()` | `self.notify("History view not yet implemented")` | Navigate to history view with charts/graphs showing stress trends |

## Broken Code (Needs Fix)

| Location | Issue | Impact |
|----------|-------|--------|
| `cli.py:81` `status` command | References `event.timestamp` and `event.duration` which don't exist on new `Event` class (should be `event.start_timestamp` and computed duration) | **AttributeError** - `status` command crashes when events exist |
| `cli.py:50,317` `status`/`history` commands | Use `get_recent_samples()` which queries legacy `samples` table | Shows stale/empty data since daemon no longer inserts into `samples` |

## Config Defined But Not Used

| Config Key | Where Defined | What Should Happen |
|------------|---------------|-------------------|
| `learning_mode` | `config.py:87` | Daemon should suppress alerts, store all samples, and track pause correlations for calibration. Currently only stored/loaded in config but never checked in daemon logic. |
| `suspects.patterns` | `config.py:46-57` | Should flag processes matching patterns as suspects in forensics and culprit identification. Pattern list exists but is never searched against process names in daemon.py or forensics.py. |

## Database Tables Status

| Table | Status | Notes |
|-------|--------|-------|
| `events` | **Active** | Tier-based event storage (start/end timestamps, peak_tier) |
| `event_samples` | **Active** | Samples captured during escalation events (tier 2: peaks, tier 3: continuous 10Hz) |
| `samples` | **Legacy/Orphaned** | Not written by daemon; still queried by `status` and `history` commands (causing stale results) |
| `process_samples` | **Legacy/Unused** | Schema exists, no INSERT statements, never populated |
| `daemon_state` | **Partial** | Only `schema_version` is used; design spec says `io_baseline` and `last_sample_id` should also be persisted |

## Design Spec Gaps (Missing CLI Commands)

| Command | Design Doc Section | Current State |
|---------|-------------------|---------------|
| `calibrate` | CLI Commands table | **Does not exist.** Should analyze learning mode data and suggest thresholds. |

## Design Spec Gaps (Missing Features)

| Feature | Design Doc Section | Current State |
|---------|-------------------|---------------|
| `history --at "<time>"` option | CLI Commands table | Option not implemented. History command only has `--hours` and `--format`. |
| Sudoers generation | Install Process step 5 | `install` command creates launchd plist but no sudoers rules for privileged operations (spindump, tailspin, powermetrics --show-process-io). |
| tailspin enable | Install Process step 6 | `install` command does not call `tailspin enable`. |
| SIGHUP config reload | Design Decisions | No signal handler in daemon for hot-reloading config. Only SIGTERM/SIGINT are handled. |
| ForensicsHealth check | Install Process step 8 | `check_forensics_health()` not implemented. |
| Event directory cleanup on prune | Storage design | `prune_old_data()` deletes database records but not orphaned event directories in `~/.local/share/pause-monitor/events/`. |

## Legacy Data Issues

| Issue | Impact | Fix Needed |
|-------|--------|-----------|
| `samples` table orphaned | `status` and `history` CLI commands show stale/no data | Either (a) update commands to use `event_samples` or (b) have daemon also write to `samples` |
| `process_samples` table unused | No per-process data stored in database | Design intended this for forensics; either delete table or implement insertion |

## Priority Recommendations

### High Priority (Broken Functionality)

1. **Fix `status` command** - References wrong Event attributes (`timestamp` → `start_timestamp`, add duration calculation). This crashes when events exist.

2. **Fix `status`/`history` data source** - These commands query legacy `samples` table which daemon no longer populates. Need to update to use `event_samples` or add backward-compatible sample insertion.

### Medium Priority (Missing Core Features)

3. **Learning mode** - Make `config.learning_mode` actually do something in daemon (suppress alerts, collect calibration data).

4. **Suspects patterns** - Use `config.suspects.patterns` to flag known-problematic processes in forensics output.

5. **Sudoers/tailspin setup** - `install` command should configure sudoers rules and enable tailspin for privileged forensics operations.

6. **`calibrate` command** - Implement CLI command to analyze learning data and suggest threshold values.

7. **Event directory cleanup** - `prune_old_data()` should also delete orphaned event directories.

### Low Priority (Nice to Have)

8. **TUI history view** - Events screen is done, history view remains as stub notification.

9. **SIGHUP config reload** - Nice to have for live tuning without daemon restart.

10. **`history --at` option** - Query what was happening at a specific point in time.

11. **Daemon state persistence** - Persist `io_baseline` and `last_sample_id` across daemon restarts.

## Verification Commands

```bash
# Check for explicit stubs/TODOs
grep -rn "TODO\|FIXME\|not.*implemented" src/pause_monitor/

# Verify status command crash (if events exist in DB)
uv run pause-monitor status

# Check learning_mode usage in daemon (should be empty)
grep -rn "learning_mode" src/pause_monitor/daemon.py

# Check suspects.patterns usage (should only be in config.py)
grep -rn "suspects\|patterns" src/pause_monitor/*.py | grep -v config.py | grep -v __pycache__

# Check legacy samples table population (should be empty)
grep -rn "insert_sample" src/pause_monitor/daemon.py

# Check event directory cleanup (should find nothing)
grep -rn "rmtree\|shutil" src/pause_monitor/storage.py

# List CLI commands that exist
uv run pause-monitor --help
```
