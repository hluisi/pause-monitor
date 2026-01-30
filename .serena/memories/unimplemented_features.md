# Unimplemented Features

**Last audited:** 2026-01-30

## Summary

Core functionality is complete. LibprocCollector implemented. Remaining items are nice-to-have features and cleanup.

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

### Medium (Nice to Have)
1. **learning_mode implementation** — Config exists, daemon should respect it
2. **suspects.patterns usage** — Highlight known-problematic processes
3. **Event directory cleanup** — Prevent orphan forensics accumulation
4. **Install sudoers setup** — Forensics needs passwordless sudo

### Low (Tech Debt)
5. **Remove legacy sampling config** — normal_interval/elevated_interval unused
6. **SIGHUP config reload** — Hot reloading would be nice
7. **calibrate command** — Auto-tune thresholds based on system profile
