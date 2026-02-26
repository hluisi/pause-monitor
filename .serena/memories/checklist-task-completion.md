---
id: checklist-task-completion
type: checklist
domain: project
subject: task-completion
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [task_completion]
tags: []
related: []
sources: []
---

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

## No Duplicate Infrastructure

- [ ] Verified no duplicate infrastructure was created (see `systems` memory)

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
