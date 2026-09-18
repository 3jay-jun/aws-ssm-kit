"""Bound structured logs before either SQLite or file serialization."""

import json
from dataclasses import fields, replace
from typing import Any

from aws_connect.domain.execution_log import ExecutionLogEvent
from aws_connect.infrastructure.masking import mask, mask_text

_METADATA_FIELDS = frozenset(
    {
        "profile_id",
        "region",
        "instance_id",
        "bucket",
        "key",
        "file_size",
        "local_port",
        "remote_port",
        "retryable",
        "attempt",
        "skipped_count",
    }
)


class MaskedExecutionLogSanitizer:
    def sanitize(self, event: ExecutionLogEvent) -> ExecutionLogEvent:
        updates: dict[str, Any] = {}
        for field in fields(event):
            value = getattr(event, field.name)
            if isinstance(value, str) and field.name not in {
                "level",
                "result",
                "phase",
                "error_category",
                "metadata_json",
            }:
                updates[field.name] = mask_text(value)[:2000]
        updates["metadata_json"] = sanitize_metadata_json(event.metadata_json)
        return replace(event, **updates)


def sanitize_metadata_json(value: str) -> str:
    try:
        metadata = json.loads(value) if len(value) <= 8192 else {}
    except (ValueError, TypeError):
        metadata = {}
    allowed = (
        {
            key: value
            for key, value in metadata.items()
            if key in _METADATA_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
        }
        if isinstance(metadata, dict)
        else {}
    )
    allowed = {
        key: value[:256] if isinstance(value, str) else value for key, value in allowed.items()
    }
    return json.dumps(mask(allowed), ensure_ascii=False)
