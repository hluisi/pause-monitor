# Unimplemented Features

> **Per-Process Scoring COMPLETE (2026-01-24).** TopCollector at 1Hz; SCHEMA_VERSION=7.
> **Per-Process Band Tracking DESIGNED (2026-01-25).** Not yet implemented.

**Last audited:** 2026-01-25

## Summary

Phase 7 (per-process scoring) is complete. The new per-process band tracking design exists but is not implemented. Several older features remain unimplemented.

---

## Explicit Stubs

| Location | Current | Expected |
|----------|---------|----------|
| `tui/app.py:788` | `self.notify("History view not yet implemented")` | TUI history view with charts/graphs |

---

## Per-Process Band Tracking (DESIGNED, NOT IMPLEMENTED)

Design doc: `docs/plans/2026-01-25-per-process-band-tracking-design.md`

| Component | Status |
|-----------|--------|
| `BandsConfig` class | ❌ Not in config.py |
| `[bands]` TOML section | ❌ Not implemented |
| `process_events` table | ❌ Not in schema |
| `process_snapshots` table | ❌ Not in schema |
| Per-process event lifecycle | ❌ Not in daemon |
| Boot time tracking | ❌ Not implemented |

---

## Config Defined But Not Used

| Config Key | Where | What Should Happen |
|------------|-------|-------------------|
| `learning_mode` | config.py | Daemon should suppress alerts, collect calibration data |
| `suspects.patterns` | config.py | Flag matching processes in forensics output |

---

## Missing CLI Features

| Feature | Status |
|---------|--------|
| `calibrate` command | Does not exist |
| `history --at "<time>"` | Option not implemented |

---

## Install Process Gaps

| Step | Status |
|------|--------|
| Sudoers rules generation | Not implemented |
| `tailspin enable` | Not called |
| `check_forensics_health()` | Function doesn't exist |

---

## Other Missing Features

| Feature | Status |
|---------|--------|
| SIGHUP config reload | Only SIGTERM/SIGINT handled |
| Event directory cleanup on prune | Only DB records deleted |
| daemon_state persistence | Only schema_version stored (not io_baseline, last_sample_id) |

---

## Legacy Tables (Orphaned)

| Table | Status |
|-------|--------|
| `samples` | Never used by current code |
| `process_samples` | Schema exists, never populated |
| `event_samples` | Legacy format, unclear if still written |

---

## Priority

### High (Blocking/Core)
1. **Per-process band tracking** — Design approved, needs implementation plan
2. **Learning mode** — Config exists but unused
3. **Suspects patterns** — Config exists but unused
4. **Install setup** — Sudoers/tailspin missing

### Medium (Nice to Have)
5. **TUI history view** — Stub exists
6. **calibrate command** — For threshold learning
7. **Event directory cleanup** — Prevent orphan accumulation
8. **SIGHUP config reload** — Hot reloading

### Low (Tech Debt)
9. **Legacy table cleanup** — Remove unused tables
10. **daemon_state completion** — Persist all state
