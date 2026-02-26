---
id: audit-issue-finder-iss-009
type: audit
domain: project
subject: issue-finder-iss-009
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [issue-finder-ISS-009]
tags: []
related: []
sources: []
---

# ISS-009: Config.save() overly long with repetitive serialization

**Category:** Complexity
**All Categories:** Complexity, Structure
**Severity:** Important
**Status:** resolved
**Created:** 2026-01-29T10:30:00Z
**Last validated:** 2026-01-29T10:30:00Z

## Grouped Findings

| # | Category | File | Line | Symbol | Description |
|---|----------|------|------|--------|-------------|
| 1 | Complexity | config.py | 269-424 | Config.save | 155 lines of repetitive tomlkit table construction |

## Investigation

### Repetitive Patterns

**Pattern 1: Flat section (4 occurrences)**
```python
section = tomlkit.table()
section.add("field1", self.section.field1)
# ... repeat for each field
doc.add("section_name", section)
```

**Pattern 2: Category selection (7 identical in rogue_selection)**
```python
xxx_sel = tomlkit.table()
xxx_sel.add("enabled", self.rogue_selection.xxx.enabled)
xxx_sel.add("count", self.rogue_selection.xxx.count)
xxx_sel.add("threshold", self.rogue_selection.xxx.threshold)
```

### Metrics

| Metric | Current | After Refactor |
|--------|---------|----------------|
| Lines of code | 155 | ~25 |
| Repetitive blocks | 14 | 0 |
| Fields to maintain manually | 50+ | 0 |
| Risk of missing field | High | Zero |

## Root Cause

The method manually serializes each dataclass field instead of leveraging dataclass introspection. All config objects are `@dataclass` types with simple fields that can be serialized generically using `dataclasses.fields()` or `dataclasses.asdict()`.

## Suggestions

**Extract generic helper:**

```python
def _dataclass_to_table(obj: Any) -> tomlkit.items.Table:
    table = tomlkit.table()
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if dataclasses.is_dataclass(value):
            table.add(field.name, _dataclass_to_table(value))
        else:
            table.add(field.name, value)
    return table
```

**Refactored save():**

```python
def save(self, path: Path | None = None) -> None:
    path = path or self.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    doc.add("learning_mode", self.learning_mode)
    
    for name in ["sampling", "retention", "alerts", "suspects", 
                 "forensics", "sentinel", "bands", "scoring", "rogue_selection"]:
        doc.add(name, _dataclass_to_table(getattr(self, name)))
        doc.add(tomlkit.nl())
    
    path.write_text(tomlkit.dumps(doc))
```

## Notes

- Test with assertion that old and new save() produce identical output
- Field ordering preserved by dataclasses.fields()
- Nested dataclasses (scoring, rogue_selection) handled recursively
- Consider similar refactoring for load() method
