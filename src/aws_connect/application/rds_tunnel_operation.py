"""Single-resume MFA coordination for RDS tunnel starts."""

from __future__ import annotations

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import (
    CancellationToken,
    OperationContext,
    OperationResult,
    ProgressReporter,
    execute_operation,
    new_operation_id,
)
from aws_connect.application.rds_tunnel_service import (
    RdsTunnelService,
    StartTunnelRequest,
    TunnelConnectionResult,
    TunnelHandle,
)


class RdsTunnelOperationCoordinator:
    """Defer every tunnel side effect until authentication has completed."""

    def __init__(
        self, tunnels: RdsTunnelService, authentication: AuthenticatedOperationCoordinator
    ) -> None:
        self._tunnels = tunnels
        self._authentication = authentication

    def local_port_available(self, port: int) -> bool:
        return self._tunnels.local_port_available(port)

    def start(
        self,
        request: StartTunnelRequest,
        *,
        cancellation: CancellationToken | None = None,
        progress: ProgressReporter | None = None,
    ) -> OperationResult[TunnelConnectionResult]:
        return self._start(request, managed=False, cancellation=cancellation, progress=progress)

    def _start(
        self,
        request: StartTunnelRequest,
        *,
        managed: bool,
        cancellation: CancellationToken | None = None,
        progress: ProgressReporter | None = None,
    ) -> OperationResult[TunnelConnectionResult]:
        context = _context(cancellation=cancellation, progress=progress)
        return self._authentication.start_long(
            request.profile,
            lambda current: (
                self._tunnels.start_managed(request, context=current)
                if managed
                else self._tunnels.start(request, context=current)
            ),
            context,
        )

    def resume(
        self, operation_id: str, mfa_code: str | None
    ) -> OperationResult[TunnelConnectionResult]:
        return self._authentication.resume(operation_id, mfa_code)

    def cancel(self, operation_id: str) -> None:
        """Release both authentication layers when an adapter cannot collect MFA."""

        self._authentication.cancel(operation_id)

    def start_managed(self, request: StartTunnelRequest) -> OperationResult[TunnelConnectionResult]:
        return self.start_managed_operation(request)

    def start_managed_operation(
        self,
        request: StartTunnelRequest,
        *,
        cancellation: CancellationToken | None = None,
        progress: ProgressReporter | None = None,
    ) -> OperationResult[TunnelConnectionResult]:
        return self._start(
            request,
            managed=True,
            cancellation=cancellation,
            progress=progress,
        )

    def active_tunnels(self, profile_id: int | None = None) -> list[TunnelConnectionResult]:
        return self._tunnels.active_tunnels(profile_id)

    def stop(
        self,
        operation_id: str,
        *,
        cancellation: CancellationToken | None = None,
        progress: ProgressReporter | None = None,
    ) -> OperationResult[TunnelHandle]:
        context = _context(operation_id=operation_id, cancellation=cancellation, progress=progress)
        return execute_operation(
            lambda current: self._tunnels.stop(operation_id, context=current), context
        )

    def stop_all(self, profile_id: int | None = None) -> list[TunnelHandle]:
        return self._tunnels.stop_all(profile_id)


def _context(
    *,
    operation_id: str | None = None,
    cancellation: CancellationToken | None = None,
    progress: ProgressReporter | None = None,
) -> OperationContext:
    return OperationContext(
        operation_id=operation_id or new_operation_id(),
        cancellation=cancellation or CancellationToken(),
        progress=progress,
    )
