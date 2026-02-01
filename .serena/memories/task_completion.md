# Task Completion Checklist

Before marking any task complete, verify:

## Code Quality

- [ ] **Lint passes**: `uv run ruff check . && uv run ruff format .`
- [ ] **Tests pass**: `uv run pytest`
- [ ] **No new warnings** in test output
- [ ] **Type hints** on all new public functions

## No Stubs

Search for prohibited patterns:

```bash
grep -r "TODO\|FIXME\|NotImplementedError\|pass$" src/
```

- [ ] No `TODO` or `FIXME` comments you created
- [ ] No `pass` or `...` as function bodies
- [ ] No `raise NotImplementedError`
- [ ] No placeholder return values

## No Regressions

- [ ] Existing tests still pass
- [ ] No removed functionality without explicit request
- [ ] No accidental deletions

## Documentation

- [ ] Update `implementation_guide` memory if architecture changed
- [ ] Update `design_spec` memory if spec changed
- [ ] Update `unimplemented_features` if status changed

## Schema Changes

If database schema changed:

- [ ] Increment schema version in `storage.py`
- [ ] Delete database file (no migrations)
- [ ] Update `data_schema` memory

## Final Verification

```bash
# Full check sequence
uv run ruff check . && uv run ruff format . && uv run pytest
```

All checks must pass before claiming task complete.

## Philosophy Reminders

| Don't | Do |
|-------|-----|
| Write stubs | Implement fully or don't start |
| Swallow errors | Fail visibly |
| Add migrations | Delete DB, recreate |
| Keep deprecated code | Delete immediately |
| Over-engineer | Minimum complexity for current task |
