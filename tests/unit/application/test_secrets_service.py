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


def test_successful_lookup_auto_registers_and_saved_crud_is_profile_scoped() -> None:
    service, gateway = build_service('{"host":"db.internal"}')
    store = Mock()
    service._saved = store
    store.get_saved_secret_by_identifier.return_value = None
    created = SavedSecret(3, 7, "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:test")
    store.create_saved_secret.return_value = created
    store.list_saved_secrets.return_value = [created]
    store.get_saved_secret.return_value = created
    store.update_saved_secret.return_value = SavedSecret(3, 7, "db/prod")

    service.get("db/dev", "dev")
    assert service.list_saved("dev") == [created]
    assert service.update_saved(3, "db/prod", "dev").identifier == "db/prod"
    service.delete_saved(3, "dev")

    gateway.get_secret_value.assert_called_once()
    store.create_saved_secret.assert_called_once()
    store.delete_saved_secret.assert_called_once_with(3)


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
