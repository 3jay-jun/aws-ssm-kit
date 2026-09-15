"""AWS profile CRUD and validation use cases."""

from __future__ import annotations

from dataclasses import dataclass

from aws_connect.application.ports import CredentialProtector, IdentityGateway, ProfileStore
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials, default_mfa_arn
from aws_connect.domain.errors import ConfigurationError, CredentialValidationError


@dataclass(frozen=True, slots=True, repr=False)
class SaveProfileRequest:
    name: str
    region: str
    account_id: str
    user_id: str
    access_key: str | None = None
    secret_key: str | None = None
    mfa_arn: str | None = None
    profile_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    id: int
    name: str
    region: str
    account_id: str
    user_id: str
    mfa_arn: str
    is_default: bool


class ProfileService:
    """Own profile validation, persistence and active-profile selection."""

    def __init__(
        self,
        store: ProfileStore,
        protector: CredentialProtector,
        identity_gateway: IdentityGateway,
    ) -> None:
        self._store = store
        self._protector = protector
        self._identity_gateway = identity_gateway

    def list(self) -> list[ProfileSummary]:
        return [self._summary(profile) for profile in self._store.list()]

    def show(self, selector: str | int | None = None) -> ProfileSummary:
        return self._summary(self.resolve(selector))

    def create(self, request: SaveProfileRequest) -> ProfileSummary:
        credentials = self._requested_credentials(request)
        self._validate_identity(request, credentials)
        profile = AwsProfile(
            id=None,
            name=request.name,
            region=request.region,
            account_id=request.account_id,
            user_id=request.user_id,
            mfa_arn=request.mfa_arn or default_mfa_arn(request.account_id, request.user_id),
            encrypted_access_key=self._protector.protect(credentials.access_key),
            encrypted_secret_key=self._protector.protect(credentials.secret_key),
        )
        return self._summary(self._store.create(profile))

    def update(self, request: SaveProfileRequest) -> ProfileSummary:
        if request.profile_id is None:
            raise self._configuration("profile.id.required")
        current = self.resolve(request.profile_id)
        credentials = self._credentials_for_update(request, current)
        self._validate_identity(request, credentials)
        updated = AwsProfile(
            id=current.id,
            name=request.name,
            region=request.region,
            account_id=request.account_id,
            user_id=request.user_id,
            # GUI users do not edit the implementation-level MFA ARN. An omitted
            # value therefore preserves existing custom devices, while an explicit
            # CLI value still replaces it.
            mfa_arn=request.mfa_arn if request.mfa_arn is not None else current.mfa_arn,
            encrypted_access_key=self._protector.protect(credentials.access_key),
            encrypted_secret_key=self._protector.protect(credentials.secret_key),
            is_default=current.is_default,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
        saved = self._store.update(updated)
        self._store.delete_session(request.profile_id)
        return self._summary(saved)

    def clone(self, selector: str | int, name: str) -> ProfileSummary:
        """Clone protected credentials and metadata into a new, inactive profile."""

        source = self.resolve(selector)
        cloned = AwsProfile(
            id=None,
            name=name.strip(),
            region=source.region,
            account_id=source.account_id,
            user_id=source.user_id,
            mfa_arn=source.mfa_arn,
            encrypted_access_key=source.encrypted_access_key,
            encrypted_secret_key=source.encrypted_secret_key,
        )
        return self._summary(self._store.create(cloned))

    def use(self, selector: str | int) -> ProfileSummary:
        profile = self.resolve(selector)
        return self._summary(self._store.set_default(profile.require_id()))

    def resolve(self, selector: str | int | None) -> AwsProfile:
        if selector is None:
            profile = self._store.get_default()
        elif isinstance(selector, int) or selector.isdigit():
            profile = self._store.get(int(selector))
        else:
            profile = self._store.get_by_name(selector)
        if profile is None:
            raise self._configuration("profile.not_found")
        return profile

    def reveal_credentials(self, profile: AwsProfile) -> PlainCredentials:
        return PlainCredentials(
            self._protector.unprotect(profile.encrypted_access_key),
            self._protector.unprotect(profile.encrypted_secret_key),
        )

    def _requested_credentials(self, request: SaveProfileRequest) -> PlainCredentials:
        if request.access_key is None or request.secret_key is None:
            raise self._configuration("profile.credentials.required")
        return PlainCredentials(request.access_key, request.secret_key)

    def _credentials_for_update(
        self, request: SaveProfileRequest, current: AwsProfile
    ) -> PlainCredentials:
        if request.access_key is None and request.secret_key is None:
            return self.reveal_credentials(current)
        if request.access_key is None or request.secret_key is None:
            raise self._configuration("profile.credentials.incomplete")
        return PlainCredentials(request.access_key, request.secret_key)

    def _validate_identity(
        self, request: SaveProfileRequest, credentials: PlainCredentials
    ) -> None:
        identity = self._identity_gateway.get_identity(credentials, request.region)
        if identity.account_id != request.account_id or identity.user_id != request.user_id:
            raise CredentialValidationError(
                message_code="credentials.identity_mismatch",
                technical_cause="Configured Account ID or IAM user does not match STS identity",
                aws_service="sts",
                aws_action="GetCallerIdentity",
            )

    @staticmethod
    def _summary(profile: AwsProfile) -> ProfileSummary:
        return ProfileSummary(
            id=profile.require_id(),
            name=profile.name,
            region=profile.region,
            account_id=profile.account_id,
            user_id=profile.user_id,
            mfa_arn=profile.mfa_arn,
            is_default=profile.is_default,
        )

    @staticmethod
    def _configuration(code: str) -> ConfigurationError:
        return ConfigurationError(message_code=code, technical_cause=code)
