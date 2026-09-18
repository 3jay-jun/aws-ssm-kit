"""Task-local diagnostic identity; never carries request payloads."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    correlation_id: str
    operation_id: str


@dataclass(slots=True)
class AwsCallDiagnostic:
    service: str | None = None
    action: str | None = None
    request_id: str | None = None


current_aws_call: ContextVar[AwsCallDiagnostic | None] = ContextVar("aws_call", default=None)


current_execution: ContextVar[ExecutionIdentity | None] = ContextVar("execution", default=None)


@contextmanager
def execution_scope(
    correlation_id: str | None = None, operation_id: str | None = None
) -> Iterator[ExecutionIdentity]:
    parent = current_execution.get()
    identity = ExecutionIdentity(
        correlation_id or (parent.correlation_id if parent else str(uuid4())),
        operation_id or str(uuid4()),
    )
    token = current_execution.set(identity)
    aws_token = current_aws_call.set(AwsCallDiagnostic())
    try:
        yield identity
    finally:
        current_aws_call.reset(aws_token)
        current_execution.reset(token)
