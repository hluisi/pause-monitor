# Insights

Accumulated knowledge from sessions. Last updated: 2026-01-30.

## Patterns

### Three-Perspective Validation

Three complementary perspectives catch issues a single reviewer misses:

| Perspective | Finds | Blind Spot |
|-------------|-------|------------|
| **Implementer/Executor** | Missing details, ambiguity, practical issues (async gaps, wrong APIs) | Assumes APIs work as documented |
| **Critic** | Unvalidated assumptions, runtime risks, circular logic | Focuses on negatives, may miss what works |
| **Domain Expert** | Platform-specific errors (wrong flags, missing behaviors) | May not see integration issues |

Each has blind spots the others cover. The implementer assumes APIs exist; the critic questions if they should; the expert knows what actually exists.

**Example findings only multi-perspective caught:**
- Security vulnerability (sudoers wildcards) — Platform Expert
- `mach_absolute_time` vs `mach_continuous_time` behavior difference — Platform Expert  
- Log syntax bugs from line-by-line verification — Implementer
- Whether the core approach could work at all (SIP, heuristics) — Critic

### Subagent-Driven Development

Fresh subagent per task avoids context pollution:
1. **Controller** extracts task specs, provides context, dispatches
2. **Implementer subagent** writes code/tests/commits (fresh context)
3. **Spec reviewer** verifies implementation matches requirements
4. **Quality reviewer** ensures code is maintainable

Two-stage review catches different issues: spec compliance prevents under/over-building, code quality prevents technical debt.

### TDD Red-Green-Refactor

Every task follows: write failing test → verify fail → implement minimal code → verify pass → commit.

The test suite is the arbiter of task boundaries — if tests fail between commits, the task split is wrong.

### Design Documents: What vs How

Good designs specify *what* and *why*, leaving *how* to implementers:
- Code examples become implicit mandates — implementers feel obligated to follow them
- The Implementer validator perspective can *pull* designs toward more prescription
- Better framing: "Is this a missing requirement, or implementation freedom?"

Essential design doc sections:
- **Known Limitations** — prevents expecting magic
- **Alternative Approaches** — shows informed trade-offs
- **Open Questions** — captures deferred decisions for future maintainers

### Single Source of Truth for Data

powermetrics as sole data source eliminates disagreements between tools:
- One subprocess is more efficient than spawn-per-sample AND psutil (many syscalls)
- Data format matches what Apple engineers use to debug macOS
- Provides metrics nothing else can (responsible PID, coalition, per-process I/O)

Same principle applies to config: define defaults in one place (the dataclass), reference that elsewhere.

### Ring Buffer Pre-Incident Capture

Always recording, only persisting on incident — "time machine" that lets you look backward from detection:
- Separation of *collection frequency* from *storage frequency*
- Circular buffer (`collections.deque` with `maxlen`) for O(1) append with automatic eviction
- When rogue detected, context is immediately available for forensics
- Ring buffer always has data (top N by score) — even on healthy systems

## Decisions

### No Stubs Rule

**Stubs are bugs, not technical debt.** A stub is code that pretends to do something but doesn't:
- `return None/[]/{}`, `pass`, `...`, `raise NotImplementedError`, `# TODO`

Writing a stub is worse than writing nothing because:
1. Creates false confidence the feature exists
2. Callers wire up to it and get silent failures
3. The bug surfaces weeks later

**The rule:** If you can't implement it fully, don't touch it. The feature doesn't exist yet, and that's fine.

### No Migrations for Personal Projects

Migration code is accidental complexity:
- Serves users who might have old data — with no users, it's pure debt
- Accumulates bugs and makes schema changes scary

Better: Schema versioning detects mismatches → "delete and recreate" instead of migration scripts.

### Dead Code Removal

When you see `# Deprecated`, `# TODO: remove`, `# kept for compat` in a personal project — delete immediately:
- Dead code stubs create false confidence
- Future agents may pattern-match on code that shouldn't exist
- Reduces cognitive load and makes architecture unambiguous

### Push vs Poll for Real-Time

Push is inherently simpler — server knows when to send, no timing coordination needed. Socket stream for real-time display, SQLite fallback for when daemon isn't running.

## Gotchas

### macOS Privileges

**powermetrics requires root** — no workaround:
- Wireshark uses `/dev/bpf*` (file I/O) → group permissions suffice
- powermetrics reads kernel performance counters via IOKit → requires actual root
- macOS entitlements for these APIs are reserved for Apple-signed code
- **Standard workaround:** sudoers with NOPASSWD for specific commands

**QoS class elevation doesn't require root** — use `pthread_set_qos_class_self_np`:
- Sets scheduler hints for CPU priority, I/O priority, and timer coalescing
- `QOS_CLASS_USER_INITIATED` (0x19) is appropriate for daemons needing timely wakeups
- Called via ctypes to `/usr/lib/libSystem.B.dylib`
- Unlike `nice -10`, this works without elevated privileges
- Better than nice anyway: affects I/O priority and timer behavior, not just CPU

### `top -l 2` Two-Sample Requirement

macOS `top` first sample is instantaneous snapshot (inaccurate CPU%), second sample is delta over interval (accurate). Parser must use the LAST `PID` header, not the first, or CPU% will be 5-20x inflated.

### Cumulative vs Instantaneous Metrics

macOS `top` reports some metrics as **cumulative since process start**:
- `CSWS` (context switches), `MSGSENT`/`MSGRECV`/`SYSBSD`, `PAGEINS`

A process hammering the system 2 hours ago and now sleeping still shows huge cumulative numbers. Score must account for this (state multipliers help).

### Zombie Processes on Async Timeout

When `asyncio.wait_for` times out, the spawned subprocess keeps running. Must explicitly kill:
```python
except asyncio.TimeoutError:
    process.kill()
    await process.wait()  # Reap the zombie
```

### UTF-8 Decode Errors from System Commands

`pmset -g log` and similar can contain non-UTF-8 bytes. Use `errors="replace"` when decoding.

### UV Tool Install vs Source

`uv tool install .` copies package to `~/.local/share/uv/tools/`. Editing source doesn't affect installed copy. Use `uv run rogue-hunter` during development, or `uv tool install . --force && uv cache clean --force` to update.

### Nix-Darwin Symlink Behavior

Nix-darwin creates symlinks from `/etc/` to `/etc/static/` for managed files. Direct editing fails; must enable in nix config instead.

### File Ownership with Sudo

When a process runs as root (via sudo), files it creates are owned by root. Common pitfall with daemons needing elevated privileges for *some* operations.

## Architecture

### Tiered Monitoring with Escalation

Three-tier system: Sentinel (normal) → Elevated (concerned) → Critical (problem):
- Tier state machine with hysteresis prevents oscillation
- Escalation is immediate; de-escalation requires sustained low readings
- Peak stress tracked during elevated states for forensics

### Pageins: The Smoking Gun

Pageins (swap page-ins per second) is the most direct measurement of memory thrashing:
- `pageins` tells you pauses are *happening right now*
- A system with 0 pageins rarely pauses no matter how high other metrics
- `top_pagein_processes` answers "who's causing pauses?" vs `top_cpu_processes` for "who's using resources?"

### Per-Process Rogue Detection

**Old model:** Measure system stress, then hunt for culprits
**New model:** Continuously identify rogue processes with attribution built-in

The peak score from ALL processes IS the system stress indicator — the worst rogue's score tells you how bad things are. No separate "find culprit" step needed.

**Four dimensions of rogue behavior:**
- **Blocking (40%)**: I/O bottlenecks, memory thrashing — directly hurts others
- **Contention (30%)**: CPU fighting, scheduler pressure — forces others to wait
- **Pressure (20%)**: Memory hogging, kernel overhead — degrades capacity
- **Efficiency (10%)**: Stalled pipelines, thread proliferation — wastes resources

### Process State Multipliers

Applied *after* base score to reflect current impact vs capability:
- idle: 0.5, sleeping: 0.6, stopped: 0.7, halted: 0.8, zombie: 0.9, running/stuck: 1.0

A sleeping process with base score 60 shows as 36 (60 × 0.6). Base score is what it *would* contribute if active.

### System Freeze Detection

**You cannot observe a freeze from inside a frozen system.** The only way to detect "system was unresponsive" is to notice afterward that time jumped. Sample arrival latency IS the freeze measurement — if collection takes 10 seconds instead of 0.05, that's a 10-second freeze.

Note: Rogue Hunter's primary goal is identifying processes causing system stress BEFORE they cause freezes. The forensics capture (tailspin) provides kernel-level detail for post-mortem analysis when freezes do occur.

## Debugging

### Memory Drift Detection

Memories drift during active development:
- Date headers are your first staleness indicator
- Cross-reference (memory vs code vs git) catches drift early
- Override notices create clear hierarchy without deleting useful context mid-refactor

### Top Diagnostic Metrics

Beyond the obvious CPU/memory:
- **state="stuck"** — process in uninterruptible sleep, cannot even kill -9
- **cmprs > 0** — macOS actively compressing memory to avoid swapping (early warning)
- **csw extremes** — 0 suggests frozen, 50k+ suggests thrashing
- **instrs/cycles ratio** — below 1 IPC suggests memory-bound code

### Plan Drift During Refactors

Plans suffer "accumulated drift" — each edit adds changes without reconciling with existing content. Common failures:
- Summary tables diverge from details
- Code snippets not updated when architecture changes
- Cross-references stale after restructuring

Fix: Have one authoritative source and reference it (DRY for docs).
