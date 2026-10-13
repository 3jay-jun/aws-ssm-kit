import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import OperationResult, OperationState
from aws_connect.application.rds_tunnel_service import (
    TunnelConnectionResult,
    TunnelHandle,
    TunnelOwner,
)
from aws_connect.application.ssm_session import SessionEndReason
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import build_parser, main
from aws_connect.domain.errors import ConfigurationError, PortAlreadyInUseError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession


def saved() -> TunnelSession:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    return TunnelSession(
        4,
        7,
        "dev-db",
        "db.example.internal",
        3306,
        13306,
        TargetMode.FIXED,
        "i-0123456789abcdef0",
        now,
        now,
    )


def services() -> ApplicationServices:
    return ApplicationServices(Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock())


@pytest.mark.parametrize("command", ["create", "update"])
@pytest.mark.parametrize("hours", [None, 1, 8, 12, 24])
def test_duration_cli_request_and_outputs(command: str, hours: int | None, capsys) -> None:
    app = services()
    existing = replace(saved(), session_duration_hours=8)
    app.tunnel_sessions.show.return_value = existing
    result = replace(
        existing,
        session_duration_hours=hours if hours is not None else 12 if command == "create" else 8,
    )
    getattr(app.tunnel_sessions, command).return_value = result
    args = ["rds", "session", command]
    if command == "update":
        args.append("dev-db")
    args += [
        "--name",
        "dev-db",
        "--host",
        "db.internal",
        "--remote-port",
        "3306",
        "--local-port",
        "13306",
        "--target-mode",
        "select",
    ]
    if hours is not None:
        args += ["--session-duration-hours", str(hours)]
    assert build_parser().parse_args(args).session_duration_hours == hours
    assert main([*args, "--output", "json"], services=app) == 0
    assert (
        json.loads(capsys.readouterr().out)["session_duration_hours"]
        == result.session_duration_hours
    )
    assert getattr(app.tunnel_sessions, command).call_args.args[0].session_duration_hours == hours
    assert main(args, services=app) == 0
    assert f"session_duration_hours: {result.session_duration_hours}" in capsys.readouterr().out


@pytest.mark.parametrize("hours", ["36", "0", "2", "bad"])
def test_cli_rejects_invalid_tunnel_duration(hours: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "rds",
                "session",
                "create",
                "--name",
                "db",
                "--host",
                "db.internal",
                "--remote-port",
                "3306",
                "--local-port",
                "13306",
                "--target-mode",
                "select",
                "--session-duration-hours",
                hours,
            ]
        )


@pytest.mark.parametrize("output", ["json", "text"])
def test_cli_expiration_returns_success_with_reason(output: str, capsys) -> None:
    app = services()
    app.rds_tunnels.start.return_value = OperationResult(
        "operation",
        OperationState.SUCCEEDED,
        TunnelConnectionResult(
            saved(),
            "i-0123456789abcdef0",
            TunnelHandle(
                "ssm-test",
                TunnelOwner.FOREGROUND,
                OperationState.SUCCEEDED,
                end_reason=SessionEndReason.MAX_DURATION,
            ),
            0,
        ),
    )
    assert main(["rds", "tunnel", "start", "dev-db", "--output", output], services=app) == 0
    rendered = capsys.readouterr().out
    if output == "json":
        payload = json.loads(rendered)
        assert payload["end_reason"] == "max_duration"
        assert payload["plugin_exit_code"] == 0
    else:
        assert "end_reason: max_duration" in rendered


def test_session_list_and_create_use_application_service(capsys) -> None:
    app = services()
    app.tunnel_sessions.list.return_value = [saved()]
    assert main(["rds", "session", "list", "--output", "json"], services=app) == 0
    assert json.loads(capsys.readouterr().out)["tunnel_sessions"][0]["name"] == "dev-db"

    app.tunnel_sessions.create.return_value = saved()
    assert (
        main(
            [
                "rds",
                "session",
                "create",
                "--profile",
                "dev",
                "--name",
                "dev-db",
                "--host",
                "db.example.internal",
                "--remote-port",
                "3306",
                "--local-port",
                "13306",
                "--target-mode",
                "fixed",
                "--target-instance-id",
                "i-0123456789abcdef0",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    request = app.tunnel_sessions.create.call_args.args[0]
    assert request.target_mode is TargetMode.FIXED


def test_session_show_update_and_delete_use_application_service(capsys) -> None:
    app = services()
    app.tunnel_sessions.show.return_value = saved()
    app.tunnel_sessions.update.return_value = saved()

    assert main(["rds", "session", "show", "dev-db"], services=app) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "rds",
                "session",
                "update",
                "dev-db",
                "--name",
                "dev-db",
                "--host",
                "db.example.internal",
                "--remote-port",
                "3306",
                "--local-port",
                "13306",
                "--target-mode",
                "fixed",
                "--target-instance-id",
                "i-0123456789abcdef0",
            ],
            services=app,
        )
        == 0
    )
    assert app.tunnel_sessions.update.call_args.args[0].tunnel_id == 4
    capsys.readouterr()

    assert main(["rds", "session", "delete", "dev-db"], services=app) == 0
    app.tunnel_sessions.delete.assert_called_once_with("dev-db", None)


def test_session_clone_uses_application_use_case_and_stable_outputs(capsys) -> None:
    app = services()
    clone = saved()
    app.tunnel_sessions.clone.return_value = clone

    assert (
        main(
            [
                "rds",
                "session",
                "clone",
                "dev-db",
                "--name",
                "dev-db-copy",
                "--profile",
                "dev",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["id"] == clone.id
    app.tunnel_sessions.clone.assert_called_once_with("dev-db", "dev-db-copy", "dev")

    app.tunnel_sessions.clone.side_effect = ConfigurationError(
        "rds.session.name.duplicate", "test duplicate"
    )
    assert (
        main(
            [
                "rds",
                "session",
                "clone",
                "dev-db",
                "--name",
                "dev-db-copy",
                "--output",
                "json",
            ],
            services=app,
        )
        == 10
    )
    assert json.loads(capsys.readouterr().err)["error"]["code"] == ("rds.session.name.duplicate")


def test_rds_session_help_includes_clone_contract(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["rds", "session", "--help"])

    assert raised.value.code == 0
    assert "{list,show,create,update,clone,delete}" in capsys.readouterr().out


def test_tunnel_start_does_not_read_mfa_when_session_is_ready(monkeypatch, capsys) -> None:
    app = services()
    result = TunnelConnectionResult(
        saved(),
        "i-0123456789abcdef0",
        TunnelHandle("session-test", TunnelOwner.FOREGROUND, OperationState.SUCCEEDED),
        0,
    )
    app.rds_tunnels.start.return_value = OperationResult(
        "op", OperationState.SUCCEEDED, value=result
    )
    read = Mock(side_effect=AssertionError("MFA input should not be read"))
    monkeypatch.setattr("aws_connect.cli_main._read_mfa", read)

    assert (
        main(
            ["rds", "tunnel", "start", "dev-db", "--profile", "dev", "--output", "json"],
            services=app,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["owner"] == "foreground"
    read.assert_not_called()


def test_tunnel_typed_port_error_uses_central_mapper(capsys) -> None:
    app = services()
    app.rds_tunnels.start.side_effect = PortAlreadyInUseError(
        "rds.tunnel.local_port_in_use", "test"
    )
    assert main(["rds", "tunnel", "start", "dev-db", "--output", "json"], services=app) == 50
    assert json.loads(capsys.readouterr().err)["error"]["code"] == ("rds.tunnel.local_port_in_use")


def test_mfa_cancel_returns_standard_interrupt_exit(monkeypatch, capsys) -> None:
    app = services()
    app.rds_tunnels.start.return_value = OperationResult("op", OperationState.MFA_REQUIRED)
    app.rds_tunnels.resume.return_value = OperationResult("op", OperationState.CANCELLED)
    monkeypatch.setattr("aws_connect.cli_main._read_mfa", lambda _: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    assert main(["rds", "tunnel", "start", "dev-db"], services=app) == 130
    assert capsys.readouterr().err == "operation.cancelled\n"


def test_noninteractive_rds_mfa_cancels_pending_and_returns_exit_20(monkeypatch, capsys) -> None:
    app = services()
    app.rds_tunnels.start.return_value = OperationResult("auth-op", OperationState.MFA_REQUIRED)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["rds", "tunnel", "start", "dev-db"], services=app) == 20
    assert capsys.readouterr().err.startswith("auth.mfa_required")
    app.rds_tunnels.cancel.assert_called_once_with("auth-op")
    app.rds_tunnels.resume.assert_not_called()


def test_detached_status_and_stop_are_not_cli_commands() -> None:
    parser = build_parser()
    for command in ("status", "stop"):
        try:
            parser.parse_args(["rds", "tunnel", command])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError(f"detached command unexpectedly supported: {command}")
