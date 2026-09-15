"""Cross-feature connection ownership used by destructive profile actions."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from aws_connect.application.ec2_service import Ec2Service
from aws_connect.application.operations import OperationState
from aws_connect.application.ports import ProfileStore
from aws_connect.application.profile_service import ProfileService
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.domain.errors import ApplicationError, ConfigurationError


@dataclass(frozen=True, slots=True)
class ProfileConnections:
    profile_id: int
    ec2_count: int
    rds_count: int

    @property
    def has_active(self) -> bool:
        return self.ec2_count > 0 or self.rds_count > 0


class ProfileConnectionLifecycleService:
    """Query and close profile-owned connections before deleting credentials."""

    def __init__(
        self,
        profiles: ProfileService,
        store: ProfileStore,
        ec2: Ec2Service,
        rds: RdsTunnelOperationCoordinator,
    ) -> None:
        self._profiles = profiles
        self._store = store
        self._ec2 = ec2
        self._rds = rds
        self._lock = Lock()

    def active(self, profile_id: int) -> ProfileConnections:
        ec2 = []
        rds = []
        first_error: ApplicationError | None = None
        try:
            ec2 = self._ec2.external_sessions(profile_id)
        except ApplicationError as error:
            first_error = error
        try:
            rds = self._rds.active_tunnels(profile_id)
        except ApplicationError as error:
            if first_error is None:
                first_error = error
        reported_failure = next((item.error for item in ec2 if item.error is not None), None)
        if reported_failure is None:
            reported_failure = next((item.error for item in rds if item.error is not None), None)
        if first_error is not None:
            raise first_error
        if reported_failure is not None:
            raise reported_failure
        return ProfileConnections(
            profile_id,
            sum(item.state is OperationState.RUNNING for item in ec2),
            sum(item.handle.state is OperationState.RUNNING for item in rds),
        )

    def delete(self, selector: str | int, *, stop_active: bool) -> None:
        with self._lock:
            profile_id = self._profiles.resolve(selector).require_id()
            active = self.active(profile_id)
            if active.has_active and not stop_active:
                raise ConfigurationError(
                    "profile.connections.active",
                    "Active EC2 or RDS connections must be stopped before profile deletion",
                )
            if active.has_active:
                first_error: ApplicationError | None = None
                try:
                    self._ec2.stop_external_for_profile(profile_id)
                except ApplicationError as error:
                    first_error = error
                try:
                    self._rds.stop_all(profile_id)
                except ApplicationError as error:
                    if first_error is None:
                        first_error = error
                if first_error is not None:
                    raise first_error
            self._store.delete(profile_id)
