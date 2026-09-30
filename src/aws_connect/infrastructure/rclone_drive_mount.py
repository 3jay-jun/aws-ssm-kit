"""Private, foreground-owned rclone mounts. Cache uncertainty always fails closed."""

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess  # nosec B404 - fixed executable/argv, no shell, private child environment
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO

from aws_connect.application.operations import OperationContext
from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive
from aws_connect.infrastructure.windows_drive_runtime import (
    OwnedProcessJob,
    available_drive_letters,
    winfsp_available,
)


@dataclass(repr=False)
class _Mount:
    process: subprocess.Popen[bytes]
    job: OwnedProcessJob
    lease: BinaryIO
    port: int
    password: str
    source: str
    mounted: bool = False
    state: DriveState = DriveState.CONNECTING
    error: str = ""
    lock: RLock = field(default_factory=RLock)


def _error(code: str) -> ConfigurationError:
    return ConfigurationError(f"drive.{code}", f"S3 drive: {code}")


def remote_environment(
    drive: S3Drive, credentials: PlainCredentials, region: str
) -> tuple[dict[str, str], str, str]:
    """Build stable remote names for recoverable cache; secrets never enter argv/config files."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("AWS_", "RCLONE_"))
    }
    env.update(
        {
            "RCLONE_CONFIG_SOURCE_TYPE": "s3",
            "RCLONE_CONFIG_SOURCE_PROVIDER": "AWS",
            "RCLONE_CONFIG_SOURCE_ACCESS_KEY_ID": credentials.access_key,
            "RCLONE_CONFIG_SOURCE_SECRET_ACCESS_KEY": credentials.secret_key,
            "RCLONE_CONFIG_SOURCE_SESSION_TOKEN": credentials.session_token or "",
            "RCLONE_CONFIG_SOURCE_REGION": region,
            "RCLONE_CONFIG_SOURCE_NO_CHECK_BUCKET": "true",
        }
    )
    source = f"source:{drive.bucket}" + (f"/{drive.prefix}" if drive.prefix else "")
    remote = source
    # A combine layer per component supports nested synthetic paths without altering S3 keys.
    for index, part in enumerate(
        reversed(drive.local_prefix.split("/") if drive.local_prefix else [])
    ):
        name = f"folder{index}"
        env[f"RCLONE_CONFIG_{name.upper()}_TYPE"] = "combine"
        env[f"RCLONE_CONFIG_{name.upper()}_UPSTREAMS"] = json.dumps(
            f"{part}={remote}", ensure_ascii=False
        )
        remote = f"{name}:"
    return env, source, remote


def cache_is_dirty(root: Path) -> bool:
    """Inspect persisted VFS metadata, including open writers omitted from the upload queue."""
    meta = root / "vfsMeta"
    try:
        if not meta.exists():
            return any(path.is_file() for path in (root / "vfs").rglob("*"))
        for directory, _dirs, files in os.walk(meta, onerror=lambda error: _raise_cache(error)):
            for filename in files:
                path = Path(directory) / filename
                if path.stat().st_size > 1024 * 1024:
                    return True
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("Dirty") is not False:
                    return True
        return False
    except (OSError, ValueError):
        return True


def _raise_cache(error: OSError) -> None:
    raise error


class RcloneDriveMount:
    def __init__(self, executable: Path, cache: Path, access: PrivateFileAccess) -> None:
        self.executable = executable
        self._cache = cache
        self._access = access
        self._mounts: dict[int, _Mount] = {}

    def available_letters(self) -> set[str]:
        return available_drive_letters()

    def _root(self, drive: S3Drive) -> Path:
        return self._cache / str(drive.require_id())

    def _lease(self, root: Path) -> BinaryIO:
        import msvcrt

        root.mkdir(parents=True, exist_ok=True)
        self._access.restrict(root)
        stream = (root / "owner.lock").open("a+b")
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            stream.close()
            raise _error("owned_elsewhere") from error
        return stream

    def assert_editable(self, drive: S3Drive) -> None:
        if drive.require_id() in self._mounts:
            raise _error("active")
        root = self._root(drive)
        if root.exists():
            with self._lease(root):
                if (root / "pending.json").exists() or cache_is_dirty(root):
                    raise _error("cache.pending")

    def _call(
        self, mount: _Mount, method: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Fixed loopback HTTP client ignores proxy environment and cannot follow redirects.
        connection = http.client.HTTPConnection("127.0.0.1", mount.port, timeout=8)
        auth = base64.b64encode(f"aws-connect:{mount.password}".encode()).decode()
        try:
            connection.request(
                "POST",
                "/" + method,
                json.dumps(payload or {}),
                {"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            )
            response = connection.getresponse()
            body = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(body) > 1024 * 1024:
                raise _error("control.failed")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise _error("control.failed")
            return value
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise _error("control.failed") from error
        finally:
            connection.close()

    def connect(
        self, drive: S3Drive, credentials: PlainCredentials, region: str, context: OperationContext
    ) -> None:
        existing = self._mounts.get(drive.require_id())
        if existing is not None:
            with existing.lock:
                if existing.process.poll() is not None:
                    self._release(drive, existing)
                elif existing.mounted:
                    self._call(
                        existing,
                        "backend/command",
                        {
                            "fs": existing.source,
                            "command": "set",
                            "opt": {
                                "access_key_id": credentials.access_key,
                                "secret_access_key": credentials.secret_key,
                                "session_token": credentials.session_token or "",
                            },
                        },
                    )
                    existing.error = ""
                    return
                else:
                    self._release(drive, existing)
        if not self.executable.is_file():
            raise _error("runtime.rclone_missing")
        if not winfsp_available():
            raise _error("runtime.winfsp_missing")
        if drive.letter not in self.available_letters():
            raise _error("letter.busy")
        root = self._root(drive)
        lease = self._lease(root)
        process: subprocess.Popen[bytes] | None = None
        job: OwnedProcessJob | None = None
        registered = False
        try:
            if shutil.disk_usage(root).free < 256 * 1024 * 1024:
                raise _error("cache.full")
            binding = {
                "profile_id": drive.profile_id,
                "bucket": drive.bucket,
                "prefix": drive.prefix,
                "local_prefix": drive.local_prefix,
            }
            marker = root / "pending.json"
            if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != binding:
                raise _error("cache.pending")
            marker.write_text(json.dumps(binding), encoding="utf-8")
            self._access.restrict(marker)
            env, source, remote = remote_environment(drive, credentials, region)
            password = secrets.token_urlsafe(32)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env.update({"RCLONE_RC_USER": "aws-connect", "RCLONE_RC_PASS": password})
            # rclone rejects Windows NUL as a config path. Use a private empty file;
            # remote definitions and credentials still exist only in the child environment.
            config = root / "rclone.conf"
            config.write_text("", encoding="utf-8")
            self._access.restrict(config)
            argv = [
                str(self.executable),
                "rcd",
                "--config",
                str(config),
                "--rc-addr",
                f"127.0.0.1:{port}",
                "--cache-dir",
                str(root),
                "--log-level",
                "ERROR",
                "--buffer-size",
                "8M",
                "--transfers",
                "2",
                "--retries",
                "1",
                "--low-level-retries",
                "1",
            ]
            process = subprocess.Popen(
                argv,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )  # nosec B603
            job = OwnedProcessJob(process.pid)
            mount = _Mount(process, job, lease, port, password, source)
            self._mounts[drive.require_id()] = mount
            registered = True
            context.report("connecting", "drive.connecting")
            deadline = time.monotonic() + 20
            while True:
                context.raise_if_cancelled()
                if process.poll() is not None:
                    raise _error("process.failed")
                try:
                    self._call(mount, "core/version")
                    break
                except ApplicationError:
                    if time.monotonic() >= deadline:
                        raise _error("control.failed") from None
                    time.sleep(0.1)
            if drive.letter not in self.available_letters():
                raise _error("letter.busy")
            # Mark before the request: timeout does not prove the mount did not happen.
            mount.mounted = True
            self._call(
                mount,
                "mount/mount",
                {
                    "fs": remote,
                    "mountPoint": f"{drive.letter}:",
                    "vfsOpt": {
                        "CacheMode": 3,
                        "CacheMaxSize": 5368709120,
                        "CacheMinFreeSpace": 536870912,
                        "WriteBack": 1000000000,
                    },
                },
            )
            if drive.letter in self.available_letters():
                raise _error("observe.failed")
            mount.state = DriveState.CONNECTED
        except (OSError, ValueError) as error:
            raise _error("process.failed") from error
        finally:
            if not registered:
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)
                if job is not None:
                    job.close()
                lease.close()

    def status(self, drive: S3Drive) -> DriveStatus:
        mount = self._mounts.get(drive.require_id())
        if mount is None:
            try:
                self.assert_editable(drive)
            except ApplicationError as error:
                return DriveStatus(drive, DriveState.ERROR, error.message_code)
            return DriveStatus(drive, DriveState.DISCONNECTED)
        if mount.state in (DriveState.CONNECTING, DriveState.DISCONNECTING):
            return DriveStatus(drive, mount.state)
        if not mount.lock.acquire(blocking=False):
            return DriveStatus(drive, mount.state)
        try:
            if mount.process.poll() is not None:
                return DriveStatus(drive, DriveState.ERROR, "drive.process.failed")
            if not mount.mounted or drive.letter in self.available_letters():
                return DriveStatus(drive, DriveState.ERROR, "drive.observe.failed")
            disk = self._call(mount, "vfs/stats").get("diskCache", {})
            if (
                disk.get("outOfSpace")
                or shutil.disk_usage(self._root(drive)).free < 256 * 1024 * 1024
            ):
                return DriveStatus(drive, DriveState.ERROR, "drive.cache.full")
            if disk.get("erroredFiles", 0):
                return DriveStatus(drive, DriveState.ERROR, "drive.transfer.failed")
            return DriveStatus(drive, mount.state, mount.error)
        except (ApplicationError, OSError):
            return DriveStatus(drive, DriveState.ERROR, "drive.control.failed")
        finally:
            mount.lock.release()

    def disconnect(self, drive: S3Drive) -> None:
        mount = self._mounts.get(drive.require_id())
        if mount is None:
            self.assert_editable(drive)
            return
        with mount.lock:
            root = self._root(drive)
            if mount.process.poll() is not None:
                self._release(drive, mount)
                raise _error("cache.pending")
            mount.state = DriveState.DISCONNECTING
            try:
                if mount.mounted:
                    deadline = time.monotonic() + 15
                    while True:
                        disk = self._call(mount, "vfs/stats").get("diskCache")
                        if not isinstance(disk, dict):
                            raise _error("cache.pending")
                        if (
                            disk.get("uploadsQueued") == 0
                            and disk.get("uploadsInProgress") == 0
                            and disk.get("erroredFiles") == 0
                            and not cache_is_dirty(root)
                        ):
                            break
                        if time.monotonic() >= deadline:
                            raise _error("cache.pending")
                        time.sleep(0.25)
                    self._call(mount, "mount/unmount", {"mountPoint": f"{drive.letter}:"})
                    mount.mounted = False
                deadline = time.monotonic() + 10
                while drive.letter not in self.available_letters():
                    if time.monotonic() >= deadline:
                        raise _error("observe.failed")
                    time.sleep(0.1)
                # Writers can race the drain. Check persisted metadata AFTER unmount too.
                if cache_is_dirty(root):
                    raise _error("cache.pending")
                self._call(mount, "core/quit")
                mount.process.wait(timeout=10)
                if cache_is_dirty(root):
                    raise _error("cache.pending")
                (root / "pending.json").unlink(missing_ok=True)
                self._release(drive, mount)
            except (ApplicationError, OSError, subprocess.TimeoutExpired):
                mount.state = DriveState.ERROR
                mount.error = "drive.cache.pending"
                raise _error("cache.pending") from None

    def _release(self, drive: S3Drive, mount: _Mount) -> None:
        mount.job.close()
        mount.lease.close()
        mount.password = ""  # nosec B105 - erase the per-process random authentication secret
        self._mounts.pop(drive.require_id(), None)
