from __future__ import annotations

import ast
from pathlib import Path


def test_main_window_internal_method_calls_are_defined() -> None:
    """UI refactors must not leave calls to deleted MainWindow helpers."""
    project_root = Path(__file__).resolve().parents[2]
    window_dir = project_root / "irbis_control" / "ui" / "windows"
    files = (
        "main.py",
        "main_build.py",
        "main_layout.py",
        "main_operations.py",
        "main_run.py",
    )

    defined: set[str] = set()
    calls: list[tuple[str, str, int]] = []
    for filename in files:
        tree = ast.parse((window_dir / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and node.func.attr.startswith("_")
            ):
                calls.append((node.func.attr, filename, node.lineno))

    missing = [
        f"{filename}:{line}: self.{name}()"
        for name, filename, line in calls
        if name not in defined
    ]
    assert not missing, "Undefined internal UI methods:\n" + "\n".join(sorted(missing))
