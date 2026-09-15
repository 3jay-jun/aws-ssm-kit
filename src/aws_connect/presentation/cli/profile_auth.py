"""CLI adapter for profile and authentication application services."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from aws_connect.application.authentication_service import (
    AuthenticationStatus,
    OperationCoordinator,
)
from aws_connect.application.operations import OperationResult, OperationState
from aws_connect.application.profile_service import (
    ProfileService,
    ProfileSummary,
    SaveProfileRequest,
)


def profile_payload(profile: ProfileSummary) -> dict[str, Any]:
    return asdict(profile)


def profiles_payload(profiles: list[ProfileSummary]) -> dict[str, Any]:
    return {"profiles": [profile_payload(profile) for profile in profiles]}


def auth_payload(status: AuthenticationStatus) -> dict[str, Any]:
    return {
        "profile": profile_payload(status.profile),
        "state": status.state,
        "expires_at_utc": _iso(status.expires_at_utc),
        "reusable": status.reusable,
    }


def operation_payload(result: OperationResult[AuthenticationStatus]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation_id": result.operation_id,
        "state": result.state.value,
    }
    if result.value:
        payload["value"] = auth_payload(result.value)
    if result.challenge:
        payload["challenge"] = {
            "profile_id": result.challenge.profile_id,
            "device_arn": result.challenge.device_arn,
            "expires_at": result.challenge.expires_at.isoformat(),
        }
    return payload


def render(payload: dict[str, Any], *, output: str) -> str:
    if output == "json":
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if "profiles" in payload:
        profiles = payload["profiles"]
        return (
            "No profiles."
            if not profiles
            else "\n".join(
                f"{'*' if item['is_default'] else ' '} {item['id']}: {item['name']} "
                f"({item['account_id']}/{item['user_id']}, {item['region']})"
                for item in profiles
            )
        )
    if "instances" in payload:
        instances = payload["instances"]
        return (
            "No online SSM instances."
            if not instances
            else "\n".join(
                f"{item['instance_id']}\t{item['name'] or '-'}\t"
                f"{item['private_ip_address'] or '-'}\t{item['platform_name'] or '-'}"
                for item in instances
            )
        )
    if "tunnel_sessions" in payload:
        sessions = payload["tunnel_sessions"]
        return (
            "No saved RDS tunnel sessions."
            if not sessions
            else "\n".join(
                f"{item['id']}: {item['name']} localhost:{item['local_port']} -> "
                f"{item['host']}:{item['remote_port']} ({item['target_mode']})"
                for item in sessions
            )
        )
    if "secret_id" in payload and "fields" in payload:
        fields = payload["fields"]
        heading = f"{payload['secret_id']} ({payload['kind']})"
        return "\n".join([heading, *(f"{item['path']}: {item['value']}" for item in fields)])
    if "s3_locations" in payload:
        values = payload["s3_locations"]
        return (
            "No saved S3 locations."
            if not values
            else "\n".join(
                f"{item['id']}: {item['name']} s3://{item['bucket']}/{item['prefix']}"
                for item in values
            )
        )
    if "objects" in payload:
        values = payload["objects"]
        return (
            "No objects."
            if not values
            else "\n".join(
                f"{'DIR' if item['is_prefix'] else item['size']}\t{item['key']}" for item in values
            )
        )
    if "upload_preview" in payload:
        lines = [
            f"{item['source']} -> {item['target_uri']} (exists={str(item['exists']).lower()})"
            for item in payload["upload_preview"]
        ]
        if payload.get("state"):
            lines.append(f"{payload['state']}: {payload.get('bytes_transferred', 0)} bytes")
        else:
            lines.append(
                "Preview only. Re-run with --confirm; add --overwrite for existing objects."
            )
        return "\n".join(lines)
    if "tunnel" in payload:
        tunnel = payload["tunnel"]
        return (
            f"Tunnel {payload['session_id']} closed: localhost:{tunnel['local_port']} -> "
            f"{tunnel['host']}:{tunnel['remote_port']} via {payload['target_instance_id']}."
        )
    if "target_mode" in payload:
        return (
            f"{payload['name']}: localhost:{payload['local_port']} -> "
            f"{payload['host']}:{payload['remote_port']} ({payload['target_mode']})"
        )
    if "session_id" in payload:
        return f"Session {payload['session_id']} closed ({payload['instance_id']})."
    if "log_entries" in payload:
        entries = payload["log_entries"]
        return (
            "No managed log entries."
            if not entries
            else "\n".join(
                f"{item['occurred_at']}\t{item['feature']}\t{item['target']}\t"
                f"{item['result']}\t{item['message_code']}"
                for item in entries
            )
        )
    if "state" in payload and "profile" in payload:
        return f"{payload['profile']['name']}: {payload['state']}"
    if "id" in payload:
        return f"{payload['name']} ({payload['account_id']}/{payload['user_id']})"
    return "OK"


def create_profile(
    service: ProfileService,
    *,
    name: str,
    region: str,
    account_id: str,
    user_id: str,
    mfa_arn: str | None,
    access_key: str,
    secret_key: str,
) -> ProfileSummary:
    return service.create(
        SaveProfileRequest(
            name=name,
            region=region,
            account_id=account_id,
            user_id=user_id,
            mfa_arn=mfa_arn,
            access_key=access_key,
            secret_key=secret_key,
        )
    )


def refresh_auth(
    coordinator: OperationCoordinator, selector: str | int | None, mfa_code: str | None
) -> OperationResult[AuthenticationStatus]:
    started = coordinator.start_refresh(selector)
    if started.state is not OperationState.MFA_REQUIRED:
        return started
    return coordinator.resume(started.operation_id, mfa_code)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
