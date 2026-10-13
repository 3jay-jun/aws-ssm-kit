"""Atomic export to the per-user AWS shared credentials file."""

from __future__ import annotations

import configparser
import io
import os
import tempfile
from pathlib import Path
from threading import Lock

from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ConfigurationError

_WRITE_LOCK = Lock()
_KEYS = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")


class AwsSharedCredentialsWriter:
    """Update one AWS Connect profile and ``default`` without exposing values."""

    def __init__(
        self,
        path: Path | None = None,
        file_access: PrivateFileAccess | None = None,
    ) -> None:
        self._path = (path or Path.home() / ".aws" / "credentials").expanduser().resolve()
        self._file_access = file_access

    def write(self, profile_id: int, credentials: PlainCredentials) -> None:
        if profile_id <= 0:
            raise ConfigurationError(
                "credentials.shared_file.profile.invalid",
                "Shared credentials profile ID must be positive",
            )
        if credentials.session_token is None:
            raise ConfigurationError(
                "credentials.shared_file.session_token.required",
                "Temporary credentials require a session token",
            )

        with _WRITE_LOCK:
            self._write_locked(profile_id, credentials, credentials.session_token)

    def _write_locked(
        self,
        profile_id: int,
        credentials: PlainCredentials,
        session_token: str,
    ) -> None:
        temporary: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            parser = self._read_existing()
            values = dict(
                zip(
                    _KEYS,
                    (credentials.access_key, credentials.secret_key, session_token),
                    strict=True,
                )
            )
            self._replace_section(parser, f"aws-connect-{profile_id}", values)
            self._replace_section(parser, "default", values)

            content = io.StringIO()
            parser.write(content, space_around_delimiters=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".credentials.", suffix=".tmp", dir=self._path.parent
            )
            os.close(descriptor)
            temporary = Path(raw_path)
            temporary.write_text(content.getvalue(), encoding="utf-8", newline="\n")
            if self._file_access is not None:
                self._file_access.restrict(temporary)
            os.replace(temporary, self._path)
            temporary = None
            if self._file_access is not None:
                self._file_access.restrict(self._path)
        except (OSError, UnicodeError, configparser.Error) as error:
            raise ConfigurationError(
                "credentials.shared_file.write_failed",
                type(error).__name__,
            ) from error
        except ConfigurationError as error:
            raise ConfigurationError(
                "credentials.shared_file.write_failed",
                error.message_code,
            ) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _read_existing(self) -> configparser.RawConfigParser:
        parser = configparser.RawConfigParser(interpolation=None)
        if self._path.exists():
            with self._path.open("r", encoding="utf-8") as source:
                parser.read_file(source)
        return parser

    @staticmethod
    def _replace_section(
        parser: configparser.RawConfigParser,
        name: str,
        values: dict[str, str],
    ) -> None:
        if parser.has_section(name):
            parser.remove_section(name)
        parser.add_section(name)
        for key, value in values.items():
            parser.set(name, key, value)
