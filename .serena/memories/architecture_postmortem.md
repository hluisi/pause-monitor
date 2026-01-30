# Architecture Post-Mortem

**Written:** 2026-01-29
**Updated:** 2026-01-29
**Status:** Current state assessment — action required

> **Update 2026-01-29:** Added collector design flaw. TopCollector spawns `top -l 2` every 2 seconds and throws away 50% of samples. Being replaced with LibprocCollector using native APIs.

## Executive Summary

The application's data architecture is fundamentally misaligned with its purpose. Forensic data that should be in the database is written to disk files. The ring buffer, which is a querying mechanism, is being serialized and saved. Time-based collection exists in an event-driven model. Features were added without discussion.

---

## The Application's Purpose

Track macOS system health. When processes cross band thresholds, collect forensic data about what happened. Store that data in a database for later analysis and querying.

---

## The Ring Buffer: What It Is vs How It's Used

### What It Is

A **real-time mechanism** — a sliding window of the last 30 seconds of ProcessSamples.

**Correct usage:**
1. Daemon collects samples at 1Hz, pushes to ring buffer
2. Ring buffer holds last 30 samples (30 seconds)
3. When a process crosses a band threshold → query the ring buffer
4. Extract relevant ProcessScore data from the buffer
5. Store extracted data in database (process_snapshots table)
6. Buffer continues rolling — it's ephemeral

**Secondary use:** Feed real-time data to TUI via socket. The TUI displays what's in the buffer. This is fine.

### How It's Actually Used

The ring buffer is serialized to JSON and written to disk files (`ring_buffer.json` in event directories).

**Likely cause of confusion:** Someone thought "the TUI needs data when it reconnects, so save the ring buffer." But TUI repopulation should come from the socket's initial state message, not disk persistence.

### The Problem

Writing the ring buffer to disk treats it as data to preserve rather than a tool to query. The buffer's contents should be parsed for relevant insights and those insights stored in the database — not the raw buffer dumped to a file.

---

## Data Flow: Expected vs Actual

### Expected Flow

```
Process crosses threshold
         │
         ▼
Query ring buffer ("What happened in last 30s?")
         │
         ▼
Extract relevant ProcessScore snapshots
         │
         ▼
Store in database (process_snapshots table)
         │
         ▼
Database is the forensic record
```

### Actual Flow

```
Process crosses threshold (or pause detected, or score >= 80 with cooldown)
         │
         ▼
Create directory in ~/.local/share/pause-monitor/events/
         │
         ▼
Write ring_buffer.json (entire buffer serialized)
Write metadata.json
Write spindump.txt (raw Apple diagnostic)
Write tailspin.tailspin (raw Apple binary)
Write system.log (raw logs)
         │
         ▼
Files sit on disk permanently
Database has minimal data
```

---

## The Collector: Worst Offender

The data collection approach is the most egregious example of a bad design decision.

### What TopCollector Does

```
Every 2 seconds:
    Spawn subprocess: top -l 2 -s 1 ...
    Wait ~1 second for top to collect TWO samples
    Parse text output
    THROW AWAY sample 1 (invalid CPU%)
    Use sample 2
```

### Why This Is Wrong

1. **Spawning subprocess every 2 seconds** — For a forensic monitoring tool, we're adding overhead to the system we're monitoring
2. **Two samples, use one** — The `-l 2` flag exists because sample 1 has invalid CPU% (no delta). We throw away 50% of collected data.
3. **Text parsing** — Fragile, slow, when structured APIs exist
4. **Missing data** — libproc provides disk I/O, energy, instructions, cycles, wakeups, GPU time. Top doesn't expose these.

### The "Clever" Trap

Someone saw that `top -l 2` gives accurate CPU deltas on sample 2 and thought this was clever. It's not. It's solving a startup problem (first sample invalid) by degrading every subsequent collection.

**The right approach:** Run a persistent process, discard the first sample at startup, use all subsequent samples. Or better: use native APIs (libproc) directly with no subprocess at all.

### What Should Have Been Asked

"How do htop, btop, Activity Monitor get their data?" 

Answer: They call `proc_pid_rusage()` and `proc_pidinfo()` directly. No subprocess. No text parsing. More data available.

---

## Features Added Without Discussion

| Feature | What It Does | Who Asked For It |
|---------|--------------|------------------|
| Pause detection | Triggers forensics when timing ratio exceeds threshold | Legacy — should be removed (bands replaced this) |
| 60-second forensics cooldown | Throttles forensics captures | Unknown — never discussed |
| Hourly system_samples | Collects full ProcessSamples every hour | Unknown — contradicts event-driven model |
| Disk file persistence | Writes forensics to events/ directory | Unknown — should go to database |
| Ring buffer serialization | Dumps entire buffer to JSON file | Unknown — misunderstands buffer's purpose |

---

## Database: What It Has vs What It Should Have

### Current Tables

| Table | Contents | Assessment |
|-------|----------|------------|
| `daemon_state` | Schema version | Fine |
| `process_events` | Event metadata, peak snapshot | Partial — good structure |
| `process_snapshots` | Entry/checkpoint/exit snapshots | Partial — good structure, underused |
| `system_samples` | Hourly full samples | Unnecessary — event-driven, not time-driven |

### What's Missing

The forensic data from spindump, tailspin, and system logs should be:
1. Written to /tmp
2. Parsed for relevant insights
3. Insights stored in database
4. Temp files discarded

Currently: raw files written to persistent storage, never parsed.

---

## The CLI Contradiction

A CLI exists to query the database for forensic data. But the forensic data isn't in the database — it's in disk files.

This means:
- `pause-monitor events` shows event metadata but not the actual forensics
- The real diagnostic data requires manually browsing `~/.local/share/pause-monitor/events/`
- The database serves as an index to disk files rather than the forensic record itself

---

## Root Cause

Agents made design and implementation decisions without consulting the user. When ambiguity existed (e.g., "where should forensics data go?"), agents guessed rather than asking. The guesses accumulated into a system that contradicts its own purpose.

The meta-problem: agents treated implementation details as their domain rather than the user's. Design decisions were made silently and justified after the fact rather than discussed before implementation.

---

## What Needs to Change

### Replace (Critical)
- **TopCollector with LibprocCollector** — Use native macOS APIs (see `libproc_and_iokit_research` memory)

### Remove
- Pause detection (bands replaced this)
- Forensics cooldown (or discuss if actually needed)
- Hourly system_samples collection
- Ring buffer serialization to disk
- Persistent disk file storage for forensics

### Fix
- Forensics flow: write to /tmp → parse → store insights in DB → discard temp
- Ring buffer: query it, don't serialize it
- Database: should be the complete forensic record
- CLI: should query comprehensive data from database

### Add (After Discussion)
- Parsed forensics data tables (thread stacks, kernel events, log entries)
- Whatever extraction makes sense for spindump/tailspin/logs

---

## Lessons

1. **Don't shell out when APIs exist.** Activity Monitor doesn't run `top`. It calls libproc directly. If system tools exist, look at how they work.
2. **Mechanisms are not data.** The ring buffer is a tool to query, not data to save.
2. **Ask, don't guess.** When there's ambiguity about where data should go or what features are needed, ask.
3. **The database exists for a reason.** If we have a database for forensic data, forensic data should be in it.
4. **Time-based collection doesn't belong in an event-driven system.** Hourly samples contradict the band-threshold model.
5. **Features need discussion.** Cooldowns, timers, and persistence decisions are design choices, not implementation details.
6. **"Clever" is a warning sign.** If a solution seems clever, question it. The `top -l 2` trick looked clever but degraded every single collection cycle to solve a one-time startup problem.
