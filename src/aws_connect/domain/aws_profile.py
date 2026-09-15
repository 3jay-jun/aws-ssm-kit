"""AWS profile and temporary-session domain rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aws_connect.domain.errors import ConfigurationError

_ACCOUNT_ID = re.compile(r"^\d{12}$")
_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
_ACCESS_KEY = re.compile(r"^[A-Z0-9]{16,128}$")
SESSION_REFRESH_WINDOW = timedelta(minutes=30)


def default_mfa_arn(account_id: str, user_id: str) -> str:
    """Build the conventional IAM virtual MFA ARN."""

    return f"arn:aws:iam::{account_id}:mfa/{user_id}"


def validated_region(region: str) -> str:
    """Return a normalized AWS Region or raise the shared domain error."""

    normalized = region.strip()
    if not _REGION.fullmatch(normalized):
        raise _configuration("profile.region.invalid")
    return normalized


@dataclass(frozen=True, slots=True)
class AwsProfile:
    """Persisted non-secret profile metadata plus protected credentials."""

    id: int | None
    name: str
    region: str
    account_id: str
    user_id: str
    mfa_arn: str
    encrypted_access_key: bytes
    encrypted_secret_key: bytes
    is_default: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise _configuration("profile.name.required")
        object.__setattr__(self, "region", validated_region(self.region))
        if not _ACCOUNT_ID.fullmatch(self.account_id):
            raise _configuration("profile.account_id.invalid")
        if not self.user_id.strip() or "/" in self.user_id:
            raise _configuration("profile.user_id.invalid")
        if not self.mfa_arn.startswith(f"arn:aws:iam::{self.account_id}:mfa/"):
            raise _configuration("profile.mfa_arn.invalid")
        if not self.encrypted_access_key or not self.encrypted_secret_key:
            raise _configuration("profile.credentials.required")

    def require_id(self) -> int:
        """Return the persistent identity or fail for an unsaved profile."""

        if self.id is None:
            raise _configuration("profile.id.required")
        return self.id


@dataclass(frozen=True, slots=True, repr=False)
class PlainCredentials:
    """In-memory credentials; repr intentionally never reveals values."""

    access_key: str
    secret_key: str
    session_token: str | None = None

    def __post_init__(self) -> None:
        if not _ACCESS_KEY.fullmatch(self.access_key):
            raise _configuration("credentials.access_key.invalid")
        if len(self.secret_key) < 16:
            raise _configuration("credentials.secret_key.invalid")
        if self.session_token is not None and not self.session_token:
            raise _configuration("credentials.session_token.invalid")


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    """Protected temporary credentials stored for one profile."""

    profile_id: int
    encrypted_access_key: bytes
    encrypted_secret_key: bytes
    encrypted_session_token: bytes
    expires_at_utc: datetime
    verified_at_utc: datetime

    def needs_refresh(self, now: datetime) -> bool:
        """Return true when expired or within the shared refresh window."""

        normalized_now = now.astimezone(UTC)
        return self.expires_at_utc.astimezone(UTC) <= normalized_now + SESSION_REFRESH_WINDOW


def _configuration(code: str) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=code)
