"""Read-only capability observations; never an authorization guarantee."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aws_connect.application.activity_log_service import ActivityLogService
from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.ports import (
    Clock,
    Ec2InventoryGateway,
    ManagedInstanceGateway,
    S3Gateway,
    SecretsGateway,
)
from aws_connect.application.profile_service import ProfileService
from aws_connect.domain.errors import ApplicationError, AwsPermissionError
from aws_connect.domain.execution_log import (
    ErrorCategory,
    ExecutionLogEvent,
    ExecutionLogFilter,
    ExecutionResult,
)
from aws_connect.domain.sensitive_data import REDACTED


class PermissionState(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    key: str
    label: str
    service: str
    actions: tuple[str, ...]
    feature: str | None = None


# Display aliases remain distinct from actual AWS API action names.
PERMISSIONS: dict[str, tuple[PermissionDefinition, ...]] = {
    "ec2": (
        PermissionDefinition(
            "inventory", "인스턴스 조회 (DescribeInstances)", "ec2", ("DescribeInstances",)
        ),
        PermissionDefinition(
            "session", "SSM 세션 연결 (StartSession)", "ssm", ("StartSession",), "ec2"
        ),
        PermissionDefinition(
            "managed",
            "인스턴스 정보 조회 (DescribeInstanceInformation)",
            "ssm",
            ("DescribeInstanceInformation",),
        ),
    ),
    "rds": (
        PermissionDefinition("inventory", "중계 EC2 조회", "ec2", ("DescribeInstances",)),
        PermissionDefinition("session", "SSM 세션 연결", "ssm", ("StartSession",), "rds"),
        PermissionDefinition(
            "forward", "포트포워딩 (StartPortForwardingSession)", "ssm", ("StartSession",), "rds"
        ),
    ),
    "secrets": (
        PermissionDefinition(
            "list", "Secret 목록 조회 (ListSecrets)", "secretsmanager", ("ListSecrets",)
        ),
        PermissionDefinition(
            "get", "Secret 값 조회 (GetSecretValue)", "secretsmanager", ("GetSecretValue",)
        ),
        PermissionDefinition(
            "write",
            "Secret 생성/수정 (Create/UpdateSecret)",
            "secretsmanager",
            ("CreateSecret", "UpdateSecret"),
        ),
        PermissionDefinition(
            "delete", "Secret 삭제 (DeleteSecret)", "secretsmanager", ("DeleteSecret",)
        ),
    ),
    "s3": (
        PermissionDefinition("buckets", "버킷 목록 조회 (ListBuckets)", "s3", ("ListBuckets",)),
        PermissionDefinition("list", "파일 목록 조회 (ListObjects)", "s3", ("ListObjectsV2",)),
        PermissionDefinition("put", "파일 업로드 (PutObject)", "s3", ("PutObject",)),
        PermissionDefinition("delete", "파일 삭제 (DeleteObject)", "s3", ("DeleteObject",)),
    ),
}


@dataclass(frozen=True, slots=True)
class PermissionObservation:
    key: str
    label: str
    state: PermissionState
    explanation: str


@dataclass(frozen=True, slots=True)
class FeatureCapability:
    feature: str
    state: CapabilityState
    permissions: tuple[PermissionObservation, ...]


@dataclass(frozen=True, slots=True)
class DashboardPermissions:
    profile_id: int
    region: str
    checked_at: datetime
    features: tuple[FeatureCapability, ...]


def capability_state(states: Sequence[PermissionState]) -> CapabilityState:
    if all(state is PermissionState.ALLOWED for state in states):
        return CapabilityState.AVAILABLE
    if all(state is PermissionState.DENIED for state in states):
        return CapabilityState.UNAVAILABLE
    if PermissionState.ALLOWED in states:
        return CapabilityState.PARTIAL
    return CapabilityState.UNKNOWN


def _historical_state(
    definition: PermissionDefinition,
    events: Sequence[ExecutionLogEvent],
) -> PermissionState:
    # Latest result per action/resource: a network failure invalidates older certainty.
    latest: dict[tuple[str, str, str], PermissionState] = {}
    for event in events:
        action = event.aws_action
        # Successful compound APIs prove PutObject; their failures may instead be
        # caused by source GetObject or KMS and must not imply a PutObject denial.
        if (
            event.aws_service == "s3"
            and event.result is ExecutionResult.SUCCESS
            and action in ("CopyObject", "CompleteMultipartUpload")
        ):
            action = "PutObject"
        if event.aws_service != definition.service or action not in definition.actions:
            continue
        if definition.feature is not None and event.feature != definition.feature:
            continue
        metadata = json.loads(event.metadata_json)
        key = (action, str(metadata.get("bucket", "")), event.target)
        if key in latest:
            continue
        latest[key] = (
            PermissionState.DENIED
            if event.error_category is ErrorCategory.PERMISSION
            else PermissionState.ALLOWED
            if event.result is ExecutionResult.SUCCESS
            else PermissionState.UNKNOWN
        )
    if {action for action, _, _ in latest} != set(definition.actions):
        return PermissionState.UNKNOWN
    states = set(latest.values())
    return next(iter(states)) if len(states) == 1 else PermissionState.UNKNOWN


class DashboardService:
    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        activity_logs: ActivityLogService,
        clock: Clock,
        inventory: Ec2InventoryGateway,
        managed: ManagedInstanceGateway,
        secrets: SecretsGateway,
        s3: S3Gateway,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._activity_logs = activity_logs
        self._clock = clock
        self._inventory = inventory
        self._managed = managed
        self._secrets = secrets
        self._s3 = s3

    def check_permissions(self, profile_id: int) -> DashboardPermissions:
        profile = self._profiles.resolve(profile_id)
        events = self._activity_logs.list(ExecutionLogFilter(completed_only=True), 10000)
        scoped = []
        for event in events:
            metadata = json.loads(event.metadata_json)
            if (
                metadata.get("profile_id") == profile_id
                and metadata.get("region") == profile.region
            ):
                scoped.append(event)
        scoped.sort(key=lambda event: event.occurred_at, reverse=True)
        observations: dict[tuple[str, str], tuple[PermissionState, str]] = {}
        try:
            credentials = self._sessions.require_credentials(profile_id)
        except ApplicationError:
            # No cached certainty while authentication is unavailable.
            scoped = []
            authentication_unavailable = True
        else:
            authentication_unavailable = False
            probes: tuple[tuple[str, str, Callable[[], object]], ...] = (
                (
                    "ec2",
                    "DescribeInstances",
                    lambda: self._inventory.list_inventory(credentials, profile.region),
                ),
                (
                    "ssm",
                    "DescribeInstanceInformation",
                    lambda: self._managed.list_managed(credentials, profile.region),
                ),
                (
                    "secretsmanager",
                    "ListSecrets",
                    lambda: self._secrets.list_secrets(credentials, profile.region),
                ),
                ("s3", "ListBuckets", lambda: self._s3.list_buckets(credentials, profile.region)),
            )
            # Recheck the location actually browsed through the S3 application
            # service. Account-wide bucket enumeration is a separate permission.
            location = next(
                (
                    json.loads(event.metadata_json)
                    for event in scoped
                    if event.feature == "s3"
                    and event.action == "list"
                    and json.loads(event.metadata_json).get("bucket")
                    and "prefix" in json.loads(event.metadata_json)
                ),
                None,
            )
            if location is not None:
                bucket, prefix = str(location["bucket"]), str(location.get("prefix", ""))
            if location is not None and len(prefix) < 256 and REDACTED not in bucket + prefix:
                probes += (
                    (
                        "s3",
                        "ListObjectsV2",
                        lambda: self._s3.list_objects(credentials, profile.region, bucket, prefix),
                    ),
                )
            for service, action, probe in probes:
                try:
                    probe()
                except AwsPermissionError:
                    observation = (
                        PermissionState.DENIED,
                        "현재 조회 요청에서 명확한 권한 거부가 확인되었습니다.",
                    )
                except ApplicationError:
                    observation = (
                        PermissionState.UNKNOWN,
                        "조회 실패로 판정할 수 없습니다. 인증·네트워크·리소스 상태를 확인하세요.",
                    )
                else:
                    observation = (
                        PermissionState.ALLOWED,
                        "현재 프로필·리전의 목록 조회가 성공했습니다.",
                    )
                observations[(service, action)] = observation
                if service == "s3" and action == "ListObjectsV2":
                    observations[(service, action)] = (
                        observation[0],
                        f"S3 탭의 s3://{bucket}/{prefix} 조회 기준. " + observation[1],
                    )
        features = []
        for feature, definitions in PERMISSIONS.items():
            rows = []
            for definition in definitions:
                state = _historical_state(definition, scoped)
                explanation = (
                    "인증 상태를 확인한 뒤 다시 시도하세요."
                    if authentication_unavailable
                    else "동일 프로필·리전의 저장된 실제 작업 결과입니다. 대상별 권한은 다릅니다."
                    if state is not PermissionState.UNKNOWN
                    else "근거가 없거나 결과가 다릅니다. 변경·접속을 실행하여 확인하지 않습니다."
                )
                if len(definition.actions) == 1:
                    state, explanation = observations.get(
                        (definition.service, definition.actions[0]), (state, explanation)
                    )
                rows.append(
                    PermissionObservation(definition.key, definition.label, state, explanation)
                )
            features.append(
                FeatureCapability(
                    feature, capability_state([row.state for row in rows]), tuple(rows)
                )
            )
        return DashboardPermissions(profile_id, profile.region, self._clock.now(), tuple(features))
