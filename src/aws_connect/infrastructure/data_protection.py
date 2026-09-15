"""Windows DPAPI credential-protection boundary and deterministic test fake."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.errors import DataProtectionError


class FakeCredentialProtector:
    """Reversible test-only protector that never belongs in production composition."""

    _prefix = b"fake-protected:"

    def protect(self, value: str) -> bytes:
        return self._prefix + value.encode("utf-8")

    def unprotect(self, value: bytes) -> str:
        if not value.startswith(self._prefix):
            raise _protection_error("credentials.decrypt.failed")
        return value.removeprefix(self._prefix).decode("utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDpapiProtector:
    """Protect values for the current Windows user with installation entropy."""

    _ENTROPY_HEADER = b"AWSC-DPAPI-ENTROPY-v1\0"

    def __init__(self, entropy_path: Path, file_access: PrivateFileAccess | None = None) -> None:
        if os.name != "nt":
            raise _protection_error("dpapi.windows_required")
        self._file_access = file_access
        entropy_path.parent.mkdir(parents=True, exist_ok=True)
        if file_access is not None:
            file_access.restrict(entropy_path.parent)
            if entropy_path.exists():
                file_access.restrict(entropy_path)
        self._entropy = self._load_or_create_entropy(entropy_path)

    def protect(self, value: str) -> bytes:
        return protect_current_user_bytes(value.encode("utf-8"), entropy=self._entropy)

    def unprotect(self, value: bytes) -> str:
        try:
            return unprotect_current_user_bytes(value, entropy=self._entropy).decode("utf-8")
        except UnicodeDecodeError as error:
            raise _protection_error("credentials.decrypt.failed", error) from error

    def _load_or_create_entropy(self, path: Path) -> bytes:
        if not path.exists():
            entropy = os.urandom(32)
            protected = protect_current_user_bytes(entropy)
            self._replace_entropy(path, self._ENTROPY_HEADER + protected)
            return entropy
        stored = path.read_bytes()
        if stored.startswith(self._ENTROPY_HEADER):
            protected = stored.removeprefix(self._ENTROPY_HEADER)
            if not protected:
                raise _protection_error("dpapi.entropy.invalid")
            try:
                return unprotect_current_user_bytes(protected)
            except DataProtectionError as error:
                raise _protection_error("dpapi.entropy.decrypt_failed", error) from error
        if len(stored) == 32:
            protected = protect_current_user_bytes(stored)
            self._replace_entropy(path, self._ENTROPY_HEADER + protected)
            return stored
        raise _protection_error("dpapi.entropy.invalid")

    def _replace_entropy(self, path: Path, protected: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_bytes(protected)
            if self._file_access is not None:
                self._file_access.restrict(temporary)
            os.replace(temporary, path)
            if self._file_access is not None:
                self._file_access.restrict(path)
        except OSError as error:
            raise _protection_error("dpapi.entropy.write_failed", error) from error
        finally:
            temporary.unlink(missing_ok=True)


def protect_current_user_bytes(value: bytes, *, entropy: bytes | None = None) -> bytes:
    """Protect an in-memory value for the current Windows user without filesystem state."""

    return _crypt_current_user(value, decrypt=False, entropy=entropy)


def unprotect_current_user_bytes(value: bytes, *, entropy: bytes | None = None) -> bytes:
    """Unprotect a current-user DPAPI value without creating a temporary artifact."""

    return _crypt_current_user(value, decrypt=True, entropy=entropy)


def _crypt_current_user(value: bytes, *, decrypt: bool, entropy: bytes | None) -> bytes:
    source, source_buffer = _blob(value)
    entropy_blob: _DataBlob | None = None
    entropy_buffer: ctypes.Array[ctypes.c_char] | None = None
    if entropy is not None:
        entropy_blob, entropy_buffer = _blob(entropy)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    entropy_pointer = ctypes.byref(entropy_blob) if entropy_blob is not None else None
    try:
        if decrypt:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(source), None, entropy_pointer, None, None, 1, ctypes.byref(result)
            )
        else:
            ok = crypt32.CryptProtectData(
                ctypes.byref(source),
                "AWS Connect",
                entropy_pointer,
                None,
                None,
                1,
                ctypes.byref(result),
            )
        if not ok:
            raise ctypes.WinError()
        return ctypes.string_at(result.pbData, result.cbData)
    except OSError as error:
        raise _protection_error(
            "credentials.decrypt.failed" if decrypt else "credentials.encrypt.failed", error
        ) from error
    finally:
        del source_buffer, entropy_buffer
        if result.pbData:
            kernel32.LocalFree(result.pbData)


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _protection_error(code: str, cause: Exception | None = None) -> DataProtectionError:
    return DataProtectionError(message_code=code, technical_cause=str(cause or code))
