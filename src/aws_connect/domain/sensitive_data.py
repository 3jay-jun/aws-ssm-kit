"""Single source of truth for names that carry sensitive values."""

from __future__ import annotations

import re

REDACTED = "***REDACTED***"
_SENSITIVE_PARTS = (
    "token",
    "key",
    "access_key",
    "secret",
    "session_token",
    "sessiontoken",
    "tokenvalue",
    "streamurl",
    "password",
    "passwd",
    "mfa",
    "api_key",
    "apikey",
    "private_key",
)
_KEY_WORD = re.compile(r"(?:^|[_\-.])key$|Key$")


def is_sensitive_name(name: str) -> bool:
    """Return whether a field name denotes a value that must be masked by default."""

    normalized = name.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS) or bool(_KEY_WORD.search(name))
