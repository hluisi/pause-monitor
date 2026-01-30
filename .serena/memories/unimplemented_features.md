# Unimplemented Features

> **Phase 8 COMPLETE (2026-01-29).** Per-process band tracking implemented. SCHEMA_VERSION=9.

**Last audited:** 2026-01-29

## Summary

Core functionality is complete. Per-process band tracking with `ProcessTracker`, `BandsConfig`, and `process_events`/`process_snapshots` tables all implemented and wired into daemon. No explicit stubs remain in source code. Remaining gaps are config options that exist but aren't used, and install/setup automation.

---

## Explicit Stubs

| Location | Current | Expected |
|----------|---------|----------|
| (none) | - | - |

No stubs found. TUI history stub removed.

---

## Config Defined But Not Used

| Config Key | Where | What Should Happen |
|------------|-------|-------------------|
| `learning_mode` | config.py:240 | Daemon should suppress alerts, collect calibration data |
| `suspects.patterns` | config.py:42-51 | Flag matching processes in forensics output or TUI |
| `sampling.normal_interval` | config.py:12 | Legacy - daemon uses 1Hz TopCollector now |
| `sampling.elevated_interval` | config.py:13 | Legacy - daemon uses 1Hz TopCollector now |

---

## Missing CLI Features

| Feature | Status |
|---------|--------|
| `calibrate` command | Does not exist |
| `history --at "<time>"` | Option not implemented (basic history works) |

---

## Install Process Gaps

| Step | Status |
|------|--------|
| Sudoers rules generation | Not implemented (forensics needs sudo) |
| `tailspin enable` | Not called during install |

---

## Other Missing Features

| Feature | Status |
|---------|--------|
| SIGHUP config reload | Only SIGTERM/SIGINT handled |
| Event directory cleanup on prune | Prune deletes DB records but not forensics artifacts in events_dir |

---

## Deleted Components

These are mentioned in old docs but no longer exist:

| Component | Status |
|-----------|--------|
| `sentinel.py` | DELETED - no TierManager |
| `stress.py` | DELETED |
| `TierManager` class | DELETED |

---

## Priority

### High (Blocking/Core)
(none - core features complete)

### Medium (Nice to Have)
1. **learning_mode implementation** - Config exists, daemon should respect it
2. **suspects.patterns usage** - Highlight known-problematic processes
3. **Event directory cleanup** - Prevent orphan forensics accumulation
4. **Install sudoers setup** - Forensics needs passwordless sudo

### Low (Tech Debt)
5. **Remove legacy sampling config** - normal_interval/elevated_interval unused
6. **SIGHUP config reload** - Hot reloading would be nice
7. **calibrate command** - Auto-tune thresholds based on system profile
