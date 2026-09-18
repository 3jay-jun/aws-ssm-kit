"""Profile-scoped, locally editable Secrets Manager lookup snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from aws_connect.domain.errors import ConfigurationError


class SecretLookupMode(StrEnum):
    DIRECT = "direct"
    VIA_EC2 = "via_ec2"


@dataclass(frozen=True, slots=True)
class SavedSecret:
    id: int | None
    profile_id: int
    identifier: str
    value: str = field(default="", repr=False)
    lookup_mode: SecretLookupMode = field(default=SecretLookupMode.DIRECT, repr=False)
    relay_instance_id: str | None = field(default=None, repr=False)

    last_retrieved_at: datetime | None = None

    def __post_init__(self) -> None:
        identifier = self.identifier.strip()
        if self.profile_id <= 0:
            raise ConfigurationError("secret.saved.profile.invalid", "Profile ID must be positive")
        if not identifier or len(identifier) > 2048 or "\x00" in identifier:
            raise ConfigurationError(
                "secret.saved.identifier.invalid", "A valid Secret name or ARN is required"
            )
        if not isinstance(self.value, str):
            raise ConfigurationError(
                "secret.saved.value.invalid", "Saved Secret value must be text"
            )
        try:
            lookup_mode = SecretLookupMode(self.lookup_mode)
        except ValueError as error:
            raise ConfigurationError(
                "secret.saved.lookup_mode.invalid", "Saved Secret lookup mode is invalid"
            ) from error
        relay_instance_id = (
            self.relay_instance_id.strip() if self.relay_instance_id is not None else None
        )
        if relay_instance_id == "":
            relay_instance_id = None
        if lookup_mode is SecretLookupMode.VIA_EC2 and relay_instance_id is None:
            raise ConfigurationError(
                "secret.saved.relay.required",
                "An EC2 relay instance is required for an EC2-mediated saved Secret",
            )
        if lookup_mode is SecretLookupMode.DIRECT and relay_instance_id is not None:
            raise ConfigurationError(
                "secret.saved.relay.unexpected",
                "A directly retrieved saved Secret cannot have an EC2 relay instance",
            )
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "lookup_mode", lookup_mode)
        object.__setattr__(self, "relay_instance_id", relay_instance_id)

    def require_id(self) -> int:
        if self.id is None:
            raise ConfigurationError("secret.saved.id.required", "Saved Secret ID is required")
        return self.id
