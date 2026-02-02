# Scoring System Redesign

## Y-Statement Summary

**In the context of** a personal macOS process monitoring tool that needs to identify resource-hogging processes,

**facing** a scoring system with single-core assumptions that causes all scores to cluster in one band (40-44), several always-zero metrics that contribute nothing, and no graduated response as process severity increases,

**we decided for** a disproportionate-share scoring model using Apple-style resource weighting, where each process is scored based on how many multiples of "fair share" it consumes across CPU, GPU, memory, disk I/O, and wakeups, with graduated forensics that ramp up as processes move through bands,

**to achieve** scores that spread meaningfully across all bands, accurate identification of resource hogs regardless of hardware capacity, and appropriate data capture at each severity level,

**accepting** that the formula will need tuning based on observed behavior, and that some currently-collected metrics may be dropped if they provide no signal.

## Problem Statement

The current scoring system fails to identify resource-hogging processes accurately.

**Score compression:** All 60 events in the database fall in the "elevated" band (40-44). The "high" and "critical" bands are unreachable because normalization thresholds assume single-core operation. On a 16-core M4 Max, a process using 800% CPU (50% of machine capacity) maxes out the CPU component instantly, leaving no headroom to differentiate between "busy" and "on fire."

**Dead metrics:** Several metrics are always zero in practice — `qos_interactive`, `pageins_rate`, `gpu_time` (now fixed), `zombie_children`. These contribute nothing but still occupy weight in the formula.

**Backwards signals:** The `runnable_time` metric penalizes processes that are *waiting* for CPU, not *hogging* it. High runnable time indicates a victim of system stress, not a cause.

**No graduated response:** Currently, nothing happens until "elevated" (tracking), then nothing more until "high" (full forensics). There's no middle ground — no lightweight trend capture for moderately concerning processes.

**What happens if we do nothing:** The tool continues to flag everything at the same severity level, making it impossible to distinguish routine activity from genuine resource hogs. Historical data accumulates but provides no insight because all scores look the same.

## Goals

**1. Scores reflect disproportionate resource consumption**

A process using 25% of machine resources among 500 processes is using 125× its fair share. The score should reflect this disproportionality, not the raw percentage. One process dominating should score high; many processes sharing equally should score low.

**2. Scores spread across all bands**

During normal operation, scores should distribute across low, medium, elevated, high, and critical — not cluster in one band. When the system is healthy, most processes should be low/medium. When something is wrong, the culprit should stand out in high/critical.

**3. Hardware-relative normalization**

Thresholds must scale with machine capacity. 800% CPU on a 16-core machine is 50% utilization (moderate). The same on a 2-core machine is 400% utilization (impossible, but illustrates the point). Scores should be comparable regardless of hardware.

**4. Apple-style resource weighting**

Weight resources according to their system impact: GPU time counts more than CPU time, wakeups are penalized for causing system disruption, disk I/O and memory contribute proportionally.

**5. Graduated band behaviors**

Each band triggers progressively more data capture. Low bands get minimal tracking; high bands get full forensics. The response scales with severity.

## Non-Goals

**Not changing band thresholds or names**

The five bands (low, medium, elevated, high, critical) and their score thresholds (0-19, 20-39, 40-49, 50-69, 70-100) remain as-is. The fix is in how scores are calculated, not where the boundaries lie.

**Not predicting pauses before they happen**

This tool identifies resource hogs for post-hoc analysis. It captures data so you can investigate after something goes wrong. Real-time pause prediction would require different instrumentation.

**Not supporting multiple machines**

This is a personal tool for one M4 Max. We will tune coefficients for this hardware. Portability across different Macs is not a concern.

**Not touching anomaly detection in this refactor**

The tool has capabilities for tracking process behavior over time. This refactor focuses solely on fixing the point-in-time scoring formula — anomaly detection and historical baselines are unaffected and out of scope for this change.

**Not adding network I/O**

Apple's coefficients include network packets, but we're not currently collecting network metrics. Adding network monitoring is out of scope for this design.

## Proposed Approach

### Scoring Model: Disproportionate Share with Apple Weighting

Each process receives a score based on two factors:

**1. Fair share calculation**

For each resource type, calculate the process's share of total system usage. Compare this to "fair share" (1 ÷ active process count). A process using 10× its fair share of any resource is notable; 50× is significant; 100×+ is extreme.

**2. Apple-style resource weighting**

Not all resources are equal. Following Apple's energy impact model:
- **CPU time** — baseline weight (1.0)
- **GPU time** — weighted higher (2-4×) because GPU work is intensive
- **Wakeups** — penalized as equivalent CPU time (~200μs per wakeup) because they cause system-wide disruption
- **Disk I/O** — weighted per byte, accumulates at scale
- **Memory** — weighted by proportion of system RAM consumed

Metrics that don't contribute signal (always zero, or measuring victimhood) receive zero or minimal weight in the formula but remain collected for potential future use.

**3. Composite score**

The weighted disproportionality across all resources combines into a single 0-100 score. The formula emphasizes resources where the process is most disproportionate (dominant resource) while still accounting for pressure across multiple dimensions.

### Graduated Band Behaviors

Each band triggers progressively more data capture:

**Low (0-19):** No persistence. Process data exists only in the ring buffer for real-time display.

**Medium (20-39):** Light tracking begins. Snapshots captured to database at longer intervals. Minimal storage footprint, but establishes a record that the process was notable.

**Elevated (40-49):** Full tracking. Regular snapshots, trend data preserved. Enough context to understand process behavior over time.

**High (50-69):** Tracking plus system context. In addition to process snapshots, capture system-level state (memory pressure, load, other high-scoring processes). Partial forensics — enough to correlate process behavior with system conditions.

**Critical (70-100):** Full forensics. Trigger tailspin capture, system logs, complete ring buffer context. Maximum data for post-incident investigation.

### Dominant Category Reporting

The score identifies a "dominant resource" — whichever resource the process is most disproportionately consuming. This helps triage: a memory-dominant hog needs different investigation than a GPU-dominant one. The dominant resource and its disproportionality factor are reported alongside the score.

## Alternatives Considered

| Option | Description | Why Not Selected |
|--------|-------------|------------------|
| **Percentile-based scoring** | Rank each process against all others. Top 1% scores 95+, top 5% scores 80+, etc. | Scores become relative to current process set. A "high" score during idle might be "medium" during heavy load. We want scores to reflect actual resource consumption, not just ranking. |
| **Fix thresholds only** | Keep current formula, just raise normalization thresholds to account for 16 cores. | Addresses score compression but doesn't capture the "disproportionate share" concept. A process using 50% of machine would score 50, but that's massive for one process among hundreds. |
| **Pure Apple Energy Impact** | Copy Apple's formula exactly, using their coefficients. | Energy Impact measures power consumption, not resource hogging. A process can have low energy impact (efficient) while still consuming disproportionate resources. Our goals differ from Apple's. |
| **Drop categories entirely** | Sum all resources into one score without the blocking/contention/pressure/efficiency groupings. | Loses the "dominant category" insight which helps triage. Knowing a process is memory-dominant vs CPU-dominant is valuable for investigation. |
| **Machine learning anomaly detection** | Train a model on "normal" behavior, flag deviations. | Overkill for a personal tool. Requires training data, ongoing calibration, and adds complexity. Simple weighted formulas are predictable and debuggable. |

## Consequences & Trade-offs

**Positive:**

- Scores will spread across bands, making severity meaningful
- Resource hogs will stand out regardless of how many cores the machine has
- Graduated forensics captures data proportional to severity — not all-or-nothing
- Apple's weighting model is battle-tested and grounded in real system impact research
- Dominant resource reporting aids investigation triage

**Negative:**

- Formula will require tuning after implementation — initial weights may need adjustment based on observed score distributions
- Disproportionate share calculation requires knowing total system resource usage, adding a small computation step per sample
- Historical data becomes incomparable — old scores (pre-refactor) used different formula, can't compare directly to new scores
- More data captured at lower bands increases storage usage over time

**Neutral:**

- Current four categories (blocking, contention, pressure, efficiency) may evolve — the dominant resource model is similar but organized around resource types rather than behavioral categories
- Ring buffer and snapshot schema remain unchanged — this affects scoring and capture triggers, not data structures
- Database schema version will increment — existing data remains but represents the old formula

## Success Criteria

**1. Score distribution across bands**

During normal usage, scores should appear in multiple bands — not 100% clustered in one. A healthy system should show mostly low/medium with occasional elevated. Under load, high and critical should be reachable.

**2. Resource hogs are identifiable**

When a single process consumes disproportionate resources (e.g., 25%+ of CPU, memory, or GPU among hundreds of processes), it should score significantly higher than processes with typical usage.

**3. Dominant resource is accurate**

The reported dominant resource should match manual inspection. A process visibly hammering disk I/O should report disk as dominant, not CPU or memory.

**4. Graduated capture occurs**

Processes at different bands should have different amounts of stored data. A critical event should have full forensics; an elevated event should have snapshots but no tailspin; a medium event should have lighter records.

**5. Scores are stable and predictable**

Similar resource usage should produce similar scores. The formula shouldn't produce erratic jumps from minor changes in input. Scores should be explainable by looking at the process's resource consumption.

## Open Questions

**1. Active process threshold for fair share**

Fair share should divide by *active* processes, not total process count. Need to define "active" — processes above some minimum resource usage? Processes not in idle/sleeping state? The threshold for what counts as active needs definition.

**2. Disproportionality-to-score curve**

The goal isn't to artificially scale to 100. The goal is that genuinely severe resource hogging reaches the critical band and triggers full diagnostics. What curve (linear, logarithmic, or other) maps disproportionality to scores such that critical is reachable but not trivially so?

**3. Medium band capture specifics**

What snapshot interval for medium band? What data is captured vs. omitted compared to elevated?

**4. High band system context specifics**

What system-level state is captured at high band? Memory pressure, load average, other high-scoring processes — which of these, and how?

**5. Configuration surface**

All coefficients, thresholds, and intervals should be configurable. Needs clear definition of what goes in config.toml vs. what's hardcoded.

---

*Design created: 2026-02-01*
