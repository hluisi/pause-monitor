---
id: audit-issue-finder-iss-002
type: audit
domain: project
subject: issue-finder-iss-002
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [issue-finder-ISS-002]
tags: []
related: []
sources: []
---

# ISS-002: Legacy tier system remnants throughout codebase

**Category:** Unnecessary Code
**All Categories:** Unnecessary Code, Structure
**Severity:** Important
**Status:** resolved
**Created:** 2026-01-29T10:30:00Z
**Last validated:** 2026-01-29T10:30:00Z

## Grouped Findings

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Unnecessary Code | daemon.py | 397,405 | _main_loop | tier=1 hardcoded in ring_buffer.push and broadcast |
| 2 | Unnecessary Code | tui/app.py | 115-117 | update_info | tier_labels dict always shows "Normal" |
| 3 | Unnecessary Code | tests/test_integration.py | 303-320 | TIER_TEST_CASES | Comments reference removed tier system |

## Investigation

### Trace Up (what depends on this code)

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| RingSample.tier | ringbuffer.py:22 | Storage | Dataclass field storing always-1 value |
| SocketServer.broadcast() | socket_server.py:101,117 | Forwarding | Sends tier in JSON message |
| TUI _on_message() | tui/app.py:619,627,644 | Consumer | Extracts tier from socket message |

### Trace Down (what this code depends on)

| Symbol | File:Line | Relationship | Notes |
|--------|-----------|--------------|-------|
| tier_labels dict | tui/app.py:115 | Display mapping | Always resolves to "Normal" |
| BandsConfig | config.py | Replacement system | New per-process tracking |

### Related Patterns

The old tier system (Tier, TierAction, TierManager, TiersConfig, sentinel.py) was removed and replaced with per-process band tracking via ProcessTracker. The tier field in RingSample, socket protocol, and TUI display remains as vestigial infrastructure.

## Root Cause

**The tier field is vestigial infrastructure from an incomplete refactor.**

The refactor replaced daemon-side tier decision logic with per-process band tracking. However:
- `RingSample.tier` still exists but always receives `1`
- Socket protocol includes `"tier"` for backward compatibility
- TUI displays tier labels but it's always "Tier: 1 (Normal)"
- Test variable names reference the old system

The tier field travels through the system but carries no information.

## Suggestions

**Recommended: Remove tier completely**

| Step | File | Change |
|------|------|--------|
| 1 | ringbuffer.py | Remove `tier` field from `RingSample` |
| 2 | ringbuffer.py | Remove `tier` parameter from `RingBuffer.push()` |
| 3 | daemon.py | Update `ring_buffer.push()` calls (remove tier arg) |
| 4 | socket_server.py | Remove `tier` from `broadcast()` and messages |
| 5 | forensics.py | Remove `tier` from ring buffer JSON output |
| 6 | tui/app.py | Remove tier display, use max_score or band instead |
| 7 | Tests | Update all tests using `tier=` parameter |

**Files requiring updates:** daemon.py, ringbuffer.py, socket_server.py, forensics.py, tui/app.py, test_socket_client.py, test_socket_server.py, test_tui_connection.py, test_daemon.py, test_forensics.py, test_integration.py, test_ringbuffer.py

## Notes

- test_no_tiers.py verifies tier classes are removed but doesn't check lowercase `tier` field usage
- test_integration.py TIER_TEST_CASES tests scoring accuracy, should be renamed to SCORE_RANGE_TEST_CASES
- If external tools parse socket protocol, removing tier would be breaking (unlikely for internal tool)
