from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.authentication_service import (
    AuthenticationService,
    OperationCoordinator,
)
from aws_connect.application.operations import MAX_PENDING_AUTHENTICATION_OPERATIONS, OperationState
from aws_connect.application.ports import AwsIdentity, IssuedSession
from aws_connect.application.profile_service import ProfileService, SaveProfileRequest
from aws_connect.domain.aws_profile import PlainCredentials, SessionCredentials
from aws_connect.domain.errors import CredentialValidationError
from aws_connect.infrastructure.data_protection import FakeCredentialProtector
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 11, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class ConcurrentGateway:
    def __init__(self, clock: FixedClock, barrier: Barrier | None = None) -> None:
        self.clock = clock
        self.barrier = barrier
        self.refresh_calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = Lock()

    def get_identity(self, credentials: PlainCredentials, region: str) -> AwsIdentity:
        suffix = credentials.access_key[-1]
        user = "developer-2" if suffix == "2" else "developer"
        return AwsIdentity("123456789012", user, f"arn:aws:iam::123456789012:user/{user}")

    def get_session_token(
        self,
        credentials: PlainCredentials,
        region: str,
        mfa_arn: str | None,
        mfa_code: str | None,
    ) -> IssuedSession:
        with self._lock:
            self.refresh_calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            suffix = "2" if mfa_arn and "developer-2" in mfa_arn else "1"
            return IssuedSession(
                PlainCredentials(
                    f"SESSIONKEYTEST00{suffix}",
                    "temporary-test-secret",
                    "temporary-test-token",
                ),
                self.clock.now() + timedelta(hours=12),
            )
        finally:
            with self._lock:
                self.active -= 1


def build(tmp_path, gateway: ConcurrentGateway | None = None):
    store = SqliteProfileStore(tmp_path / "state.db")
    clock = gateway.clock if gateway is not None else FixedClock()
    gateway = gateway or ConcurrentGateway(clock)
    protector = FakeCredentialProtector()
    profiles = ProfileService(store, protector, gateway)
    authentication = AuthenticationService(profiles, store, protector, gateway, clock)
    refreshes = OperationCoordinator(authentication, clock)
    authenticated = AuthenticatedOperationCoordinator(refreshes, clock)
    created = profiles.create(
        SaveProfileRequest(
            "dev",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "ACCESSKEYTEST0001",
            "not-sensitive-test-value",
        )
    )
    return store, clock, gateway, profiles, authentication, refreshes, authenticated, created


def test_expired_token_is_deleted_refreshed_and_original_operation_continues_once(tmp_path) -> None:
    store, clock, gateway, _profiles, authentication, _refreshes, operations, profile = build(
        tmp_path
    )
    authentication.refresh(profile.id, "123456")
    clock.value += timedelta(hours=12)
    calls = 0

    def original_request() -> str:
        nonlocal calls
        calls += 1
        if authentication.session_guard.credentials(profile.id) is None:
            raise CredentialValidationError("auth.mfa_required", "test")
        return "continued"

    started = operations.start(profile.id, original_request)
    assert started.state is OperationState.MFA_REQUIRED
    assert store.get_session(profile.id) is None

    resumed = operations.resume(started.operation_id, "123456")

    assert resumed.state is OperationState.SUCCEEDED
    assert resumed.value == "continued"
    assert calls == 2  # initial guard check plus exactly one post-MFA execution
    assert gateway.refresh_calls == 2
    assert operations.resume(started.operation_id, "123456").error.message_code == (
        "auth.operation.not_resumable"
    )


def test_pending_callable_is_not_represented_and_expired_entries_are_swept(tmp_path) -> None:
    _store, clock, _gateway, _profiles, _auth, _refreshes, operations, profile = build(tmp_path)

    def contains_private_identifier() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    first = operations.start(profile.id, contains_private_identifier)
    assert "contains_private_identifier" not in repr(operations._pending[first.operation_id])
    clock.value += timedelta(minutes=6)
    operations.start(profile.id, contains_private_identifier)

    assert first.operation_id not in operations._pending
    assert operations.resume(first.operation_id, "123456").error.message_code == (
        "auth.operation.not_resumable"
    )


def test_successful_action_returns_without_starting_mfa(tmp_path) -> None:
    _store, _clock, gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    result = operations.start(profile.id, lambda: "already-authorized")

    assert result.state is OperationState.SUCCEEDED
    assert result.value == "already-authorized"
    assert gateway.refresh_calls == 0
    assert refreshes._pending == {}


def test_profile_without_mfa_refreshes_and_retries_authenticated_action_immediately(
    tmp_path,
) -> None:
    _store, _clock, gateway, profiles, authentication, refreshes, operations, _profile = build(
        tmp_path
    )
    profile = profiles.create(
        SaveProfileRequest(
            "automation",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "ACCESSKEYTEST0001",
            "not-sensitive-test-value",
            mfa_enabled=False,
        )
    )
    calls = 0

    def original_request() -> str:
        nonlocal calls
        calls += 1
        return authentication.session_guard.require_credentials(profile.id).access_key

    result = operations.start(profile.id, original_request)

    assert result.state is OperationState.SUCCEEDED
    assert result.value == "SESSIONKEYTEST001"
    assert calls == 2
    assert gateway.refresh_calls == 1
    assert refreshes._pending == {}


def test_feature_mfa_required_discards_expiry_reusable_cache_and_requires_mfa(tmp_path) -> None:
    store, _clock, gateway, profiles, authentication, refreshes, operations, profile = build(
        tmp_path
    )
    authentication.refresh(profile.id, "123456")
    protected_profile = profiles.resolve(profile.id)
    calls = 0

    def stale_caller_requires_mfa() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CredentialValidationError("auth.mfa_required", "stale caller state")
        return "retried"

    result = operations.start(profile.id, stale_caller_requires_mfa)

    assert result.state is OperationState.MFA_REQUIRED
    assert calls == 1
    assert store.get_session(profile.id) is None
    # Recovery deletes only the disposable session, never the stored long-lived keys.
    assert (
        profiles.resolve(profile.id).encrypted_access_key == protected_profile.encrypted_access_key
    )
    assert (
        profiles.resolve(profile.id).encrypted_secret_key == protected_profile.encrypted_secret_key
    )

    resumed = operations.resume(result.operation_id, "123456")

    assert resumed.state is OperationState.SUCCEEDED
    assert resumed.value == "retried"
    assert calls == 2
    assert gateway.refresh_calls == 2
    assert refreshes._pending == {}


def test_nonexpired_undecryptable_cache_recovers_through_one_mfa_resume(tmp_path) -> None:
    store, clock, gateway, _profiles, authentication, refreshes, operations, profile = build(
        tmp_path
    )
    store.put_session(
        SessionCredentials(
            profile.id,
            b"not-protected",
            b"not-protected",
            b"not-protected",
            clock.now() + timedelta(hours=12),
            clock.now(),
        )
    )
    # Status is intentionally expiry-only and makes no DPAPI or AWS call.
    assert authentication.status(profile.id).reusable
    calls = 0

    def guarded_action() -> str:
        nonlocal calls
        calls += 1
        authentication.session_guard.require_credentials(profile.id)
        return "recovered"

    started = operations.start(profile.id, guarded_action)

    assert started.state is OperationState.MFA_REQUIRED
    assert store.get_session(profile.id) is None
    assert gateway.refresh_calls == 0

    resumed = operations.resume(started.operation_id, "123456")

    assert resumed.state is OperationState.SUCCEEDED
    assert resumed.value == "recovered"
    assert calls == 2
    assert gateway.refresh_calls == 1
    assert operations.resume(started.operation_id, "123456").error.message_code == (
        "auth.operation.not_resumable"
    )


def test_nonexpired_identity_mismatch_cache_is_deleted_before_mfa_challenge(tmp_path) -> None:
    store, _clock, gateway, _profiles, authentication, _refreshes, operations, profile = build(
        tmp_path
    )
    authentication.refresh(profile.id, "123456")
    gateway.refresh_calls = 0
    original_identity = gateway.get_identity(
        PlainCredentials("SESSIONKEYTEST001", "temporary-test-secret", "temporary-test-token"),
        "ap-northeast-2",
    )

    def mismatched_identity(credentials: PlainCredentials, region: str) -> AwsIdentity:
        del credentials, region
        return AwsIdentity("000000000000", "other", "arn:aws:iam::000000000000:user/other")

    gateway.get_identity = mismatched_identity  # type: ignore[method-assign]

    started = operations.start(
        profile.id,
        lambda: authentication.session_guard.require_credentials(profile.id),
    )

    assert started.state is OperationState.MFA_REQUIRED
    assert store.get_session(profile.id) is None
    assert gateway.refresh_calls == 0

    gateway.get_identity = lambda _credentials, _region: original_identity  # type: ignore[method-assign]
    resumed = operations.resume(started.operation_id, "123456")
    assert resumed.state is OperationState.SUCCEEDED
    assert resumed.value is not None and resumed.value.session_token


def test_outer_pending_capacity_cancels_only_its_new_inner_challenge(tmp_path) -> None:
    _store, _clock, _gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    def requires_mfa() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    accepted = [
        operations.start(profile.id, requires_mfa)
        for _index in range(MAX_PENDING_AUTHENTICATION_OPERATIONS)
    ]
    # Simulate a higher-level challenge outliving a refresh-layer entry. The next
    # refresh can start, but the bounded callable store must reject it and clean it.
    refreshes.cancel(accepted[0].operation_id)

    rejected = operations.start(profile.id, requires_mfa)

    assert rejected.state is OperationState.FAILED
    assert rejected.error is not None
    assert rejected.error.message_code == "auth.operation.capacity_exceeded"
    assert rejected.operation_id not in refreshes._pending
    assert len(operations._pending) == MAX_PENDING_AUTHENTICATION_OPERATIONS


def test_direct_resume_of_expired_action_clears_both_pending_layers(tmp_path) -> None:
    _store, clock, _gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    def requires_mfa() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    started = operations.start(profile.id, requires_mfa)
    clock.value += timedelta(minutes=6)

    result = operations.resume(started.operation_id, "123456")

    assert result.state is OperationState.FAILED
    assert result.error is not None
    assert result.error.message_code == "mfa.challenge.expired"
    assert started.operation_id not in operations._pending
    assert started.operation_id not in refreshes._pending


def test_explicit_cancel_discards_authenticated_action(tmp_path) -> None:
    _store, _clock, _gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    def requires_mfa() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    started = operations.start(profile.id, requires_mfa)
    operations.cancel(started.operation_id)

    assert started.operation_id not in operations._pending
    assert started.operation_id not in refreshes._pending


def test_cancel_and_invalid_mfa_clear_both_pending_layers(tmp_path) -> None:
    _store, _clock, _gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    def requires_mfa() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    cancelled = operations.start(profile.id, requires_mfa)
    assert operations.resume(cancelled.operation_id, None).state is OperationState.CANCELLED
    assert cancelled.operation_id not in operations._pending
    assert cancelled.operation_id not in refreshes._pending

    rejected = operations.start(profile.id, requires_mfa)
    failed = operations.resume(rejected.operation_id, "bad")
    assert failed.state is OperationState.FAILED
    assert failed.error.message_code == "mfa.code.invalid"
    assert rejected.operation_id not in operations._pending
    assert rejected.operation_id not in refreshes._pending


def test_pending_capacity_rejects_only_new_request_and_preserves_live_challenges(
    tmp_path,
) -> None:
    _store, _clock, _gateway, _profiles, _auth, refreshes, operations, profile = build(tmp_path)

    def requires_mfa() -> str:
        raise CredentialValidationError("auth.mfa_required", "test")

    accepted = [
        operations.start(profile.id, requires_mfa)
        for _index in range(MAX_PENDING_AUTHENTICATION_OPERATIONS)
    ]
    rejected = operations.start(profile.id, requires_mfa)

    assert len(operations._pending) == MAX_PENDING_AUTHENTICATION_OPERATIONS
    assert len(refreshes._pending) == MAX_PENDING_AUTHENTICATION_OPERATIONS
    assert all(result.operation_id in operations._pending for result in accepted)
    assert all(result.operation_id in refreshes._pending for result in accepted)
    assert rejected.state is OperationState.FAILED
    assert rejected.error is not None
    assert rejected.error.message_code == "auth.operation.capacity_exceeded"
    assert rejected.operation_id not in operations._pending
    assert rejected.operation_id not in refreshes._pending
    assert all(
        operations.resume(result.operation_id, None).state is OperationState.CANCELLED
        for result in accepted
    )


def test_ready_profile_bypasses_pending_challenge_capacity(tmp_path) -> None:
    (
        _store,
        _clock,
        _gateway,
        profiles,
        authentication,
        refreshes,
        _operations,
        profile,
    ) = build(tmp_path)
    for _index in range(MAX_PENDING_AUTHENTICATION_OPERATIONS):
        assert refreshes.start_refresh(profile.id).state is OperationState.MFA_REQUIRED
    ready = profiles.create(
        SaveProfileRequest(
            "dev-2",
            "ap-northeast-2",
            "123456789012",
            "developer-2",
            "ACCESSKEYTEST0002",
            "another-test-secret",
        )
    )
    authentication.refresh(ready.id, "123456")

    result = refreshes.start_refresh(ready.id)

    assert result.state is OperationState.SUCCEEDED
    assert result.value is not None and result.value.reusable
    assert len(refreshes._pending) == MAX_PENDING_AUTHENTICATION_OPERATIONS


def test_same_profile_concurrent_refresh_issues_one_sts_token(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, _refreshes, _operations, profile = build(
        tmp_path
    )
    gateway.refresh_calls = 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(
            executor.map(lambda _index: authentication.refresh(profile.id, "123456"), range(2))
        )

    assert all(value.reusable for value in values)
    assert gateway.refresh_calls == 1
    assert store.get_session(profile.id) is not None


def test_same_profile_concurrent_feature_resumes_share_one_sts_refresh(tmp_path) -> None:
    store, _clock, gateway, _profiles, authentication, _refreshes, operations, profile = build(
        tmp_path
    )
    calls = 0
    calls_lock = Lock()

    def guarded_action() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        authentication.session_guard.require_credentials(profile.id)
        return "continued"

    first = operations.start(profile.id, guarded_action)
    second = operations.start(profile.id, guarded_action)
    gateway.refresh_calls = 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda operation_id: operations.resume(operation_id, "123456"),
                (first.operation_id, second.operation_id),
            )
        )

    assert all(result.state is OperationState.SUCCEEDED for result in results)
    assert all(result.value == "continued" for result in results)
    assert calls == 4
    assert gateway.refresh_calls == 1
    assert store.get_session(profile.id) is not None


def test_different_profiles_refresh_independently(tmp_path) -> None:
    clock = FixedClock()
    gateway = ConcurrentGateway(clock, Barrier(2))
    store, _clock, _gateway, profiles, authentication, *_rest = build(tmp_path, gateway)
    second = profiles.create(
        SaveProfileRequest(
            "dev-2",
            "ap-northeast-2",
            "123456789012",
            "developer-2",
            "ACCESSKEYTEST0002",
            "another-test-secret",
        )
    )
    first = profiles.show("dev")
    gateway.refresh_calls = 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(authentication.refresh, first.id, "123456"),
            executor.submit(authentication.refresh, second.id, "654321"),
        ]
        [future.result(timeout=10) for future in futures]

    assert gateway.refresh_calls == 2
    assert gateway.max_active == 2
    assert store.get_session(first.id) is not None
    assert store.get_session(second.id) is not None
