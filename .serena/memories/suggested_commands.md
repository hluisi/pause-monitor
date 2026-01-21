# Suggested Commands

## Development
```bash
uv sync                        # Install dependencies
uv run pytest                  # Run all tests
uv run pytest -v               # Verbose test output
uv run pytest tests/test_X.py  # Run specific test file
uv run ruff check .            # Lint
uv run ruff format .           # Format
```

## Running
```bash
uv run pause-monitor daemon    # Run daemon in foreground
uv run pause-monitor tui       # Launch TUI dashboard
uv run pause-monitor status    # Quick health check
```

## Git
Standard git commands work as expected on macOS.
