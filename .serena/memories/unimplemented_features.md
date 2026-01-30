# Unimplemented Features

> ⚠️ **COLLECTOR REDESIGN PENDING (2026-01-29).** TopCollector to be replaced with LibprocCollector.

**Last audited:** 2026-01-29

## Summary

**Critical:** TopCollector must be replaced with LibprocCollector. Current implementation spawns `top -l 2` every 2 seconds, wastes 50% of samples, and misses many available metrics. See `libproc_and_iokit_research` memory for the correct approach.

---

## High Priority: Collector Replacement

### Problem

TopCollector is fundamentally broken:
1. Spawns subprocess every 2 seconds
2. Uses `top -l 2` which takes TWO samples
3. First sample always has invalid CPU% (no delta)
4. Throws away 50% of collected data
5. Text parsing is fragile and slow
6. Missing metrics: disk I/O, energy, instructions, cycles, wakeups, GPU

### Solution

Replace with LibprocCollector using native macOS APIs:
- `proc_pid_rusage()` — CPU, memory, disk I/O, energy, wakeups
- `proc_pidinfo()` — Context switches, syscalls, threads
- `sysctl` — Process state, listing
- IOKit — Per-process GPU time (optional)

**Benefits:**
- No subprocess spawning
- No text parsing
- More data available
- Lower overhead
- Same APIs that Activity Monitor uses

**See `libproc_and_iokit_research` memory for complete API documentation.**

---

## Explicit Stubs

| Location | Current | Expected |
|----------|---------|----------|
| (none) | - | - |

No stubs found.

---

## Config Defined But Not Used

| Config Key | Where | What Should Happen |
|------------|-------|-------------------|
| `learning_mode` | config.py:240 | Daemon should suppress alerts, collect calibration data |
| `suspects.patterns` | config.py:42-51 | Flag matching processes in forensics output or TUI |
| `sampling.normal_interval` | config.py:12 | Legacy - remove after collector replacement |
| `sampling.elevated_interval` | config.py:13 | Legacy - remove after collector replacement |

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

These no longer exist:
- `sentinel.py` — Deleted
- `stress.py` — Deleted
- `TierManager` class — Deleted
- `PowermetricsStream` — Deleted (was replaced by TopCollector, now TopCollector being replaced)

---

## Priority

### Critical (Blocking)
1. **Replace TopCollector with LibprocCollector** — Current approach is fundamentally wrong

### Medium (Nice to Have)
2. **learning_mode implementation** — Config exists, daemon should respect it
3. **suspects.patterns usage** — Highlight known-problematic processes
4. **Event directory cleanup** — Prevent orphan forensics accumulation
5. **Install sudoers setup** — Forensics needs passwordless sudo

### Low (Tech Debt)
6. **Remove legacy sampling config** — normal_interval/elevated_interval unused
7. **SIGHUP config reload** — Hot reloading would be nice
8. **calibrate command** — Auto-tune thresholds based on system profile
