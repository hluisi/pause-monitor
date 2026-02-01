# Suggested Commands

## Development

| Command | Purpose |
|---------|---------|
| `uv sync` | Install dependencies |
| `uv run ruff check . && uv run ruff format .` | Lint and format |
| `uv run pytest` | Run test suite |
| `uv run pytest -x` | Stop on first failure |
| `uv run pytest tests/test_collector.py` | Run specific test file |
| `uv run pytest -k "test_name"` | Run tests matching pattern |

## Running the Application

| Command | Purpose |
|---------|---------|
| `uv run rogue-hunter daemon` | Run sampler (foreground) |
| `uv run rogue-hunter tui` | Launch interactive dashboard |
| `uv run rogue-hunter status` | Quick health check |
| `uv run rogue-hunter events` | List pause events |
| `uv run rogue-hunter events <id>` | Show event details |

## Configuration

| Command | Purpose |
|---------|---------
| `uv run rogue-hunter config show` | Show current config |
| `uv run rogue-hunter config edit` | Edit config file |
| `uv run rogue-hunter config reset` | Reset to defaults |

## Service Management

| Command | Purpose |
|---------|---------
| `uv run rogue-hunter perms install` | Set up sudoers rules for forensics |
| `uv run rogue-hunter perms uninstall` | Remove sudoers rules |
| `uv run rogue-hunter perms status` | Check permissions status |
| `uv run rogue-hunter service install` | Install launchd service |
| `uv run rogue-hunter service uninstall` | Remove launchd service |
| `uv run rogue-hunter service status` | Check service status |

## Git Workflow

| Command | Purpose |
|---------|---------|
| `git status` | Check working tree |
| `git diff` | View unstaged changes |
| `git log --oneline -10` | Recent commits |

## Database

| Command | Purpose |
|---------|---------|
| `sqlite3 ~/.local/share/rogue-hunter/data.db` | Open database |
| `rm ~/.local/share/rogue-hunter/data.db` | Reset database |

## Debugging

| Command | Purpose |
|---------|---------|
| `tail -f ~/.local/state/rogue-hunter/daemon.log` | Watch daemon logs |
| `lsof /tmp/rogue-hunter/daemon.sock` | Check socket status |

## Vendor Docs

| Command | Purpose |
|---------|---------|
| `cd vendor/textual-docs && git pull` | Update Textual docs |
