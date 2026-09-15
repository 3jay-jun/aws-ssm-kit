"""Saved S3 location invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from aws_connect.domain.errors import ConfigurationError

_BUCKET = re.compile(r"^(?!\d{1,3}(?:\.\d{1,3}){3}$)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


@dataclass(frozen=True, slots=True)
class S3Location:
    id: int | None
    profile_id: int
    name: str
    bucket: str
    prefix: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        bucket = self.bucket.strip()
        prefix = self.prefix.strip().lstrip("/")
        if not name:
            raise _invalid("s3.location.name.required")
        if not _BUCKET.fullmatch(bucket) or ".." in bucket or ".-" in bucket or "-." in bucket:
            raise _invalid("s3.bucket.invalid")
        if self.profile_id <= 0:
            raise _invalid("s3.location.profile_id.invalid")
        if "\x00" in prefix:
            raise _invalid("s3.prefix.invalid")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "bucket", bucket)
        object.__setattr__(self, "prefix", prefix)

    def require_id(self) -> int:
        if self.id is None:
            raise _invalid("s3.location.id.required")
        return self.id


def _invalid(code: str) -> ConfigurationError:
    return ConfigurationError(code, code)
