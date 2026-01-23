# Unimplemented Features and Stubs

> **Phase 5 COMPLETE (2026-01-22).** Redesign eliminated Sentinel; daemon now uses powermetrics directly.

**Last audited:** 2026-01-22

## Completed (via Redesign)

- ~~Sentinel slow loop~~ - Replaced by Daemon powermetrics integration
- ~~TUI socket streaming~~ - Implemented via SocketServer/SocketClient
- ~~Complete 8-factor stress (including pageins)~~ - All factors now calculated from powermetrics
- ~~Process attribution~~ - Using powermetrics top_cpu_processes + top_pagein_processes

## Recently Implemented

| Feature | Implementation |
|---------|----------------|
| **Ring Buffer** | `ringbuffer.py` - Stores last 30s of stress samples and process snapshots |
| **Tier State Machine** | `sentinel.py:TierManager` - SENTINEL/ELEVATED/CRITICAL transitions (extracted, Sentinel class deleted) |
| **Culprit Identification** | `forensics.py:identify_culprits()` - Maps stress factors to processes from ring buffer |
| **Event Status Management** | CLI `events mark` command and TUI screens for reviewed/pinned/dismissed |
| **GPU Stress Factor** | Added to StressBreakdown and database schema |
| **Wakeups Stress Factor** | Added to StressBreakdown and database schema |
| **TUI Events Screen** | Full event listing with filtering and status management |
| **Socket Server** | `socket_server.py:SocketServer` - Broadcasts ring buffer to TUI via Unix socket |
| **Socket Client** | `socket_client.py:SocketClient` - TUI receives real-time data from daemon |
| **10Hz Main Loop** | `daemon.py:Daemon._main_loop()` - Single 100ms loop driven by powermetrics stream |

### Phase 5 Complete (2026-01-22)

| Feature | Implementation |
|---------|----------------|
| **TierAction Enum** | `sentinel.py:TierAction` - tier state machine actions |
| **8-Factor Stress** | `stress.py:StressBreakdown` includes all 8 factors including `pageins` |
| **Daemon._calculate_stress()** | Calculates all 8 stress factors from PowermetricsResult |
| **Daemon._handle_tier_action()** | Handles TierAction transitions, writes bookmarks on tier2_exit |
| **Daemon._main_loop()** | Single 10Hz loop processing powermetrics stream |
| **Daemon._handle_pause()** | Pause detection with forensics capture |
| **Daemon._maybe_update_peak()** | Peak stress tracking during elevated/critical tiers |
| **Event.peak_stress** | `storage.py:Event` tracks peak stress during event |
| **SCHEMA_VERSION=5** | Schema updated for stress_pageins column |

### Deleted (No Longer Exists)

| Component | Replacement |
|-----------|-------------|
| `Sentinel` class | Deleted entirely, use `TierManager` directly |
| `calculate_stress()` function | Deleted, use `Daemon._calculate_stress()` |
| `IOBaselineManager` class | Deleted |
| `SamplePolicy` | Deleted |
| `slow_interval_ms` config | Deleted |

## Explicit Stubs

| Location | Current Behavior | Expected Behavior |
|----------|------------------|-------------------|
| `tui/app.py:525` `action_show_history()` | `self.notify("History view not yet implemented")` | Navigate to history view with charts/graphs showing stress trends |

## Config Defined But Not Used

| Config Key | Where Defined | What Should Happen |
|------------|---------------|-------------------|
| `learning_mode` | `config.py:86` | Daemon should suppress alerts, store all samples, and track pause correlations for calibration. Currently only stored/loaded but never checked in daemon logic. |
| `suspects.patterns` | `config.py:43-57` | Should be used to flag processes matching patterns as suspects in forensics and culprit identification. Pattern list exists but is never searched against process names. |

## Database Tables Not Populated

| Table | Schema Exists | Insert Function | Problem |
|-------|---------------|-----------------|---------|
| `process_samples` | Yes (`storage.py:51-63`) | None exists | Table defined but no code ever inserts rows. Design doc says top 10 CPU + top 5 I/O processes per sample should be captured. |
| `daemon_state` | Yes (`storage.py:88-93`) | Only in `init_database` | Only `schema_version` is stored. Design doc specifies `io_baseline` and `last_sample_id` should be persisted across daemon restarts. |

## Design Doc Gaps

Features specified in design_spec but not implemented:

| Feature | Design Doc Section | Current State |
|---------|-------------------|---------------|
| **`calibrate` CLI command** | CLI Commands table | Command does not exist. Should analyze learning mode data and suggest thresholds. |
| **`history --at "<time>"` option** | CLI Commands table | Option not implemented. History command only has `--hours` and `--format`. |
| **Sudoers generation** | Install Process step 5 | `install` command creates launchd plist but no sudoers rules for privileged operations. |
| **tailspin enable** | Install Process step 6 | `install` does not call `tailspin enable`. |
| **SIGHUP config reload** | Design Decisions | No signal handler in daemon for hot-reloading config. |
| **Per-process I/O capture** | process_samples table | PowermetricsStream uses minimal samplers, does not request `--show-process-io`. |
| **ForensicsHealth check** | Install Process step 8 | `check_forensics_health()` not implemented. |
| **Responsible PID mapping** | "Maps XPC helper activity to parent app" | Not parsed from powermetrics plist. |
| **Energy impact sorting** | "processes sorted by Apple's composite energy score" | Not implemented in process capture. |

## Code Review Deferred Items

| Source | Issue | Rationale for Deferral |
|--------|-------|----------------------|
| Task 11 review | Event directory cleanup on prune | Pre-existing gap - `prune_old_data` doesn't delete orphaned event directories. Would need to track event_dir paths and rmtree them. |

## Priority Recommendations

### High Priority

1. **Per-process data capture** - Need to add `tasks,disk` samplers to PowermetricsStream and parse per-process metrics from plist. This is essential for meaningful culprit identification.

2. **Sudoers/tailspin setup** - Forensics requires privileged access. Install command should configure sudoers rules and enable tailspin.

3. **Process samples insertion** - Schema exists but data is never captured. Need `insert_process_sample()` function and daemon logic to call it.

### Medium Priority

4. **Learning mode** - Make `config.learning_mode` actually do something in daemon (suppress alerts, collect calibration data).

5. **Suspects patterns** - Use `config.suspects.patterns` to flag known-problematic processes in forensics output.

6. **`calibrate` command** - Implement CLI command to analyze learning data and suggest threshold values.

### Low Priority

7. **TUI history view** - Events screen is done, history view remains as stub notification.

8. **SIGHUP config reload** - Nice to have for live tuning without daemon restart.

9. **`history --at` option** - Query what was happening at a specific point in time.

10. **Daemon state persistence** - Persist `io_baseline` and `last_sample_id` across daemon restarts.

## Verification Commands

```bash
# Check for explicit stubs/TODOs
grep -rn "TODO\|FIXME\|placeholder\|not.*implemented" src/pause_monitor/

# Check learning_mode usage in daemon
grep -rn "learning_mode" src/pause_monitor/daemon.py

# Check suspects.patterns usage (should appear outside config.py)
grep -rn "suspects\|patterns" src/pause_monitor/*.py | grep -v config.py | grep -v __pycache__

# Check process_samples insertions
grep -rn "INSERT INTO process_samples" src/

# Check powermetrics samplers being used
grep -rn "samplers\|show-process" src/pause_monitor/collector.py

# List all CLI commands
uv run pause-monitor --help
```
