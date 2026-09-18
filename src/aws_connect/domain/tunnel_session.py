"""Saved RDS tunnel session invariants."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aws_connect.domain.errors import ConfigurationError

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_INSTANCE_ID = re.compile(r"^i-[0-9a-fA-F]{8,17}$")


class TargetMode(StrEnum):
    """How the SSM relay target is chosen."""

    FIXED = "fixed"
    SELECT = "select"


@dataclass(frozen=True, slots=True)
class TunnelSession:
    """Reusable local-to-RDS forwarding configuration."""

    id: int | None
    profile_id: int
    name: str
    host: str
    remote_port: int
    local_port: int
    target_mode: TargetMode
    target_instance_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        normalized_host = self.host.strip()
        if not normalized_name:
            raise _configuration("rds.session.name.required")
        if not _valid_host(normalized_host):
            raise _configuration("rds.session.host.invalid")
        if not 1 <= self.remote_port <= 65535:
            raise _configuration("rds.session.remote_port.invalid")
        validate_local_port(self.local_port)
        if self.profile_id <= 0:
            raise _configuration("rds.session.profile_id.invalid")
        if self.target_mode is TargetMode.FIXED:
            if not self.target_instance_id or not _INSTANCE_ID.fullmatch(self.target_instance_id):
                raise _configuration("rds.session.target_instance_id.invalid")
        elif self.target_instance_id is not None:
            raise _configuration("rds.session.select_target_must_be_empty")

    def require_id(self) -> int:
        if self.id is None:
            raise _configuration("rds.session.id.required")
        return self.id

    def resolve_target(self, selected_instance_id: str | None) -> str:
        """Return the configured or run-time target without consulting AWS."""

        candidate = (
            self.target_instance_id
            if self.target_mode is TargetMode.FIXED
            else selected_instance_id
        )
        if candidate is None or not _INSTANCE_ID.fullmatch(candidate):
            code = (
                "rds.session.target_instance_id.invalid"
                if self.target_mode is TargetMode.FIXED
                else "rds.tunnel.target_required"
            )
            raise _configuration(code)
        return candidate


def validate_local_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise _configuration("rds.session.local_port.invalid")


def _valid_host(value: str) -> bool:
    if not value or len(value) > 253 or any(character.isspace() for character in value):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        normalized = value[:-1] if value.endswith(".") else value
        return bool(normalized) and all(
            _HOST_LABEL.fullmatch(label) for label in normalized.split(".")
        )


def _configuration(code: str) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=code)
