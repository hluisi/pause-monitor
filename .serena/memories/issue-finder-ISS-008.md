# ISS-008: CLI database check + connection boilerplate duplication

**Category:** Duplication
**All Categories:** Duplication, Structure
**Severity:** Important
**Status:** resolved
**Created:** 2026-01-29T10:30:00Z
**Last validated:** 2026-01-29T10:30:00Z

## Grouped Findings

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Duplication | cli.py | 48-52 | status | db check + connection pattern |
| 2 | Duplication | cli.py | 98-102 | events | same pattern |
| 3 | Duplication | cli.py | 149-153 | events_show | same pattern |
| 4 | Duplication | cli.py | 218-222 | history | same pattern |
| 5 | Duplication | cli.py | 326-345 | prune | same pattern |

## Investigation

### The Duplicated Pattern

Each function follows the same boilerplate:
```python
config = Config.load()
if not config.db_path.exists():
    click.echo("Database not found. Run 'rogue-hunter daemon' first.")
    return
conn = get_connection(config.db_path)
try:
    # ... actual work ...
finally:
    conn.close()
```

### Variations

| Function | Config Source | Error Style |
|----------|---------------|-------------|
| status | Config.load() | return |
| events | Config.load() (ctx.obj) | return |
| events_show | ctx.obj["config"] | SystemExit(1) |
| history | Config.load() | return |
| prune | Config.load() | return |

## Root Cause

No centralized abstraction for "database-aware CLI command." Each command independently implements:
1. Configuration loading
2. Database existence validation
3. Connection lifecycle management

The pattern is repeated 5 times with minor variations (8-12 lines each = 40-60 lines total).

## Suggestions

**Recommended: Context Manager**

```python
@contextmanager
def require_database(config: Config | None = None, exit_on_missing: bool = False):
    if config is None:
        config = Config.load()
    if not config.db_path.exists():
        if exit_on_missing:
            click.echo("Error: Database not found", err=True)
            raise SystemExit(1)
        click.echo("Database not found. Run 'rogue-hunter daemon' first.")
        raise DatabaseNotAvailable()
    conn = get_connection(config.db_path)
    try:
        yield conn
    finally:
        conn.close()
```

Usage:
```python
@main.command()
def status() -> None:
    config = Config.load()
    try:
        with require_database(config) as conn:
            # actual work
    except DatabaseNotAvailable:
        return
```

## Notes

- `events_show` gets config via `ctx.obj["config"]` from parent group - must preserve Click context pattern
- Error message inconsistency: most say long message, `events_show` says "Error: Database not found"
- Connection is never pooled (fine for CLI, but easy to forget `finally: conn.close()`)
