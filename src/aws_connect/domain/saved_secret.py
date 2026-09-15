"""Profile-scoped references to Secrets Manager entries; values are never persisted."""

from __future__ import annotations

from dataclasses import dataclass

from aws_connect.domain.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class SavedSecret:
    id: int | None
    profile_id: int
    identifier: str

    def __post_init__(self) -> None:
        identifier = self.identifier.strip()
        if self.profile_id <= 0:
            raise ConfigurationError("secret.saved.profile.invalid", "Profile ID must be positive")
        if not identifier or len(identifier) > 2048 or "\x00" in identifier:
            raise ConfigurationError(
                "secret.saved.identifier.invalid", "A valid Secret name or ARN is required"
            )
        object.__setattr__(self, "identifier", identifier)

    def require_id(self) -> int:
        if self.id is None:
            raise ConfigurationError("secret.saved.id.required", "Saved Secret ID is required")
        return self.id
