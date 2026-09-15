import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aws_connect.application.authentication_service import AuthenticationStatus
from aws_connect.application.profile_service import ProfileSummary
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import build_parser, main
from aws_connect.domain.errors import ConfigurationError, CredentialValidationError


def summary() -> ProfileSummary:
    return ProfileSummary(
        1,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        True,
    )


def services() -> ApplicationServices:
    profiles = Mock()
    authentication = Mock()
    operations = Mock()
    return ApplicationServices(
        profiles,
        authentication,
        operations,
        Mock(),
        connection_lifecycle=Mock(),
    )


def test_profile_parser_exposes_explicit_mfa_usage_flags() -> None:
    parser = build_parser()
    common = [
        "profile",
        "create",
        "--name",
        "dev",
        "--region",
        "ap-northeast-2",
        "--account-id",
        "123456789012",
        "--user-id",
        "developer",
    ]

    assert parser.parse_args(common).mfa is None
    assert parser.parse_args([*common, "--mfa"]).mfa is True
    assert parser.parse_args([*common, "--no-mfa"]).mfa is False


def test_profile_create_passes_disabled_mfa_to_shared_request(capsys, monkeypatch) -> None:
    app = services()
    app.profiles.create.return_value = replace(summary(), mfa_enabled=False)
    monkeypatch.setattr(
        "aws_connect.cli_main._read_credentials",
        lambda _stdin: ("ACCESSKEYTEST0001", "not-sensitive-test-value"),
    )

    exit_code = main(
        [
            "profile",
            "create",
            "--name",
            "automation",
            "--region",
            "ap-northeast-2",
            "--account-id",
            "123456789012",
            "--user-id",
            "developer",
            "--no-mfa",
            "--output",
            "json",
        ],
        services=app,
    )

    assert exit_code == 0
    request = app.profiles.create.call_args.args[0]
    assert request.mfa_enabled is False
    assert json.loads(capsys.readouterr().out)["mfa_enabled"] is False


@pytest.mark.parametrize(
    ("extra", "stop_active"),
    [([], False), (["--stop-active"], True)],
)
def test_profile_delete_uses_checked_connection_lifecycle(
    capsys, extra: list[str], stop_active: bool
) -> None:
    app = services()

    assert main(["profile", "delete", "dev", *extra, "--output", "json"], services=app) == 0

    assert json.loads(capsys.readouterr().out) == {"deleted": True}
    app.connection_lifecycle.delete.assert_called_once_with(  # type: ignore[union-attr]
        "dev", stop_active=stop_active
    )


def test_profile_delete_without_stop_active_preserves_typed_active_error(capsys) -> None:
    app = services()
    app.connection_lifecycle.delete.side_effect = ConfigurationError(  # type: ignore[union-attr]
        "profile.connections.active", "test"
    )

    assert main(["profile", "delete", "dev"], services=app) == 10

    assert capsys.readouterr().err.startswith("profile.connections.active")


def test_profile_list_json_is_stable_and_contains_no_credentials(capsys) -> None:
    app = services()
    app.profiles.list.return_value = [summary()]

    assert main(["profile", "list", "--output", "json"], services=app) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["profiles"][0]["name"] == "dev"
    assert "access_key" not in json.dumps(payload)
    assert "secret_key" not in json.dumps(payload)


def test_auth_status_uses_shared_service(capsys) -> None:
    app = services()
    app.authentication.status.return_value = AuthenticationStatus(
        summary(), "MFA_REQUIRED", None, False
    )

    assert main(["auth", "status", "--profile", "dev", "--output", "json"], services=app) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "MFA_REQUIRED"
    app.authentication.status.assert_called_once_with("dev")


def test_typed_error_is_json_on_stderr_without_technical_cause(capsys) -> None:
    app = services()
    app.profiles.show.side_effect = CredentialValidationError(
        "credentials.invalid", "literal-sensitive-value"
    )

    assert main(["profile", "show", "dev", "--output", "json"], services=app) == 20
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "credentials.invalid"
    assert "literal-sensitive-value" not in captured.err


@pytest.mark.parametrize("output", ["text", "json"])
def test_unexpected_error_has_code_70_and_correlation_without_cause(capsys, output) -> None:
    app = services()
    app.profiles.list.side_effect = RuntimeError("literal-sensitive-value")

    assert main(["profile", "list", "--output", output], services=app) == 70
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "literal-sensitive-value" not in captured.err
    if output == "json":
        error = json.loads(captured.err)["error"]
        assert error["code"] == "internal.error"
        assert error["correlation_id"]
    else:
        assert captured.err.startswith("internal.error (correlation: ")


def test_secret_credentials_are_not_supported_as_arguments() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(
            [
                "profile",
                "create",
                "--name",
                "dev",
                "--region",
                "ap-northeast-2",
                "--account-id",
                "123456789012",
                "--user-id",
                "developer",
                "--secret-key",
                "forbidden",
            ]
        )

    assert caught.value.code == 2
