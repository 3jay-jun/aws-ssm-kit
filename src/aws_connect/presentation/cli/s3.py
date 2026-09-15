"""S3 CLI payload mapping without transfer business rules."""

from __future__ import annotations

from typing import Any

from aws_connect.application.operations import ProgressEvent
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import UploadPlan, UploadSummary
from aws_connect.domain.s3_location import S3Location


def location_payload(value: S3Location) -> dict[str, Any]:
    return {
        "id": value.id,
        "profile_id": value.profile_id,
        "name": value.name,
        "bucket": value.bucket,
        "prefix": value.prefix,
    }


def locations_payload(values: list[S3Location]) -> dict[str, Any]:
    return {"s3_locations": [location_payload(value) for value in values]}


def objects_payload(bucket: str, prefix: str, values: list[S3Object]) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "prefix": prefix,
        "objects": [
            {
                "key": value.key,
                "size": value.size,
                "last_modified": value.last_modified.isoformat() if value.last_modified else None,
                "is_prefix": value.is_prefix,
            }
            for value in values
        ],
    }


def upload_plan_payload(plan: UploadPlan) -> dict[str, Any]:
    return {
        "upload_preview": [
            {
                "source": str(item.source),
                "target_uri": item.uri,
                "size": item.size,
                "exists": item.exists,
            }
            for item in plan.items
        ],
        "requires_overwrite_confirmation": any(item.exists for item in plan.items),
        "uploaded": False,
    }


def upload_result_payload(
    summary: UploadSummary,
    operation_id: str,
    preview: dict[str, Any],
    progress: list[ProgressEvent] | None = None,
) -> dict[str, Any]:
    return {
        **preview,
        "operation_id": operation_id,
        "state": "SUCCEEDED",
        "uploaded": list(summary.uploaded),
        "bytes_transferred": summary.bytes_transferred,
        "progress": [
            {
                "operation_id": event.operation_id,
                "phase": event.phase,
                "completed": event.completed,
                "total": event.total,
                "message_code": event.message_code,
                "target_uri": event.target,
            }
            for event in (progress or [])
        ],
    }


def render_upload_progress(event: ProgressEvent) -> str:
    """Render one event immediately without carrying CLI text into Application."""

    completed = event.completed if event.completed is not None else "?"
    total = event.total if event.total is not None else "?"
    target = f" {event.target}" if event.target else ""
    return f"{event.message_code}{target}: {completed}/{total} bytes"
