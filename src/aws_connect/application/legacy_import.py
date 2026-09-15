"""Safe, one-way import of the legacy BAT configuration file."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from aws_connect.application.ports import (
    CredentialProtector,
    ProfileStore,
    TunnelSessionStore,
)
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials, default_mfa_arn
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession


@dataclass(frozen=True, slots=True)
class LegacyImportRequest:
    """Non-secret choices for one explicit legacy import."""

    source: Path
    profile_name: str = "legacy-import"
    tunnel_name: str = "legacy-rds"
    include_tunnel: bool = True
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class LegacyImportPreview:
    """Non-secret summary shown before the one-way import is explicitly applied."""

    profile_name: str
    region: str
    account_id: str
    user_id: str
    tunnel_name: str | None
    tunnel_host: str | None
    remote_port: int | None
    local_port: int | None
    credentials_present: bool = True
    source_will_be_preserved: bool = True


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    """Safe result that intentionally contains no source values or credentials."""

    profile_id: int
    profile_name: str
    tunnel_id: int | None
    tunnel_imported: bool
    source_preserved: bool = True


class LegacyIniImportService:
    """Import legacy plaintext credentials and protect them before persistence."""

    def __init__(
        self,
        profiles: ProfileStore,
        tunnels: TunnelSessionStore,
        protector: CredentialProtector,
    ) -> None:
        self._profiles = profiles
        self._tunnels = tunnels
        self._protector = protector

    def preview(self, request: LegacyImportRequest) -> LegacyImportPreview:
        source = request.source.expanduser().resolve()
        _validate_source(source)
        if self._profiles.get_by_name(request.profile_name) is not None:
            raise _import_error("legacy.profile.name.conflict")
        values = _read_ini(source)
        tunnel_template = (
            _build_tunnel(values, request.tunnel_name) if request.include_tunnel else None
        )
        # Validate credential presence without retaining or returning either value.
        _required(values, "aws", "access_key")
        _required(values, "aws", "secret_key")
        return LegacyImportPreview(
            profile_name=request.profile_name,
            region=_required(values, "aws", "region"),
            account_id=_required(values, "aws", "account_id"),
            user_id=_required(values, "aws", "user_id"),
            tunnel_name=tunnel_template.name if tunnel_template else None,
            tunnel_host=tunnel_template.host if tunnel_template else None,
            remote_port=tunnel_template.remote_port if tunnel_template else None,
            local_port=tunnel_template.local_port if tunnel_template else None,
        )

    def import_file(self, request: LegacyImportRequest) -> LegacyImportResult:
        """Compatibility alias with the same explicit-confirmation safety contract."""

        return self.apply(request)

    def apply(self, request: LegacyImportRequest) -> LegacyImportResult:
        if not request.confirmed:
            raise _import_error("legacy.import.confirmation.required")
        source = request.source.expanduser().resolve()
        _validate_source(source)
        if self._profiles.get_by_name(request.profile_name) is not None:
            raise _import_error("legacy.profile.name.conflict")

        values = _read_ini(source)
        profile = _build_profile(values, request.profile_name, self._protector)
        tunnel_template = (
            _build_tunnel(values, request.tunnel_name) if request.include_tunnel else None
        )

        created = self._profiles.create(profile)
        profile_id = created.require_id()
        tunnel_id: int | None = None
        try:
            if tunnel_template is not None:
                tunnel = self._tunnels.create_tunnel(
                    TunnelSession(
                        id=None,
                        profile_id=profile_id,
                        name=tunnel_template.name,
                        host=tunnel_template.host,
                        remote_port=tunnel_template.remote_port,
                        local_port=tunnel_template.local_port,
                        target_mode=tunnel_template.target_mode,
                        target_instance_id=tunnel_template.target_instance_id,
                    )
                )
                tunnel_id = tunnel.require_id()
        except ApplicationError:
            # Import is one logical operation. Compensate rather than leave a partial profile.
            self._profiles.delete(profile_id)
            raise
        return LegacyImportResult(
            profile_id=profile_id,
            profile_name=created.name,
            tunnel_id=tunnel_id,
            tunnel_imported=tunnel_id is not None,
        )


def _validate_source(source: Path) -> None:
    if not source.is_file():
        raise _import_error("legacy.ini.not_found")
    if source.suffix.lower() != ".ini":
        raise _import_error("legacy.ini.extension.invalid")


def _read_ini(source: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with source.open("r", encoding="utf-8-sig") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as error:
        # Never include source text or parser details: they may contain plaintext credentials.
        raise _import_error("legacy.ini.read.failed", type(error).__name__) from error
    if not parser.has_section("aws"):
        raise _import_error("legacy.ini.aws.required")
    return parser


def _build_profile(
    values: configparser.ConfigParser,
    profile_name: str,
    protector: CredentialProtector,
) -> AwsProfile:
    region = _required(values, "aws", "region")
    account_id = _required(values, "aws", "account_id")
    user_id = _required(values, "aws", "user_id")
    credentials = PlainCredentials(
        _required(values, "aws", "access_key"),
        _required(values, "aws", "secret_key"),
    )
    return AwsProfile(
        id=None,
        name=profile_name,
        region=region,
        account_id=account_id,
        user_id=user_id,
        mfa_arn=default_mfa_arn(account_id, user_id),
        encrypted_access_key=protector.protect(credentials.access_key),
        encrypted_secret_key=protector.protect(credentials.secret_key),
    )


def _build_tunnel(values: configparser.ConfigParser, tunnel_name: str) -> TunnelSession | None:
    if not values.has_section("rds"):
        return None
    host = values.get("rds", "host", fallback="").strip()
    port = values.get("rds", "port", fallback="").strip()
    if not host:
        host = values.get("rds", "fallback_host", fallback="").strip()
    if not port:
        port = values.get("rds", "fallback_port", fallback="").strip()
    if not host and not port:
        return None
    if not host or not port:
        raise _import_error("legacy.rds.incomplete")
    local_port = values.get("rds", "local_port", fallback="").strip() or port
    try:
        remote_port_number = int(port)
        local_port_number = int(local_port)
    except ValueError as error:
        raise _import_error("legacy.rds.port.invalid") from error
    return TunnelSession(
        id=None,
        profile_id=1,  # Replaced with the persisted profile identity before storage.
        name=tunnel_name,
        host=host,
        remote_port=remote_port_number,
        local_port=local_port_number,
        target_mode=TargetMode.SELECT,
        target_instance_id=None,
    )


def _required(values: configparser.ConfigParser, section: str, option: str) -> str:
    value = values.get(section, option, fallback="").strip()
    if not value:
        raise _import_error(f"legacy.{section}.{option}.required")
    return value


def _import_error(code: str, cause: str | None = None) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=cause or code)
