# Unimplemented Features

**Last audited:** 2026-01-30

## Summary

Core functionality is complete. LibprocCollector, ProcessTracker, and all major design spec components are implemented. The codebase has no stubs. Remaining items are minor spec discrepancies and nice-to-have features.

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
| (none) | - | All config fields are actively used |

---

## Design Spec Discrepancies

| Design Spec Item | Actual Implementation | Notes |
|------------------|----------------------|-------|
| `BandsConfig.low = 20` | Not in config | Design spec shows this, but implementation uses implicit "low" band (score < medium). Harmless - band logic works correctly via `get_band()` method. |
| `BandsConfig.forensics_cooldown = 60` | Not in config | Design spec mentions this, but forensics cooldown is not configurable. Hardcoded behavior exists in daemon. |

---

## Missing CLI Features

| Feature | Status |
|---------|--------|
| `calibrate` command | Does not exist (mentioned in old spec, low priority) |

---

## Install Process Gaps

| Step | Status |
|------|--------|
| Sudoers rule for `tailspin save` | Not implemented |
| `tailspin enable` | Not called during install |
| Remove live spindump capture | Code still attempts `spindump -notarget` |

**Required sudoers rule** (`/etc/sudoers.d/pause-monitor`):
```bash
<user> ALL = (root) NOPASSWD: /usr/bin/tailspin save -o /Users/<user>/.local/share/pause-monitor/events/*
```

**Context:** Only `tailspin save` needs sudo. The rule is intentionally narrow — tailspin can only write to the events directory. Live spindump should be removed from forensics.py (tailspin decode provides the same data from during-the-pause, not after).

---

## Other Missing Features

| Feature | Status |
|---------|--------|
| SIGHUP config reload | Only SIGTERM/SIGINT handled in daemon |
| Event directory cleanup on prune | Prune deletes DB records but not forensics artifacts in events_dir |

---

## Priority

### Medium (Nice to Have)
1. **Event directory cleanup** - Prune should also delete orphan forensics files in `~/.local/share/pause-monitor/events/`
2. **Forensics cooldown config** - Make cooldown configurable via BandsConfig
3. **Install sudoers setup** - Add sudoers rule for `tailspin save` during install

### Low (Tech Debt)
4. **SIGHUP config reload** - Hot reloading config without daemon restart
5. **Calibrate command** - Auto-tune thresholds based on system profile (low value, manual config editing works)
