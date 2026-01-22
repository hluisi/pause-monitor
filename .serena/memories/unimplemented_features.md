# Unimplemented Features and Stubs

**Last audited:** 2026-01-21

## Recently Implemented

| Feature | Implementation |
|---------|----------------|
| **Ring Buffer** | `ringbuffer.py` - Stores last 30s of stress samples and process snapshots |
| **Sentinel Monitoring** | `sentinel.py` - Fast 100ms loop for pause detection with tiered monitoring |
| **Tier State Machine** | `sentinel.py:TierManager` - SENTINEL/ELEVATED/CRITICAL transitions |
| **Culprit Identification** | `forensics.py:identify_culprits()` - Maps stress factors to processes from ring buffer |
| **Event Status Management** | CLI `events mark` command and TUI screens for reviewed/pinned/dismissed |
| **GPU Stress Factor** | Added to StressBreakdown and database schema |
| **Wakeups Stress Factor** | Added to StressBreakdown and database schema |
| **TUI Events Screen** | Full event listing with filtering and status management |
| **Sentinel Integration** | `daemon.py` integrates Sentinel for pause detection callbacks |

## Explicit Stubs

| Location | Current Behavior | Expected Behavior |
|----------|------------------|-------------------|
| `sentinel.py:241-242` `_slow_loop()` | `# TODO: Collect GPU/wakeups/thermal via powermetrics` - only sleeps | Collect GPU/wakeups/thermal via powermetrics streaming and cache for fast loop |
| `tui/app.py:525` `action_show_history()` | `self.notify("History view not yet implemented")` | Navigate to history view with charts/graphs showing stress trends |
| `collector.py:126-129` `_get_io_counters()` | Returns `(0, 0)` with comment "Placeholder" | Parse IOKit or ioreg for actual disk I/O bytes read/written |
| `collector.py:132-135` `_get_network_counters()` | Returns `(0, 0)` with comment "Placeholder" | Parse netstat for actual network bytes sent/received |

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

4. **Sentinel slow loop implementation** - Currently a stub. Should collect GPU/wakeups/thermal and cache for fast loop stress calculation.

### Medium Priority

5. **Learning mode** - Make `config.learning_mode` actually do something in daemon (suppress alerts, collect calibration data).

6. **Suspects patterns** - Use `config.suspects.patterns` to flag known-problematic processes in forensics output.

7. **I/O and network counters** - Replace placeholder functions with actual IOKit/netstat implementations.

8. **`calibrate` command** - Implement CLI command to analyze learning data and suggest threshold values.

### Low Priority

9. **TUI history view** - Events screen is done, history view remains as stub notification.

10. **SIGHUP config reload** - Nice to have for live tuning without daemon restart.

11. **`history --at` option** - Query what was happening at a specific point in time.

12. **Daemon state persistence** - Persist `io_baseline` and `last_sample_id` across daemon restarts.

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
