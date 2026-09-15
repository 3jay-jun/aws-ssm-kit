"""EC2 Session Manager listing and foreground connection use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.operations import OperationContext, OperationState
from aws_connect.application.ports import (
    Ec2FavoriteStore,
    Ec2InventoryGateway,
    Ec2Metadata,
    Ec2MetadataGateway,
    Ec2PowerGateway,
    ManagedInstance,
    ManagedInstanceGateway,
    PluginDiagnostic,
    SessionPlugin,
)
from aws_connect.application.profile_service import ProfileService
from aws_connect.application.ssm_session import (
    ForegroundSsmSessionRunner,
    ManagedSsmSession,
    ManagedSsmSessionRunner,
)
from aws_connect.domain.aws_profile import PlainCredentials, validated_region
from aws_connect.domain.errors import (
    ApplicationError,
    AwsPermissionError,
    ConfigurationError,
    PluginExecutionError,
    TargetNotConnectedError,
)


@dataclass(frozen=True, slots=True)
class Ec2Target:
    instance_id: str
    name: str | None
    private_ip_address: str | None
    platform_name: str | None
    ssm_ping_status: str
    instance_state: str | None = "running"
    favorite: bool = False
    power_actions_available: bool = True

    @property
    def ping_status(self) -> str:
        """Compatibility alias for existing CLI/GUI adapters."""

        return self.ssm_ping_status


@dataclass(frozen=True, slots=True)
class Ec2TargetFilter:
    """Application-owned target filtering rules shared by presentation adapters."""

    keyword: str = ""
    ping_status: str = "Online"
    instance_state: str = "all"


def filter_ec2_targets(targets: list[Ec2Target], criteria: Ec2TargetFilter) -> list[Ec2Target]:
    """Apply shared power/SSM/keyword rules and favorite-first ordering."""

    if criteria.ping_status not in {"all", "Online", "Offline"}:
        raise ConfigurationError(
            "ec2.filter.status.unsupported",
            "SSM status must be all, Online, or Offline",
        )
    if criteria.instance_state not in {"all", "running", "stopped"}:
        raise ConfigurationError(
            "ec2.filter.instance_state.unsupported",
            "EC2 state must be all, running, or stopped",
        )
    query = criteria.keyword.strip().casefold()
    filtered = [
        target
        for target in targets
        if (criteria.ping_status == "all" or target.ssm_ping_status == criteria.ping_status)
        and (criteria.instance_state == "all" or target.instance_state == criteria.instance_state)
        and (
            not query
            or query
            in " ".join(
                filter(
                    None,
                    (
                        target.name,
                        target.instance_id,
                        target.private_ip_address,
                        target.platform_name,
                    ),
                )
            ).casefold()
        )
    ]
    return sorted(filtered, key=_target_sort_key)


def _target_sort_key(target: Ec2Target) -> tuple[bool, str, str]:
    """SSOT for stable favorite-first target ordering."""

    return (not target.favorite, (target.name or "").casefold(), target.instance_id)


@dataclass(frozen=True, slots=True)
class Ec2ConnectionResult:
    instance_id: str
    session_id: str
    plugin_exit_code: int


@dataclass(frozen=True, slots=True)
class Ec2PowerActionResult:
    instance_id: str
    action: str
    previous_state: str
    request_accepted: bool = True


@dataclass(frozen=True, slots=True)
class ExternalSessionHandle:
    operation_id: str
    process_id: int
    ssm_session_id: str
    instance_id: str
    profile_id: int
    state: OperationState
    exit_code: int | None = None
    error: ApplicationError | None = None


@dataclass(frozen=True, slots=True)
class _ExternalSession:
    profile_id: int
    instance_id: str
    process_id: int
    ssm_session_id: str


class Ec2Service:
    """Own target enrichment, online validation and SSM session cleanup."""

    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        managed_instances: ManagedInstanceGateway,
        metadata: Ec2MetadataGateway,
        plugin: SessionPlugin,
        managed_runner: ManagedSsmSessionRunner | None = None,
        inventory: Ec2InventoryGateway | None = None,
        power: Ec2PowerGateway | None = None,
        favorites: Ec2FavoriteStore | None = None,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._managed_instances = managed_instances
        self._metadata = metadata
        self._inventory = inventory
        self._power = power
        self._favorites = favorites
        self._runner = ForegroundSsmSessionRunner(managed_instances, plugin)
        self._managed_runner = managed_runner or ManagedSsmSessionRunner(managed_instances, plugin)
        self._external: dict[str, _ExternalSession] = {}
        self._external_lock = Lock()

    def list_targets(self, selector: str | int | None = None) -> list[Ec2Target]:
        profile = self._profiles.resolve(selector)
        credentials = self._sessions.require_credentials(profile.require_id())
        return self._list(credentials, profile.region, profile.require_id())

    def list_targets_in_region(
        self,
        selector: str | int | None,
        region: str,
    ) -> list[Ec2Target]:
        """List online SSM targets in an explicit feature-level Region."""

        profile = self._profiles.resolve(selector)
        credentials = self._sessions.require_credentials(profile.require_id())
        return self._list(credentials, self._validated_region(region), profile.require_id())

    def set_favorite(
        self,
        instance_id: str,
        favorite: bool,
        selector: str | int | None = None,
        *,
        region: str | None = None,
    ) -> None:
        if self._favorites is None:
            raise ConfigurationError(
                "ec2.favorite.unavailable", "EC2 favorite persistence is not configured"
            )
        profile = self._profiles.resolve(selector)
        selected_region = self._validated_region(region or profile.region)
        normalized_id = instance_id.strip()
        if not normalized_id:
            raise ConfigurationError("ec2.instance_id.required", "EC2 instance ID is required")
        self._favorites.set_ec2_favorite(
            profile.require_id(), selected_region, normalized_id, favorite
        )

    def start_instance(
        self,
        instance_id: str,
        selector: str | int | None = None,
        *,
        region: str | None = None,
        context: OperationContext | None = None,
    ) -> Ec2PowerActionResult:
        return self._power_action("start", instance_id, selector, region, context)

    def reboot_instance(
        self,
        instance_id: str,
        selector: str | int | None = None,
        *,
        region: str | None = None,
        context: OperationContext | None = None,
    ) -> Ec2PowerActionResult:
        return self._power_action("reboot", instance_id, selector, region, context)

    def connect(
        self,
        instance_id: str,
        selector: str | int | None = None,
        *,
        region: str | None = None,
        context: OperationContext | None = None,
    ) -> Ec2ConnectionResult:
        profile = self._profiles.resolve(selector)
        credentials = self._sessions.require_credentials(profile.require_id())
        context = context or OperationContext()
        context.raise_if_cancelled()
        context.report("target-validation", "ec2.session.target.validate", target=instance_id)
        selected_region = self._validated_region(region or profile.region)
        self._require_target(credentials, selected_region, instance_id)

        result = self._runner.run(credentials, selected_region, instance_id, context=context)
        return Ec2ConnectionResult(instance_id, result.session_id, result.plugin_exit_code)

    def diagnose_plugin(self) -> PluginDiagnostic:
        return self._runner.diagnose()

    def connect_external(
        self,
        instance_id: str,
        selector: str | int | None = None,
        *,
        region: str | None = None,
        context: OperationContext | None = None,
        document_name: str | None = None,
        parameters: Mapping[str, Sequence[str]] | None = None,
    ) -> ExternalSessionHandle:
        """Open an EC2 session in a user-owned Windows Terminal process."""

        profile = self._profiles.resolve(selector)
        credentials = self._sessions.require_credentials(profile.require_id())
        context = context or OperationContext()
        context.raise_if_cancelled()
        context.report("target-validation", "ec2.session.target.validate", target=instance_id)
        selected_region = self._validated_region(region or profile.region)
        self._require_target(credentials, selected_region, instance_id)
        context.raise_if_cancelled()
        if document_name is None and parameters is None:
            managed = self._managed_runner.start(
                credentials,
                selected_region,
                instance_id,
                external_terminal=True,
                context=context,
            )
        else:
            managed = self._managed_runner.start(
                credentials,
                selected_region,
                instance_id,
                document_name=document_name,
                parameters=parameters,
                external_terminal=True,
                context=context,
            )
        with self._external_lock:
            self._external[managed.operation_id] = _ExternalSession(
                profile.require_id(), instance_id, managed.process_id, managed.session_id
            )
        return self._external_handle(managed, profile.require_id(), instance_id)

    @staticmethod
    def _validated_region(region: str) -> str:
        return validated_region(region)

    def external_sessions(self, profile_id: int | None = None) -> list[ExternalSessionHandle]:
        """Reap every owned process, then project the requested profile's snapshots."""

        return [
            handle
            for handle in self.reap_external_sessions()
            if profile_id is None or handle.profile_id == profile_id
        ]

    def reap_external_sessions(self) -> list[ExternalSessionHandle]:
        """Poll every owned external session regardless of the selected UI profile."""

        handles: list[ExternalSessionHandle] = []
        with self._external_lock:
            sessions = list(self._external.items())
        for operation_id, metadata in sessions:
            try:
                managed = self._managed_runner.status(operation_id)
            except ApplicationError as error:
                managed = ManagedSsmSession(
                    operation_id,
                    metadata.process_id,
                    metadata.ssm_session_id,
                    OperationState.FAILED,
                    error=error,
                )
            handles.append(
                self._external_handle(managed, metadata.profile_id, metadata.instance_id)
            )
            if managed.state is not OperationState.RUNNING:
                with self._external_lock:
                    self._external.pop(operation_id, None)
        return handles

    def stop_external(
        self,
        operation_id: str,
        *,
        context: OperationContext | None = None,
    ) -> ExternalSessionHandle:
        with self._external_lock:
            metadata = self._external.get(operation_id)
        if metadata is None:
            raise PluginExecutionError(
                "session.operation.not_found", "External EC2 session is not tracked"
            )
        context = context or OperationContext(operation_id=operation_id)
        context.raise_if_cancelled()
        context.report("stopping", "ec2.session.stop", target=metadata.instance_id)
        try:
            managed = self._managed_runner.stop(operation_id)
        finally:
            with self._external_lock:
                self._external.pop(operation_id, None)
        context.report("stopped", "ec2.session.stopped", completed=1, total=1)
        return self._external_handle(managed, metadata.profile_id, metadata.instance_id)

    def stop_external_for_profile(self, profile_id: int) -> list[ExternalSessionHandle]:
        with self._external_lock:
            operation_ids = [
                operation_id
                for operation_id, metadata in self._external.items()
                if metadata.profile_id == profile_id
            ]
        stopped: list[ExternalSessionHandle] = []
        first_error: ApplicationError | None = None
        for operation_id in operation_ids:
            try:
                handle = self.stop_external(operation_id)
                stopped.append(handle)
                if handle.error is not None and first_error is None:
                    first_error = handle.error
            except ApplicationError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        return stopped

    @staticmethod
    def _external_handle(
        managed: ManagedSsmSession, profile_id: int, instance_id: str
    ) -> ExternalSessionHandle:
        return ExternalSessionHandle(
            managed.operation_id,
            managed.process_id,
            managed.session_id,
            instance_id,
            profile_id,
            managed.state,
            managed.exit_code,
            managed.error,
        )

    def _list(self, credentials: PlainCredentials, region: str, profile_id: int) -> list[Ec2Target]:
        if self._inventory is not None:
            try:
                return self._inventory_targets(credentials, region, profile_id)
            except AwsPermissionError:
                pass
        return self._online_fallback(credentials, region, profile_id)

    def _online_fallback(
        self, credentials: PlainCredentials, region: str, profile_id: int
    ) -> list[Ec2Target]:
        managed = [
            item
            for item in self._managed_instances.list_online(credentials, region)
            if item.ping_status == "Online"
        ]
        metadata = self._metadata_or_empty(credentials, region, managed)
        favorites = self._favorite_ids(profile_id, region)
        targets: list[Ec2Target] = []
        for item in managed:
            details = metadata.get(item.instance_id)
            targets.append(
                Ec2Target(
                    instance_id=item.instance_id,
                    name=details.name if details else None,
                    private_ip_address=(details.private_ip_address if details else item.ip_address),
                    platform_name=item.platform_name,
                    ssm_ping_status="Online",
                    instance_state=None,
                    favorite=item.instance_id in favorites,
                    power_actions_available=False,
                )
            )
        return sorted(targets, key=_target_sort_key)

    def _inventory_targets(
        self, credentials: PlainCredentials, region: str, profile_id: int
    ) -> list[Ec2Target]:
        if self._inventory is None:
            return []
        inventory = self._inventory.list_inventory(credentials, region)
        managed = self._managed_instances.list_managed(credentials, region)
        managed_by_id = {item.instance_id: item for item in managed}
        favorites = self._favorite_ids(profile_id, region)
        targets: list[Ec2Target] = []
        for instance in inventory:
            ssm = managed_by_id.get(instance.instance_id)
            targets.append(
                Ec2Target(
                    instance_id=instance.instance_id,
                    name=instance.name,
                    private_ip_address=instance.private_ip_address,
                    platform_name=(
                        ssm.platform_name if ssm and ssm.platform_name else instance.platform_name
                    ),
                    ssm_ping_status="Online" if ssm and ssm.ping_status == "Online" else "Offline",
                    instance_state=instance.state,
                    favorite=instance.instance_id in favorites,
                )
            )
        return sorted(targets, key=_target_sort_key)

    def _favorite_ids(self, profile_id: int, region: str) -> set[str]:
        if self._favorites is None:
            return set()
        return self._favorites.list_ec2_favorites(profile_id, region)

    def _require_target(self, credentials: PlainCredentials, region: str, instance_id: str) -> None:
        if any(
            target.instance_id == instance_id and target.ping_status == "Online"
            for target in self._managed_instances.list_online(credentials, region)
        ):
            return
        raise TargetNotConnectedError(
            message_code="ec2.target.not_online",
            technical_cause="The requested instance is not an online SSM managed node",
            aws_service="ssm",
            aws_action="DescribeInstanceInformation",
        )

    def _metadata_or_empty(
        self,
        credentials: PlainCredentials,
        region: str,
        managed: list[ManagedInstance],
    ) -> dict[str, Ec2Metadata]:
        if not managed:
            return {}
        try:
            return self._metadata.describe(
                credentials, region, [item.instance_id for item in managed]
            )
        except AwsPermissionError:
            return {}

    def _power_action(
        self,
        action: str,
        instance_id: str,
        selector: str | int | None,
        region: str | None,
        context: OperationContext | None,
    ) -> Ec2PowerActionResult:
        if self._inventory is None or self._power is None:
            raise ConfigurationError(
                "ec2.power.unavailable",
                "EC2 power actions require DescribeInstances permission and gateway configuration",
            )
        profile = self._profiles.resolve(selector)
        credentials = self._sessions.require_credentials(profile.require_id())
        selected_region = self._validated_region(region or profile.region)
        operation = context or OperationContext()
        operation.raise_if_cancelled()
        operation.report("target-validation", f"ec2.{action}.target.validate", target=instance_id)
        inventory = {
            item.instance_id: item
            for item in self._inventory.list_inventory(credentials, selected_region)
        }
        target = inventory.get(instance_id)
        if target is None:
            raise ConfigurationError(
                "ec2.instance.not_found", "The requested EC2 instance was not found"
            )
        required_state = "stopped" if action == "start" else "running"
        if target.state != required_state:
            raise ConfigurationError(
                f"ec2.{action}.state.invalid",
                f"EC2 {action} requires state {required_state}; current state is {target.state}",
            )
        operation.raise_if_cancelled()
        if action == "start":
            self._power.start_instance(credentials, selected_region, instance_id)
        else:
            self._power.reboot_instance(credentials, selected_region, instance_id)
        operation.report("requested", f"ec2.{action}.requested", completed=1, total=1)
        return Ec2PowerActionResult(instance_id, action, target.state)
