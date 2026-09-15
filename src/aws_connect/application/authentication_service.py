"""Authentication, session reuse and single-resume MFA coordination."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from aws_connect.application.operations import (
    MAX_PENDING_AUTHENTICATION_OPERATIONS,
    MfaChallenge,
    OperationResult,
    OperationState,
    authentication_capacity_error,
    new_operation_id,
)
from aws_connect.application.ports import Clock, CredentialProtector, IdentityGateway, ProfileStore
from aws_connect.application.profile_service import ProfileService, ProfileSummary
from aws_connect.domain.aws_profile import PlainCredentials, SessionCredentials
from aws_connect.domain.errors import (
    ConfigurationError,
    CredentialValidationError,
    DataProtectionError,
    MfaValidationError,
)

_MFA_CODE = re.compile(r"^\d{6}$")


@dataclass(frozen=True, slots=True)
class AuthenticationStatus:
    profile: ProfileSummary
    state: str
    expires_at_utc: datetime | None
    reusable: bool


class AuthenticationService:
    """Validate long-lived credentials and safely replace temporary sessions."""

    def __init__(
        self,
        profiles: ProfileService,
        store: ProfileStore,
        protector: CredentialProtector,
        gateway: IdentityGateway,
        clock: Clock,
    ) -> None:
        self._profiles = profiles
        self._store = store
        self._protector = protector
        self._gateway = gateway
        self._clock = clock
        self._session_guard = SessionGuard(profiles, store, protector, gateway, clock)
        self._refresh_locks: dict[int, Lock] = {}
        self._refresh_locks_guard = Lock()

    @property
    def session_guard(self) -> SessionGuard:
        return self._session_guard

    def status(self, selector: str | int | None = None) -> AuthenticationStatus:
        profile = self._profiles.resolve(selector)
        profile_id = profile.require_id()
        session = self._store.get_session(profile_id)
        if session is None:
            return AuthenticationStatus(
                self._profiles.show(profile_id), "MFA_REQUIRED", None, False
            )
        reusable = not session.needs_refresh(self._clock.now())
        if not reusable:
            self._store.delete_session(profile_id)
        return AuthenticationStatus(
            self._profiles.show(profile_id),
            "READY" if reusable else "MFA_REQUIRED",
            session.expires_at_utc,
            reusable,
        )

    def validate(self, selector: str | int | None = None) -> AuthenticationStatus:
        profile = self._profiles.resolve(selector)
        identity = self._gateway.get_identity(
            self._profiles.reveal_credentials(profile), profile.region
        )
        self._assert_identity(
            profile.account_id, profile.user_id, identity.account_id, identity.user_id
        )
        return self.status(profile.id)

    def reusable_credentials(self, selector: str | int | None = None) -> PlainCredentials | None:
        """Compatibility facade; new AWS features inject ``session_guard`` directly."""

        return self._session_guard.credentials(selector)

    def discard_cached_session(self, selector: str | int | None = None) -> int:
        """Discard only the disposable temporary session for one profile.

        Long-lived access credentials belong to the profile record and are never
        changed by authentication recovery.  Keeping this operation separate from
        ``status`` preserves the GUI startup contract: status remains a local,
        non-network projection and callers must explicitly request recovery.
        """

        profile_id = self._profiles.resolve(selector).require_id()
        self._store.delete_session(profile_id)
        return profile_id

    def refresh(self, profile_id: int, mfa_code: str) -> AuthenticationStatus:
        if not _MFA_CODE.fullmatch(mfa_code):
            raise MfaValidationError(
                message_code="mfa.code.invalid",
                technical_cause="MFA code must contain exactly six digits",
            )
        refresh_lock = self._profile_refresh_lock(profile_id)
        with refresh_lock:
            # Another operation may have refreshed this profile while this caller
            # waited. Re-check inside the per-profile critical section so STS is
            # invoked at most once for concurrent challenges.
            if self._session_guard.credentials(profile_id) is not None:
                return self.status(profile_id)
            profile = self._profiles.resolve(profile_id)
            issued = self._gateway.get_session_token(
                self._profiles.reveal_credentials(profile),
                profile.region,
                profile.mfa_arn,
                mfa_code,
            )
            identity = self._gateway.get_identity(issued.credentials, profile.region)
            self._assert_identity(
                profile.account_id, profile.user_id, identity.account_id, identity.user_id
            )
            now = self._clock.now().astimezone(UTC)
            session = SessionCredentials(
                profile_id=profile_id,
                encrypted_access_key=self._protector.protect(issued.credentials.access_key),
                encrypted_secret_key=self._protector.protect(issued.credentials.secret_key),
                encrypted_session_token=self._protector.protect(
                    issued.credentials.session_token or ""
                ),
                expires_at_utc=issued.expires_at_utc.astimezone(UTC),
                verified_at_utc=now,
            )
            self._store.put_session(session)
            return self.status(profile_id)

    def _profile_refresh_lock(self, profile_id: int) -> Lock:
        with self._refresh_locks_guard:
            return self._refresh_locks.setdefault(profile_id, Lock())

    @staticmethod
    def _assert_identity(
        expected_account: str,
        expected_user: str,
        actual_account: str,
        actual_user: str,
    ) -> None:
        if expected_account != actual_account or expected_user != actual_user:
            raise CredentialValidationError(
                message_code="credentials.identity_mismatch",
                technical_cause="Session identity does not match its owning profile",
                aws_service="sts",
                aws_action="GetCallerIdentity",
            )


class SessionGuard:
    """Return only current, identity-matching session credentials to AWS features."""

    def __init__(
        self,
        profiles: ProfileService,
        store: ProfileStore,
        protector: CredentialProtector,
        gateway: IdentityGateway,
        clock: Clock,
    ) -> None:
        self._profiles = profiles
        self._store = store
        self._protector = protector
        self._gateway = gateway
        self._clock = clock

    def credentials(self, selector: str | int | None = None) -> PlainCredentials | None:
        profile = self._profiles.resolve(selector)
        profile_id = profile.require_id()
        session = self._store.get_session(profile_id)
        if session is None:
            return None
        if session.needs_refresh(self._clock.now()):
            self._store.delete_session(profile_id)
            return None
        try:
            credentials = PlainCredentials(
                self._protector.unprotect(session.encrypted_access_key),
                self._protector.unprotect(session.encrypted_secret_key),
                self._protector.unprotect(session.encrypted_session_token),
            )
            identity = self._gateway.get_identity(credentials, profile.region)
            AuthenticationService._assert_identity(
                profile.account_id, profile.user_id, identity.account_id, identity.user_id
            )
        except (ConfigurationError, CredentialValidationError, DataProtectionError):
            self._store.delete_session(profile_id)
            return None
        return credentials

    def require_credentials(self, profile_id: int) -> PlainCredentials:
        """Return reusable credentials or raise the shared MFA-required error."""

        credentials = self.credentials(profile_id)
        if credentials is None:
            raise CredentialValidationError(
                message_code="auth.mfa_required",
                technical_cause="No reusable authenticated session is available",
            )
        return credentials


@dataclass(slots=True)
class _PendingRefresh:
    profile_id: int
    expires_at: datetime
    resumed: bool = False


class OperationCoordinator:
    """Hold MFA refresh requests in memory and permit exactly one resume."""

    def __init__(self, authentication: AuthenticationService, clock: Clock) -> None:
        self._authentication = authentication
        self._clock = clock
        self._pending: dict[str, _PendingRefresh] = {}
        self._lock = Lock()

    def start_refresh(
        self,
        selector: str | int | None = None,
        *,
        discard_cached_session: bool = False,
    ) -> OperationResult[AuthenticationStatus]:
        with self._lock:
            self._sweep_expired_locked()
        if discard_cached_session:
            self._authentication.discard_cached_session(selector)
        status = self._authentication.status(selector)
        operation_id = new_operation_id()
        if status.reusable:
            return OperationResult(operation_id, OperationState.SUCCEEDED, value=status)
        expires_at = self._clock.now() + timedelta(minutes=5)
        challenge = MfaChallenge(
            operation_id=operation_id,
            profile_id=status.profile.id,
            device_arn=status.profile.mfa_arn,
            expires_at=expires_at,
        )
        with self._lock:
            # Status inspection intentionally runs outside the lock because it may
            # validate cached credentials over AWS. Re-check here to keep the bound
            # correct when several callers arrive concurrently.
            self._sweep_expired_locked()
            if len(self._pending) >= MAX_PENDING_AUTHENTICATION_OPERATIONS:
                return self._capacity_failure(operation_id)
            self._pending[operation_id] = _PendingRefresh(status.profile.id, expires_at)
        return OperationResult(operation_id, OperationState.MFA_REQUIRED, challenge=challenge)

    def resume(
        self, operation_id: str, mfa_code: str | None
    ) -> OperationResult[AuthenticationStatus]:
        with self._lock:
            pending = self._pending.get(operation_id)
            if pending is None or pending.resumed:
                return self._failure(operation_id, "mfa.operation.not_resumable")
            pending.resumed = True
            self._pending.pop(operation_id, None)
        if mfa_code is None:
            return OperationResult(operation_id, OperationState.CANCELLED)
        if self._clock.now() >= pending.expires_at:
            return self._failure(operation_id, "mfa.challenge.expired")
        try:
            status = self._authentication.refresh(pending.profile_id, mfa_code)
        except MfaValidationError as error:
            return OperationResult(operation_id, OperationState.FAILED, error=error)
        return OperationResult(operation_id, OperationState.SUCCEEDED, value=status)

    def cancel(self, operation_id: str) -> None:
        """Forget a pending refresh owned by a higher-level operation."""

        with self._lock:
            self._pending.pop(operation_id, None)

    def _sweep_expired_locked(self) -> None:
        now = self._clock.now()
        expired = [key for key, pending in self._pending.items() if now >= pending.expires_at]
        for key in expired:
            self._pending.pop(key, None)

    @staticmethod
    def _failure(operation_id: str, code: str) -> OperationResult[AuthenticationStatus]:
        return OperationResult(
            operation_id,
            OperationState.FAILED,
            error=MfaValidationError(message_code=code, technical_cause=code),
        )

    @staticmethod
    def _capacity_failure(operation_id: str) -> OperationResult[AuthenticationStatus]:
        return OperationResult(
            operation_id,
            OperationState.FAILED,
            error=authentication_capacity_error(),
        )
