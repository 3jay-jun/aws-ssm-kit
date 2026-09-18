import json
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import (
    CancellationToken,
    OperationCancelled,
    OperationContext,
)
from aws_connect.application.ports import (
    Ec2Metadata,
    ListedSecret,
    ManagedInstance,
    RetrievedSecret,
)
from aws_connect.application.secrets_service import (
    SecretKind,
    SecretLookupMode,
    SecretsService,
    build_persistent_remote_secret_command,
)
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials
from aws_connect.domain.errors import (
    AwsPermissionError,
    ConfigurationError,
    CredentialValidationError,
)
from aws_connect.domain.saved_secret import SavedSecret
from aws_connect.domain.sensitive_data import REDACTED


def build_service(secret_string: str) -> tuple[SecretsService, Mock]:
    profile = AwsProfile(
        7,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"access",
        b"secret",
    )
    profiles = Mock()
    profiles.resolve.return_value = profile
    sessions = Mock()
    sessions.require_credentials.return_value = PlainCredentials(
        "ACCESSKEYTEST0001", "local-test-secret", "local-test-token"
    )
    gateway = Mock()
    gateway.get_secret_value.return_value = RetrievedSecret(
        "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:test",
        secret_string,
        "version-1",
        ("AWSCURRENT",),
    )
    gateway.list_secrets.return_value = [ListedSecret("db/dev", "arn:test")]
    return SecretsService(profiles, sessions, gateway), gateway


def test_persistent_remote_command_runs_fixed_lookup_then_keeps_platform_shell() -> None:
    linux = build_persistent_remote_secret_command("db/dev", "Linux")
    windows = build_persistent_remote_secret_command("db/dev", "Windows")

    assert linux.startswith("aws secretsmanager get-secret-value --secret-id 'db/dev'")
    assert linux.endswith('exec "${SHELL:-/bin/sh}" -l')
    assert windows.startswith('powershell.exe -NoLogo -NoExit -Command "aws secretsmanager')
    assert "--query SecretString --output text" in windows

    with pytest.raises(ConfigurationError, match="secret.relay.platform.unsupported"):
        build_persistent_remote_secret_command("db/dev", "Plan9")


def test_direct_lookup_upserts_raw_value_and_direct_context() -> None:
    raw_value = '{"host":"db.internal"}'
    service, gateway = build_service(raw_value)
    store = Mock()
    service._saved = store

    result = service.get("db/dev", "dev")

    gateway.get_secret_value.assert_called_once()
    store.upsert_saved_secret.assert_called_once_with(
        SavedSecret(
            None,
            7,
            "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:test",
            value=raw_value,
            lookup_mode=SecretLookupMode.DIRECT,
            last_retrieved_at=result.retrieved_at,
        )
    )
    store.create_saved_secret.assert_not_called()


def test_lookup_snapshot_refresh_uses_upsert_for_existing_identifier() -> None:
    service, _gateway = build_service("new-local-snapshot")
    store = Mock()
    service._saved = store
    refreshed = SavedSecret(3, 7, "db/dev", value="new-local-snapshot")
    store.upsert_saved_secret.return_value = refreshed

    result = service.remember(
        "db/dev",
        "dev",
        value="new-local-snapshot",
        lookup_mode=SecretLookupMode.DIRECT,
    )

    assert result is refreshed
    store.upsert_saved_secret.assert_called_once_with(
        SavedSecret(
            None,
            7,
            "db/dev",
            value="new-local-snapshot",
            lookup_mode=SecretLookupMode.DIRECT,
        )
    )
    store.get_saved_secret_by_identifier.assert_not_called()
    store.create_saved_secret.assert_not_called()


def test_saved_crud_is_profile_scoped_and_local_update_never_writes_aws() -> None:
    service, gateway = build_service("unused")
    store = Mock()
    service._saved = store
    existing = SavedSecret(
        3,
        7,
        "db/dev",
        value="old-local-value",
        lookup_mode=SecretLookupMode.VIA_EC2,
        relay_instance_id="i-relay",
    )
    updated = SavedSecret(
        3,
        7,
        "db/prod",
        value="new-local-value",
        lookup_mode=SecretLookupMode.VIA_EC2,
        relay_instance_id="i-relay",
    )
    store.list_saved_secrets.return_value = [existing]
    store.get_saved_secret.return_value = existing
    store.update_saved_secret.return_value = updated

    assert service.list_saved("dev") == [existing]
    assert service.update_saved(3, "db/prod", "dev", value="new-local-value") == updated
    service.delete_saved(3, "dev")

    store.update_saved_secret.assert_called_once_with(updated)
    store.delete_saved_secret.assert_called_once_with(3)
    gateway.put_secret_value.assert_not_called()
    gateway.get_secret_value.assert_not_called()


def test_load_saved_uses_local_snapshot_only_and_enforces_profile_scope() -> None:
    service, gateway = build_service("aws-value-must-not-be-read")
    store = Mock()
    service._saved = store
    existing = SavedSecret(
        3,
        7,
        "db/dev",
        value='{ "password": "local-only" }',  # pragma: allowlist secret
        lookup_mode=SecretLookupMode.VIA_EC2,
        relay_instance_id="i-relay",
    )
    store.get_saved_secret.return_value = existing

    saved, result = service.load_saved(3, "dev")

    assert saved is existing
    assert result.secret_id == "db/dev"
    assert result.field("password").reveal() == "local-only"
    gateway.get_secret_value.assert_not_called()
    gateway.put_secret_value.assert_not_called()
    service._sessions.require_credentials.assert_not_called()

    store.get_saved_secret.return_value = SavedSecret(3, 8, "db/dev")
    with pytest.raises(ConfigurationError, match="secret.saved.not_found"):
        service.load_saved(3, "dev")


def test_json_is_flattened_and_sensitive_names_are_masked_recursively() -> None:
    raw_password = "phase-seven-private-value"  # pragma: allowlist secret
    payload = {
        "host": "db.internal",
        "port": 3306,
        "credentials": {"password": raw_password},  # pragma: allowlist secret
        "items": [{"apiKey": "nested-private"}],  # pragma: allowlist secret
    }
    service, gateway = build_service(json.dumps(payload))

    result = service.get("db/dev", "dev")

    assert result.kind is SecretKind.JSON
    assert result.field("host").masked_value == "db.internal"
    assert result.field("credentials.password").masked_value == REDACTED
    assert result.field("credentials.password").reveal() == raw_password
    assert result.field("items[0].apiKey").masked_value == REDACTED
    assert raw_password not in repr(result)
    assert result.rds_endpoint() == ("db.internal", 3306)
    gateway.get_secret_value.assert_called_once()


def test_plain_text_and_json_scalar_are_fully_masked_by_default() -> None:
    text, _ = build_service("plain-private-value")
    scalar, _ = build_service('"json-private-value"')

    text_result = text.get("plain")
    scalar_result = scalar.get("scalar")

    assert text_result.kind is SecretKind.TEXT
    assert text_result.field("$").masked_value == REDACTED
    assert scalar_result.kind is SecretKind.JSON
    assert scalar_result.field("$").masked_value == REDACTED


def test_direct_get_never_depends_on_optional_list_operation() -> None:
    service, gateway = build_service('{"host":"db.internal","port":"5432"}')
    gateway.list_secrets.side_effect = AssertionError("ListSecrets must not be called")

    result = service.get("arn:test")

    assert result.rds_endpoint() == ("db.internal", 5432)
    gateway.list_secrets.assert_not_called()


def test_optional_list_returns_only_metadata() -> None:
    service, gateway = build_service("unused")

    assert service.list("dev") == [ListedSecret("db/dev", "arn:test")]
    gateway.list_secrets.assert_called_once()


def test_explicit_top_level_field_save_writes_one_new_aws_version() -> None:
    service, gateway = build_service('{"host":"db.internal","port":3306}')
    gateway.put_secret_value.return_value = "version-2"
    result = service.get("db/dev", "dev")

    updated = service.save_top_level_field(result, "port", "5432", "dev")

    secret_string = gateway.put_secret_value.call_args.args[3]
    assert json.loads(secret_string) == {"host": "db.internal", "port": 5432}
    assert updated.field("port").reveal() == 5432
    assert updated.version_id == "version-2"


def test_field_save_rejects_nested_or_non_json_updates_before_aws_call() -> None:
    service, gateway = build_service('{"database":{"password":"old"}}')
    result = service.get("db/dev", "dev")

    with pytest.raises(ConfigurationError, match="secret.field.update.unsupported"):
        service.save_top_level_field(result, "database.password", "new", "dev")

    gateway.put_secret_value.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        '{"host":"","port":3306}',
        '{"host":"db.internal","port":0}',
        '{"host":"db.internal","port":true}',
        '{"nested":{"host":"db.internal","port":3306}}',
    ],
)
def test_rds_copy_accepts_only_valid_top_level_host_and_port(raw: str) -> None:
    service, _ = build_service(raw)

    with pytest.raises(ConfigurationError, match="secret.rds_endpoint.invalid"):
        service.get("db").rds_endpoint()


def test_missing_identifier_and_session_fail_before_aws_call() -> None:
    service, gateway = build_service("unused")
    with pytest.raises(ConfigurationError, match="secret.id.required"):
        service.get("  ")
    service._sessions.require_credentials.side_effect = CredentialValidationError(
        "auth.mfa_required", "test"
    )
    with pytest.raises(CredentialValidationError, match="auth.mfa_required"):
        service.get("db")
    gateway.get_secret_value.assert_not_called()


def test_absent_field_error_does_not_include_secret_value() -> None:
    service, _ = build_service('{"password":"private-value"}')
    result = service.get("db")

    with pytest.raises(ConfigurationError) as caught:
        result.field("missing")

    assert "private-value" not in caught.value.technical_cause


def test_via_ec2_requires_consent_valid_id_and_selected_online_instance() -> None:
    service, direct = build_service('{"host":"db.internal","port":3306}')
    store = Mock()
    service._saved = store
    managed = Mock()
    managed.list_online.return_value = [
        ManagedInstance("i-online", "Online", "10.0.0.1", "Amazon Linux")
    ]
    remote = Mock()
    remote.get_secret_value.return_value = RetrievedSecret(
        "db/dev", '{"host":"db.internal","port":3306}'
    )
    service._managed_instances = managed
    service._remote_gateway = remote

    with pytest.raises(ConfigurationError, match="secret.relay.confirmation.required"):
        service.get("db/dev", "dev", mode=SecretLookupMode.VIA_EC2, instance_id="i-online")
    with pytest.raises(ConfigurationError, match="secret.id.remote.invalid"):
        service.get(
            "db/dev'; whoami",
            "dev",
            mode=SecretLookupMode.VIA_EC2,
            instance_id="i-online",
            confirmed=True,
        )
    with pytest.raises(ConfigurationError, match="secret.relay.instance.not_online"):
        service.get(
            "db/dev",
            "dev",
            mode=SecretLookupMode.VIA_EC2,
            instance_id="i-offline",
            confirmed=True,
        )

    result = service.get(
        "db/dev",
        "dev",
        mode=SecretLookupMode.VIA_EC2,
        instance_id="i-online",
        confirmed=True,
    )

    assert result.rds_endpoint() == ("db.internal", 3306)
    direct.get_secret_value.assert_not_called()
    assert remote.get_secret_value.call_args.args[2:5] == (
        "i-online",
        "Amazon Linux",
        "db/dev",
    )
    store.upsert_saved_secret.assert_called_once_with(
        SavedSecret(
            None,
            7,
            "db/dev",
            value='{"host":"db.internal","port":3306}',
            lookup_mode=SecretLookupMode.VIA_EC2,
            relay_instance_id="i-online",
            last_retrieved_at=result.retrieved_at,
        )
    )


def test_direct_permission_failure_never_automatically_falls_back_to_ec2() -> None:
    service, direct = build_service("unused")
    direct.get_secret_value.side_effect = AwsPermissionError(
        "aws.permission.denied",
        "GetSecretValue denied",
        aws_service="secretsmanager",
        aws_action="GetSecretValue",
    )
    remote = Mock()
    service._remote_gateway = remote

    with pytest.raises(AwsPermissionError):
        service.get("db/dev", "dev")

    remote.get_secret_value.assert_not_called()


def test_relay_targets_are_online_platform_bearing_nodes_only() -> None:
    service, _direct = build_service("unused")
    managed = Mock()
    managed.list_online.return_value = [
        ManagedInstance("i-linux", "Online", None, "Ubuntu"),
        ManagedInstance("i-no-platform", "Online", None, None),
        ManagedInstance("i-stale", "ConnectionLost", None, "Linux"),
    ]
    service._managed_instances = managed
    metadata = Mock()
    metadata.describe.return_value = {"i-linux": Ec2Metadata("i-linux", "web-dev", "10.0.0.10")}
    service._metadata = metadata

    targets = service.list_relay_targets("dev")

    assert [(item.instance_id, item.platform_name, item.name) for item in targets] == [
        ("i-linux", "Ubuntu", "web-dev")
    ]
    metadata.describe.assert_called_once()


def test_via_ec2_honors_cancellation_before_remote_command() -> None:
    service, _direct = build_service("unused")
    managed = Mock()
    managed.list_online.return_value = [
        ManagedInstance("i-online", "Online", None, "Windows Server")
    ]
    remote = Mock()
    service._managed_instances = managed
    service._remote_gateway = remote
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(OperationCancelled):
        service.get(
            "db/dev",
            "dev",
            mode=SecretLookupMode.VIA_EC2,
            instance_id="i-online",
            confirmed=True,
            context=OperationContext(cancellation=cancellation),
        )

    remote.get_secret_value.assert_not_called()


def test_json_view_masks_nested_sensitive_keys_and_preserves_structure() -> None:
    import json

    service, _gateway = build_service(
        '{"host":"db.internal","items":[{"token":"private","monkey":"hidden"}],'
        '"password":{"nested":"hidden"},"empty":[]}'
    )
    result = service.get("db/dev", "dev")
    masked = json.loads(result.json_text())
    assert masked["host"] == "db.internal"
    assert masked["items"][0] == {"token": "***REDACTED***", "monkey": "***REDACTED***"}
    assert masked["password"]["nested"] == "***REDACTED***"
    assert masked["empty"] == []
    assert json.loads(result.json_text(reveal=True))["items"][0]["token"] == "private"


def test_relay_connection_test_reuses_online_validation_without_secret_request() -> None:
    service, gateway = build_service("plain")
    managed = Mock()
    managed.list_online.return_value = [ManagedInstance("i-test", "Online", None, "Linux")]
    service._managed_instances = managed
    assert service.test_relay_connection("i-test", "dev").instance_id == "i-test"
    gateway.get_secret_value.assert_not_called()
    managed.list_online.return_value = []
    with pytest.raises(ConfigurationError, match="secret.relay.instance.not_online"):
        service.test_relay_connection("i-test", "dev")


def test_local_edit_preserves_last_retrieved_time_and_manual_creation_has_no_time() -> None:
    from datetime import UTC, datetime

    service, gateway = build_service("plain")
    saved = Mock()
    service._saved = saved
    timestamp = datetime(2026, 9, 17, tzinfo=UTC)
    saved.get_saved_secret.return_value = SavedSecret(1, 7, "db/dev", last_retrieved_at=timestamp)
    service.update_saved(1, "db/dev", "dev", value="edited")
    assert saved.update_saved_secret.call_args.args[0].last_retrieved_at == timestamp
    service.remember("db/manual", "dev", value="manual")
    assert saved.upsert_saved_secret.call_args.args[0].last_retrieved_at is None
    gateway.get_secret_value.assert_not_called()
