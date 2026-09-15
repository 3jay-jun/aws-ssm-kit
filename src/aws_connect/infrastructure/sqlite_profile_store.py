"""Versioned SQLite persistence for profiles and temporary sessions."""

from __future__ import annotations

import builtins
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.aws_profile import AwsProfile, SessionCredentials
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_location import S3Location
from aws_connect.domain.saved_secret import SavedSecret, SecretLookupMode
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE aws_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            region TEXT NOT NULL,
            account_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            mfa_arn TEXT NOT NULL,
            encrypted_access_key BLOB NOT NULL,
            encrypted_secret_key BLOB NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX only_one_default_profile
            ON aws_profiles(is_default) WHERE is_default = 1;
        CREATE TABLE session_credentials (
            profile_id INTEGER PRIMARY KEY,
            encrypted_access_key BLOB NOT NULL,
            encrypted_secret_key BLOB NOT NULL,
            encrypted_session_token BLOB NOT NULL,
            expires_at_utc TEXT NOT NULL,
            verified_at_utc TEXT NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE tunnel_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            remote_port INTEGER NOT NULL CHECK (remote_port BETWEEN 1 AND 65535),
            local_port INTEGER NOT NULL CHECK (local_port BETWEEN 1 AND 65535),
            target_mode TEXT NOT NULL CHECK (target_mode IN ('fixed', 'select')),
            target_instance_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT,
            FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE,
            UNIQUE (profile_id, name),
            CHECK (
                (target_mode = 'fixed' AND target_instance_id IS NOT NULL) OR
                (target_mode = 'select' AND target_instance_id IS NULL)
            )
        );
        CREATE INDEX tunnel_sessions_by_profile
            ON tunnel_sessions(profile_id, name);
        """,
    ),
    (
        3,
        """
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        """
        CREATE TABLE s3_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            bucket TEXT NOT NULL,
            prefix TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE,
            UNIQUE (profile_id, name)
        );
        CREATE INDEX s3_locations_by_profile ON s3_locations(profile_id, name);
        """,
    ),
    (
        5,
        """
        CREATE TABLE ec2_favorites (
            profile_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (profile_id, region, instance_id),
            FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE
        );
        CREATE INDEX ec2_favorites_by_profile_region
            ON ec2_favorites(profile_id, region);
        """,
    ),
    (
        6,
        """
        CREATE TABLE saved_secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            identifier TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE,
            UNIQUE (profile_id, identifier)
        );
        CREATE INDEX saved_secrets_by_profile ON saved_secrets(profile_id, identifier);
        """,
    ),
    (
        7,
        """
        ALTER TABLE aws_profiles
        ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 1
            CHECK (mfa_enabled IN (0, 1));
        """,
    ),
    (
        8,
        """
        ALTER TABLE saved_secrets
        ADD COLUMN value TEXT NOT NULL DEFAULT '';
        ALTER TABLE saved_secrets
        ADD COLUMN lookup_mode TEXT NOT NULL DEFAULT 'direct'
            CHECK (lookup_mode IN ('direct', 'via_ec2'));
        ALTER TABLE saved_secrets
        ADD COLUMN relay_instance_id TEXT;
        """,
    ),
)


class SqliteProfileStore:
    """Short-transaction SQLite adapter with FK enforcement and busy timeout."""

    def __init__(self, path: Path, file_access: PrivateFileAccess | None = None) -> None:
        self._path = path
        self._file_access = file_access
        path.parent.mkdir(parents=True, exist_ok=True)
        if file_access is not None:
            file_access.restrict(path.parent)
            self._restrict_existing_database_files()
        self.migrate()
        if file_access is not None:
            self._restrict_existing_database_files()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def expected_schema_version(self) -> int:
        return max(version for version, _sql in MIGRATIONS)

    def current_schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in MIGRATIONS:
                if version not in applied:
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, _now_text()),
                    )

    def _restrict_existing_database_files(self) -> None:
        if self._file_access is None:
            return
        for candidate in (
            self._path,
            Path(f"{self._path}-journal"),
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            if candidate.exists():
                self._file_access.restrict(candidate)

    def list(self) -> list[AwsProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM aws_profiles ORDER BY is_default DESC, name"
            ).fetchall()
        return [_profile(row) for row in rows]

    def get(self, profile_id: int) -> AwsProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aws_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return _profile(row) if row else None

    def get_by_name(self, name: str) -> AwsProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aws_profiles WHERE name = ?", (name,)
            ).fetchone()
        return _profile(row) if row else None

    def create(self, profile: AwsProfile) -> AwsProfile:
        now = _now_text()
        try:
            with self._connect() as connection:
                make_default = not bool(
                    connection.execute("SELECT 1 FROM aws_profiles LIMIT 1").fetchone()
                )
                cursor = connection.execute(
                    """INSERT INTO aws_profiles
                    (name, region, account_id, user_id, mfa_arn, mfa_enabled,
                     encrypted_access_key, encrypted_secret_key, is_default, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        profile.name,
                        profile.region,
                        profile.account_id,
                        profile.user_id,
                        profile.mfa_arn,
                        int(profile.mfa_enabled),
                        profile.encrypted_access_key,
                        profile.encrypted_secret_key,
                        int(make_default or profile.is_default),
                        now,
                        now,
                    ),
                )
                profile_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as error:
            raise _store_error("profile.name.duplicate", error) from error
        created = self.get(profile_id)
        if created is None:
            raise _store_error("profile.create.failed")
        return created

    def update(self, profile: AwsProfile) -> AwsProfile:
        profile_id = profile.require_id()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE aws_profiles SET name=?, region=?, account_id=?, user_id=?,
                    mfa_arn=?, mfa_enabled=?, encrypted_access_key=?, encrypted_secret_key=?,
                    updated_at=?
                    WHERE id=?""",
                    (
                        profile.name,
                        profile.region,
                        profile.account_id,
                        profile.user_id,
                        profile.mfa_arn,
                        int(profile.mfa_enabled),
                        profile.encrypted_access_key,
                        profile.encrypted_secret_key,
                        _now_text(),
                        profile_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _store_error("profile.not_found")
        except sqlite3.IntegrityError as error:
            raise _store_error("profile.name.duplicate", error) from error
        updated = self.get(profile_id)
        if updated is None:
            raise _store_error("profile.update.failed")
        return updated

    def delete(self, profile_id: int) -> None:
        with self._connect() as connection:
            was_default = connection.execute(
                "SELECT is_default FROM aws_profiles WHERE id=?", (profile_id,)
            ).fetchone()
            if was_default is None:
                raise _store_error("profile.not_found")
            connection.execute("DELETE FROM aws_profiles WHERE id=?", (profile_id,))
            if bool(was_default[0]):
                replacement = connection.execute(
                    "SELECT id FROM aws_profiles ORDER BY name LIMIT 1"
                ).fetchone()
                if replacement:
                    connection.execute(
                        "UPDATE aws_profiles SET is_default=1 WHERE id=?", (replacement[0],)
                    )

    def set_default(self, profile_id: int) -> AwsProfile:
        with self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM aws_profiles WHERE id=?", (profile_id,)
            ).fetchone():
                raise _store_error("profile.not_found")
            connection.execute("UPDATE aws_profiles SET is_default=0 WHERE is_default=1")
            connection.execute(
                "UPDATE aws_profiles SET is_default=1, updated_at=? WHERE id=?",
                (_now_text(), profile_id),
            )
        selected = self.get(profile_id)
        if selected is None:
            raise _store_error("profile.not_found")
        return selected

    def get_default(self) -> AwsProfile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM aws_profiles WHERE is_default=1").fetchone()
        return _profile(row) if row else None

    def get_session(self, profile_id: int) -> SessionCredentials | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_credentials WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if row is None:
                return None
            try:
                expires_at = datetime.fromisoformat(str(row["expires_at_utc"]))
                verified_at = datetime.fromisoformat(str(row["verified_at_utc"]))
                if expires_at.tzinfo is None or verified_at.tzinfo is None:
                    raise ValueError("session timestamps must include a timezone")
                return SessionCredentials(
                    profile_id=int(row["profile_id"]),
                    encrypted_access_key=bytes(row["encrypted_access_key"]),
                    encrypted_secret_key=bytes(row["encrypted_secret_key"]),
                    encrypted_session_token=bytes(row["encrypted_session_token"]),
                    expires_at_utc=expires_at,
                    verified_at_utc=verified_at,
                )
            except (TypeError, ValueError, OverflowError):
                # Treat a malformed cached token as disposable authentication
                # state. Deletion commits atomically with this read transaction.
                connection.execute(
                    "DELETE FROM session_credentials WHERE profile_id=?", (profile_id,)
                )
                return None

    def put_session(self, session: SessionCredentials) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO session_credentials VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                encrypted_access_key=excluded.encrypted_access_key,
                encrypted_secret_key=excluded.encrypted_secret_key,
                encrypted_session_token=excluded.encrypted_session_token,
                expires_at_utc=excluded.expires_at_utc, verified_at_utc=excluded.verified_at_utc""",
                (
                    session.profile_id,
                    session.encrypted_access_key,
                    session.encrypted_secret_key,
                    session.encrypted_session_token,
                    session.expires_at_utc.isoformat(),
                    session.verified_at_utc.isoformat(),
                ),
            )

    def delete_session(self, profile_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM session_credentials WHERE profile_id=?", (profile_id,))

    def get_setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def put_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO app_settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM app_settings WHERE key=?", (key,))

    def list_ec2_favorites(self, profile_id: int, region: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT instance_id FROM ec2_favorites WHERE profile_id=? AND region=?",
                (profile_id, region),
            ).fetchall()
        return {str(row["instance_id"]) for row in rows}

    def set_ec2_favorite(
        self, profile_id: int, region: str, instance_id: str, favorite: bool
    ) -> None:
        try:
            with self._connect() as connection:
                if favorite:
                    connection.execute(
                        """INSERT INTO ec2_favorites
                        (profile_id, region, instance_id, created_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT(profile_id, region, instance_id) DO NOTHING""",
                        (profile_id, region, instance_id, _now_text()),
                    )
                else:
                    connection.execute(
                        """DELETE FROM ec2_favorites
                        WHERE profile_id=? AND region=? AND instance_id=?""",
                        (profile_id, region, instance_id),
                    )
        except sqlite3.IntegrityError as error:
            raise _store_error("ec2.favorite.persistence.invalid", error) from error

    def replace_settings(self, values: Mapping[str, str | None]) -> None:
        """Atomically replace a bounded group of setting overrides."""

        with self._connect() as connection:
            for key, value in values.items():
                if value is None:
                    connection.execute("DELETE FROM app_settings WHERE key=?", (key,))
                else:
                    connection.execute(
                        """INSERT INTO app_settings(key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                        (key, value),
                    )

    def list_tunnels(self, profile_id: int) -> builtins.list[TunnelSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tunnel_sessions WHERE profile_id=? ORDER BY name", (profile_id,)
            ).fetchall()
        return [_tunnel(row) for row in rows]

    def get_tunnel(self, tunnel_id: int) -> TunnelSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tunnel_sessions WHERE id=?", (tunnel_id,)
            ).fetchone()
        return _tunnel(row) if row else None

    def get_tunnel_by_name(self, profile_id: int, name: str) -> TunnelSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tunnel_sessions WHERE profile_id=? AND name=?",
                (profile_id, name),
            ).fetchone()
        return _tunnel(row) if row else None

    def create_tunnel(self, session: TunnelSession) -> TunnelSession:
        now = _now_text()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO tunnel_sessions
                    (profile_id, name, host, remote_port, local_port, target_mode,
                     target_instance_id, created_at, updated_at, last_used_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session.profile_id,
                        session.name,
                        session.host,
                        session.remote_port,
                        session.local_port,
                        session.target_mode.value,
                        session.target_instance_id,
                        now,
                        now,
                        _optional_datetime_text(session.last_used_at),
                    ),
                )
                tunnel_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as error:
            raise _tunnel_store_error(error) from error
        created = self.get_tunnel(tunnel_id)
        if created is None:
            raise _store_error("rds.session.create.failed")
        return created

    def update_tunnel(self, session: TunnelSession) -> TunnelSession:
        tunnel_id = session.require_id()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE tunnel_sessions SET name=?, host=?, remote_port=?, local_port=?,
                    target_mode=?, target_instance_id=?, updated_at=?
                    WHERE id=? AND profile_id=?""",
                    (
                        session.name,
                        session.host,
                        session.remote_port,
                        session.local_port,
                        session.target_mode.value,
                        session.target_instance_id,
                        _now_text(),
                        tunnel_id,
                        session.profile_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _store_error("rds.session.not_found")
        except sqlite3.IntegrityError as error:
            raise _tunnel_store_error(error) from error
        updated = self.get_tunnel(tunnel_id)
        if updated is None:
            raise _store_error("rds.session.update.failed")
        return updated

    def delete_tunnel(self, tunnel_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM tunnel_sessions WHERE id=?", (tunnel_id,))
            if cursor.rowcount != 1:
                raise _store_error("rds.session.not_found")

    def touch_tunnel(self, tunnel_id: int, used_at: datetime) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tunnel_sessions SET last_used_at=?, updated_at=? WHERE id=?",
                (used_at.isoformat(), _now_text(), tunnel_id),
            )
            if cursor.rowcount != 1:
                raise _store_error("rds.session.not_found")

    def list_s3_locations(self, profile_id: int) -> builtins.list[S3Location]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM s3_locations WHERE profile_id=? ORDER BY name", (profile_id,)
            ).fetchall()
        return [_s3_location(row) for row in rows]

    def get_s3_location(self, location_id: int) -> S3Location | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM s3_locations WHERE id=?", (location_id,)
            ).fetchone()
        return _s3_location(row) if row else None

    def get_s3_location_by_name(self, profile_id: int, name: str) -> S3Location | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM s3_locations WHERE profile_id=? AND name=?", (profile_id, name)
            ).fetchone()
        return _s3_location(row) if row else None

    def create_s3_location(self, location: S3Location) -> S3Location:
        now = _now_text()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO s3_locations
                    (profile_id, name, bucket, prefix, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        location.profile_id,
                        location.name,
                        location.bucket,
                        location.prefix,
                        now,
                        now,
                    ),
                )
                location_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as error:
            raise _s3_store_error(error) from error
        created = self.get_s3_location(location_id)
        if created is None:
            raise _store_error("s3.location.create.failed")
        return created

    def update_s3_location(self, location: S3Location) -> S3Location:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE s3_locations SET name=?, bucket=?, prefix=?, updated_at=?
                    WHERE id=? AND profile_id=?""",
                    (
                        location.name,
                        location.bucket,
                        location.prefix,
                        _now_text(),
                        location.require_id(),
                        location.profile_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _store_error("s3.location.not_found")
        except sqlite3.IntegrityError as error:
            raise _s3_store_error(error) from error
        updated = self.get_s3_location(location.require_id())
        if updated is None:
            raise _store_error("s3.location.update.failed")
        return updated

    def delete_s3_location(self, location_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM s3_locations WHERE id=?", (location_id,))
            if cursor.rowcount != 1:
                raise _store_error("s3.location.not_found")

    def list_saved_secrets(self, profile_id: int) -> builtins.list[SavedSecret]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM saved_secrets WHERE profile_id=? ORDER BY identifier",
                (profile_id,),
            ).fetchall()
        return [_saved_secret(row) for row in rows]

    def get_saved_secret(self, saved_secret_id: int) -> SavedSecret | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM saved_secrets WHERE id=?", (saved_secret_id,)
            ).fetchone()
        return _saved_secret(row) if row else None

    def get_saved_secret_by_identifier(
        self, profile_id: int, identifier: str
    ) -> SavedSecret | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM saved_secrets WHERE profile_id=? AND identifier=?",
                (profile_id, identifier),
            ).fetchone()
        return _saved_secret(row) if row else None

    def create_saved_secret(self, saved_secret: SavedSecret) -> SavedSecret:
        now = _now_text()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO saved_secrets
                    (profile_id, identifier, value, lookup_mode, relay_instance_id,
                     created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        saved_secret.profile_id,
                        saved_secret.identifier,
                        saved_secret.value,
                        saved_secret.lookup_mode.value,
                        saved_secret.relay_instance_id,
                        now,
                        now,
                    ),
                )
                saved_secret_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as error:
            raise _saved_secret_store_error(error) from error
        created = self.get_saved_secret(saved_secret_id)
        if created is None:
            raise _store_error("secret.saved.create.failed")
        return created

    def upsert_saved_secret(self, saved_secret: SavedSecret) -> SavedSecret:
        """Insert or refresh a lookup snapshot while preserving an existing row ID."""

        now = _now_text()
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO saved_secrets
                    (profile_id, identifier, value, lookup_mode, relay_instance_id,
                     created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id, identifier) DO UPDATE SET
                        value=excluded.value,
                        lookup_mode=excluded.lookup_mode,
                        relay_instance_id=excluded.relay_instance_id,
                        updated_at=excluded.updated_at""",
                    (
                        saved_secret.profile_id,
                        saved_secret.identifier,
                        saved_secret.value,
                        saved_secret.lookup_mode.value,
                        saved_secret.relay_instance_id,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    """SELECT * FROM saved_secrets
                    WHERE profile_id=? AND identifier=?""",
                    (saved_secret.profile_id, saved_secret.identifier),
                ).fetchone()
                if row is None:
                    raise _store_error("secret.saved.upsert.failed")
                persisted = _saved_secret(row)
        except sqlite3.IntegrityError as error:
            raise _saved_secret_store_error(error) from error
        return persisted

    def update_saved_secret(self, saved_secret: SavedSecret) -> SavedSecret:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE saved_secrets SET identifier=?, value=?, lookup_mode=?,
                    relay_instance_id=?, updated_at=?
                    WHERE id=? AND profile_id=?""",
                    (
                        saved_secret.identifier,
                        saved_secret.value,
                        saved_secret.lookup_mode.value,
                        saved_secret.relay_instance_id,
                        _now_text(),
                        saved_secret.require_id(),
                        saved_secret.profile_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _store_error("secret.saved.not_found")
        except sqlite3.IntegrityError as error:
            raise _saved_secret_store_error(error) from error
        updated = self.get_saved_secret(saved_secret.require_id())
        if updated is None:
            raise _store_error("secret.saved.update.failed")
        return updated

    def delete_saved_secret(self, saved_secret_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM saved_secrets WHERE id=?", (saved_secret_id,))
            if cursor.rowcount != 1:
                raise _store_error("secret.saved.not_found")


def _profile(row: sqlite3.Row) -> AwsProfile:
    return AwsProfile(
        id=int(row["id"]),
        name=str(row["name"]),
        region=str(row["region"]),
        account_id=str(row["account_id"]),
        user_id=str(row["user_id"]),
        mfa_arn=str(row["mfa_arn"]),
        encrypted_access_key=bytes(row["encrypted_access_key"]),
        encrypted_secret_key=bytes(row["encrypted_secret_key"]),
        mfa_enabled=bool(row["mfa_enabled"]),
        is_default=bool(row["is_default"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _tunnel(row: sqlite3.Row) -> TunnelSession:
    return TunnelSession(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        name=str(row["name"]),
        host=str(row["host"]),
        remote_port=int(row["remote_port"]),
        local_port=int(row["local_port"]),
        target_mode=TargetMode(str(row["target_mode"])),
        target_instance_id=(str(row["target_instance_id"]) if row["target_instance_id"] else None),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        last_used_at=(
            datetime.fromisoformat(str(row["last_used_at"])) if row["last_used_at"] else None
        ),
    )


def _s3_location(row: sqlite3.Row) -> S3Location:
    return S3Location(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        name=str(row["name"]),
        bucket=str(row["bucket"]),
        prefix=str(row["prefix"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _saved_secret(row: sqlite3.Row) -> SavedSecret:
    return SavedSecret(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        identifier=str(row["identifier"]),
        value=str(row["value"]),
        lookup_mode=SecretLookupMode(str(row["lookup_mode"])),
        relay_instance_id=(
            str(row["relay_instance_id"]) if row["relay_instance_id"] is not None else None
        ),
    )


def _now_text() -> str:
    return datetime.now().astimezone().isoformat()


def _optional_datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _store_error(code: str, cause: Exception | None = None) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=str(cause or code))


def _tunnel_store_error(error: sqlite3.IntegrityError) -> ConfigurationError:
    cause = str(error)
    code = (
        "rds.session.name.duplicate"
        if "tunnel_sessions.profile_id, tunnel_sessions.name" in cause
        else "rds.session.persistence.invalid"
    )
    return _store_error(code, error)


def _s3_store_error(error: sqlite3.IntegrityError) -> ConfigurationError:
    cause = str(error)
    code = (
        "s3.location.name.duplicate"
        if "s3_locations.profile_id, s3_locations.name" in cause
        else "s3.location.persistence.invalid"
    )
    return _store_error(code, error)


def _saved_secret_store_error(error: sqlite3.IntegrityError) -> ConfigurationError:
    cause = str(error)
    code = (
        "secret.saved.identifier.duplicate"
        if "saved_secrets.profile_id, saved_secrets.identifier" in cause
        else "secret.saved.persistence.invalid"
    )
    return _store_error(code, error)
