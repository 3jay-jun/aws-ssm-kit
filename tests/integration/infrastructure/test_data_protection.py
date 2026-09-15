import os
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from aws_connect.domain.aws_profile import AwsProfile
from aws_connect.domain.errors import DataProtectionError
from aws_connect.infrastructure.data_protection import (
    FakeCredentialProtector,
    WindowsDpapiProtector,
)
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


def test_fake_protector_round_trip_and_rejects_unknown_ciphertext() -> None:
    protector = FakeCredentialProtector()
    protected = protector.protect("local-test-value")

    assert b"local-test-value" in protected  # fake is intentionally test-only
    assert protector.unprotect(protected) == "local-test-value"
    with pytest.raises(DataProtectionError):
        protector.unprotect(b"not-from-fake")


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_windows_dpapi_round_trip(tmp_path) -> None:
    entropy_path = tmp_path / "entropy"
    protector = WindowsDpapiProtector(entropy_path)

    protected = protector.protect("local-test-value")

    assert b"local-test-value" not in protected
    assert protector.unprotect(protected) == "local-test-value"
    stored_entropy = entropy_path.read_bytes()
    assert stored_entropy.startswith(WindowsDpapiProtector._ENTROPY_HEADER)
    assert len(stored_entropy) > len(WindowsDpapiProtector._ENTROPY_HEADER) + 32
    with pytest.raises(DataProtectionError):
        protector.unprotect(b"damaged-protected-value")


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_plaintext_entropy_is_atomically_migrated(tmp_path) -> None:
    entropy_path = tmp_path / "entropy"
    legacy_entropy = b"x" * 32
    entropy_path.write_bytes(legacy_entropy)

    protector = WindowsDpapiProtector(entropy_path)

    stored = entropy_path.read_bytes()
    assert legacy_entropy not in stored
    assert stored.startswith(WindowsDpapiProtector._ENTROPY_HEADER)
    assert not entropy_path.with_suffix(".tmp").exists()
    assert protector.unprotect(protector.protect("round-trip")) == "round-trip"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_invalid_or_corrupted_entropy_is_rejected(tmp_path) -> None:
    entropy_path = tmp_path / "entropy"
    entropy_path.write_bytes(b"invalid")
    with pytest.raises(DataProtectionError) as invalid:
        WindowsDpapiProtector(entropy_path)
    assert invalid.value.message_code == "dpapi.entropy.invalid"

    entropy_path.write_bytes(WindowsDpapiProtector._ENTROPY_HEADER + b"corrupted")
    with pytest.raises(DataProtectionError) as corrupted:
        WindowsDpapiProtector(entropy_path)
    assert corrupted.value.message_code == "dpapi.entropy.decrypt_failed"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_sqlite_never_contains_plain_credentials(tmp_path) -> None:
    protector = WindowsDpapiProtector(tmp_path / "entropy")
    store = SqliteProfileStore(tmp_path / "state.db")
    access_key = "ACCESSKEYTEST0001"
    secret_key = "not-sensitive-test-value"  # pragma: allowlist secret
    store.create(
        AwsProfile(
            None,
            "dev",
            "ap-northeast-2",
            "123456789012",
            "developer",
            "arn:aws:iam::123456789012:mfa/developer",
            protector.protect(access_key),
            protector.protect(secret_key),
        )
    )

    raw_database = (tmp_path / "state.db").read_bytes()
    assert access_key.encode() not in raw_database
    assert secret_key.encode() not in raw_database


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_entropy_directory_temporary_and_final_files_are_restricted(tmp_path: Path) -> None:
    entropy_path = tmp_path / "private" / "entropy"
    file_access = Mock()

    WindowsDpapiProtector(entropy_path, file_access)

    assert file_access.restrict.call_args_list == [
        call(entropy_path.parent),
        call(entropy_path.with_suffix(".tmp")),
        call(entropy_path),
    ]
