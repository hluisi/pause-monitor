# Unimplemented Features and Stubs

> **Per-Process Scoring Redesign COMPLETE (2026-01-24).** TopCollector at 1Hz; SCHEMA_VERSION=7.

**Last audited:** 2026-01-24

## Completed (via Redesign)

- ~~Sentinel slow loop~~ - Replaced by Daemon TopCollector integration
- ~~TUI socket streaming~~ - Implemented via SocketServer/SocketClient
- ~~Complete 8-factor stress~~ - All factors now calculated from top output
- ~~Process attribution~~ - Using TopCollector rogue selection with scoring
- ~~Ring Buffer~~ - `ringbuffer.py` stores last 30s of stress samples
- ~~Tier State Machine~~ - `sentinel.py:TierManager` manages tier transitions
- ~~Event Status Management~~ - CLI `events mark` command and TUI EventsScreen
- ~~Socket Server/Client~~ - Real-time data streaming to TUI
- ~~10Hz Main Loop~~ - Replaced by 1Hz TopCollector loop
- ~~`status` command crash~~ - Fixed to use correct Event attributes (start_timestamp, peak_tier, peak_stress)
- ~~`history` command stale data~~ - Now uses events table correctly
- ~~Per-process stressor scoring~~ - TopCollector with 8 weighted factors
- ~~Schema v7~~ - JSON blob storage for ProcessSamples

## Explicit Stubs

| Location | Current Behavior | Expected Behavior |
|----------|------------------|-------------------|
| `tui/app.py:778-780` `action_show_history()` | `self.notify("History view not yet implemented")` | Navigate to history view with charts/graphs showing stress trends |

## Config Defined But Not Used

| Config Key | Where Defined | What Should Happen |
|------------|---------------|-------------------|
| `learning_mode` | `config.py:142` | Daemon should suppress alerts, store all samples, and track pause correlations for calibration. Currently only stored/loaded in config but never checked in daemon logic. |
| `suspects.patterns` | `config.py:46-57` | Should flag processes matching patterns as suspects in forensics and culprit identification. Pattern list exists but is never searched against process names in daemon.py or forensics.py. |

## Database Tables Status

| Table | Status | Notes |
|-------|--------|-------|
| `events` | **Active** | Tier-based event storage (start/end timestamps, peak_tier) |
| `process_sample_records` | **Active (v7)** | JSON blob storage for ProcessSamples during escalation |
| `event_samples` | **Active (legacy format)** | Powermetrics-style samples with stress breakdown - may be unused with new TopCollector |
| `samples` | **Legacy/Orphaned** | Not written or read by current code |
| `process_samples` | **Legacy/Unused** | Schema exists, never populated |
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
|-------|--------|------------|
| `samples` table orphaned | Table exists, never used | Either delete table in future schema migration or document as legacy |
| `process_samples` table unused | Table exists, never populated | Either delete table in future schema migration or document as legacy |
| `event_samples` table potentially orphaned | May not be used with new TopCollector/ProcessSamples approach | Audit daemon.py to confirm whether still written |

## Priority Recommendations

### High Priority (Missing Core Features)

1. **Learning mode** - Make `config.learning_mode` actually do something in daemon (suppress alerts, collect calibration data).

2. **Suspects patterns** - Use `config.suspects.patterns` to flag known-problematic processes in forensics output.

3. **Sudoers/tailspin setup** - `install` command should configure sudoers rules and enable tailspin for privileged forensics operations.

4. **`calibrate` command** - Implement CLI command to analyze learning data and suggest threshold values.

5. **Event directory cleanup** - `prune_old_data()` should also delete orphaned event directories.

### Medium Priority (Nice to Have)

6. **TUI history view** - Events screen is done, history view remains as stub notification.

7. **SIGHUP config reload** - Nice to have for live tuning without daemon restart.

8. **`history --at` option** - Query what was happening at a specific point in time.

9. **Daemon state persistence** - Persist `io_baseline` and `last_sample_id` across daemon restarts.

10. **`check_forensics_health()`** - Verify forensics tools are available and working during install.

### Low Priority (Tech Debt)

11. **Legacy table cleanup** - Remove `samples` and `process_samples` tables in future schema migration if confirmed unused.

12. **Audit `event_samples` usage** - Determine if still written with new TopCollector approach.

## Verification Commands

```bash
# Check for explicit stubs/TODOs
grep -rn "TODO\|FIXME\|not.*implemented" src/pause_monitor/

# Check learning_mode usage in daemon (should be empty)
grep -rn "learning_mode" src/pause_monitor/daemon.py

# Check suspects.patterns usage (should only be in config.py)
grep -rn "suspects\|patterns" src/pause_monitor/*.py | grep -v config.py | grep -v __pycache__

# Check event directory cleanup (should find nothing)
grep -rn "rmtree\|shutil" src/pause_monitor/storage.py

# List CLI commands that exist
uv run pause-monitor --help

# Check schema version
sqlite3 ~/.local/share/pause-monitor/data.db "SELECT value FROM daemon_state WHERE key='schema_version'"
```
