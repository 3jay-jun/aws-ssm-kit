"""Saved drive mappings; S3 validation remains owned by S3Location."""

import re
from dataclasses import dataclass
from enum import StrEnum

from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_location import S3Location


class DriveState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class S3Drive:
    id: int | None
    profile_id: int
    name: str
    bucket: str
    prefix: str
    letter: str
    local_prefix: str = ""

    def __post_init__(self) -> None:
        location = S3Location(None, self.profile_id, self.name, self.bucket, self.prefix)
        letter = self.letter.upper().rstrip(":")
        local = self.local_prefix.replace("\\", "/").strip("/")
        if not re.fullmatch("[D-Z]", letter):
            raise ConfigurationError("drive.letter.invalid", "Choose a drive from D: to Z:")
        parts = local.split("/") if local else []
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if any(
            not p
            or p in {".", ".."}
            or p.endswith((".", " "))
            or p.split(".")[0].upper() in reserved
            or re.search(r'[<>:"|?*\x00-\x1f=]', p)
            for p in parts
        ):
            raise ConfigurationError("drive.prefix.invalid", "Enter a valid relative Windows path")
        if any(ord(c) < 32 for c in location.prefix):
            raise ConfigurationError("drive.prefix.invalid", "Control characters are not supported")
        for key, value in (
            ("name", location.name),
            ("bucket", location.bucket),
            ("prefix", location.prefix.rstrip("/")),
            ("letter", letter),
            ("local_prefix", local),
        ):
            object.__setattr__(self, key, value)

    def require_id(self) -> int:
        if self.id is None:
            raise ConfigurationError("drive.not_found", "Save the drive before connecting")
        return self.id

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/" + (f"{self.prefix}/" if self.prefix else "")

    @property
    def windows_path(self) -> str:
        return f"{self.letter}:\\" + self.local_prefix.replace("/", "\\")


@dataclass(frozen=True, slots=True)
class DriveStatus:
    drive: S3Drive
    state: DriveState
    message_code: str = ""
