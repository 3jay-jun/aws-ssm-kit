import ctypes
import logging
import os
import re
import sqlite3
from ctypes import wintypes
from pathlib import Path

import pytest

from aws_connect.bootstrap import build_application_services, runtime_paths
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.infrastructure.logging_setup import RotatingLogConfigurator
from aws_connect.infrastructure.private_file_access import WindowsPrivateFileAccess
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore

_DACL_SECURITY_INFORMATION = 0x00000004
_ERROR_INSUFFICIENT_BUFFER = 122
_SDDL_REVISION_1 = 1
_ACE_PATTERN = re.compile(r"\(([^)]*)\)")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs are Windows-only")
def test_real_acl_is_protected_current_user_only_for_file_and_directory(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    file = directory / "state.db"
    file.write_bytes(b"before")
    access = WindowsPrivateFileAccess()

    access.restrict(directory)
    access.restrict(file)

    directory_sddl = _dacl_sddl(directory)
    file_sddl = _dacl_sddl(file)
    assert directory_sddl.startswith("D:P")
    assert directory_sddl.count("(") == 1
    assert "OICI" in directory_sddl
    assert file_sddl.startswith("D:P")
    assert file_sddl.count("(") == 1
    file.write_bytes(b"after")
    assert file.read_bytes() == b"after"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs are Windows-only")
def test_bootstrap_restricts_all_private_runtime_artifacts(tmp_path: Path) -> None:
    data_directory = tmp_path / "runtime"
    paths = runtime_paths(data_directory)
    services = build_application_services(data_directory)
    assert services.diagnostic_logs is not None
    archive = tmp_path / "export" / "diagnostics.zip"
    services.diagnostic_logs.export(archive)

    for directory in (paths.data, paths.default_logs):
        sddl = _dacl_sddl(directory)
        assert sddl.startswith("D:P")
        assert sddl.count("(") == 1
        assert "OICI" in sddl
    for file in (
        paths.database,
        paths.entropy,
        paths.default_logs / "aws-connect.log",
        archive,
    ):
        sddl = _dacl_sddl(file)
        assert sddl.startswith("D:P")
        assert sddl.count("(") == 1
    logging.shutdown()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs are Windows-only")
def test_runtime_sqlite_sidecars_inherit_only_the_current_user_ace(tmp_path: Path) -> None:
    access = WindowsPrivateFileAccess()

    rollback_root = tmp_path / "rollback"
    rollback_database = rollback_root / "state.db"
    SqliteProfileStore(rollback_database, access)
    owner_sid = _single_trustee(_dacl_sddl(rollback_root))
    rollback = sqlite3.connect(rollback_database)
    try:
        rollback.execute("PRAGMA journal_mode=DELETE")
        rollback.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        rollback.commit()
        rollback.execute("BEGIN IMMEDIATE")
        rollback.execute("INSERT INTO sample VALUES ('test')")
        journal = Path(f"{rollback_database}-journal")
        assert journal.exists()
        _assert_current_user_only(rollback_database, owner_sid)
        _assert_current_user_only(journal, owner_sid)
    finally:
        rollback.rollback()
        rollback.close()

    wal_root = tmp_path / "wal"
    wal_database = wal_root / "state.db"
    SqliteProfileStore(wal_database, access)
    wal = sqlite3.connect(wal_database)
    try:
        assert wal.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        wal.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        wal.commit()
        for path in (wal_database, Path(f"{wal_database}-wal"), Path(f"{wal_database}-shm")):
            assert path.exists()
            _assert_current_user_only(path, owner_sid)
    finally:
        wal.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs are Windows-only")
def test_rotated_logs_inherit_only_the_current_user_ace(tmp_path: Path) -> None:
    logs = tmp_path / "rotating-logs"
    configurator = RotatingLogConfigurator(
        WindowsPrivateFileAccess(),
        max_bytes=80,
        backups=2,
    )
    configurator.apply(AppSettings(logs, LogLevel.INFO))
    logger = logging.getLogger("aws_connect.acl_test")
    try:
        for index in range(12):
            logger.info("rotation %s creates a sufficiently long managed record", index)
        for handler in logging.getLogger("aws_connect").handlers:
            handler.flush()

        owner_sid = _single_trustee(_dacl_sddl(logs))
        managed = list(logs.glob("aws-connect.log*"))
        assert any(path.name != "aws-connect.log" for path in managed)
        for path in managed:
            _assert_current_user_only(path, owner_sid)
    finally:
        root_logger = logging.getLogger("aws_connect")
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()


def _assert_current_user_only(path: Path, owner_sid: str) -> None:
    assert _trustees(_dacl_sddl(path)) == {owner_sid}


def _single_trustee(sddl: str) -> str:
    trustees = _trustees(sddl)
    assert len(trustees) == 1
    return next(iter(trustees))


def _trustees(sddl: str) -> set[str]:
    return {ace.rsplit(";", maxsplit=1)[-1] for ace in _ACE_PATTERN.findall(sddl)}


def _dacl_sddl(path: Path) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID

    required = wintypes.DWORD()
    advapi32.GetFileSecurityW(
        str(path),
        _DACL_SECURITY_INFORMATION,
        None,
        0,
        ctypes.byref(required),
    )
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(required.value)
    if not advapi32.GetFileSecurityW(
        str(path),
        _DACL_SECURITY_INFORMATION,
        descriptor,
        required,
        ctypes.byref(required),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    sddl = wintypes.LPWSTR()
    length = wintypes.DWORD()
    if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor,
        _SDDL_REVISION_1,
        _DACL_SECURITY_INFORMATION,
        ctypes.byref(sddl),
        ctypes.byref(length),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if sddl.value is None:
            raise OSError("ACL query returned an empty SDDL")
        return sddl.value
    finally:
        kernel32.LocalFree(sddl)
