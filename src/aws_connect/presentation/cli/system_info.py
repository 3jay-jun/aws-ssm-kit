"""CLI mapping for the bootstrap system information use case."""

import json

from aws_connect.application.ports import SessionPlugin
from aws_connect.application.system_info import DoctorService, SystemInfoService


def render_system_info(
    service: SystemInfoService, *, output: str, plugin: SessionPlugin | None = None
) -> str:
    """Render system information for human or machine consumption."""

    result = service.get()
    payload: dict[str, object] = {
        "application_name": result.application_name,
        "status": result.status,
    }
    if plugin is not None:
        diagnostic = plugin.diagnose()
        payload["session_manager_plugin"] = {
            "path": str(diagnostic.path),
            "exists": diagnostic.exists,
            "version": diagnostic.version,
            "supported": diagnostic.supported,
            "environment_ready": diagnostic.environment_ready,
        }
        if not (diagnostic.exists and diagnostic.supported and diagnostic.environment_ready):
            payload["status"] = "degraded"
    if output == "json":
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
    if plugin is None:
        return f"{result.application_name}: {result.status}"
    return (
        f"{result.application_name}: {payload['status']}\n"
        f"Session Manager Plugin: {diagnostic.version or 'unavailable'} "
        f"({diagnostic.path})"
    )


def render_doctor(service: DoctorService, *, output: str) -> str:
    system, diagnostics = service.get()
    payload: dict[str, object] = {
        "application_name": system.application_name,
        "status": system.status,
        **diagnostics.to_payload(),
    }
    if output == "json":
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    database = diagnostics.database
    plugin = diagnostics.session_manager_plugin
    return "\n".join(
        (
            f"{system.application_name}: {system.status}",
            f"Database: schema {database.migration_version}/{database.expected_migration_version} "
            f"({database.path})",
            f"DPAPI: {'available' if diagnostics.data_protection.available else 'unavailable'}",
            f"Logs: {diagnostics.logging.level} ({diagnostics.logging.path})",
            f"Session Manager Plugin: {plugin.version or 'unavailable'} ({plugin.path})",
            f"Profile: {diagnostics.profile.default_profile or 'not configured'} "
            f"[{diagnostics.profile.session_state}]",
            f"Region: {diagnostics.profile.region or 'not configured'} "
            f"[{diagnostics.profile.region_access}]",
        )
    )
