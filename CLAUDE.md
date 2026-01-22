# pause-monitor

System health monitoring daemon that tracks down intermittent macOS system pauses.

## Commands

```bash
# Development
uv sync                        # Install dependencies
uv run ruff check . && uv run ruff format .  # Lint + format
uv run pytest                  # Run tests

# Runtime
uv run pause-monitor daemon    # Run sampler (foreground)
uv run pause-monitor tui       # Interactive dashboard
uv run pause-monitor status    # Quick health check
uv run pause-monitor events    # List pause events (add <id> for details)
uv run pause-monitor config    # Manage configuration
uv run pause-monitor install   # Set up launchd service
```

## Architecture

| Module | Purpose |
| -------- | --------- |
| `cli.py` | Click-based CLI commands |
| `config.py` | Configuration loading/saving (TOML) |
| `daemon.py` | Background sampler with adaptive intervals |
| `collector.py` | Metrics collection via powermetrics |
| `stress.py` | Multi-factor stress scoring |
| `forensics.py` | Pause event capture (spindump, tailspin, logs) |
| `storage.py` | SQLite operations with auto-pruning |
| `notifications.py` | macOS notification center alerts |
| `sleepwake.py` | Sleep/wake detection via pmset |
| `tui/` | Textual-based dashboard |

## Data Locations

| Purpose | Path |
| --------- | ------ |
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |

## Agent Discipline

These rules exist because agents repeatedly made these mistakes. Follow them exactly.

### Code Review Findings Are Not Optional

> "No critical issues" ≠ "review passed."

| Level | Meaning | Action Required |
| ------- | --------- | ----------------- |
| **Critical** | Blockers — won't work or dangerous | Must fix before merge |
| **Important** | Real problems that will bite later | Must fix OR get user approval to defer |
| **Minor** | Improvements for maintainability | Fix if quick, OR add to TodoWrite, OR explicitly decline with rationale |

1. **Process ALL severity levels.** Don't stop at "no critical issues."
2. **Important findings require resolution** — fix now, or ask user to defer.
3. **Never silently ignore findings.** Every item needs visible response: fixed, deferred with tracking, or declined with rationale.

| Red Flag | Do Instead |
| -------- | ---------- |
| "No critical issues" → move on | Read full review for Important/Minor |
| "Review passed" with Important items | Important items = review needs work |
| Not mentioning Minor findings | Acknowledge each, even if declining |

### No Untracked Stubs

**Stubs are technical debt. Untracked stubs are bugs.**

1. **Don't create stubs without explicit approval.** Implement the feature, don't write `return (0, 0)` and move on.

2. **If you MUST stub** (user approved, dependency unavailable):
   - Add `TODO(stub):` comment explaining what it should do
   - Add TodoWrite item immediately
   - Update `unimplemented_features` memory
   - Task is NOT complete until stub is resolved

3. **Before claiming done**, verify no new `TODO`, `FIXME`, `pass`, `...`, placeholder returns, or "not implemented" messages.

| Red Flag | Do Instead |
| ---------- | ------------ |
| `return None` / `[]` / `{}` / `(0, 0)` | Implement the logic |
| `pass` or `...` in function body | Implement the function |
| `raise NotImplementedError` | Implement or ask if stub is acceptable |
| `# TODO: implement later` | Implement now or get approval to defer |

### Zero Linter Errors

**Linter errors are not someone else's problem.**

Before claiming any task complete, run:
```bash
uv run ruff check . && uv run ruff format .
```

| Excuse | Reality |
| ------ | ------- |
| "I only touched X, those errors were there before" | You touched the codebase. Clean up what you see. |
| "It's just a warning" | Warnings become errors. Fix them now. |
| "The tests pass" | Tests passing ≠ code complete. Lint must pass too. |
| "I'll fix it in the next PR" | No. Fix it now or don't claim done. |

**If you introduce ANY new linter errors, the task is not complete.**

## Project Knowledge

| Memory | Purpose |
| -------- | --------- |
| `design_spec` | What SHOULD exist (canonical spec) |
| `implementation_guide` | What DOES exist and how (includes design decisions) |
| `unimplemented_features` | What's MISSING or incomplete |

**Read:** `mcp__serena__read_memory("design_spec")` etc.
**Audit:** Use `auditing-codebase` skill in `.claude/skills/`
