"""CLI-only rendering for EC2 application DTOs."""

from __future__ import annotations

from typing import Any

from aws_connect.application.ec2_service import Ec2ConnectionResult, Ec2Target


def targets_payload(targets: list[Ec2Target]) -> dict[str, Any]:
    return {
        "instances": [
            {
                "instance_id": target.instance_id,
                "name": target.name,
                "private_ip_address": target.private_ip_address,
                "platform_name": target.platform_name,
                "ping_status": target.ping_status,
            }
            for target in targets
        ]
    }


def connection_payload(result: Ec2ConnectionResult) -> dict[str, Any]:
    return {
        "instance_id": result.instance_id,
        "session_id": result.session_id,
        "plugin_exit_code": result.plugin_exit_code,
    }
