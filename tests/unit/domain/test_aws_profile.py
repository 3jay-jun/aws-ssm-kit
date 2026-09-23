from datetime import UTC, datetime, timedelta

import pytest

from aws_connect.domain.aws_profile import (
    AwsProfile,
    PlainCredentials,
    SessionCredentials,
    is_long_session_duration,
)
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


def test_session_refresh_window_is_proportional_and_capped_at_thirty_minutes() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    one_hour = SessionCredentials(1, b"a", b"b", b"c", now + timedelta(hours=1), now)
    twelve_hours = SessionCredentials(1, b"a", b"b", b"c", now + timedelta(hours=12), now)

    assert not one_hour.needs_refresh(now + timedelta(minutes=53, seconds=59))
    assert one_hour.needs_refresh(now + timedelta(minutes=54))
    assert not twelve_hours.needs_refresh(now + timedelta(hours=11, minutes=29))
    assert twelve_hours.needs_refresh(now + timedelta(hours=11, minutes=30))


def test_profile_session_duration_is_validated_and_long_sessions_are_classified() -> None:
    profile = AwsProfile(
        None,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"protected-a",
        b"protected-b",
        session_duration_hours=24,
    )

    assert profile.session_duration_hours == 24
    assert is_long_session_duration(24)
    assert not is_long_session_duration(12)

    with pytest.raises(ConfigurationError) as caught:
        AwsProfile(
            None,
            "invalid",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "arn:aws:iam::123456789012:mfa/developer",
            b"protected-a",
            b"protected-b",
            session_duration_hours=2,
        )
    assert caught.value.message_code == "profile.session_duration.invalid"
