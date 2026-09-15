"""Windows current-user-only ACL adapter for private application files."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

from aws_connect.domain.errors import ConfigurationError

_DACL_SECURITY_INFORMATION = 0x00000004
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ERROR_INSUFFICIENT_BUFFER = 122


class _AclBackend(Protocol):
    def current_user_sid(self) -> str: ...

    def set_current_user_only(self, path: Path, sid: str, *, directory: bool) -> None: ...


class WindowsPrivateFileAccess:
    """Replace a path's DACL with one protected ACE for the current user."""

    def __init__(
        self,
        backend: _AclBackend | None = None,
        *,
        platform_name: str = os.name,
    ) -> None:
        if backend is None and platform_name != "nt":
            raise _acl_error("private_file_access.windows_required", "Windows is required")
        self._backend = backend or _CtypesAclBackend()
        try:
            self._current_user_sid = self._backend.current_user_sid()
        except OSError as error:
            raise _acl_error("private_file_access.current_user_failed", error) from error

    def restrict(self, path: Path) -> None:
        """Restrict an existing file or directory without ignoring ACL failures."""

        try:
            is_directory = path.is_dir()
            if not is_directory and not path.is_file():
                raise FileNotFoundError(path)
            self._backend.set_current_user_only(
                path,
                self._current_user_sid,
                directory=is_directory,
            )
        except OSError as error:
            raise _acl_error("private_file_access.restrict_failed", error) from error


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


class _CtypesAclBackend:
    """Small ctypes boundary around the Windows token and security APIs."""

    def __init__(self) -> None:
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self._kernel32.GetCurrentProcess.argtypes = []
        self._kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [wintypes.LPVOID]
        self._kernel32.LocalFree.restype = wintypes.LPVOID
        self._advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._advapi32.OpenProcessToken.restype = wintypes.BOOL
        self._advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.GetTokenInformation.restype = wintypes.BOOL
        self._advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self._advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self._advapi32.SetFileSecurityW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        self._advapi32.SetFileSecurityW.restype = wintypes.BOOL

    def current_user_sid(self) -> str:
        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            required = wintypes.DWORD()
            self._advapi32.GetTokenInformation(
                token,
                _TOKEN_USER,
                None,
                0,
                ctypes.byref(required),
            )
            error_code = ctypes.get_last_error()
            if error_code != _ERROR_INSUFFICIENT_BUFFER or required.value == 0:
                raise ctypes.WinError(error_code)
            buffer = ctypes.create_string_buffer(required.value)
            if not self._advapi32.GetTokenInformation(
                token,
                _TOKEN_USER,
                buffer,
                required,
                ctypes.byref(required),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            string_sid = wintypes.LPWSTR()
            if not self._advapi32.ConvertSidToStringSidW(
                token_user.user.sid, ctypes.byref(string_sid)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if string_sid.value is None:
                    raise OSError("ConvertSidToStringSidW returned an empty SID")
                return string_sid.value
            finally:
                self._kernel32.LocalFree(string_sid)
        finally:
            self._kernel32.CloseHandle(token)

    def set_current_user_only(self, path: Path, sid: str, *, directory: bool) -> None:
        inheritance = "OICI" if directory else ""
        sddl = f"D:P(A;{inheritance};FA;;;{sid})"
        descriptor = wintypes.LPVOID()
        descriptor_size = wintypes.DWORD()
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(descriptor_size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._advapi32.SetFileSecurityW(
                str(path),
                _DACL_SECURITY_INFORMATION,
                descriptor,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.LocalFree(descriptor)


def _acl_error(code: str, cause: object) -> ConfigurationError:
    return ConfigurationError(message_code=code, technical_cause=type(cause).__name__)
