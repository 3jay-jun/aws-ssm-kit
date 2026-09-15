"""Pure display mappings for the authenticated application shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aws_connect.application.authentication_service import AuthenticationStatus
from aws_connect.application.operations import ProgressEvent
from aws_connect.application.profile_service import ProfileSummary


@dataclass(frozen=True, slots=True)
class AuthenticationHeaderViewModel:
    profile_name: str
    state_text: str
    account_id: str
    user_id: str
    expiry_text: str
    authenticated: bool


@dataclass(frozen=True, slots=True)
class ActiveTunnelSummaryViewModel:
    name: str
    local_port: int
    host: str
    remote_port: int
    operation_id: str | None = None

    @property
    def local_address(self) -> str:
        return f"127.0.0.1:{self.local_port}"

    @property
    def display_text(self) -> str:
        return f"{self.name} · {self.local_address} → {self.host}:{self.remote_port}"


@dataclass(frozen=True, slots=True)
class S3ProgressViewModel:
    operation_id: str
    target_uri: str | None
    completed: int | None
    total: int | None
    percent: int | None
    status_text: str


def build_s3_progress_view_model(event: ProgressEvent) -> S3ProgressViewModel:
    percent = min(100, int((event.completed or 0) * 100 / event.total)) if event.total else None
    status = f"업로드 중: {event.target}" if event.target else event.message_code
    return S3ProgressViewModel(
        event.operation_id,
        event.target,
        event.completed,
        event.total,
        percent,
        status,
    )


def empty_authentication_header() -> AuthenticationHeaderViewModel:
    return AuthenticationHeaderViewModel(
        profile_name="프로필 없음",
        state_text="인증정보 없음",
        account_id="—",
        user_id="—",
        expiry_text="—",
        authenticated=False,
    )


def checking_authentication_header(profile: ProfileSummary) -> AuthenticationHeaderViewModel:
    return AuthenticationHeaderViewModel(
        profile_name=profile.name,
        state_text="인증 확인 중",
        account_id=_format_account(profile.account_id),
        user_id=profile.user_id,
        expiry_text="확인 중…",
        authenticated=False,
    )


def build_authentication_header(
    status: AuthenticationStatus, now: datetime | None = None
) -> AuthenticationHeaderViewModel:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    ready = status.state == "READY" and status.reusable
    return AuthenticationHeaderViewModel(
        profile_name=status.profile.name,
        state_text="인증됨" if ready else "MFA 인증 필요",
        account_id=_format_account(status.profile.account_id),
        user_id=status.profile.user_id,
        expiry_text=_expiry(status.expires_at_utc, current),
        authenticated=ready,
    )


def _format_account(account_id: str) -> str:
    if len(account_id) != 12:
        return account_id
    return f"{account_id[:4]} {account_id[4:8]} {account_id[8:]}"


def _expiry(expires_at: datetime | None, now: datetime) -> str:
    if expires_at is None:
        return "토큰 없음"
    remaining = max(0, int((expires_at.astimezone(UTC) - now).total_seconds()))
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d} 남음"
