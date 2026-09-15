from datetime import UTC, datetime, timedelta

import pytest

from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials, SessionCredentials
from aws_connect.domain.errors import ConfigurationError


def test_profile_rejects_invalid_account() -> None:
    with pytest.raises(ConfigurationError) as caught:
        AwsProfile(
            None,
            "dev",
            "ap-northeast-2",
            "wrong",
            "developer",
            "arn:aws:iam::wrong:mfa/developer",
            b"protected-a",
            b"protected-b",
        )

    assert caught.value.message_code == "profile.account_id.invalid"


def test_plain_credentials_repr_never_contains_values() -> None:
    credentials = PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value")

    assert "ACCESSKEYTEST0001" not in repr(credentials)
    assert "not-sensitive-test-value" not in repr(credentials)


def test_session_refresh_threshold_is_thirty_minutes() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    session = SessionCredentials(1, b"a", b"b", b"c", now + timedelta(minutes=30), now)

    assert session.needs_refresh(now)
    assert not SessionCredentials(
        1, b"a", b"b", b"c", now + timedelta(minutes=31), now
    ).needs_refresh(now)
