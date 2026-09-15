"""CLI-only input and rendering for RDS tunnel application DTOs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from aws_connect.application.operations import OperationResult, OperationState
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.application.rds_tunnel_service import (
    StartTunnelRequest,
    TunnelConnectionResult,
)
from aws_connect.domain.tunnel_session import TunnelSession


def tunnel_session_payload(session: TunnelSession) -> dict[str, Any]:
    payload = asdict(session)
    payload["target_mode"] = session.target_mode.value
    for field in ("created_at", "updated_at", "last_used_at"):
        value = payload[field]
        payload[field] = value.isoformat() if value else None
    return payload


def tunnel_sessions_payload(sessions: list[TunnelSession]) -> dict[str, Any]:
    return {"tunnel_sessions": [tunnel_session_payload(session) for session in sessions]}


def tunnel_connection_payload(result: TunnelConnectionResult) -> dict[str, Any]:
    return {
        "tunnel": tunnel_session_payload(result.tunnel),
        "target_instance_id": result.target_instance_id,
        "session_id": result.handle.session_id,
        "owner": result.handle.owner.value,
        "state": result.handle.state.value,
        "plugin_exit_code": result.plugin_exit_code,
    }


def start_tunnel(
    coordinator: RdsTunnelOperationCoordinator,
    request: StartTunnelRequest,
    mfa_code_provider: Callable[[], str | None],
) -> OperationResult[TunnelConnectionResult]:
    started = coordinator.start(request)
    if started.state is not OperationState.MFA_REQUIRED:
        return started
    return coordinator.resume(started.operation_id, mfa_code_provider())
