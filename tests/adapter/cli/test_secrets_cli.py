import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from aws_connect.application.operations import MfaChallenge, OperationResult, OperationState
from aws_connect.application.secrets_service import SecretField, SecretKind, SecretResult
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import main
from aws_connect.domain.errors import AwsPermissionError

RAW = "phase-seven-private-value"  # pragma: allowlist secret


def services() -> ApplicationServices:
    return ApplicationServices(Mock(), Mock(), Mock(), Mock(), secrets=Mock())


def result() -> SecretResult:
    return SecretResult(
        "arn:test",
        SecretKind.JSON,
        (
            SecretField("host", "db.internal", False),
            SecretField("password", RAW, True),
        ),
        "version",
        ("AWSCURRENT",),
    )


def test_default_human_and_json_output_never_print_sensitive_values(capsys) -> None:
    app = services()
    app.secrets.get.return_value = result()

    assert main(["secrets", "get", "db"], services=app) == 0
    human = capsys.readouterr().out
    assert "db.internal" in human
    assert RAW not in human
    assert "REDACTED" in human

    assert main(["secrets", "get", "db", "--output", "json"], services=app) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revealed"] is False
    assert payload["fields"][1]["value"] == "***REDACTED***"
    assert RAW not in json.dumps(payload)
    app.secrets.get.assert_called_with("db", None)


def test_explicit_reveal_is_the_only_cli_path_that_prints_raw_value(capsys) -> None:
    app = services()
    app.secrets.get.return_value = result()

    assert main(["secrets", "get", "db", "--reveal", "--output", "json"], services=app) == 0

    output = capsys.readouterr().out
    assert RAW in output
    assert json.loads(output)["revealed"] is True


def test_typed_permission_error_is_centrally_mapped_without_aws_message(capsys) -> None:
    app = services()
    app.secrets.get.side_effect = AwsPermissionError(
        "aws.permission.denied",
        f"Access denied for {RAW}",
        aws_service="secretsmanager",
        aws_action="GetSecretValue",
    )

    assert main(["secrets", "get", "db", "--output", "json"], services=app) == 30

    error = capsys.readouterr().err
    assert RAW not in error
    assert json.loads(error)["error"]["aws_action"] == "GetSecretValue"

    assert main(["secrets", "get", "db"], services=app) == 30
    human = capsys.readouterr().err
    assert "secretsmanager:GetSecretValue" in human
    assert RAW not in human


def test_secret_get_resumes_shared_mfa_without_exposing_code(monkeypatch, capsys) -> None:
    coordinator = Mock()
    app = ApplicationServices(
        Mock(), Mock(), Mock(), Mock(), secrets=Mock(), authenticated_operations=coordinator
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
    coordinator.resume.return_value = OperationResult("auth-op", OperationState.SUCCEEDED, result())
    monkeypatch.setattr("sys.stdin", io.StringIO("123456\n"))

    assert main(["secrets", "get", "db", "--mfa-stdin", "--output", "json"], services=app) == 0
    output = capsys.readouterr().out
    assert "123456" not in output
    assert json.loads(output)["fields"][0]["value"] == "db.internal"
    coordinator.resume.assert_called_once_with("auth-op", "123456")
