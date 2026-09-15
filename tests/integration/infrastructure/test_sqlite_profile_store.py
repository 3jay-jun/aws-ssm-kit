import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from aws_connect.application.rds_tunnel_service import TunnelSessionService
from aws_connect.domain.aws_profile import AwsProfile, SessionCredentials
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_location import S3Location
from aws_connect.domain.saved_secret import SavedSecret, SecretLookupMode
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession
from aws_connect.infrastructure.sqlite_profile_store import MIGRATIONS, SqliteProfileStore


def profile(name: str) -> AwsProfile:
    return AwsProfile(
        None,
        name,
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"encrypted-a",
        b"encrypted-b",
    )


def test_migrations_crud_default_and_cascade(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    first = store.create(profile("first"))
    second = store.create(profile("second"))
    assert first.is_default
    assert not second.is_default

    selected = store.set_default(second.id or 0)
    assert selected.is_default
    assert store.get_default() == selected

    now = datetime.now(UTC)
    store.put_session(
        SessionCredentials(second.id or 0, b"a", b"b", b"c", now + timedelta(hours=1), now)
    )
    store.delete(second.id or 0)
    assert store.get_session(second.id or 0) is None
    assert store.get_default() is not None


def test_duplicate_profile_name_is_typed(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    store.create(profile("same"))

    with pytest.raises(ConfigurationError) as caught:
        store.create(profile("same"))

    assert caught.value.message_code == "profile.name.duplicate"


def test_tunnel_migration_crud_uniqueness_and_profile_cascade(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    profile_id = owner.require_id()
    unsaved = TunnelSession(
        None,
        profile_id,
        "dev-db",
        "db.example.internal",
        3306,
        13306,
        TargetMode.FIXED,
        "i-0123456789abcdef0",
    )
    created = store.create_tunnel(unsaved)
    assert store.get_tunnel_by_name(profile_id, "dev-db") == created

    with pytest.raises(ConfigurationError) as caught:
        store.create_tunnel(unsaved)
    assert caught.value.message_code == "rds.session.name.duplicate"

    changed = TunnelSession(
        created.id,
        profile_id,
        "dev-db-2",
        created.host,
        5432,
        15432,
        TargetMode.SELECT,
    )
    assert store.update_tunnel(changed).remote_port == 5432
    store.touch_tunnel(created.require_id(), datetime.now(UTC))
    assert store.get_tunnel(created.require_id()).last_used_at is not None
    store.delete(profile_id)
    assert store.list_tunnels(profile_id) == []


def test_tunnel_clone_uses_new_sqlite_identity_and_drops_usage_history(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    original = store.create_tunnel(
        TunnelSession(
            None,
            owner.require_id(),
            "dev-db",
            "db.example.internal",
            3306,
            13306,
            TargetMode.FIXED,
            "i-0123456789abcdef0",
        )
    )
    store.touch_tunnel(original.require_id(), datetime.now(UTC))
    profiles = Mock()
    profiles.resolve.return_value = owner
    service = TunnelSessionService(profiles, store)

    cloned = service.clone(original.require_id(), "dev-db-copy", owner.require_id())
    reloaded_original = store.get_tunnel(original.require_id())

    assert cloned.id != original.id
    assert cloned.name == "dev-db-copy"
    assert cloned.host == original.host
    assert cloned.remote_port == original.remote_port
    assert cloned.local_port == original.local_port
    assert cloned.target_mode is original.target_mode
    assert cloned.target_instance_id == original.target_instance_id
    assert cloned.created_at is not None and cloned.updated_at is not None
    assert cloned.last_used_at is None
    assert reloaded_original is not None and reloaded_original.last_used_at is not None

    with pytest.raises(ConfigurationError) as caught:
        service.clone(original.require_id(), "dev-db-copy", owner.require_id())
    assert caught.value.message_code == "rds.session.name.duplicate"


def test_versioned_settings_migration_persists_and_deletes_values(tmp_path) -> None:
    database = tmp_path / "state.db"
    store = SqliteProfileStore(database)

    assert store.current_schema_version() == store.expected_schema_version == 8
    assert store.get_setting("log.level") is None
    store.put_setting("log.level", "WARNING")
    store.put_setting("log.level", "ERROR")

    reopened = SqliteProfileStore(database)
    assert reopened.get_setting("log.level") == "ERROR"
    reopened.delete_setting("log.level")
    assert reopened.get_setting("log.level") is None


def test_profile_mfa_usage_is_persisted_and_existing_default_is_enabled(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    enabled = store.create(profile("enabled"))
    disabled_profile = profile("disabled")
    disabled = store.create(
        AwsProfile(
            id=disabled_profile.id,
            name=disabled_profile.name,
            region=disabled_profile.region,
            account_id=disabled_profile.account_id,
            user_id=disabled_profile.user_id,
            mfa_arn=disabled_profile.mfa_arn,
            encrypted_access_key=disabled_profile.encrypted_access_key,
            encrypted_secret_key=disabled_profile.encrypted_secret_key,
            mfa_enabled=False,
        )
    )

    assert enabled.mfa_enabled
    assert not disabled.mfa_enabled


def test_mfa_usage_migration_enables_existing_profiles(tmp_path) -> None:
    database = tmp_path / "legacy-state.db"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executescript(MIGRATIONS[0][1])
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (now,)
        )
        connection.execute(
            """INSERT INTO aws_profiles
            (name, region, account_id, user_id, mfa_arn, encrypted_access_key,
             encrypted_secret_key, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "existing",
                "ap-northeast-2",
                "123456789012",
                "developer",
                "arn:aws:iam::123456789012:mfa/developer",
                b"encrypted-a",
                b"encrypted-b",
                1,
                now,
                now,
            ),
        )

    migrated = SqliteProfileStore(database).get_by_name("existing")

    assert migrated is not None and migrated.mfa_enabled


def test_s3_location_migration_crud_uniqueness_and_profile_cascade(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    profile_id = owner.require_id()
    original = S3Location(None, profile_id, "reports", "test-upload-bucket", "reports/")
    created = store.create_s3_location(original)
    assert store.get_s3_location_by_name(profile_id, "reports") == created

    with pytest.raises(ConfigurationError, match="s3.location.name.duplicate"):
        store.create_s3_location(original)

    updated = S3Location(created.id, profile_id, "uploads", "test-upload-bucket", "incoming/")
    assert store.update_s3_location(updated).name == "uploads"
    store.delete(profile_id)
    assert store.list_s3_locations(profile_id) == []


def test_ec2_favorite_migration_is_region_scoped_idempotent_and_cascades(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    profile_id = owner.require_id()

    store.set_ec2_favorite(profile_id, "ap-northeast-2", "i-favorite", True)
    store.set_ec2_favorite(profile_id, "ap-northeast-2", "i-favorite", True)
    store.set_ec2_favorite(profile_id, "us-east-1", "i-other-region", True)

    assert store.list_ec2_favorites(profile_id, "ap-northeast-2") == {"i-favorite"}
    store.set_ec2_favorite(profile_id, "ap-northeast-2", "i-favorite", False)
    assert store.list_ec2_favorites(profile_id, "ap-northeast-2") == set()
    store.delete(profile_id)
    assert store.list_ec2_favorites(profile_id, "us-east-1") == set()


def test_saved_secret_v7_migration_applies_snapshot_defaults(tmp_path) -> None:
    database = tmp_path / "legacy-v7.db"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version, sql in MIGRATIONS:
            if version > 7:
                break
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, now),
            )
        profile_id = connection.execute(
            """INSERT INTO aws_profiles
            (name, region, account_id, user_id, mfa_arn, encrypted_access_key,
             encrypted_secret_key, is_default, created_at, updated_at, mfa_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy",
                "ap-northeast-2",
                "123456789012",
                "developer",
                "arn:aws:iam::123456789012:mfa/developer",
                b"encrypted-a",
                b"encrypted-b",
                1,
                now,
                now,
                1,
            ),
        ).lastrowid
        connection.execute(
            """INSERT INTO saved_secrets
            (profile_id, identifier, created_at, updated_at) VALUES (?, ?, ?, ?)""",
            (profile_id, "db/legacy", now, now),
        )

    store = SqliteProfileStore(database)
    migrated = store.get_saved_secret_by_identifier(int(profile_id or 0), "db/legacy")

    assert store.current_schema_version() == store.expected_schema_version == 8
    assert migrated is not None
    assert migrated.value == ""
    assert migrated.lookup_mode is SecretLookupMode.DIRECT
    assert migrated.relay_instance_id is None


def test_saved_secret_snapshot_crud_upsert_profile_scope_and_cascade(tmp_path) -> None:
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    other_owner = store.create(profile("other"))
    profile_id = owner.require_id()
    other_profile_id = other_owner.require_id()
    created = store.create_saved_secret(
        SavedSecret(
            None,
            profile_id,
            "db/dev",
            value="initial-value",
            lookup_mode=SecretLookupMode.VIA_EC2,
            relay_instance_id="i-relay",
        )
    )

    assert store.get_saved_secret_by_identifier(profile_id, "db/dev") == created
    with pytest.raises(ConfigurationError, match="secret.saved.identifier.duplicate"):
        store.create_saved_secret(SavedSecret(None, profile_id, "db/dev"))

    updated = SavedSecret(
        created.id,
        profile_id,
        "db/prod",
        value="locally-edited-value",
        lookup_mode=SecretLookupMode.DIRECT,
    )
    assert store.update_saved_secret(updated) == updated
    other = store.create_saved_secret(
        SavedSecret(None, other_profile_id, "db/prod", value="other-profile-value")
    )
    with sqlite3.connect(store.path) as connection:
        before = connection.execute(
            "SELECT id, created_at FROM saved_secrets WHERE id=?", (created.require_id(),)
        ).fetchone()

    refreshed = store.upsert_saved_secret(
        SavedSecret(
            None,
            profile_id,
            "db/prod",
            value="refreshed-value",
            lookup_mode=SecretLookupMode.VIA_EC2,
            relay_instance_id="i-new-relay",
        )
    )
    with sqlite3.connect(store.path) as connection:
        after = connection.execute(
            "SELECT id, created_at FROM saved_secrets WHERE id=?", (created.require_id(),)
        ).fetchone()

    assert before == after
    assert refreshed.id == created.id
    assert refreshed.value == "refreshed-value"
    assert refreshed.lookup_mode is SecretLookupMode.VIA_EC2
    assert refreshed.relay_instance_id == "i-new-relay"
    assert store.get_saved_secret_by_identifier(other_profile_id, "db/prod") == other

    store.delete(profile_id)
    assert store.list_saved_secrets(profile_id) == []
    assert store.list_saved_secrets(other_profile_id) == [other]


def test_corrupt_cached_session_is_atomically_deleted_instead_of_leaking_value_error(
    tmp_path,
) -> None:
    database = tmp_path / "state.db"
    store = SqliteProfileStore(database)
    owner = store.create(profile("owner"))
    now = datetime.now(UTC)
    store.put_session(SessionCredentials(owner.require_id(), b"a", b"b", b"c", now, now))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE session_credentials SET expires_at_utc=? WHERE profile_id=?",
            ("not-a-timestamp", owner.require_id()),
        )

    assert store.get_session(owner.require_id()) is None
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM session_credentials WHERE profile_id=?",
                (owner.require_id(),),
            ).fetchone()[0]
            == 0
        )


def test_database_directory_file_and_existing_sidecars_are_restricted(tmp_path: Path) -> None:
    database = tmp_path / "private" / "state.db"
    SqliteProfileStore(database)
    sidecars = [Path(f"{database}{suffix}") for suffix in ("-journal", "-wal", "-shm")]
    for sidecar in sidecars:
        sidecar.touch()
    file_access = Mock()

    SqliteProfileStore(database, file_access)

    expected = [call(database.parent), call(database), *(call(path) for path in sidecars)]
    assert file_access.restrict.call_args_list[:5] == expected
    assert file_access.restrict.call_args_list.count(call(database)) == 2
