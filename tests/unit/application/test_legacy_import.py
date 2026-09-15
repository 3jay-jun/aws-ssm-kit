from pathlib import Path

import pytest

from aws_connect.application.legacy_import import LegacyImportRequest, LegacyIniImportService
from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure.data_protection import FakeCredentialProtector
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore

TEST_ACCESS_KEY = "AKIA" + "ABCDEFGH" + "IJKLMNOP"


class ObscuringTestProtector:
    """Reversible unit fake whose storage bytes cannot equal the source values."""

    def protect(self, value: str) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value.encode())

    def unprotect(self, value: bytes) -> str:
        return bytes(byte ^ 0xA5 for byte in value).decode()


def _write_legacy(path: Path, *, secret: str = "secret-value-long-enough") -> bytes:
    contents = (
        "[aws]\n"
        "region=ap-northeast-2\n"
        "account_id=123456789012\n"
        "user_id=developer\n"
        f"access_key={TEST_ACCESS_KEY}\n"
        f"secret_key={secret}\n\n"
        "[rds]\n"
        "host=db.internal\n"
        "port=3306\n"
        "local_port=13306\n"
    ).encode()
    path.write_bytes(contents)
    return contents


def test_import_protects_credentials_creates_tunnel_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "aws_info.ini"
    original = _write_legacy(source)
    store = SqliteProfileStore(tmp_path / "data" / "aws_connect.db")
    protector = ObscuringTestProtector()
    service = LegacyIniImportService(store, store, protector)

    preview = service.preview(LegacyImportRequest(source, "개발계", "개발 DB"))
    assert preview.credentials_present
    assert not hasattr(preview, "access_key")
    result = service.apply(LegacyImportRequest(source, "개발계", "개발 DB", confirmed=True))

    profile = store.get(result.profile_id)
    assert profile is not None
    assert protector.unprotect(profile.encrypted_access_key) == TEST_ACCESS_KEY
    assert protector.unprotect(profile.encrypted_secret_key) == "secret-value-long-enough"
    assert result.tunnel_imported
    assert store.get_tunnel(result.tunnel_id or 0).host == "db.internal"  # type: ignore[union-attr]
    assert source.read_bytes() == original
    database = (tmp_path / "data" / "aws_connect.db").read_bytes()
    assert TEST_ACCESS_KEY.encode() not in database
    assert b"secret-value-long-enough" not in database


def test_import_rejects_name_conflict_without_overwriting_source(tmp_path: Path) -> None:
    source = tmp_path / "aws_info.ini"
    original = _write_legacy(source)
    store = SqliteProfileStore(tmp_path / "aws_connect.db")
    service = LegacyIniImportService(store, store, FakeCredentialProtector())
    service.apply(LegacyImportRequest(source, confirmed=True))

    with pytest.raises(ConfigurationError) as failure:
        service.apply(LegacyImportRequest(source, confirmed=True))

    assert failure.value.message_code == "legacy.profile.name.conflict"
    assert len(store.list()) == 1
    assert source.read_bytes() == original


def test_import_error_never_contains_plaintext_secret(tmp_path: Path) -> None:
    source = tmp_path / "aws_info.ini"
    source.write_text("[aws]\nsecret_key=plain-text-never-leak\n", encoding="utf-8")
    store = SqliteProfileStore(tmp_path / "aws_connect.db")
    service = LegacyIniImportService(store, store, FakeCredentialProtector())

    with pytest.raises(ConfigurationError) as failure:
        service.preview(LegacyImportRequest(source))

    assert "plain-text-never-leak" not in repr(failure.value)
    assert "plain-text-never-leak" not in failure.value.technical_cause


def test_import_without_rds_section_only_creates_profile(tmp_path: Path) -> None:
    source = tmp_path / "aws_info.ini"
    _write_legacy(source)
    source.write_text(source.read_text().split("[rds]")[0], encoding="utf-8")
    store = SqliteProfileStore(tmp_path / "aws_connect.db")

    result = LegacyIniImportService(store, store, FakeCredentialProtector()).apply(
        LegacyImportRequest(source, confirmed=True)
    )

    assert result.tunnel_imported is False
    assert result.tunnel_id is None


def test_import_requires_explicit_confirmation_and_never_changes_source(tmp_path: Path) -> None:
    source = tmp_path / "aws_info.ini"
    original = _write_legacy(source)
    store = SqliteProfileStore(tmp_path / "aws_connect.db")
    service = LegacyIniImportService(store, store, FakeCredentialProtector())

    with pytest.raises(ConfigurationError) as failure:
        service.apply(LegacyImportRequest(source))

    assert failure.value.message_code == "legacy.import.confirmation.required"
    assert store.list() == []
    assert source.read_bytes() == original
