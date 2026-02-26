---
id: audit-config-2026
type: audit
domain: project
subject: config-2026
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [config_audit_2026]
tags: []
related: []
sources: []
---

# Configuration Options Audit

**Audit Date:** 2026-01-29
**Auditor:** Claude (Opus 4.5)

## Summary

- **Total config options:** 54 (was 57, 3 forensics timeouts removed)
- **✓ Valid (actively used):** 51
- **⚠️ Unused/Dead:** 3
- **Removed (2026-01-29):** `spindump_timeout`, `tailspin_timeout`, `logs_timeout`

---

## ✓ VALID CONFIG OPTIONS

### RetentionConfig
| Option | Status | Location Used |
|--------|--------|---------------|
| `events_days` | ✓ Valid | `daemon.py:316`, `cli.py:382,396`, `storage.py:271` |

### ForensicsConfig
| Option | Status | Notes |
|--------|--------|-------|
| ~~`spindump_timeout`~~ | **REMOVED** | Timeouts removed 2026-01-29 |
| ~~`tailspin_timeout`~~ | **REMOVED** | Forensic captures now run to completion |
| ~~`logs_timeout`~~ | **REMOVED** | Ensures data capture during system stress |

### SentinelConfig
| Option | Status | Location Used |
|--------|--------|---------------|
| `ring_buffer_seconds` | ✓ Valid | `daemon.py:54` (sets ring buffer capacity) |
| `sample_interval_ms` | ⚠️ **UNUSED** | Only logged at `daemon.py:160`, never controls timing |

### BandsConfig
| Option | Status | Location Used |
|--------|--------|---------------|
| `medium` | ✓ Valid | Via `get_band()` in `tracker.py:122,203,207` |
| `elevated` | ✓ Valid | Via `get_band()` + direct use `daemon.py:386` |
| `high` | ✓ Valid | Via `get_band()` in `tracker.py` |
| `critical` | ✓ Valid | Via `get_band()` in `tracker.py` |
| `tracking_band` | ✓ Valid | Derives `tracking_threshold` used in `tracker.py:84` |
| `forensics_band` | ⚠️ **UNUSED** | Never checked; forensics hardcoded to "high"/"critical" |
| `checkpoint_interval` | ✓ Valid | `tracker.py:106-108` |
| `forensics_cooldown` | ⚠️ **UNUSED** | Never checked anywhere |

### ScoringConfig.weights (ScoringWeights)
All 8 options are ✓ **Valid** - used in `collector.py:271-278`

| Option | Status |
|--------|--------|
| `cpu` | ✓ Valid |
| `state` | ✓ Valid |
| `pageins` | ✓ Valid |
| `mem` | ✓ Valid |
| `cmprs` | ✓ Valid |
| `csw` | ✓ Valid |
| `sysbsd` | ✓ Valid |
| `threads` | ✓ Valid |

### ScoringConfig.normalization (NormalizationConfig)
All 7 options are ✓ **Valid** - used in `collector.py:259-267`

| Option | Status |
|--------|--------|
| `cpu` | ✓ Valid |
| `mem_gb` | ✓ Valid |
| `cmprs_gb` | ✓ Valid |
| `pageins` | ✓ Valid |
| `csw` | ✓ Valid |
| `sysbsd` | ✓ Valid |
| `threads` | ✓ Valid |

### ScoringConfig.state_multipliers (StateMultipliers)
All 7 options are ✓ **Valid** - accessible via `.get()` method at `collector.py:282`

| Option | Status |
|--------|--------|
| `idle` | ✓ Valid |
| `sleeping` | ✓ Valid |
| `stopped` | ✓ Valid |
| `halted` | ✓ Valid |
| `zombie` | ✓ Valid |
| `running` | ✓ Valid |
| `stuck` | ✓ Valid |

### RogueSelectionConfig (CategorySelection per category)
All category configs (cpu, mem, cmprs, threads, csw, sysbsd, pageins) use all 3 fields:

| Field | Status | Location Used |
|-------|--------|---------------|
| `enabled` | ✓ Valid | `collector.py:223` |
| `count` | ✓ Valid | `collector.py:229` |
| `threshold` | ✓ Valid | `collector.py:226` |

### RogueSelectionConfig.state (StateSelection)
| Option | Status | Location Used |
|--------|--------|---------------|
| `enabled` | ✓ Valid | `collector.py:199` |
| `count` | ✓ Valid | `collector.py:203` |
| `states` | ✓ Valid | `collector.py:201` |

---

## ⚠️ UNUSED CONFIG OPTIONS (Dead Code)

### 1. `sentinel.sample_interval_ms`
- **Definition:** `config.py:30`
- **Problem:** Only logged at daemon startup (`daemon.py:160`), never actually used to control sample timing
- **Reality:** Sample interval is controlled by how long `top -l 2 -s 1` takes (~1 second)
- **Recommendation:** Either implement adaptive timing or remove this config and document that sampling is ~1Hz

### 2. `bands.forensics_band`
- **Definition:** `config.py:46`
- **Problem:** The `forensics_threshold` property exists but is never called
- **Location of hardcoding:** `tracker.py:151` and `tracker.py:223` check `band in ("high", "critical")` directly
- **Recommendation:** Replace hardcoded check with `band in (self.bands.forensics_band, "critical")` or remove config

### 3. `bands.forensics_cooldown`
- **Definition:** `config.py:48`
- **Problem:** No cooldown logic exists anywhere in the codebase
- **Expected behavior:** Should prevent forensics from triggering more often than every N seconds
- **Recommendation:** Implement cooldown tracking in `ProcessTracker` or remove this config

---

## Notes

- The `forensics_threshold` property at `config.py:81-83` is tested but never called in production code
- The CLI `config show` command displays all options, including the unused ones, which may confuse users
