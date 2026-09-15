"""Central redaction policy applied before diagnostics are serialized."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from aws_connect.domain.sensitive_data import REDACTED, is_sensitive_name

_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)(\b(?:aws_)?(?:secret_access_key|secret_key|session_token|tokenvalue|"
        r"streamurl|password|mfa_code)\b\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)"
    ),
    re.compile(r"(?i)(\"(?:TokenValue|StreamUrl|SecretAccessKey|SessionToken)\"\s*:\s*)\"[^\"]*\""),
)


def mask(value: Any, *, known_secrets: Sequence[str] = ()) -> Any:
    """Recursively redact sensitive keys and registered literal values."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED if is_sensitive_name(str(key)) else mask(item, known_secrets=known_secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask(item, known_secrets=known_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(mask(item, known_secrets=known_secrets) for item in value)
    if isinstance(value, str):
        return mask_text(value, known_secrets=known_secrets)
    return value


def mask_text(value: str, *, known_secrets: Sequence[str] = ()) -> str:
    """Redact literal secrets and common credential shapes in free-form text."""

    result = value
    for secret in known_secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    result = _TEXT_PATTERNS[0].sub(REDACTED, result)
    for pattern in _TEXT_PATTERNS[1:]:
        result = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", result)
    return result


class MaskingFilter(logging.Filter):
    """Redact known in-memory values before a LogRecord reaches handlers."""

    def __init__(self, known_secrets: Sequence[str] = ()) -> None:
        super().__init__()
        self._known_secrets = tuple(known_secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(str(record.msg), known_secrets=self._known_secrets)
        if record.args:
            record.args = tuple(
                mask(item, known_secrets=self._known_secrets) for item in record.args
            )
        return True
