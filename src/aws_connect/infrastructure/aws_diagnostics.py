"""Capture only AWS request metadata, never response bodies or credentials."""

from typing import Any

from aws_connect.application.execution_context import current_aws_call
from aws_connect.infrastructure.masking import mask_text


def observed_client(client: Any) -> Any:
    client.meta.events.register("after-call.*.*", _capture_response)
    return client


def _capture_response(model: Any, parsed: Any, **_kwargs: Any) -> None:
    diagnostic = current_aws_call.get()
    if diagnostic is None or not isinstance(parsed, dict):
        return
    diagnostic.service = str(model.service_model.service_name)
    diagnostic.action = str(model.name)
    request_id = parsed.get("ResponseMetadata", {}).get("RequestId")
    diagnostic.request_id = mask_text(str(request_id))[:160] if request_id else None
