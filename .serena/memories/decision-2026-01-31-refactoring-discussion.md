---
id: decision-2026-01-31-refactoring-discussion
type: decision
domain: project
subject: 2026-01-31-refactoring-discussion
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [refactoring_discussion_2026-01-31]
tags: []
related: []
sources: []
---

# Refactoring Discussion - 2026-01-31

Captured discussion about potential improvements to scoring, data structures, and thresholds.

## Bugs Fixed This Session

### 1. Rate Calculation Bug (FIXED)
**Location:** `collector.py:498`
**Problem:** `wall_delta_sec` was computed after `_last_collect_time` was updated, resulting in `start - start = 0`. All rate calculations returned 0.
**Fix:** Derive `wall_delta_sec` from `wall_delta_ns` (computed before the update).

### 2. Empty TUI Due to Threshold Filtering (FIXED)
**Location:** `collector.py:_select_rogues()`
**Problem:** Only processes above `score_threshold` were included in rogues. If nothing scored above 20, TUI showed nothing.
**Fix:** Changed to always return top N by score, regardless of threshold. ProcessTracker independently applies its threshold for persistence.

## Pending Improvements to Evaluate

### Global Stress Score Algorithm
**Current:** `max(peak, RMS)` from top N rogues using unenriched `.high` values
**Proposed:** Peak from ALL scored processes using `.current` values
**Rationale:** 
- Considers all 500 processes, not just selection
- Uses current reality, not stale values
- Simple and intuitive: "system stress = worst offender's score"

### MetricValue Structure (current/low/high)
**Question:** Is the low/high enrichment valuable for forensics?
**Analysis:**
- Low/high shows volatility but lacks timestamps (when did high occur?)
- Forensic snapshots already capture point-in-time values
- Buffer context captures full 30-second history
- Adds complexity: enrichment process, 3x storage, source of bugs

**Options:**
1. Keep for TUI display only (shows volatility at a glance)
2. Remove entirely - snapshots + buffer provide forensic context
3. Replace with trend indicator (rising/falling/stable)

**Decision:** Deferred - evaluate after observing real usage

### Normalization Thresholds
**Potentially too high (causing low scores):**
| Metric | Current | Consider |
|--------|---------|----------|
| `pageins_rate` | 100/s | 25-50/s |
| `disk_io_rate` | 100 MB/s | 50 MB/s |
| `csw_rate` | 10k/s | 2-5k/s |
| `wakeups_rate` | 1k/s | 200-500/s |
| `mem_gb` | 8 GB | 4 GB |

**Recommendation:** Run app, observe real values, then tune based on data rather than guesswork.

### Category Weights
**Current:** Blocking 40%, Contention 30%, Pressure 20%, Efficiency 10%
**Assessment:** Appropriate for rogue hunting
- Blocking + Contention (70%) = "hurting others" - should score highest
- Pressure + Efficiency (30%) = "resource hog" - secondary concern

**Decision:** Keep current weights

### RogueSelectionConfig.score_threshold
**Status:** Now unused by collector (after _select_rogues fix)
**Action:** Consider removing from config, or repurpose for something else

## Architecture Insights

### Separation of Concerns (Improved)
- **Collector:** "What are the top N most interesting processes?" (display)
- **ProcessTracker:** "Which processes are stressed enough to track?" (persistence)
- **Ring Buffer:** "What's the recent history of top processes?" (forensic context)

### Data Flow
1. Collector scores ALL ~500 processes
2. Selects top N by score (no threshold filtering)
3. Ring buffer stores samples (always has data)
4. ProcessTracker applies its own threshold for persistence
5. TUI always has something to display

## Next Steps (When Resuming)
1. Implement peak-from-all-processes for global stress score
2. Run app and observe real metric values
3. Tune normalization thresholds based on real data
4. Evaluate MetricValue usefulness with real forensic analysis
