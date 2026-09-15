"""Secrets Manager CLI rendering with masked output by default."""

from __future__ import annotations

from typing import Any

from aws_connect.application.ports import ListedSecret
from aws_connect.application.secrets_service import SecretResult


def secrets_payload(values: list[ListedSecret]) -> dict[str, Any]:
    return {"secrets": [{"name": value.name, "arn": value.arn} for value in values]}


def secret_payload(result: SecretResult, *, reveal: bool = False) -> dict[str, Any]:
    """Serialize raw field values only after the caller explicitly requested reveal."""

    return {
        "secret_id": result.secret_id,
        "kind": result.kind.value,
        "version_id": result.version_id,
        "version_stages": list(result.version_stages),
        "revealed": reveal,
        "fields": [
            {
                "path": field.path,
                "value": field.reveal() if reveal else field.masked_value,
                "sensitive": field.sensitive,
            }
            for field in result.fields
        ],
    }
