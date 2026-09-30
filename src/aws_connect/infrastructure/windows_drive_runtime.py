"""Windows drive observation and parent-owned process lifetime."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from aws_connect.domain.errors import ConfigurationError


def available_drive_letters() -> set[str]:
    if os.name != "nt":
        raise ConfigurationError("drive.windows.required", "S3 drives require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    mask = kernel.GetLogicalDrives()
    if not mask:
        raise ConfigurationError("drive.observe.failed", "Cannot inspect Windows drive letters")
    return {chr(index + 65) for index in range(3, 26) if not mask & (1 << index)}


def winfsp_available() -> bool:
    if os.name != "nt":
        return False
    import winreg

    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0, winreg.KEY_READ | view
            ) as key:
                directory, _ = winreg.QueryValueEx(key, "InstallDir")
            if (Path(directory) / "bin" / "winfsp-x64.dll").is_file():
                return True
        except OSError:
            continue
    return False


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_ws", ctypes.c_size_t),
        ("max_ws", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class OwnedProcessJob:
    """Kill only our mount child if its owning application dies unexpectedly."""

    def __init__(self, pid: int) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel = kernel
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._handle = kernel.CreateJobObjectW(None, None)
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        try:
            if (
                not self._handle
                or not process
                or not kernel.SetInformationJobObject(
                    self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
                or not kernel.AssignProcessToJobObject(self._handle, process)
            ):
                self.close()
                raise ConfigurationError(
                    "drive.process.failed", "Cannot own mount process lifetime"
                )
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
