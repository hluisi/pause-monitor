# Style and Conventions

## Code Style
- Line length: 100 characters
- Target Python: 3.11
- Ruff linting: E, F, I, W rules
- Type hints required
- Docstrings for public classes/methods

## Testing
- pytest with pytest-asyncio
- Async mode: auto
- Test files in `tests/` directory
- TDD approach (test first, then implement)

## Patterns
- Dataclasses for configuration
- Properties for derived paths
- tomlkit for TOML serialization (preserves formatting)
