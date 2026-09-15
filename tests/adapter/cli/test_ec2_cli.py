import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from aws_connect.application.ec2_service import Ec2ConnectionResult, Ec2Target
from aws_connect.application.operations import MfaChallenge, OperationResult, OperationState
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import main
from aws_connect.domain.errors import PluginExecutionError, TargetNotConnectedError


def services() -> ApplicationServices:
    return ApplicationServices(Mock(), Mock(), Mock(), Mock(), Mock())


def test_ec2_list_json_uses_application_service(capsys) -> None:
    app = services()
    app.ec2.list_targets.return_value = [
        Ec2Target("i-online", "web", "10.0.0.1", "Amazon Linux", "Online")
    ]

    assert main(["ec2", "list", "--profile", "dev", "--output", "json"], services=app) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "instances": [
            {
                "instance_id": "i-online",
                "name": "web",
                "ping_status": "Online",
                "platform_name": "Amazon Linux",
                "private_ip_address": "10.0.0.1",
            }
        ]
    }
    app.ec2.list_targets.assert_called_once_with("dev")


def test_ec2_connect_json_and_keyboard_interrupt_cleanup_result(capsys) -> None:
    app = services()
    app.ec2.connect.return_value = Ec2ConnectionResult("i-online", "session-test", 0)
    assert (
        main(
            ["ec2", "connect", "i-online", "--profile", "dev", "--output", "json"],
            services=app,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["session_id"] == "session-test"

    app.ec2.connect.side_effect = KeyboardInterrupt
    assert main(["ec2", "connect", "i-online"], services=app) == 130
    assert capsys.readouterr().err == "operation.cancelled\n"


def test_ec2_cancel_cleanup_failure_uses_typed_plugin_exit_not_130(capsys) -> None:
    app = services()
    app.ec2.connect.side_effect = PluginExecutionError(
        "session.cancel.cleanup_failed", "EndSession failed (RuntimeError)"
    )

    assert main(["ec2", "connect", "i-online", "--output", "json"], services=app) == 60
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "session.cancel.cleanup_failed"


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (
            TargetNotConnectedError("ec2.target.not_online", "test"),
            50,
        ),
        (PluginExecutionError("plugin.exit.nonzero", "test"), 60),
    ],
)
def test_ec2_typed_errors_use_central_exit_mapping(capsys, error, exit_code) -> None:
    app = services()
    app.ec2.connect.side_effect = error

    assert main(["ec2", "connect", "i-online", "--output", "json"], services=app) == exit_code
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == error.message_code


def test_ec2_list_reads_mfa_from_stdin_and_resumes_shared_operation_once(
    monkeypatch, capsys
) -> None:
    coordinator = Mock()
    app = ApplicationServices(
        Mock(), Mock(), Mock(), Mock(), Mock(), authenticated_operations=coordinator
    )
    challenge = MfaChallenge(
        "auth-op",
        7,
        "arn:aws:iam::123456789012:mfa/developer",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    coordinator.start.return_value = OperationResult(
        "auth-op", OperationState.MFA_REQUIRED, challenge=challenge
    )
    coordinator.resume.return_value = OperationResult(
        "auth-op",
        OperationState.SUCCEEDED,
        [Ec2Target("i-online", "web", "10.0.0.1", "Linux", "Online")],
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("123456\n"))

    assert main(["ec2", "list", "--mfa-stdin", "--output", "json"], services=app) == 0
    assert json.loads(capsys.readouterr().out)["instances"][0]["instance_id"] == "i-online"
    coordinator.resume.assert_called_once_with("auth-op", "123456")
