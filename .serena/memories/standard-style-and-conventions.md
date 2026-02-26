---
id: standard-style-and-conventions
type: standard
domain: agent
subject: style-and-conventions
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [style_and_conventions]
tags: []
related: []
sources: []
---

# Style and Conventions

## Code Style

| Aspect | Convention |
|--------|------------|
| Line length | 100 characters |
| Target Python | 3.14 |
| Type hints | Required on all public functions |
| Docstrings | Google style, brief |
| Imports | Sorted by ruff (isort rules) |

## Linting

Ruff with rules: `E` (errors), `F` (pyflakes), `I` (isort), `W` (warnings)

```bash
uv run ruff check . && uv run ruff format .
```

## Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `ProcessScore`, `LibprocCollector` |
| Functions | snake_case | `get_boot_time`, `collect_metrics` |
| Constants | SCREAMING_SNAKE | `STATE_SEVERITY`, `BAND_SEVERITY` |
| Private | Leading underscore | `_collect_sync`, `_PrevSample` |
| Dataclasses | PascalCase | `MetricValue`, `ProcessSamples` |

## Logging

Use structlog throughout:

```python
import structlog
log = structlog.get_logger()

log.info("event_name", key=value)
log.error("error_name", error=str(e))
```

## Data Classes

Use `@dataclass` for data structures. Include serialization methods:

```python
@dataclass
class MetricValue:
    current: float | int
    low: float | int
    high: float | int

    def to_dict(self) -> dict:
        return {"current": self.current, "low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, d: dict) -> "MetricValue":
        return cls(current=d["current"], low=d["low"], high=d["high"])
```

## Async Patterns

- Use `asyncio` for I/O-bound operations
- Prefer `async def` for database operations
- Use `asyncio.to_thread()` for blocking operations

## Error Handling

- Fail visibly — don't swallow errors or degrade silently
- No fallbacks for internal code
- Validate only at system boundaries

## Testing

- pytest with pytest-asyncio
- `asyncio_mode = "auto"` in pyproject.toml
- Test files: `tests/test_<module>.py`
- Use fixtures from `conftest.py`

## Using Project Systems

This project has established infrastructure for common concerns. Before creating new utilities, check `systems` memory for existing solutions.

## Philosophy

| Principle | Practice |
|-----------|----------|
| Delete, don't deprecate | Remove old code immediately |
| No stubs | Implement fully or don't write it |
| No migrations | Schema change = delete DB, recreate |
| Breaking changes are free | No versioning, no `_v2` suffixes |
