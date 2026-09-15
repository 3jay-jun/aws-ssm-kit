"""Safe Secrets Manager lookup and presentation-neutral result contracts."""

from __future__ import annotations

import builtins
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.operations import OperationContext
from aws_connect.application.ports import (
    Ec2MetadataGateway,
    ListedSecret,
    ManagedInstanceGateway,
    RemoteSecretCommandGateway,
    SavedSecretStore,
    SecretsGateway,
)
from aws_connect.application.profile_service import ProfileService
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import AwsPermissionError, ConfigurationError
from aws_connect.domain.saved_secret import SavedSecret
from aws_connect.domain.saved_secret import SecretLookupMode as SecretLookupMode
from aws_connect.domain.sensitive_data import REDACTED, is_sensitive_name


class SecretKind(StrEnum):
    JSON = "json"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class SecretRelayTarget:
    instance_id: str
    platform_name: str
    name: str | None = None


_SECRET_NAME = re.compile(r"[A-Za-z0-9/_+=.@-]{1,512}\Z")
_SECRET_ARN = re.compile(
    r"arn:(?:aws|aws-cn|aws-us-gov):secretsmanager:[a-z0-9-]+:\d{12}:"
    r"secret:[A-Za-z0-9/_+=.@-]{1,512}\Z"
)
# Bandit B105 sees ``secret`` in this executable command; it contains no credential value.
_REMOTE_SECRET_COMMAND = (
    "aws secretsmanager get-secret-value --secret-id '{secret_id}' "  # nosec B105
    "--query SecretString --output text"
)


@dataclass(frozen=True, slots=True, repr=False)
class SecretField:
    """One displayable value; raw data is intentionally absent from repr."""

    path: str
    _raw_value: object
    sensitive: bool

    @property
    def masked_value(self) -> object:
        return REDACTED if self.sensitive else self._raw_value

    def reveal(self) -> object:
        """Return raw data only for an explicit presentation action."""

        return self._raw_value


@dataclass(frozen=True, slots=True, repr=False)
class SecretResult:
    secret_id: str
    kind: SecretKind
    fields: tuple[SecretField, ...]
    version_id: str | None = None
    version_stages: tuple[str, ...] = ()
    _raw_secret_string: str = ""

    def field(self, path: str) -> SecretField:
        try:
            return next(item for item in self.fields if item.path == path)
        except StopIteration as error:
            raise ConfigurationError(
                message_code="secret.field.not_found",
                technical_cause="The requested field is absent from the retrieved secret",
            ) from error

    def rds_endpoint(self) -> tuple[str, int]:
        """Extract a conventional host/port pair for an explicit RDS editor copy."""

        host_field = next((item for item in self.fields if item.path.casefold() == "host"), None)
        port_field = next((item for item in self.fields if item.path.casefold() == "port"), None)
        host = host_field.reveal() if host_field is not None else None
        port = port_field.reveal() if port_field is not None else None
        try:
            if not isinstance(port, (str, int)) or isinstance(port, bool):
                raise ValueError
            parsed_port = int(port)
        except (TypeError, ValueError) as error:
            raise ConfigurationError(
                message_code="secret.rds_endpoint.invalid",
                technical_cause="Secret JSON must contain a valid top-level host and port",
            ) from error
        if not isinstance(host, str) or not host.strip() or not 1 <= parsed_port <= 65535:
            raise ConfigurationError(
                message_code="secret.rds_endpoint.invalid",
                technical_cause="Secret JSON must contain a valid top-level host and port",
            )
        return host.strip(), parsed_port

    def raw_secret_string(self) -> str:
        """Return the transient source only for an explicit update operation."""

        return self._raw_secret_string


class SecretsService:
    """Retrieve exactly one named secret without requiring ListSecrets permission."""

    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        gateway: SecretsGateway,
        managed_instances: ManagedInstanceGateway | None = None,
        remote_gateway: RemoteSecretCommandGateway | None = None,
        metadata: Ec2MetadataGateway | None = None,
        saved: SavedSecretStore | None = None,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._gateway = gateway
        self._managed_instances = managed_instances
        self._remote_gateway = remote_gateway
        self._metadata = metadata
        self._saved = saved

    def get(
        self,
        secret_id: str,
        profile: str | int | None = None,
        *,
        mode: SecretLookupMode = SecretLookupMode.DIRECT,
        instance_id: str | None = None,
        confirmed: bool = False,
        context: OperationContext | None = None,
    ) -> SecretResult:
        identifier = secret_id.strip()
        if not identifier:
            raise ConfigurationError(
                message_code="secret.id.required",
                technical_cause="A Secret name or ARN is required",
            )
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        if mode is SecretLookupMode.DIRECT:
            retrieved = self._gateway.get_secret_value(credentials, selected.region, identifier)
        elif mode is SecretLookupMode.VIA_EC2:
            _validate_remote_secret_id(identifier)
            if not confirmed:
                raise ConfigurationError(
                    "secret.relay.confirmation.required",
                    "EC2-mediated Secret lookup requires explicit acknowledgement",
                )
            relay = self._require_online_relay(
                credentials, selected.region, (instance_id or "").strip()
            )
            operation = context or OperationContext()
            operation.raise_if_cancelled()
            operation.report(
                "remote-command", "secret.relay.command.start", target=relay.instance_id
            )
            if self._remote_gateway is None:
                raise ConfigurationError(
                    "secret.relay.unavailable", "EC2-mediated Secret lookup is not configured"
                )
            retrieved = self._remote_gateway.get_secret_value(
                credentials,
                selected.region,
                relay.instance_id,
                relay.platform_name,
                identifier,
                operation.raise_if_cancelled,
            )
            operation.report(
                "remote-command", "secret.relay.command.succeeded", completed=1, total=1
            )
        else:
            raise ConfigurationError("secret.lookup.mode.invalid", "Unknown Secret lookup mode")
        result = _parse(
            retrieved.secret_id,
            retrieved.secret_string,
            retrieved.version_id,
            retrieved.version_stages,
        )
        if self._saved is not None:
            self.remember(
                result.secret_id,
                selected.require_id(),
                value=retrieved.secret_string,
                lookup_mode=mode,
                relay_instance_id=(
                    instance_id.strip()
                    if mode is SecretLookupMode.VIA_EC2 and instance_id is not None
                    else None
                ),
            )
        return result

    def list_saved(self, profile: str | int | None = None) -> list[SavedSecret]:
        selected = self._profiles.resolve(profile)
        return self._require_saved_store().list_saved_secrets(selected.require_id())

    def remember(
        self,
        identifier: str,
        profile: str | int | None = None,
        *,
        value: str | None = None,
        lookup_mode: SecretLookupMode | None = None,
        relay_instance_id: str | None = None,
    ) -> SavedSecret:
        selected = self._profiles.resolve(profile)
        profile_id = selected.require_id()
        store = self._require_saved_store()
        has_lookup_snapshot = (
            value is not None or lookup_mode is not None or relay_instance_id is not None
        )
        candidate = SavedSecret(
            None,
            profile_id,
            identifier,
            value="" if value is None else value,
            lookup_mode=lookup_mode or SecretLookupMode.DIRECT,
            relay_instance_id=relay_instance_id,
        )
        if has_lookup_snapshot:
            return store.upsert_saved_secret(candidate)
        existing = store.get_saved_secret_by_identifier(profile_id, candidate.identifier)
        if existing is not None:
            return existing
        try:
            return store.create_saved_secret(candidate)
        except ConfigurationError as error:
            if error.message_code != "secret.saved.identifier.duplicate":
                raise
            concurrent = store.get_saved_secret_by_identifier(profile_id, candidate.identifier)
            if concurrent is None:
                raise
            return concurrent

    def update_saved(
        self,
        saved_secret_id: int,
        identifier: str,
        profile: str | int | None = None,
        *,
        value: str | None = None,
    ) -> SavedSecret:
        selected = self._profiles.resolve(profile)
        profile_id = selected.require_id()
        existing = self._require_saved_store().get_saved_secret(saved_secret_id)
        if existing is None or existing.profile_id != profile_id:
            raise ConfigurationError("secret.saved.not_found", "Saved Secret was not found")
        return self._require_saved_store().update_saved_secret(
            SavedSecret(
                saved_secret_id,
                profile_id,
                identifier,
                value=existing.value if value is None else value,
                lookup_mode=existing.lookup_mode,
                relay_instance_id=existing.relay_instance_id,
            )
        )

    def load_saved(
        self, saved_secret_id: int, profile: str | int | None = None
    ) -> tuple[SavedSecret, SecretResult]:
        """Load one persisted snapshot without making an AWS request."""

        selected = self._profiles.resolve(profile)
        existing = self._require_saved_store().get_saved_secret(saved_secret_id)
        if existing is None or existing.profile_id != selected.require_id():
            raise ConfigurationError("secret.saved.not_found", "Saved Secret was not found")
        return existing, _parse(existing.identifier, existing.value, None, ())

    def delete_saved(self, saved_secret_id: int, profile: str | int | None = None) -> None:
        selected = self._profiles.resolve(profile)
        existing = self._require_saved_store().get_saved_secret(saved_secret_id)
        if existing is None or existing.profile_id != selected.require_id():
            raise ConfigurationError("secret.saved.not_found", "Saved Secret was not found")
        self._require_saved_store().delete_saved_secret(saved_secret_id)

    def _require_saved_store(self) -> SavedSecretStore:
        if self._saved is None:
            raise ConfigurationError(
                "secret.saved.unavailable", "Saved Secret storage is not configured"
            )
        return self._saved

    def list(self, profile: str | int | None = None) -> list[ListedSecret]:
        """Return optional catalog metadata; direct lookup never depends on this permission."""

        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        return self._gateway.list_secrets(credentials, selected.region)

    def save_top_level_field(
        self,
        result: SecretResult,
        path: str,
        value_text: str,
        profile: str | int | None = None,
    ) -> SecretResult:
        """Write one top-level JSON field as a new AWS Secret version."""

        if result.kind is not SecretKind.JSON or not path or "." in path or "[" in path:
            raise ConfigurationError(
                "secret.field.update.unsupported",
                "Only top-level JSON fields can be updated safely",
            )
        try:
            document = json.loads(result.raw_secret_string())
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                "secret.field.update.unsupported", "Secret source is not a JSON object"
            ) from error
        if not isinstance(document, dict) or path not in document:
            raise ConfigurationError(
                "secret.field.not_found", "The requested top-level field is absent"
            )
        try:
            value: object = json.loads(value_text)
        except json.JSONDecodeError:
            value = value_text
        document[path] = value
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        secret_string = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        version_id = self._gateway.put_secret_value(
            credentials, selected.region, result.secret_id, secret_string
        )
        return _parse(result.secret_id, secret_string, version_id, ("AWSCURRENT",))

    def list_relay_targets(
        self, profile: str | int | None = None
    ) -> builtins.list[SecretRelayTarget]:
        if self._managed_instances is None:
            raise ConfigurationError(
                "secret.relay.unavailable", "EC2-mediated Secret lookup is not configured"
            )
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        managed = [
            item
            for item in self._managed_instances.list_online(credentials, selected.region)
            if item.ping_status == "Online" and item.platform_name
        ]
        metadata = {}
        if self._metadata is not None and managed:
            try:
                metadata = self._metadata.describe(
                    credentials,
                    selected.region,
                    [item.instance_id for item in managed],
                )
            except AwsPermissionError:
                # EC2 Name is optional display metadata; the validated instance ID
                # remains usable when DescribeInstances is intentionally denied.
                metadata = {}
        return [
            SecretRelayTarget(
                item.instance_id,
                item.platform_name or "",
                metadata[item.instance_id].name if item.instance_id in metadata else None,
            )
            for item in managed
        ]

    def _require_online_relay(
        self, credentials: PlainCredentials, region: str, instance_id: str
    ) -> SecretRelayTarget:
        if not instance_id:
            raise ConfigurationError(
                "secret.relay.instance.required", "Select one Online EC2 relay instance"
            )
        if self._managed_instances is None:
            raise ConfigurationError(
                "secret.relay.unavailable", "EC2-mediated Secret lookup is not configured"
            )
        targets = self._managed_instances.list_online(credentials, region)
        selected = next(
            (
                item
                for item in targets
                if item.instance_id == instance_id and item.ping_status == "Online"
            ),
            None,
        )
        if selected is None:
            raise ConfigurationError(
                "secret.relay.instance.not_online",
                "The selected EC2 relay is not an Online SSM managed node",
            )
        if not selected.platform_name or not _supported_platform(selected.platform_name):
            raise ConfigurationError(
                "secret.relay.platform.unsupported",
                "The selected EC2 relay platform is unavailable",
            )
        return SecretRelayTarget(selected.instance_id, selected.platform_name)


def _validate_remote_secret_id(secret_id: str) -> None:
    if _SECRET_NAME.fullmatch(secret_id) or _SECRET_ARN.fullmatch(secret_id):
        return
    raise ConfigurationError(
        "secret.id.remote.invalid",
        "Remote Secret ID contains unsupported characters or is not a valid ARN",
    )


def build_remote_secret_command(secret_id: str) -> str:
    """Return the sole allowlisted remote command after injection-safe validation."""

    _validate_remote_secret_id(secret_id)
    return _REMOTE_SECRET_COMMAND.format(secret_id=secret_id)


def build_persistent_remote_secret_command(secret_id: str, platform_name: str) -> str:
    """Run the fixed lookup and keep the selected platform's shell interactive."""

    command = build_remote_secret_command(secret_id)
    normalized_platform = platform_name.casefold()
    if "windows" in normalized_platform:
        return f'powershell.exe -NoLogo -NoExit -Command "{command}"'
    if any(token in normalized_platform for token in ("linux", "ubuntu", "unix")):
        return f'{command}; exec "${{SHELL:-/bin/sh}}" -l'
    raise ConfigurationError(
        "secret.relay.platform.unsupported",
        "The selected EC2 relay platform cannot keep an interactive command session open",
    )


def _supported_platform(platform_name: str) -> bool:
    normalized = platform_name.casefold()
    return any(token in normalized for token in ("windows", "linux", "ubuntu", "unix"))


def _parse(
    secret_id: str,
    secret_string: str,
    version_id: str | None,
    version_stages: tuple[str, ...],
) -> SecretResult:
    try:
        value = json.loads(secret_string)
    except json.JSONDecodeError:
        return SecretResult(
            secret_id,
            SecretKind.TEXT,
            (SecretField("$", secret_string, True),),
            version_id,
            version_stages,
            secret_string,
        )
    if not isinstance(value, (Mapping, list)):
        return SecretResult(
            secret_id,
            SecretKind.JSON,
            (SecretField("$", value, True),),
            version_id,
            version_stages,
            secret_string,
        )
    fields: list[SecretField] = []
    _flatten(value, "", False, fields)
    return SecretResult(
        secret_id, SecretKind.JSON, tuple(fields), version_id, version_stages, secret_string
    )


def _flatten(value: Any, path: str, inherited_sensitive: bool, result: list[SecretField]) -> None:
    if isinstance(value, Mapping):
        if not value:
            result.append(SecretField(path or "$", {}, inherited_sensitive))
        for key, item in value.items():
            name = str(key)
            child_path = f"{path}.{name}" if path else name
            _flatten(item, child_path, inherited_sensitive or is_sensitive_name(name), result)
        return
    if isinstance(value, list):
        if not value:
            result.append(SecretField(path or "$", [], inherited_sensitive))
        for index, item in enumerate(value):
            _flatten(
                item,
                f"{path}[{index}]" if path else f"[{index}]",
                inherited_sensitive,
                result,
            )
        return
    result.append(SecretField(path or "$", value, inherited_sensitive))
