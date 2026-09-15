"""Small, testable architecture guard for rules not covered by import-linter."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_CALLS = {"input", "print"}
FORBIDDEN_MODULES = {"PySide6", "tkinter"}


def violations(path: Path, *, layer: str) -> list[str]:
    """Return forbidden presentation dependencies found in a source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[str] = []
    if layer not in {"domain", "application"}:
        return errors
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            errors.append(f"{path}:{node.lineno}: forbidden call {node.func.id}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", maxsplit=1)[0] in FORBIDDEN_MODULES:
                    errors.append(f"{path}:{node.lineno}: forbidden import {alias.name}")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".", maxsplit=1)[0] in FORBIDDEN_MODULES
        ):
            errors.append(f"{path}:{node.lineno}: forbidden import {node.module}")
    return errors


def verify(root: Path) -> list[str]:
    """Check Domain and Application source trees."""

    errors: list[str] = []
    package = root / "src" / "aws_connect"
    for layer in ("domain", "application"):
        for path in sorted((package / layer).rglob("*.py")):
            errors.extend(violations(path, layer=layer))
    return errors


def main() -> int:
    """Run the architecture guard."""

    root = Path(__file__).resolve().parents[1]
    errors = verify(root)
    if errors:
        print("Architecture verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Architecture verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
