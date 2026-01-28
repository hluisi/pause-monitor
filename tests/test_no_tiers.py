"""Tests verifying tier system has been removed."""


def test_no_tier_imports():
    """Codebase has no tier imports."""
    import ast
    from pathlib import Path

    src_dir = Path("src/pause_monitor")
    tier_names = {"Tier", "TierAction", "TierManager", "TiersConfig"}

    for py_file in src_dir.glob("**/*.py"):
        content = py_file.read_text()
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in tier_names:
                    raise AssertionError(f"Found {node.id} in {py_file}")
                if isinstance(node, ast.Attribute) and node.attr in tier_names:
                    raise AssertionError(f"Found {node.attr} in {py_file}")
        except SyntaxError:
            pass  # Skip files with syntax errors


def test_sentinel_has_no_tier_classes():
    """sentinel.py does not export Tier, TierAction, or TierManager."""
    import pause_monitor.sentinel as sentinel

    assert not hasattr(sentinel, "Tier")
    assert not hasattr(sentinel, "TierAction")
    assert not hasattr(sentinel, "TierManager")
