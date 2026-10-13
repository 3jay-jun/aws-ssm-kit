import configparser
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure.aws_shared_credentials import AwsSharedCredentialsWriter


def credentials(suffix: str = "1") -> PlainCredentials:
    return PlainCredentials(
        f"SESSIONKEYTEST00{suffix}",
        f"temporary-test-secret-{suffix}",
        f"temporary-test-token-{suffix}",
    )


def read(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def test_write_updates_named_and_default_profiles_and_preserves_others(tmp_path: Path) -> None:
    path = tmp_path / ".aws" / "credentials"
    path.parent.mkdir()
    path.write_text(
        "[existing]\naws_access_key_id = KEEPTHISKEY00001\n"
        "aws_secret_access_key = keep-this-existing-secret\n",
        encoding="utf-8",
    )
    access = Mock()
    writer = AwsSharedCredentialsWriter(path, access)

    writer.write(7, credentials())

    saved = read(path)
    assert saved.get("existing", "aws_access_key_id") == "KEEPTHISKEY00001"
    for section in ("aws-connect-7", "default"):
        assert saved.get(section, "aws_access_key_id") == "SESSIONKEYTEST001"
        assert saved.get(section, "aws_secret_access_key") == "temporary-test-secret-1"
        assert saved.get(section, "aws_session_token") == "temporary-test-token-1"
    assert access.restrict.call_count == 2
    assert list(path.parent.glob(".credentials.*.tmp")) == []


def test_write_replaces_only_owned_sections_on_refresh(tmp_path: Path) -> None:
    path = tmp_path / "credentials"
    writer = AwsSharedCredentialsWriter(path)

    writer.write(2, credentials("1"))
    writer.write(2, credentials("2"))

    saved = read(path)
    assert saved.sections() == ["aws-connect-2", "default"]
    assert saved.get("aws-connect-2", "aws_session_token") == "temporary-test-token-2"
    assert saved.get("default", "aws_session_token") == "temporary-test-token-2"


def test_invalid_existing_file_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "credentials"
    original = "[broken\nvalue = remains\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigurationError) as caught:
        AwsSharedCredentialsWriter(path).write(1, credentials())

    assert caught.value.message_code == "credentials.shared_file.write_failed"
    assert path.read_text(encoding="utf-8") == original


def test_temporary_credentials_require_session_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as caught:
        AwsSharedCredentialsWriter(tmp_path / "credentials").write(
            1, PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value")
        )

    assert caught.value.message_code == "credentials.shared_file.session_token.required"


def test_profile_id_must_be_persisted(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as caught:
        AwsSharedCredentialsWriter(tmp_path / "credentials").write(0, credentials())

    assert caught.value.message_code == "credentials.shared_file.profile.invalid"


def test_acl_failure_is_typed_and_removes_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials"
    access = Mock()
    access.restrict.side_effect = ConfigurationError(
        "private_file_access.restrict_failed", "Access denied"
    )

    with pytest.raises(ConfigurationError) as caught:
        AwsSharedCredentialsWriter(path, access).write(1, credentials())

    assert caught.value.message_code == "credentials.shared_file.write_failed"
    assert not path.exists()
    assert list(tmp_path.glob(".credentials.*.tmp")) == []


def test_default_path_uses_current_user_aws_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    AwsSharedCredentialsWriter().write(3, credentials())

    assert (tmp_path / ".aws" / "credentials").is_file()
