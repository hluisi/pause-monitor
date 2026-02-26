---
id: audit-issue-finder-iss-015
type: audit
domain: project
subject: issue-finder-iss-015
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [issue-finder-ISS-015]
tags: []
related: []
sources: []
---

# ISS-015: Unused NormalizationConfig and config fields

**Category:** Unnecessary Code
**All Categories:** Unnecessary Code, YAGNI
**Severity:** Important
**Status:** resolved
**Created:** 2026-02-02T12:00:00Z
**Last validated:** 2026-02-02T12:00:00Z

## Grouped Findings

This issue contains 4 related findings:

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Unnecessary Code | config.py | 141-170 | NormalizationConfig | Class docstring admits "Not currently used" |
| 2 | Unnecessary Code | config.py | 53 | checkpoint_interval | Comment: "Deprecated: kept for backwards compatibility" |
| 3 | Unnecessary Code | config.py | 192-194 | active_min_* | Config fields never referenced in source |
| 4 | Unnecessary Code | config.py | 213 | score_threshold | Config field never used after refactoring |

## Investigation

### Trace Up (what depends on this code)

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| ScoringConfig.normalization | config.py:189 | Contains | Field holds NormalizationConfig instance |
| _load_scoring_config() | config.py:611 | Loads | Parses TOML into NormalizationConfig |
| BandsConfig.checkpoint_interval | config.py:53 | Contains | Field in BandsConfig |
| ScoringConfig.active_min_* | config.py:192-194 | Contains | Fields in ScoringConfig |
| RogueSelectionConfig.score_threshold | config.py:213 | Contains | Field in RogueSelectionConfig |
| test_config.py | tests | Tests | Various tests verify loading |

### Trace Down (what this code depends on)

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| dataclass | stdlib | Import | All configs are dataclasses |

### Related Patterns

- `refactoring_discussion_2026-01-31` memory explicitly states `score_threshold` is "Now unused by collector (after _select_rogues fix)"
- Similar unused fields pattern across ScoringConfig
- Project philosophy says "Delete, don't deprecate"

## Root Cause

These fields represent **vestigial code from design evolution**:

1. **NormalizationConfig**: Created for a scoring system that was replaced by disproportionate-share scoring. Docstring admits it's "retained for potential custom scoring implementations" - speculative future use (YAGNI)

2. **checkpoint_interval**: Replaced by sample-based checkpoint intervals (`medium_checkpoint_samples`, `elevated_checkpoint_samples`). Comment says "kept for backwards compatibility" but project philosophy says "Delete, don't deprecate"

3. **active_min_* fields**: Appear in design plan for counting "active" processes but implementation never used them

4. **score_threshold**: Bug fix changed `_select_rogues()` from threshold-filtering to top-N selection. The threshold became unused but was never removed

## Suggestions

1. **Delete NormalizationConfig class entirely** (lines 140-176)
   - Remove from `ScoringConfig` field definition (line 189)
   - Remove from `_load_scoring_config()` (lines 611-627)
   - Delete tests referencing it

2. **Delete checkpoint_interval field** (line 53)
   - Remove from `BandsConfig` dataclass
   - Remove from `_load_bands_config()`

3. **Delete active_min_cpu, active_min_memory_mb, active_min_disk_io** (lines 192-194)
   - Remove from `ScoringConfig` dataclass
   - Remove from `_load_scoring_config()`

4. **Delete score_threshold** (line 213)
   - Remove from `RogueSelectionConfig` dataclass
   - Remove from `_load_rogue_selection_config()`

## Resolution

**Resolved:** 2026-02-02

All four findings deleted:
1. `NormalizationConfig` class removed (37 lines)
2. `checkpoint_interval` field removed from `BandsConfig`
3. `active_min_*` fields removed from `ScoringConfig`
4. `score_threshold` field removed from `RogueSelectionConfig`

Also updated:
- `_load_bands_config()` — removed checkpoint_interval parsing
- `_load_scoring_config()` — removed normalization and active_min_* parsing
- `_load_rogue_selection_config()` — removed score_threshold parsing
- 8 tests deleted from test_config.py
- Import of NormalizationConfig removed from test_config.py

All 375 tests pass.
