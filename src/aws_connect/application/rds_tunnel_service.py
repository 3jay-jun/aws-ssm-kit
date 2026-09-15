"""Saved RDS sessions and foreground port-forwarding use cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.operations import OperationContext, OperationState
from aws_connect.application.ports import (
    Clock,
    LocalPortChecker,
    ManagedInstanceGateway,
    TunnelSessionStore,
)
from aws_connect.application.profile_service import ProfileService
from aws_connect.application.ssm_session import (
    ForegroundSsmSessionRunner,
    ManagedSsmSession,
    ManagedSsmSessionRunner,
)
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials
from aws_connect.domain.errors import (
    ApplicationError,
    ConfigurationError,
    PortAlreadyInUseError,
    TargetNotConnectedError,
)
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession

PORT_FORWARD_DOCUMENT = "AWS-StartPortForwardingSessionToRemoteHost"


@dataclass(frozen=True, slots=True)
class SaveTunnelSessionRequest:
    profile: str | int | None
    name: str
    host: str
    remote_port: int
    local_port: int
    target_mode: TargetMode
    target_instance_id: str | None = None
    tunnel_id: int | None = None


class TunnelOwner(StrEnum):
    FOREGROUND = "foreground"
    GUI = "gui"


@dataclass(frozen=True, slots=True)
class TunnelHandle:
    session_id: str
    owner: TunnelOwner
    state: OperationState
    operation_id: str | None = None
    process_id: int | None = None
    local_host: str = "127.0.0.1"
    local_port: int | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class TunnelConnectionResult:
    tunnel: TunnelSession
    target_instance_id: str
    handle: TunnelHandle
    plugin_exit_code: int | None
    error: ApplicationError | None = None


@dataclass(frozen=True, slots=True)
class StartTunnelRequest:
    tunnel: str | int
    profile: str | int | None = None
    selected_instance_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ManagedTunnel:
    tunnel: TunnelSession
    target_instance_id: str
    process_id: int
    session_id: str


class TunnelSessionService:
    """Manage profile-owned reusable tunnel settings."""

    def __init__(self, profiles: ProfileService, store: TunnelSessionStore) -> None:
        self._profiles = profiles
        self._store = store

    def list(self, profile: str | int | None = None) -> list[TunnelSession]:
        return self._store.list_tunnels(self._profile_id(profile))

    def show(self, selector: str | int, profile: str | int | None = None) -> TunnelSession:
        profile_id = self._profile_id(profile)
        session = self._resolve(selector, profile_id)
        if session.profile_id != profile_id:
            raise _configuration("rds.session.not_found")
        return session

    def create(self, request: SaveTunnelSessionRequest) -> TunnelSession:
        return self._store.create_tunnel(self._from_request(request, None))

    def update(self, request: SaveTunnelSessionRequest) -> TunnelSession:
        if request.tunnel_id is None:
            raise _configuration("rds.session.id.required")
        current = self.show(request.tunnel_id, request.profile)
        return self._store.update_tunnel(self._from_request(request, current))

    def clone(
        self,
        selector: str | int,
        name: str,
        profile: str | int | None = None,
    ) -> TunnelSession:
        """Create an independently owned copy through the normal create path."""

        source = self.show(selector, profile)
        return self.create(
            SaveTunnelSessionRequest(
                profile=source.profile_id,
                name=name,
                host=source.host,
                remote_port=source.remote_port,
                local_port=source.local_port,
                target_mode=source.target_mode,
                target_instance_id=source.target_instance_id,
            )
        )

    def delete(self, selector: str | int, profile: str | int | None = None) -> None:
        self._store.delete_tunnel(self.show(selector, profile).require_id())

    def _profile_id(self, selector: str | int | None) -> int:
        return self._profiles.resolve(selector).require_id()

    def _resolve(self, selector: str | int, profile_id: int) -> TunnelSession:
        if isinstance(selector, int) or selector.isdigit():
            session = self._store.get_tunnel(int(selector))
        else:
            session = self._store.get_tunnel_by_name(profile_id, selector)
        if session is None:
            raise _configuration("rds.session.not_found")
        return session

    def _from_request(
        self, request: SaveTunnelSessionRequest, current: TunnelSession | None
    ) -> TunnelSession:
        profile_id = self._profile_id(request.profile)
        return TunnelSession(
            id=current.id if current else None,
            profile_id=profile_id,
            name=request.name,
            host=request.host,
            remote_port=request.remote_port,
            local_port=request.local_port,
            target_mode=request.target_mode,
            target_instance_id=request.target_instance_id,
            created_at=current.created_at if current else None,
            updated_at=current.updated_at if current else None,
            last_used_at=current.last_used_at if current else None,
        )


class RdsTunnelService:
    """Validate and own one foreground RDS tunnel lifecycle."""

    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        saved: TunnelSessionService,
        store: TunnelSessionStore,
        managed_instances: ManagedInstanceGateway,
        runner: ForegroundSsmSessionRunner,
        ports: LocalPortChecker,
        clock: Clock,
        managed_runner: ManagedSsmSessionRunner | None = None,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._saved = saved
        self._store = store
        self._managed_instances = managed_instances
        self._runner = runner
        self._ports = ports
        self._clock = clock
        self._managed_runner = managed_runner
        self._managed: dict[str, _ManagedTunnel] = {}
        self._managed_lock = Lock()

    def start(
        self,
        request: StartTunnelRequest,
        *,
        context: OperationContext | None = None,
    ) -> TunnelConnectionResult:
        context = context or OperationContext()
        context.raise_if_cancelled()
        profile, tunnel, credentials, target, parameters = self._prepare(request)
        context.report("target-validation", "rds.tunnel.target.validate", target=target)
        result = self._runner.run(
            credentials,
            profile.region,
            target,
            document_name=PORT_FORWARD_DOCUMENT,
            parameters=parameters,
            context=context,
        )
        self._store.touch_tunnel(tunnel.require_id(), self._clock.now())
        return TunnelConnectionResult(
            self._saved.show(tunnel.require_id(), profile.require_id()),
            target,
            TunnelHandle(result.session_id, TunnelOwner.FOREGROUND, OperationState.SUCCEEDED),
            result.plugin_exit_code,
        )

    def start_managed(
        self,
        request: StartTunnelRequest,
        *,
        context: OperationContext | None = None,
    ) -> TunnelConnectionResult:
        """Start a GUI-owned tunnel and return immediately with a running handle."""

        if self._managed_runner is None:
            raise _configuration("rds.tunnel.managed_runner.unavailable")
        context = context or OperationContext()
        context.raise_if_cancelled()
        profile, tunnel, credentials, target, parameters = self._prepare(request)
        context.report("target-validation", "rds.tunnel.target.validate", target=target)
        managed = self._managed_runner.start(
            credentials,
            profile.region,
            target,
            document_name=PORT_FORWARD_DOCUMENT,
            parameters=parameters,
            context=context,
        )
        with self._managed_lock:
            self._managed[managed.operation_id] = _ManagedTunnel(
                tunnel, target, managed.process_id, managed.session_id
            )
        self._store.touch_tunnel(tunnel.require_id(), self._clock.now())
        return TunnelConnectionResult(
            self._saved.show(tunnel.require_id(), profile.require_id()),
            target,
            self._handle(tunnel, managed, TunnelOwner.GUI),
            None,
        )

    def active_tunnels(self, profile_id: int | None = None) -> list[TunnelConnectionResult]:
        if self._managed_runner is None:
            return []
        with self._managed_lock:
            owned = [
                (operation_id, entry)
                for operation_id, entry in self._managed.items()
                if profile_id is None or entry.tunnel.profile_id == profile_id
            ]
        active: list[TunnelConnectionResult] = []
        for operation_id, entry in owned:
            try:
                managed = self._managed_runner.status(operation_id)
            except ApplicationError as error:
                managed = ManagedSsmSession(
                    operation_id,
                    entry.process_id,
                    entry.session_id,
                    OperationState.FAILED,
                    error=error,
                )
            active.append(
                TunnelConnectionResult(
                    entry.tunnel,
                    entry.target_instance_id,
                    self._handle(entry.tunnel, managed, TunnelOwner.GUI),
                    managed.exit_code,
                    managed.error,
                )
            )
            if managed.state is not OperationState.RUNNING:
                with self._managed_lock:
                    self._managed.pop(operation_id, None)
        return active

    def stop(self, operation_id: str, *, context: OperationContext | None = None) -> TunnelHandle:
        if self._managed_runner is None:
            raise _configuration("rds.tunnel.managed_runner.unavailable")
        with self._managed_lock:
            entry = self._managed.get(operation_id)
        if entry is None:
            raise _configuration("rds.tunnel.operation.not_found")
        tunnel = entry.tunnel
        context = context or OperationContext(operation_id=operation_id)
        context.raise_if_cancelled()
        context.report("stopping", "rds.tunnel.stop", target=tunnel.name)
        try:
            managed = self._managed_runner.stop(operation_id)
        finally:
            with self._managed_lock:
                self._managed.pop(operation_id, None)
        if managed.error is not None:
            raise managed.error
        context.report("stopped", "rds.tunnel.stopped", completed=1, total=1, target=tunnel.name)
        return self._handle(tunnel, managed, TunnelOwner.GUI)

    def stop_all(self, profile_id: int | None = None) -> list[TunnelHandle]:
        with self._managed_lock:
            operation_ids = [
                operation_id
                for operation_id, entry in self._managed.items()
                if profile_id is None or entry.tunnel.profile_id == profile_id
            ]
        stopped: list[TunnelHandle] = []
        first_error: ApplicationError | None = None
        for operation_id in operation_ids:
            try:
                stopped.append(self.stop(operation_id))
            except ApplicationError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        return stopped

    def _prepare(
        self, request: StartTunnelRequest
    ) -> tuple[AwsProfile, TunnelSession, PlainCredentials, str, dict[str, list[str]]]:
        profile = self._profiles.resolve(request.profile)
        tunnel = self._saved.show(request.tunnel, profile.require_id())

        # Local failure must be detected before credential validation or any AWS call.
        if not self._ports.is_available(tunnel.local_port):
            raise PortAlreadyInUseError(
                message_code="rds.tunnel.local_port_in_use",
                technical_cause=f"Local port {tunnel.local_port} is already in use",
            )
        target = tunnel.resolve_target(request.selected_instance_id)
        credentials = self._sessions.require_credentials(profile.require_id())
        self._require_online_target(credentials, profile.region, target)
        return (
            profile,
            tunnel,
            credentials,
            target,
            {
                "host": [tunnel.host],
                "portNumber": [str(tunnel.remote_port)],
                "localPortNumber": [str(tunnel.local_port)],
            },
        )

    @staticmethod
    def _handle(
        tunnel: TunnelSession, managed: ManagedSsmSession, owner: TunnelOwner
    ) -> TunnelHandle:
        return TunnelHandle(
            session_id=managed.session_id,
            owner=owner,
            state=managed.state,
            operation_id=managed.operation_id,
            process_id=managed.process_id,
            local_port=tunnel.local_port,
            exit_code=managed.exit_code,
        )

    def _require_online_target(
        self, credentials: PlainCredentials, region: str, target: str
    ) -> None:
        online = self._managed_instances.list_online(credentials, region)
        if not any(item.instance_id == target and item.ping_status == "Online" for item in online):
            raise TargetNotConnectedError(
                message_code="rds.tunnel.target_not_online",
                technical_cause="The relay instance is not an online SSM managed node",
                aws_service="ssm",
                aws_action="DescribeInstanceInformation",
            )


def _configuration(code: str) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=code)
