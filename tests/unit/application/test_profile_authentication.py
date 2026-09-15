from datetime import UTC, datetime, timedelta

import pytest

from aws_connect.application.authentication_service import (
    AuthenticationService,
    OperationCoordinator,
)
from aws_connect.application.operations import OperationState
from aws_connect.application.ports import AwsIdentity, IssuedSession
from aws_connect.application.profile_service import ProfileService, SaveProfileRequest
from aws_connect.domain.aws_profile import PlainCredentials, SessionCredentials
from aws_connect.domain.errors import (
    ConfigurationError,
    CredentialValidationError,
    MfaValidationError,
)
from aws_connect.infrastructure.data_protection import FakeCredentialProtector
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakeIdentityGateway:
    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.identity = AwsIdentity(
            "123456789012",
            "developer",
            "arn:aws:iam::123456789012:user/developer",
        )
        self.refresh_calls = 0
        self.reject_mfa = False

    def get_identity(self, credentials: PlainCredentials, region: str) -> AwsIdentity:
        return self.identity

    def get_session_token(
        self,
        credentials: PlainCredentials,
        region: str,
        mfa_arn: str,
        mfa_code: str,
    ) -> IssuedSession:
        self.refresh_calls += 1
        if self.reject_mfa:
            raise MfaValidationError("mfa.code.rejected", "test rejection")
        return IssuedSession(
            PlainCredentials("SESSIONKEYTEST001", "temporary-test-secret", "temporary-test-token"),
            self.clock.now() + timedelta(hours=12),
        )


def build_services(tmp_path):
    store = SqliteProfileStore(tmp_path / "state.db")
    protector = FakeCredentialProtector()
    clock = FixedClock()
    gateway = FakeIdentityGateway(clock)
    profiles = ProfileService(store, protector, gateway)
    authentication = AuthenticationService(profiles, store, protector, gateway, clock)
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
    return store, clock, gateway, profiles, authentication, created


def test_profile_creation_validates_expected_identity(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    gateway.identity = AwsIdentity("000000000000", "other", "arn:aws:iam::000000000000:user/other")

    with pytest.raises(CredentialValidationError):
        profiles.create(
            SaveProfileRequest(
                "bad",
                "ap-northeast-2",
                "123456789012",
                "developer",
                "ACCESSKEYTEST0002",
                "another-local-test-value",
            )
        )

    assert [item.name for item in profiles.list()] == [created.name]


def test_profile_selection_and_update_without_replacing_keys(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    second = profiles.create(
        SaveProfileRequest(
            "prod",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "ACCESSKEYTEST0002",
            "another-local-test-value",
        )
    )
    assert profiles.show().id == created.id
    assert profiles.show(str(second.id)).name == "prod"
    assert profiles.use("prod").is_default

    changed = profiles.update(
        SaveProfileRequest(
            "prod-renamed",
            "ap-northeast-2",
            "123456789012",
            "developer",
            profile_id=second.id,
        )
    )
    assert changed.name == "prod-renamed"
    with pytest.raises(ConfigurationError):
        profiles.show("missing")


def test_profile_update_without_mfa_arn_preserves_existing_custom_device(tmp_path) -> None:
    _store, _clock, _gateway, profiles, _authentication, created = build_services(tmp_path)
    custom_arn = "arn:aws:iam::123456789012:mfa/custom-device"
    customized = profiles.update(
        SaveProfileRequest(
            "dev",
            "ap-northeast-2",
            "123456789012",
            "developer",
            mfa_arn=custom_arn,
            profile_id=created.id,
        )
    )

    updated = profiles.update(
        SaveProfileRequest(
            "dev-renamed",
            "ap-northeast-2",
            "123456789012",
            "developer",
            profile_id=customized.id,
        )
    )

    assert updated.mfa_arn == custom_arn


def test_profile_update_rejects_partial_replacement(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)

    with pytest.raises(ConfigurationError) as caught:
        profiles.update(
            SaveProfileRequest(
                "dev",
                "ap-northeast-2",
                "123456789012",
                "developer",
                access_key="ACCESSKEYTEST0002",
                profile_id=created.id,
            )
        )

    assert caught.value.message_code == "profile.credentials.incomplete"

    with pytest.raises(ConfigurationError):
        profiles.update(SaveProfileRequest("dev", "ap-northeast-2", "123456789012", "developer"))

    with pytest.raises(ConfigurationError):
        profiles.create(SaveProfileRequest("empty", "ap-northeast-2", "123456789012", "developer"))


def test_profile_update_can_replace_both_credentials(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)

    changed = profiles.update(
        SaveProfileRequest(
            "dev",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "ACCESSKEYTEST0002",
            "another-local-test-value",
            profile_id=created.id,
        )
    )

    assert changed.id == created.id


def test_profile_clone_creates_independent_inactive_record_with_protected_credentials(
    tmp_path,
) -> None:
    _store, _clock, _gateway, profiles, _authentication, created = build_services(tmp_path)

    cloned = profiles.clone(created.id, "dev-copy")

    assert cloned.id != created.id
    assert cloned.name == "dev-copy"
    assert not cloned.is_default
    source_credentials = profiles.reveal_credentials(profiles.resolve(created.id))
    cloned_credentials = profiles.reveal_credentials(profiles.resolve(cloned.id))
    assert cloned_credentials == source_credentials


def test_refresh_then_reuse_and_refresh_when_near_expiry(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    coordinator = OperationCoordinator(authentication, clock)

    challenge = coordinator.start_refresh(created.id)
    assert challenge.state is OperationState.MFA_REQUIRED
    completed = coordinator.resume(challenge.operation_id, "123456")
    assert completed.state is OperationState.SUCCEEDED
    assert gateway.refresh_calls == 1
    assert authentication.reusable_credentials(created.id) is not None

    clock.value += timedelta(hours=11, minutes=31)
    assert not authentication.status(created.id).reusable


def test_validate_long_lived_credentials_and_status_without_session(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)

    assert authentication.session_guard is not None
    assert authentication.status(created.id).state == "MFA_REQUIRED"
    assert authentication.validate(created.id).profile.id == created.id

    gateway.identity = AwsIdentity("123456789012", "other", "arn:aws:iam::123456789012:user/other")
    with pytest.raises(CredentialValidationError):
        authentication.validate(created.id)


def test_near_expiry_is_not_returned_and_ready_refresh_short_circuits(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    authentication.refresh(created.id, "123456")
    coordinator = OperationCoordinator(authentication, clock)

    result = coordinator.start_refresh(created.id)
    assert result.state is OperationState.SUCCEEDED
    assert gateway.refresh_calls == 1

    clock.value += timedelta(hours=11, minutes=31)
    assert authentication.reusable_credentials(created.id) is None


def test_refresh_rejects_malformed_mfa_without_calling_aws(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)

    with pytest.raises(MfaValidationError) as caught:
        authentication.refresh(created.id, "123")

    assert caught.value.message_code == "mfa.code.invalid"
    assert gateway.refresh_calls == 0


def test_mismatched_saved_session_is_deleted_and_requires_refresh(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    authentication.refresh(created.id, "123456")
    gateway.identity = AwsIdentity("000000000000", "other", "arn:aws:iam::000000000000:user/other")

    assert authentication.reusable_credentials(created.id) is None
    assert store.get_session(created.id) is None


def test_domain_invalid_cached_session_is_deleted_instead_of_escaping(tmp_path) -> None:
    store, clock, _gateway, _profiles, authentication, created = build_services(tmp_path)
    store.put_session(
        SessionCredentials(
            created.id,
            b"short",
            b"also-short",
            b"token",
            clock.now() + timedelta(hours=12),
            clock.now(),
        )
    )

    assert authentication.session_guard.credentials(created.id) is None
    assert store.get_session(created.id) is None


def test_session_guard_requires_one_reusable_session_with_shared_error(tmp_path) -> None:
    store, _clock, _gateway, _profiles, authentication, created = build_services(tmp_path)

    with pytest.raises(CredentialValidationError) as caught:
        authentication.session_guard.require_credentials(created.id)

    assert caught.value.message_code == "auth.mfa_required"
    authentication.refresh(created.id, "123456")
    assert authentication.session_guard.require_credentials(created.id).session_token
    assert store.get_session(created.id) is not None


def test_mfa_cancel_expiry_rejection_and_single_resume(tmp_path) -> None:
    store, clock, gateway, profiles, authentication, created = build_services(tmp_path)
    coordinator = OperationCoordinator(authentication, clock)

    cancelled = coordinator.start_refresh(created.id)
    assert coordinator.resume(cancelled.operation_id, None).state is OperationState.CANCELLED

    expired = coordinator.start_refresh(created.id)
    clock.value += timedelta(minutes=6)
    assert (
        coordinator.resume(expired.operation_id, "123456").error.message_code
        == "mfa.challenge.expired"
    )  # type: ignore[union-attr]

    gateway.reject_mfa = True
    rejected = coordinator.start_refresh(created.id)
    first = coordinator.resume(rejected.operation_id, "123456")
    second = coordinator.resume(rejected.operation_id, "123456")
    assert first.error.message_code == "mfa.code.rejected"  # type: ignore[union-attr]
    assert second.error.message_code == "mfa.operation.not_resumable"  # type: ignore[union-attr]
    assert gateway.refresh_calls == 1


def test_start_refresh_sweeps_expired_challenges_before_adding_a_new_one(tmp_path) -> None:
    _store, clock, _gateway, _profiles, authentication, created = build_services(tmp_path)
    coordinator = OperationCoordinator(authentication, clock)
    expired = coordinator.start_refresh(created.id)
    clock.value += timedelta(minutes=6)

    current = coordinator.start_refresh(created.id)

    assert expired.operation_id not in coordinator._pending
    assert current.operation_id in coordinator._pending
    assert current.state is OperationState.MFA_REQUIRED
