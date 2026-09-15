from datetime import UTC, datetime

from aws_connect.application.authentication_service import AuthenticationStatus
from aws_connect.application.operations import (
    MfaChallenge,
    OperationResult,
    OperationState,
)
from aws_connect.application.profile_service import ProfileSummary
from aws_connect.presentation.cli.profile_auth import (
    auth_payload,
    operation_payload,
    profile_payload,
    profiles_payload,
    render,
)


def profile() -> ProfileSummary:
    return ProfileSummary(
        1,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        True,
    )


def test_profile_and_auth_rendering_contracts() -> None:
    item = profile_payload(profile())
    listed = profiles_payload([profile()])
    empty = profiles_payload([])
    status = auth_payload(AuthenticationStatus(profile(), "READY", None, True))

    assert render(item, output="text").startswith("dev (")
    assert render(listed, output="text").startswith("* 1: dev")
    assert render(empty, output="text") == "No profiles."
    assert render(status, output="text") == "dev: READY"
    assert '"name": "dev"' in render(item, output="json")


def test_operation_payload_includes_challenge_or_value() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    challenge = MfaChallenge("operation", 1, profile().mfa_arn, now)
    waiting = operation_payload(
        OperationResult("operation", OperationState.MFA_REQUIRED, challenge=challenge)
    )
    completed = operation_payload(
        OperationResult(
            "operation",
            OperationState.SUCCEEDED,
            value=AuthenticationStatus(profile(), "READY", now, True),
        )
    )

    assert waiting["challenge"]["profile_id"] == 1
    assert completed["value"]["expires_at_utc"] == now.isoformat()
