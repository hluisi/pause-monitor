# pause-monitor

System health monitoring daemon that tracks down intermittent macOS system pauses.

## Session Start

<serena_rules>

1. Read: `00_start_here` memory

Then proceed with the user's requests.
</serena_rules>

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

### Never Write Stubs

**Stubs are bugs.** Not technical debt. Not placeholders. Bugs.

A stub is any code that pretends to do something but doesn't:
- `return None`, `return []`, `return {}`, `return (0, 0)`
- `pass` or `...` as a function body
- `raise NotImplementedError`
- `# TODO: implement later`
- Any "placeholder" return value

**The Rule: If you cannot implement it, do not write it.**

Don't create the function signature. Don't create the file. Don't create the class. Walk away. The feature does not exist yet, and that's fine.

Writing a stub is worse than writing nothing because:
1. It creates false confidence that the feature exists
2. Callers will wire up to it and get silent failures
3. You will forget about it
4. Someone else will assume it works
5. The bug surfaces weeks later in production

| When you think... | Do this instead |
| --- | --- |
| "I'll implement this later" | Don't write anything. Later isn't now. |
| "I need the structure first" | No. Implement top-to-bottom or don't start. |
| "This shows the architecture" | Architecture with stubs is a lie. |
| "The caller needs something to call" | The caller can wait until it works. |
| "I'll track it" | Tracked bugs are still bugs. |

**There is no "If you MUST stub" exception.** If a dependency is unavailable, stop work and tell the user. If the scope is too large, reduce scope. If you don't know how to implement it, ask.

**Before claiming done:** Search for `TODO`, `FIXME`, `pass`, `...`, `NotImplementedError`, and placeholder returns. If any exist that you created, the task is not complete.

### Zero Linter Errors

**Linter errors are not someone else's problem.**

Before claiming any task complete, run:
```bash
uv run ruff check . && uv run ruff format .
```

**If you have ANY new linter errors, the task is not complete.**

### No "Not My Problem" Dismissals

**If you see it, you own it.**

The codebase does not have compartments. When you encounter a warning, deprecation, or issue during your work — regardless of whether "your changes" caused it — you are responsible for addressing it.

**You are not a contractor scoping work. You are a maintainer.** Maintainers don't walk past problems because they weren't in the ticket.

## Project Knowledge

| Memory | Purpose |
| -------- | --------- |
| `design_spec` | What SHOULD exist (canonical spec) |
| `implementation_guide` | What DOES exist and how (includes design decisions) |
| `unimplemented_features` | What's MISSING or incomplete |

**Read:** `mcp__serena__read_memory("design_spec")` etc.
**Audit:** Use `auditing-codebase` skill in `.claude/skills/`

## Reference Docs

| Library | Location | Update |
| ------- | -------- | ------ |
| Textual | `vendor/textual-docs/docs/` | `cd vendor/textual-docs && git pull` |

These are sparse-cloned from upstream repos and gitignored. Read these when you need framework documentation.
