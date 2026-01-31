# Unimplemented Features

**Last audited:** 2026-01-31

## Summary

**Rogue Hunter** core functionality is complete. LibprocCollector scores all processes on 4 dimensions (blocking/contention/pressure/efficiency), ProcessTracker manages event lifecycle, and forensics capture works. No stubs exist in the codebase.

**Recent fixes (2026-01-31):**
- Rate calculation bug fixed (all rates were 0)
- Rogue selection now always returns top N by score (TUI never empty)

**Pending evaluation:** See `refactoring_discussion_2026-01-31` for:
- Global stress score algorithm (peak from ALL vs top N)
- MetricValue (current/low/high) usefulness
- Normalization threshold tuning

---

## Explicit Stubs

| Location | Current | Expected |
|----------|---------|----------|
| (none) | - | - |

No stubs found. All `pass` statements are in Click groups or custom exceptions (idiomatic Python).

---

## Config Defined But Not Used

| Config Key | Where | What Should Happen |
|------------|-------|-------------------|
| `system.sample_interval` | `config.py:21` | Used in daemon, but `config show` doesn't display it |
| `system.forensics_debounce` | `config.py:22` | Used in daemon, but `config show` doesn't display it |
| `bands.checkpoint_interval` | `config.py:37` | Used in tracker, but `config show` doesn't display it |

**Impact:** Users running `pause-monitor config show` see an incomplete config. Low severity since users can read the TOML file directly.

---

## Design Spec Discrepancies

| Design Spec Item | Actual Implementation | Notes |
|------------------|----------------------|-------|
| Band thresholds in design spec | Different defaults | Design spec shows `medium=40, elevated=60, high=80, critical=100`. Code uses `medium=20, elevated=40, high=50, critical=70`. Intentional — lower thresholds catch more events. |
| `calibrate` command | Does not exist | Mentioned in old spec as low priority. Manual config editing works fine. |

---

## TUI Hardcoded Values

| Location | Issue |
|----------|-------|
| `tui/app.py:25` | `TRACKING_THRESHOLD = 40` is hardcoded at module level |
| `tui/app.py:30-32` | `get_tier_name()` uses hardcoded thresholds (80 for CRITICAL) |
| `tui/app.py:222-224` | `watch_score()` uses hardcoded thresholds for CSS classes |
| `tui/app.py:622` | `update_tracking()` uses `TRACKING_THRESHOLD` constant |

**Impact:** If user changes `bands.elevated` in config, the daemon will track at the new threshold but the TUI display logic still uses 40. This could cause confusion where the TUI shows a process as "NORMAL" but the daemon is tracking it.

**Fix:** The TUI has `self.config` available — the hardcoded constant should use `self.config.bands.tracking_threshold` (requires passing config to helper functions or making them methods).

---

## Missing CLI Features

| Feature | Status |
|---------|--------|
| `calibrate` command | Not implemented (low priority, manual config editing suffices) |

---

## Other Gaps

| Feature | Status |
|---------|--------|
| SIGHUP config reload | Only SIGTERM/SIGINT handled in daemon. Hot reloading requires daemon restart. |
| Event directory cleanup on prune | Prune deletes DB records but forensics artifacts go to `/tmp/pause-monitor/` and are deleted after processing. No orphan cleanup issue exists. |

---

## Priority

### Medium (Nice to Have)
1. **TUI threshold sync** - Use config values instead of hardcoded `TRACKING_THRESHOLD` in `tui/app.py`
2. **`config show` completeness** - Display `sample_interval`, `forensics_debounce`, and `checkpoint_interval`

### Low (Tech Debt)
3. **SIGHUP config reload** - Hot reloading config without daemon restart
4. **`calibrate` command** - Auto-tune thresholds based on system profile (manual config editing works)

---

## Resolved Since Last Audit

These items from the 2026-01-30 audit are now complete:

- **Sudoers setup** - `_setup_sudoers()` implemented in install command
- **`tailspin enable`** - Called during install
- **`forensics_debounce` config** - Now in `SystemConfig`, used by daemon
- **Live spindump removal** - Forensics only uses tailspin (decoded via `spindump -i`)
