"""Verified, offline MSI installation. Only msiexec is elevated, never the app."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed scripts and verified MSI; no shell execution
from ctypes import wintypes
from pathlib import Path
from typing import Any

from aws_connect.application.winfsp_runtime import RuntimeState, WinFspStatus
from aws_connect.domain.errors import ConfigurationError

RUNTIME_SPEC: dict[str, Any] = {
    "version": "2.1.25156",
    "artifact": "winfsp-2.1.25156.msi",
    "source_url": "https://github.com/winfsp/winfsp/releases/download/v2.1/winfsp-2.1.25156.msi",
    "sha256": "073a70e00f77423e34bed98b86e600def93393ba5822204fac57a29324db9f7a",  # pragma: allowlist secret # noqa: E501
    "signer": "NAVIMATICS LLC",
    "features": ["F.Main", "F.User"],
    "license_url": "https://raw.githubusercontent.com/winfsp/winfsp/v2.1/License.txt",
}
_SPEC = RUNTIME_SPEC
WINFSP_VERSION: str = _SPEC["version"]
WINFSP_ARTIFACT: str = _SPEC["artifact"]
WINFSP_SHA256: str = _SPEC["sha256"]
WINFSP_SIGNER: str = _SPEC["signer"]


def _error(code: str) -> ConfigurationError:
    return ConfigurationError(f"runtime.winfsp.{code}", "WinFsp runtime operation failed")


def powershell(script: str, *, input_text: str = "") -> str:
    """Fixed script; paths are stdin data and never interpolated into PowerShell source."""
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    try:
        result = subprocess.run(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::InputEncoding=[Text.Encoding]::UTF8; "
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script,
            ],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )  # nosec B603
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _error("observe_failed") from error
    if result.returncode:
        raise _error("observe_failed")
    return result.stdout.strip()


def boot_identity() -> str:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager"
            r"\Memory Management\PrefetchParameters",
        ) as key:
            return str(winreg.QueryValueEx(key, "BootId")[0])
    except OSError as error:
        raise _error("observe_failed") from error


def _file_version(path: Path) -> str:
    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    api.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return ""
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    api.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    if (
        not api.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length))
        or length.value < 16
    ):
        return ""
    fields = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))
    return f"{fields[2] >> 16}.{fields[2] & 0xFFFF}.{fields[3] >> 16}"


def _driver_present() -> bool:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services") as key:
        for index in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, index)
            if name.casefold().startswith("winfsp"):
                try:
                    with winreg.OpenKey(key, name) as driver:
                        if winreg.QueryValueEx(driver, "Type")[0] in (1, 2):
                            image = str(winreg.QueryValueEx(driver, "ImagePath")[0]).strip('"')
                            image = os.path.expandvars(image).removeprefix("\\??\\")
                            if image.casefold().startswith("\\systemroot\\"):
                                image = str(Path(os.environ["SYSTEMROOT"]) / image[12:])
                            if Path(image).is_file():
                                return True
                except FileNotFoundError:
                    continue
    return False


def inspect_winfsp() -> WinFspStatus:
    if os.name != "nt":
        return WinFspStatus(RuntimeState.UNSUPPORTED)
    import winreg

    directory: Path | None = None
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0, winreg.KEY_READ | view
            ) as key:
                directory = Path(winreg.QueryValueEx(key, "InstallDir")[0])
                break
        except FileNotFoundError:
            continue
        except OSError as error:
            raise _error("observe_failed") from error
    if directory is None:
        return WinFspStatus(RuntimeState.MISSING)
    dll = directory / "bin/winfsp-x64.dll"
    if not dll.is_file():
        return WinFspStatus(RuntimeState.REPAIR_REQUIRED)
    version = _file_version(dll)
    try:
        parts = tuple(int(part) for part in version.split(".")[:3])
    except ValueError:
        return WinFspStatus(RuntimeState.REPAIR_REQUIRED, version)
    if len(parts) != 3 or parts[0] != 2:
        return WinFspStatus(RuntimeState.UNSUPPORTED, version)
    if parts < tuple(int(part) for part in WINFSP_VERSION.split(".")):
        return WinFspStatus(RuntimeState.REPAIR_REQUIRED, version)
    # Observe the actual kernel driver rather than declaring a DLL alone usable.
    driver = _driver_present()
    return WinFspStatus(RuntimeState.READY if driver else RuntimeState.REPAIR_REQUIRED, version)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


class WindowsWinFspInstaller:
    def __init__(self, payload: Path, state_directory: Path) -> None:
        self.payload = payload
        self.state_directory = state_directory

    def inspect(self) -> WinFspStatus:
        if os.name == "nt":
            import winreg

            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\AWSConnect\Runtime",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                ) as key:
                    if str(winreg.QueryValueEx(key, "WinFspRebootBootId")[0]) == boot_identity():
                        return WinFspStatus(RuntimeState.REBOOT_REQUIRED, WINFSP_VERSION)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise _error("observe_failed") from error
        marker = self.state_directory / "winfsp-reboot.json"
        if marker.exists():
            try:
                recorded: dict[str, Any] = json.loads(marker.read_text(encoding="utf-8"))
                if recorded["boot"] == boot_identity():
                    return WinFspStatus(RuntimeState.REBOOT_REQUIRED, WINFSP_VERSION)
                marker.unlink()
            except (OSError, ValueError, KeyError) as error:
                raise _error("observe_failed") from error
        try:
            return inspect_winfsp()
        except OSError as error:
            raise _error("observe_failed") from error

    def remember_reboot(self) -> None:
        if os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin():
            import winreg

            with winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\AWSConnect\Runtime",
                0,
                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            ) as key:
                winreg.SetValueEx(key, "WinFspRebootBootId", 0, winreg.REG_SZ, boot_identity())
            return
        self.state_directory.mkdir(parents=True, exist_ok=True)
        (self.state_directory / "winfsp-reboot.json").write_text(
            json.dumps({"boot": boot_identity()}), encoding="utf-8"
        )

    def install(self, *, repair: bool) -> int:
        if os.name != "nt":
            raise _error("unsupported")
        if powershell("if (Get-Process rclone -ErrorAction SilentlyContinue) {'busy'}") == "busy":
            return 1618
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.payload.resolve()), 0x80000000, 1, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise _error("payload_missing")
        try:
            with self.payload.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != WINFSP_SHA256:
                    raise _error("verification_failed")
            signature = powershell(
                "$p=[Console]::In.ReadToEnd(); $s=Get-AuthenticodeSignature -LiteralPath $p; "
                "@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject} | "
                "ConvertTo-Json -Compress",
                input_text=str(self.payload.resolve()),
            )
            info = json.loads(signature)
            if info["status"] != "Valid" or WINFSP_SIGNER not in str(info["subject"]):
                raise _error("verification_failed")
            # A machine-wide mutex prevents multiple app/Setup instances launching MSI.
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            mutex = kernel.CreateMutexW(None, False, "Global\\AWSConnect.WinFsp.Install")
            if not mutex:
                raise _error("observe_failed")
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            acquired = kernel.WaitForSingleObject(mutex, 0) in (0, 0x80)
            try:
                if not acquired:
                    return 1618
                arguments = [
                    "/i",
                    str(self.payload.resolve()),
                    "ADDLOCAL=" + ",".join(_SPEC["features"]),
                    "/qn",
                    "/norestart",
                ]
                version = self.inspect().version if repair else None
                if version and version.startswith(WINFSP_VERSION):
                    arguments.extend(["REINSTALL=ALL", "REINSTALLMODE=vomus"])
                return self._execute(arguments)
            finally:
                if acquired:
                    kernel.ReleaseMutex(mutex)
                kernel.CloseHandle(mutex)
        except (OSError, ValueError, KeyError) as error:
            raise _error("verification_failed") from error
        finally:
            kernel.CloseHandle(handle)

    @staticmethod
    def _execute(arguments: list[str]) -> int:
        ole = ctypes.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole.CoInitializeEx.restype = ctypes.c_long
        initialized = ole.CoInitializeEx(None, 2) in (0, 1)
        try:
            return WindowsWinFspInstaller._execute_initialized(arguments)
        finally:
            if initialized:
                ole.CoUninitialize()

    @staticmethod
    def _execute_initialized(arguments: list[str]) -> int:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x40 | 0x100
        info.lpVerb = "runas"
        info.lpFile = str(
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/msiexec.exe"
        )
        info.lpParameters = subprocess.list2cmdline(arguments)
        info.nShow = 0
        shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
        shell.ShellExecuteExW.restype = wintypes.BOOL
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            code = ctypes.get_last_error()
            if code == 1223:
                return code
            raise _error("start_failed")
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            # Keep the locks and payload handle until Installer finishes. Never terminate MSI.
            wait = kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
            if wait != 0:
                raise _error("observe_failed")
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
                raise _error("observe_failed")
            return int(exit_code.value)
        finally:
            kernel.CloseHandle(info.hProcess)
