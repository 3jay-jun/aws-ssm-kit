"""Bounded, re-masked reading and structured writing of managed activity logs."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from aws_connect.application.activity_log_service import ActivityEvent, ManagedLogEntry
from aws_connect.infrastructure.masking import mask_text

_FILE_NAME = re.compile(r"^aws-connect\.log(?:\.\d+)?$")
_EVENT_MARKER = "activity_event="
_MAX_FILES = 8
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_FIELD_LIMIT = 160
_RESULTS = frozenset({"started", "succeeded", "failed", "cancelled", "notice"})


class StructuredActivityEventWriter:
    """Serialize only the fixed event schema to the centrally masked logger."""

    def write(self, event: ActivityEvent) -> None:
        payload = {
            "occurred_at": event.occurred_at.isoformat(),
            "feature": _safe(event.feature),
            "target": _safe(event.target),
            "result": event.result if event.result in _RESULTS else "failed",
            "message_code": _safe(event.message_code),
            "correlation_id": _safe_optional(event.correlation_id),
            "operation_id": _safe_optional(event.operation_id),
            "aws_service": _safe_optional(event.aws_service),
            "aws_action": _safe_optional(event.aws_action),
            "retryable": event.retryable if isinstance(event.retryable, bool) else None,
            "masked_detail": _safe_optional(event.masked_detail),
        }
        logging.getLogger("aws_connect.activity").info(
            "%s%s", _EVENT_MARKER, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )


class MaskedManagedLogReader:
    """Read only owned rotated logs using fixed names and strict resource bounds."""

    def read_recent(self, source_directory: Path, limit: int) -> list[ManagedLogEntry]:
        root = _resolve_directory(source_directory)
        if root is None:
            return []
        candidates = managed_log_files(root, limit=_MAX_FILES)

        entries: list[ManagedLogEntry] = []
        remaining = _MAX_TOTAL_BYTES
        for path in candidates:
            if remaining <= 0 or len(entries) >= limit:
                break
            size = min(_MAX_FILE_BYTES, remaining)
            content = _read_tail(path, size)
            remaining -= len(content)
            if not content:
                continue
            lines = content.decode("utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                # Parse the fixed schema first, then re-mask every accepted field in
                # ``_parse_entry``. Masking the serialized JSON can invalidate its
                # quoting and make an otherwise safe event unreadable.
                parsed = _parse_entry(line)
                if parsed is not None:
                    entries.append(parsed)
                    if len(entries) >= limit:
                        break
        entries.sort(key=lambda item: item.occurred_at, reverse=True)
        return entries[:limit]


def managed_log_files(source_directory: Path, *, limit: int = _MAX_FILES) -> list[Path]:
    """Return only fixed-name, non-symlink files contained by the resolved directory."""

    root = _resolve_directory(source_directory)
    if root is None:
        return []
    candidates: list[Path] = []
    try:
        for path in root.iterdir():
            if not _FILE_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            try:
                if path.resolve().parent != root:
                    continue
                path.stat()
            except OSError:
                continue
            candidates.append(path)
    except OSError:
        return []
    candidates.sort(key=_safe_mtime, reverse=True)
    return candidates[:limit]


def _read_tail(path: Path, maximum: int) -> bytes:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            start = max(0, length - maximum)
            stream.seek(start)
            data = stream.read(maximum)
        if start and b"\n" in data:
            data = data.split(b"\n", 1)[1]
        return data
    except OSError:
        return b""


def _parse_entry(line: str) -> ManagedLogEntry | None:
    marker = line.find(_EVENT_MARKER)
    if marker < 0:
        return None
    try:
        payload = json.loads(line[marker + len(_EVENT_MARKER) :])
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
        return ManagedLogEntry(
            occurred_at=occurred_at,
            feature=_safe(str(payload["feature"])),
            target=_safe(str(payload["target"])),
            result=_safe(str(payload["result"])),
            message_code=_safe(str(payload["message_code"])),
            correlation_id=_safe_optional(payload.get("correlation_id")),
            operation_id=_safe_optional(payload.get("operation_id")),
            aws_service=_safe_optional(payload.get("aws_service")),
            aws_action=_safe_optional(payload.get("aws_action")),
            retryable=_safe_bool_optional(payload.get("retryable")),
            masked_detail=_safe_optional(payload.get("masked_detail")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe(value: str) -> str:
    masked = mask_text(value).replace("\r", " ").replace("\n", " ")
    return masked[:_FIELD_LIMIT]


def _safe_optional(value: object) -> str | None:
    return _safe(str(value)) if value else None


def _safe_bool_optional(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _resolve_directory(source_directory: Path) -> Path | None:
    try:
        root = source_directory.expanduser().resolve()
        return root if root.is_dir() else None
    except (OSError, RuntimeError):
        return None
