# Per-Process Stressor Scoring Design

## Y-Statement Summary

**In the context of** identifying what causes system pauses and slowdowns,
**facing** incomplete per-process data from powermetrics and a reactive "detect then hunt" model,
**we decided for** per-process stressor scoring using top at 1Hz with 7 weighted metrics,
**to achieve** proactive rogue process identification with built-in attribution,
**accepting** lower sample rate and a breaking schema change.

## Problem Statement

The current system calculates a single system-wide stress score and then hunts for culprits when problems occur. This is backwards — we detect stress, then scramble to figure out who caused it.

We want to flip this model: continuously identify which processes are causing the most trouble, rank them by a stressor score, and have attribution ready *before* problems manifest. When a pause or slowdown occurs, we already know who the usual suspects are.

The current data source (powermetrics) cannot provide the per-process metrics needed for comprehensive stressor scoring — it lacks memory, state, context switches, syscalls, and thread information.

## Goals

1. **Per-process scoring** — Assign a 0-100 stressor score to individual processes based on their resource behavior
2. **Proactive identification** — Know which processes are "rogue" at any moment, not just during incidents
3. **Complete metrics** — Track all meaningful indicators: CPU, memory, compressed memory, state, pageins, context switches, syscalls
4. **Efficient storage** — Store less data but more useful data; rank processes rather than raw system metrics

## Non-Goals

- Real-time 10Hz sampling (1Hz is sufficient for process-level trends)
- Tracking every process (focus on rogues only)
- Replacing forensics capture (this complements it, doesn't replace spindump/tailspin)

## Proposed Approach

### Data Source

Replace powermetrics with `top` (macOS) in delta mode at 1Hz.

| | powermetrics | top |
|--|--------------|-----|
| **Rate** | 10Hz | 1Hz |
| **Metrics coverage** | 40% (cpu, pageins only) | 100% |

### Metrics & Weights

Each process is scored on 7 metrics. Weights total 100 points, minimum 5 points per metric.

| Metric | Weight | What it reveals |
|--------|--------|-----------------|
| `cpu` | 25 | CPU hogging — the most common trouble sign |
| `state` | 20 | Stuck/frozen — binary and critical when present |
| `pageins` | 15 | Disk I/O for memory — catastrophic for performance |
| `mem` | 15 | Memory footprint — drives system-wide pressure |
| `cmprs` | 10 | Compressed memory — system actively struggling with this process |
| `csw` | 10 | Context switches — scheduling behavior, thrashing indicator |
| `sysbsd` | 5 | BSD syscalls — kernel interaction overhead |

**Why these weights:**
- **cpu (25):** Most common and visible problem. If you could only see one number, this is it.
- **state (20):** A stuck process is an emergency. Binary, unambiguous.
- **pageins (15):** Any non-zero value means disk I/O for memory — orders of magnitude slower than RAM.
- **mem (15):** Large consumers cause pressure even when idle.
- **cmprs (10):** Non-zero means the system is working hard to keep this process in RAM.
- **csw (10):** Reveals behavior — zero means frozen, thousands means thrashing.
- **sysbsd (5):** Minimum investment to detect kernel-heavy processes.

**What we dropped:**
- `threads` — Used for category selection, not scoring (thread count alone isn't trouble)
- `idlew` — Power analysis, not "trouble" detection
- `vsize` — Virtual size is often meaningless on modern macOS

### Rogue Process Selection

Not every process gets scored — only those flagged as potential rogues.

**Automatic inclusion** (any match):

| Condition | Rationale |
|-----------|-----------|
| `state = "stuck"` | Frozen in kernel, always a problem |
| `pageins > 0` | Any disk I/O for memory is concerning |

**Top 3 per category:**

| Category | Metric |
|----------|--------|
| CPU | `cpu` |
| Memory | `mem` |
| Compressed | `cmprs` |
| Threads | `threads` |
| Context Switches | `csw` |
| Syscalls | `sysbsd` |

**Result:**
- 6 categories × 3 = 18 maximum from ranked selection
- Plus any stuck processes (typically 0)
- Plus any paging processes (varies)
- Deduplicated — a process appears once even if flagged in multiple categories
- Expected unique count per sample: **10-20 processes**

### Data Structure

**Per sample:**
- Timestamp
- Process count
- Top score (highest process score, for quick queries)

**Per process (within a sample):**

| Field | Purpose |
|-------|---------|
| pid | Process ID |
| command | Process name |
| cpu | Percentage |
| state | running/sleeping/stuck |
| mem | Bytes |
| cmprs | Bytes |
| pageins | Count (delta) |
| csw | Count (delta) |
| sysbsd | Count (delta) |
| threads | Count |
| score | Calculated 0-100 |
| categories | Which categories flagged this process |

### Tier Transitions

Tiers are driven by the **maximum process score** in each sample. One rogue process can escalate the whole system — because it should.

| Tier | Trigger |
|------|---------|
| SENTINEL (1) | max score < elevated threshold |
| ELEVATED (2) | max score ≥ elevated threshold |
| CRITICAL (3) | max score ≥ critical threshold |

**Proposed thresholds** (need tuning):

| Threshold | Current (system) | Proposed (process) |
|-----------|------------------|-------------------|
| Elevated | 15 | 35 |
| Critical | 50 | 65 |

Higher thresholds because a single process score is more focused than a blended system score.

**Tier-based saving:**
- Tier 2 (Elevated): Save on peak
- Tier 3 (Critical): Save continuously at 1Hz

Each saved sample contains the full rogue process list with individual scores and metrics.

## Alternatives Considered

| Option | Pros | Cons | Why Rejected |
|--------|------|------|--------------|
| Keep powermetrics, add top | Two data sources, best of both | Complexity, synchronization issues | Unnecessary complexity |
| Average top N scores for tier | Smooths outliers | Hides single bad actor | One process CAN pause a system |
| Count processes above threshold | Captures widespread pressure | Misses single severe offender | Same as above |
| Score all processes | Complete picture | 800+ processes, massive overhead | Most processes are fine |

## Consequences & Trade-offs

**Positive:**
- Attribution built-in — No more "stress is high, who did it?"
- Simpler mental model — One score per process, sort descending, done
- Richer forensics — Pause events capture individual culprits with metrics
- Less data, more value — 15-20 processes at 1Hz vs system metrics at 10Hz
- Complete picture — All 7 diagnostic metrics vs 2 from powermetrics

**Negative:**
- Lower sample rate — 1Hz instead of 10Hz
- Top parsing overhead — Spawning/parsing top each second
- Breaking change — Storage schema changes significantly
- Threshold retuning — Current thresholds won't apply

**Neutral:**
- powermetrics removed — Simpler dependency, but loses some system-wide metrics

**Accepted trade-offs:**

| We accept... | Because... |
|--------------|------------|
| 1Hz sampling | Process-level trends don't need 10Hz; rogues don't spike for 100ms |
| Losing powermetrics streaming | It couldn't provide the metrics we need |
| Schema migration | The new model is fundamentally better for our goal |

## Success Criteria

1. **Every pause event has attribution** — When a pause is captured, we can immediately see which process(es) were the top scorers
2. **Rogue processes are identified before escalation** — The ranked list shows emerging problems before they hit critical
3. **Scores correlate with observed problems** — High-scoring processes are actually causing trouble
4. **Storage is more efficient** — Less data volume with longer useful retention
5. **Threshold tuning converges** — Within a week of use, elevated/critical thresholds stabilize

## Open Questions

| Question | Impact |
|----------|--------|
| Exact elevated/critical thresholds | Needs real-world tuning; start with 35/65 |
| Should `threads` be in the score or just selection? | Currently selection-only; may revisit |
| Normalization details for each metric | Needs definition during implementation |
| How to handle PID reuse | Short-lived PIDs may recycle; track command+pid? |
| System-wide metrics (thermal, GPU) | Drop entirely or obtain separately? |
