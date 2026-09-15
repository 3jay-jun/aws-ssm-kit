from pathlib import Path

from tools.architecture_check import verify, violations


def test_current_source_obeys_architecture_boundaries() -> None:
    root = Path(__file__).resolve().parents[2]

    assert verify(root) == []


def test_forbidden_fixture_is_rejected() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "architecture"
        / "domain_imports_gui.py.txt"
    )

    errors = violations(fixture, layer="domain")

    assert any("forbidden import PySide6" in error for error in errors)
    assert any("forbidden call print" in error for error in errors)


def test_gui_does_not_import_aws_sqlite_or_cli_adapters() -> None:
    root = Path(__file__).resolve().parents[2]
    gui_root = root / "src" / "aws_connect" / "presentation" / "gui"
    forbidden = (
        "boto3",
        "sqlite3",
        "aws_connect.infrastructure",
        "aws_connect.presentation.cli",
    )
    found = [
        f"{path}: {module}"
        for path in gui_root.rglob("*.py")
        for module in forbidden
        if module in path.read_text(encoding="utf-8")
    ]

    assert found == []


def test_s3_scope_excludes_head_and_recursive_delete_apis() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "src/aws_connect/application/ports.py",
            "src/aws_connect/infrastructure/aws_s3_gateway.py",
        )
    ).lower()

    assert "list_buckets" in sources
    assert "head_object" not in sources
    assert "delete_object" in sources
    assert "delete_objects" not in sources
