from pathlib import Path

import pytest

from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure.private_file_access import WindowsPrivateFileAccess


class RecordingAclBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, bool]] = []
        self.failure: OSError | None = None

    def current_user_sid(self) -> str:
        return "S-1-5-21-test-user"

    def set_current_user_only(self, path: Path, sid: str, *, directory: bool) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append((path, sid, directory))


class FailingIdentityBackend(RecordingAclBackend):
    def current_user_sid(self) -> str:
        raise OSError("token lookup failed")


def test_injected_backend_makes_file_and_directory_rules_platform_independent(
    tmp_path: Path,
) -> None:
    backend = RecordingAclBackend()
    access = WindowsPrivateFileAccess(backend, platform_name="posix")
    directory = tmp_path / "private"
    directory.mkdir()
    file = directory / "state.db"
    file.write_bytes(b"state")

    access.restrict(directory)
    access.restrict(file)

    assert backend.calls == [
        (directory, "S-1-5-21-test-user", True),
        (file, "S-1-5-21-test-user", False),
    ]


def test_missing_path_and_backend_failure_are_typed(tmp_path: Path) -> None:
    backend = RecordingAclBackend()
    access = WindowsPrivateFileAccess(backend, platform_name="posix")

    with pytest.raises(ConfigurationError) as missing:
        access.restrict(tmp_path / "missing")
    assert missing.value.message_code == "private_file_access.restrict_failed"

    file = tmp_path / "state"
    file.touch()
    backend.failure = PermissionError("denied")
    with pytest.raises(ConfigurationError) as denied:
        access.restrict(file)
    assert denied.value.message_code == "private_file_access.restrict_failed"
    assert denied.value.technical_cause == "PermissionError"


def test_identity_failure_and_unsupported_platform_are_typed() -> None:
    with pytest.raises(ConfigurationError) as identity:
        WindowsPrivateFileAccess(FailingIdentityBackend(), platform_name="posix")
    assert identity.value.message_code == "private_file_access.current_user_failed"

    with pytest.raises(ConfigurationError) as unsupported:
        WindowsPrivateFileAccess(platform_name="posix")
    assert unsupported.value.message_code == "private_file_access.windows_required"
