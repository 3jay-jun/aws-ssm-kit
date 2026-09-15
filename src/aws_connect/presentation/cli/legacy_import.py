"""Credential-safe CLI projection for legacy imports."""

from __future__ import annotations

from typing import Any

from aws_connect.application.legacy_import import LegacyImportPreview, LegacyImportResult


def legacy_preview_payload(result: LegacyImportPreview) -> dict[str, Any]:
    return {
        "profile": {
            "name": result.profile_name,
            "region": result.region,
            "account_id": result.account_id,
            "user_id": result.user_id,
            "credentials_present": result.credentials_present,
        },
        "tunnel": {
            "name": result.tunnel_name,
            "host": result.tunnel_host,
            "remote_port": result.remote_port,
            "local_port": result.local_port,
        },
        "source_will_be_preserved": result.source_will_be_preserved,
    }


def legacy_import_payload(result: LegacyImportResult) -> dict[str, Any]:
    return {
        "profile": {"id": result.profile_id, "name": result.profile_name},
        "tunnel": {
            "imported": result.tunnel_imported,
            "id": result.tunnel_id,
        },
        "source_preserved": result.source_preserved,
    }
