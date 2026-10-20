"""Compare user-facing copy with actual producers, rather than a second list of codes."""

import argparse
import ast
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.cli_main import _record_cli_event, build_parser
from aws_connect.domain.errors import APPLICATION_ERROR_TYPES
from aws_connect.presentation.gui import errors
from aws_connect.presentation.gui.errors import error_message
from aws_connect.presentation.gui.log_messages import _OPERATIONS, log_message, log_operation

_ROOT = Path(__file__).resolve().parents[2]
_CODE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_ERROR_TYPES = {kind.__name__ for kind in APPLICATION_ERROR_TYPES} | {"ApplicationError"}


def _source_trees() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8-sig")))
        for path in (_ROOT / "src/aws_connect").rglob("*.py")
    ]


def _entry(feature: str, action: str, code: str, target: str = "example") -> ManagedLogEntry:
    return ManagedLogEntry(datetime.now(UTC), feature, target, "SUCCESS", code, operation=action)


def test_all_emitted_error_codes_have_specific_korean_copy() -> None:
    missing: list[str] = []
    checked: set[str] = set()
    for path, tree in _source_trees():
        helpers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and isinstance(node.returns, ast.Name)
            and node.returns.id in _ERROR_TYPES
        }
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = ast.unparse(call.func).rsplit(".", 1)[-1]
            if name not in _ERROR_TYPES | helpers | {"_failure"}:
                continue
            arguments = [item.value for item in call.keywords if item.arg == "message_code"]
            arguments = arguments or call.args[:1]
            for argument in arguments:
                values: list[ast.AST] = [argument]
                if isinstance(argument, ast.Name):
                    values = [
                        node.value
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Assign)
                        and any(
                            isinstance(t, ast.Name) and t.id == argument.id for t in node.targets
                        )
                    ]
                for value in values:
                    for literal in ast.walk(value):
                        if not isinstance(literal, ast.Constant) or not isinstance(
                            literal.value, str
                        ):
                            continue
                        code = literal.value
                        if name == "_error" and path.stem == "rclone_drive_mount":
                            code = "drive." + code
                        elif name == "_error" and path.stem == "winfsp_installer":
                            code = "runtime.winfsp." + code
                        if not _CODE.fullmatch(code):
                            continue
                        checked.add(code)
                        copy = error_message(code)
                        if code == "auth.mfa_required":
                            copy = (log_message(_entry("auth", "validate", code)), "인증")
                        if copy is None or not all(re.search("[가-힣]", text) for text in copy):
                            missing.append(f"{path.relative_to(_ROOT)}:{call.lineno}: {code}")
    assert len(checked) > 150, "Source inventory unexpectedly stopped covering error producers"
    assert not missing, "Missing Korean error descriptions:\n" + "\n".join(sorted(set(missing)))


def test_all_emitted_operations_have_specific_korean_names() -> None:
    missing: list[str] = []
    for path, tree in _source_trees():
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = ast.unparse(call.func).rsplit(".", 1)[-1]
            arguments: list[ast.expr] = []
            if name == "logged_operation":
                arguments = call.args[:2]
            elif name in {"record_success", "record_failure"}:
                arguments = call.args[1:3]
            elif name == "record":
                fields = {item.arg: item.value for item in call.keywords}
                if "feature" in fields and "operation" in fields:
                    arguments = [fields["feature"], fields["operation"]]
            if len(arguments) != 2 or not all(isinstance(arg, ast.Constant) for arg in arguments):
                continue
            feature, action = (str(arg.value) for arg in arguments)  # type: ignore[attr-defined]
            row = _entry(feature, action, f"{feature}.{action}.completed")
            if action not in _OPERATIONS or log_operation(row) != _OPERATIONS[action]:
                missing.append(f"{path.relative_to(_ROOT)}:{call.lineno}: {feature}.{action}")
    assert not missing, "Missing Korean operation names:\n" + "\n".join(missing)


def _leaf_commands(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    children = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    if not children:
        return [prefix]
    return [
        leaf
        for action in children
        for name, child in action.choices.items()
        for leaf in _leaf_commands(child, (*prefix, name))
    ]


def test_cli_records_can_display_every_registered_command_in_korean() -> None:
    for path in _leaf_commands(build_parser()):
        entry = _entry(
            path[0], "cli.command.succeeded", "cli.command.succeeded", ".".join(path[1:]) or path[0]
        )
        action = path[-1]
        assert action in _OPERATIONS, path
        assert log_operation(entry) == _OPERATIONS[action], path


def test_error_catalog_has_no_duplicate_keys() -> None:
    tree = ast.parse(Path(errors.__file__).read_text(encoding="utf-8-sig"))
    for assignment in (node for node in tree.body if isinstance(node, ast.AnnAssign)):
        if isinstance(assignment.target, ast.Name) and assignment.target.id == "_MESSAGES":
            assert isinstance(assignment.value, ast.Dict)
            keys = [key.value for key in assignment.value.keys if isinstance(key, ast.Constant)]
            assert len(keys) == len(set(keys))


def test_cli_drive_record_keeps_the_command_needed_for_korean_display() -> None:
    app = Mock()
    args = build_parser().parse_args(["s3-drive", "connect", "1"])
    _record_cli_event(app, args, "succeeded", "cli.command.succeeded")
    fields = app.activity_logs.record.call_args.kwargs
    entry = _entry(
        fields["feature"], fields["message_code"], fields["message_code"], fields["target"]
    )
    assert log_operation(entry) == "연결 실행"


def test_operation_inventory_rejects_generic_feature_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse('@logged_operation("ec2", "untranslated_action")\ndef example(): pass')
    monkeypatch.setattr(sys.modules[__name__], "_source_trees", lambda: [(Path(__file__), tree)])
    assert log_operation(
        _entry("ec2", "untranslated_action", "ec2.untranslated_action.completed")
    ) == ("EC2 작업")
    with pytest.raises(AssertionError, match="ec2.untranslated_action"):
        test_all_emitted_operations_have_specific_korean_names()
