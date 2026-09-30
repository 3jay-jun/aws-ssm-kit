"""Drive management shared by CLI and GUI, including destructive lifecycle gates."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import RLock

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.operations import OperationContext
from aws_connect.application.profile_service import ProfileService
from aws_connect.application.s3_drive_ports import S3DriveMount, S3DriveStore
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive


class S3DriveService:
    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        store: S3DriveStore,
        mount: S3DriveMount,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._store = store
        self._mount = mount
        self._lock = RLock()
        self._closing = False

    def list(self, profile_id: int | None = None) -> list[DriveStatus]:
        return [self._mount.status(drive) for drive in self._store.list_drives(profile_id)]

    def get(self, drive_id: int) -> S3Drive:
        value = self._store.get_drive(drive_id)
        if value is None:
            raise ConfigurationError("drive.not_found", "The saved drive no longer exists")
        return value

    def available_letters(self) -> set[str]:
        return self._mount.available_letters()

    def save(self, drive: S3Drive) -> S3Drive:
        with self._lock:
            self._profiles.resolve(drive.profile_id)
            if drive.id is not None:
                previous = self.get(drive.id)
                if previous.profile_id != drive.profile_id:
                    raise ConfigurationError("drive.owner.invalid", "Drive ownership cannot change")
                self._mount.assert_editable(previous)
            if drive.letter not in self.available_letters():
                raise ConfigurationError("drive.letter.busy", "The selected drive letter is in use")
            return self._store.save_drive(drive)

    def connect(self, drive_id: int, context: OperationContext) -> DriveStatus:
        with self._lock:
            if self._closing:
                raise ConfigurationError("drive.closing", "Application shutdown is in progress")
            drive = self.get(drive_id)
            profile = self._profiles.resolve(drive.profile_id)
            credentials = self._sessions.require_credentials(drive.profile_id)
            context.raise_if_cancelled()
            self._mount.connect(drive, credentials, profile.region, context)
            return self._mount.status(drive)

    def clone(self, drive_id: int, name: str) -> S3Drive:
        with self._lock:
            source = self.get(drive_id)
            reserved = {item.letter for item in self._store.list_drives()}
            available = self.available_letters() - reserved
            if not available:
                raise ConfigurationError("drive.letter.busy", "No unused drive letter for clone")
            return self.save(replace(source, id=None, name=name, letter=sorted(available)[0]))

    def rename(self, drive_id: int, name: str) -> S3Drive:
        with self._lock:
            return self.save(replace(self.get(drive_id), name=name))

    def connected_path(self, drive_id: int) -> str:
        with self._lock:
            drive = self.get(drive_id)
            if self._mount.status(drive).state is not DriveState.CONNECTED:
                raise ConfigurationError(
                    "drive.not_connected", "Connect the mount before opening it"
                )
            return drive.windows_path

    def disconnect(self, drive_id: int) -> None:
        with self._lock:
            self._mount.disconnect(self.get(drive_id))

    def delete(self, drive_id: int) -> None:
        with self._lock:
            drive = self.get(drive_id)
            self._mount.disconnect(drive)
            self._mount.assert_editable(drive)
            self._store.delete_drive(drive_id)

    @contextmanager
    def profile_deletion(self, profile_id: int) -> Iterator[None]:
        """Hold the connect gate through profile deletion, even with no active mounts."""
        with self._lock:
            self._stop(profile_id)
            for drive in self._store.list_drives(profile_id):
                self._mount.assert_editable(drive)
            yield

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            try:
                self._stop(None)
            except ApplicationError:
                self._closing = False
                raise

    def _stop(self, profile_id: int | None) -> None:
        first_error: ApplicationError | None = None
        for drive in self._store.list_drives(profile_id):
            try:
                self._mount.disconnect(drive)
            except ApplicationError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
