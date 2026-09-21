"""Pure display mappings for the authenticated application shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.application.authentication_service import AuthenticationStatus
from aws_connect.application.operations import ProgressEvent
from aws_connect.application.profile_service import ProfileSummary
from aws_connect.domain.execution_log import ExecutionResult


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
        return f"{self.name} · {self.connection_copy_text('mapping')}"

    def connection_copy_text(self, copy_format: str = "host_port") -> str:
        formats = {
            "host_port": f"Host: 127.0.0.1\nPort: {self.local_port}",
            "host": "127.0.0.1",
            "port": str(self.local_port),
            "address": self.local_address,
            "mapping": f"{self.local_address} → {self.host}:{self.remote_port}",
        }
        return formats[copy_format]


@dataclass(frozen=True, slots=True)
class S3ProgressViewModel:
    operation_id: str
    target_uri: str | None
    completed: int | None
    total: int | None
    percent: int | None
    status_text: str


@dataclass(frozen=True, slots=True)
class LogBadgeViewModel:
    key: str
    text: str
    icon: str | None = None
    color: str = "#172033"


@dataclass(frozen=True, slots=True)
class LogFeatureViewModel:
    key: str
    text: str
    icon: str


@dataclass(frozen=True, slots=True)
class ActivityLogRowViewModel:
    occurred_at: str
    feature: LogFeatureViewModel
    operation: str
    target: str
    result: LogBadgeViewModel
    message: str
    level: LogBadgeViewModel
    operation_id: str
    correlation_id: str
    aws_service: str
    aws_action: str
    aws_request_id: str
    phase: str
    error_category: str
    error_code: str
    required_permission: str

    def error_details(self, *, include_success: bool = False) -> dict[str, str]:
        """Only expose populated diagnostic fields for non-success outcomes."""
        if self.result.key == "success" and not include_success:
            return {}
        return {
            caption: value
            for caption, value in (
                ("오류 분류", self.error_category),
                ("오류 코드", self.error_code),
                ("필요 권한", self.required_permission),
            )
            if include_success or value != "-"
        }


_FEATURES = {
    "ec2": LogFeatureViewModel("ec2", "EC2", "tab-ec2.svg"),
    "rds": LogFeatureViewModel("rds", "RDS", "tab-rds.svg"),
    "s3": LogFeatureViewModel("s3", "S3", "tab-s3.svg"),
    "secrets": LogFeatureViewModel("secrets", "Secrets", "tab-secrets.svg"),
    "auth": LogFeatureViewModel("auth", "인증", "common-profile.svg"),
    "dashboard": LogFeatureViewModel("dashboard", "대시보드", "tab-dashboard.svg"),
    "program": LogFeatureViewModel("program", "프로그램", "logo.ico"),
}


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


def build_activity_log_row_view_model(entry: ManagedLogEntry) -> ActivityLogRowViewModel:
    return ActivityLogRowViewModel(
        occurred_at=entry.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        feature=log_feature_view_model(entry.feature),
        operation=entry.operation or _message_operation(entry.message_code),
        target=entry.target or "-",
        result=log_result_view_model(entry.result),
        message=entry.masked_detail or entry.message_code,
        level=log_level_view_model(entry.level or _level_from_result(entry.result)),
        operation_id=entry.operation_id or "-",
        correlation_id=entry.correlation_id or "-",
        aws_service=entry.aws_service or "-",
        aws_action=entry.aws_action or "-",
        aws_request_id=entry.aws_request_id or "-",
        phase=entry.phase.value,
        error_category=entry.error_category.value if entry.error_category else "-",
        error_code=entry.error_code or "-",
        required_permission=entry.required_permission or "-",
    )


def log_feature_view_model(feature: str) -> LogFeatureViewModel:
    value = feature.casefold()
    if "ec2" in value:
        return _FEATURES["ec2"]
    if "rds" in value:
        return _FEATURES["rds"]
    if "s3" in value:
        return _FEATURES["s3"]
    if "secret" in value:
        return _FEATURES["secrets"]
    if "auth" in value or "profile" in value:
        return _FEATURES["auth"]
    if "dashboard" in value:
        return _FEATURES["dashboard"]
    return _FEATURES["program"]


def log_result_view_model(result: str) -> LogBadgeViewModel:
    status = ExecutionResult.from_legacy(result)
    return {
        ExecutionResult.SUCCESS: LogBadgeViewModel(
            "success", "성공", "common-circle-check.svg", "#087443"
        ),
        ExecutionResult.WARNING: LogBadgeViewModel(
            "warning", "경고", "common-circle-exclamation.svg", "#b54708"
        ),
        ExecutionResult.FAILURE: LogBadgeViewModel(
            "failure", "실패", "common-circle-xmark.svg", "#b42318"
        ),
        ExecutionResult.CANCELLED: LogBadgeViewModel(
            "warning", "취소", "common-ban.svg", "#b54708"
        ),
    }[status]


def log_level_view_model(level: str) -> LogBadgeViewModel:
    value = level.upper()
    if value == "DEBUG":
        return LogBadgeViewModel("debug", "DEBUG")
    if value == "ERROR":
        return LogBadgeViewModel("error", "ERROR")
    if value == "WARNING":
        return LogBadgeViewModel("warning", "WARNING")
    return LogBadgeViewModel("info", "INFO")


def _level_from_result(result: str) -> str:
    badge = log_result_view_model(result)
    if badge.key == "failure":
        return "ERROR"
    if badge.key == "warning":
        return "WARNING"
    return "INFO"


def _message_operation(message_code: str) -> str:
    parts = [part for part in message_code.replace("_", ".").split(".") if part]
    return parts[-1] if parts else "-"


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
    pending_text = "MFA 인증 필요" if status.profile.mfa_enabled else "세션 발급 필요"
    return AuthenticationHeaderViewModel(
        profile_name=status.profile.name,
        state_text="인증됨" if ready else pending_text,
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


def ec2_connection_time(value: datetime | None, *, now: datetime | None = None) -> str:
    """Display connection history in the workstation's local timezone."""
    if value is None:
        return "-"
    local = value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")
