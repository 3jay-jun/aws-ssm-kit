"""Saved drive commands and a foreground session; never starts a detached service."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from aws_connect.application.s3_drive_service import S3DriveService
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.domain.s3_drive import S3Drive


def add_drive_parser(commands: Any) -> None:
    parser = commands.add_parser("s3-drive", help="Saved S3 drives and foreground mount session")
    actions = parser.add_subparsers(dest="drive_command", required=True)
    for name in ("list", "status", "save", "delete", "run", "connect"):
        action = actions.add_parser(name)
        action.add_argument("--output", choices=("human", "json"), default="human")
        if name in ("list", "save"):
            action.add_argument("--profile", required=name == "save")
        if name in ("delete", "connect"):
            action.add_argument("id", type=int)
        if name in ("run", "connect"):
            action.add_argument("--mfa-stdin", action="store_true")
        if name == "delete":
            action.add_argument("--confirm", action="store_true", required=True)
        if name == "save":
            action.add_argument("--id", type=int)
            for field in ("name", "bucket", "letter"):
                action.add_argument("--" + field, required=True)
            action.add_argument("--prefix", default="")
            action.add_argument("--local-prefix", default="")


def drive_payload(service: S3DriveService, profile_id: int | None = None) -> dict[str, Any]:
    return {"s3_drives": [asdict(item) for item in service.list(profile_id)]}


def execute_drive(
    args: argparse.Namespace,
    service: S3DriveService,
    resolve: Callable[[str | int | None], int],
    connect: Callable[[int], object],
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> dict[str, Any]:
    command = args.drive_command
    if command in ("list", "status"):
        profile = getattr(args, "profile", None)
        return drive_payload(service, resolve(profile) if profile else None)
    if command == "save":
        saved = service.save(
            S3Drive(
                args.id,
                resolve(args.profile),
                args.name,
                args.bucket,
                args.prefix,
                args.letter,
                args.local_prefix,
            )
        )
        return {"s3_drive": asdict(saved)}
    if command == "delete":
        service.delete(args.id)
        return {"deleted": True}
    if command == "connect":
        connect(args.id)
    write("S3 drive session: connect ID | disconnect ID | status | quit. Keep this process open.")
    while True:
        try:
            line = read("s3-drive> ").strip().split()
            if not line:
                continue
            if line == ["quit"]:
                service.shutdown()
                return {"closed": True}
            if line == ["status"]:
                for status in service.list():
                    write(
                        f"{status.drive.id}: {status.drive.windows_path} {status.state} "
                        f"{status.message_code}"
                    )
            elif len(line) == 2 and line[0] in {"connect", "disconnect"}:
                try:
                    drive_id = int(line[1])
                except ValueError:
                    raise ConfigurationError(
                        "drive.id.invalid", "Enter a numeric drive ID"
                    ) from None
                if line[0] == "connect":
                    connect(drive_id)
                else:
                    service.disconnect(drive_id)
            else:
                write("Use connect ID, disconnect ID, status, or quit.")
        except (KeyboardInterrupt, EOFError) as interruption:
            try:
                service.shutdown()
            except ApplicationError as error:
                write(f"{error.message_code}: close open files and retry quit; cache retained.")
                if isinstance(interruption, EOFError):
                    raise error
                continue
            return {"closed": True}
        except ApplicationError as error:
            write(f"{error.message_code}: operation failed; drive/cache retained.")
