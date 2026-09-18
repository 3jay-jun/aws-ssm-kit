"""AWS Connect command-line entry point."""

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast

from aws_connect.application.execution_context import execution_scope
from aws_connect.application.operations import (
    OperationContext,
    OperationResult,
    OperationState,
    ProgressEvent,
    execute_operation,
)
from aws_connect.bootstrap import (
    ApplicationServices,
    build_application_services,
    build_doctor_service,
)
from aws_connect.domain.errors import ApplicationError, CredentialValidationError
from aws_connect.presentation.cli.ec2 import connection_payload, targets_payload
from aws_connect.presentation.cli.errors import map_error, map_unexpected, render_human_error
from aws_connect.presentation.cli.legacy_import import legacy_import_payload, legacy_preview_payload
from aws_connect.presentation.cli.profile_auth import (
    auth_payload,
    create_profile,
    operation_payload,
    profile_payload,
    profiles_payload,
    refresh_auth,
    render,
)
from aws_connect.presentation.cli.rds import (
    tunnel_connection_payload,
    tunnel_session_payload,
    tunnel_sessions_payload,
)
from aws_connect.presentation.cli.s3 import (
    location_payload,
    locations_payload,
    objects_payload,
    render_upload_progress,
    upload_plan_payload,
    upload_result_payload,
)
from aws_connect.presentation.cli.secrets import secret_payload, secrets_payload
from aws_connect.presentation.cli.system_info import render_doctor


class _ReconfigurableTextStream(Protocol):
    def reconfigure(self, *, encoding: str) -> None: ...


def _configure_utf8_stdio() -> None:
    """Keep CLI output Unicode-safe when Windows redirects streams as cp1252."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            cast(_ReconfigurableTextStream, stream).reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build the stable command-line parser."""

    parser = argparse.ArgumentParser(prog="aws-connect-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    dashboard = subparsers.add_parser("dashboard", help="Read-only dashboard permission checks")
    dashboard.add_argument("--profile", type=int, required=True)
    _output(dashboard)
    doctor = subparsers.add_parser("doctor", help="Check the local application runtime")
    doctor.add_argument("--output", choices=("text", "json"), default="text")
    settings = subparsers.add_parser("settings", help="Manage shared local settings")
    settings_commands = settings.add_subparsers(dest="settings_command", required=True)
    _output(settings_commands.add_parser("show"))
    settings_update = settings_commands.add_parser("update")
    settings_update.add_argument("--log-directory")
    settings_update.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    _output(settings_update)
    _output(settings_commands.add_parser("reset"))
    logs = subparsers.add_parser("logs", help="Export re-masked diagnostic logs")
    logs_commands = logs.add_subparsers(dest="logs_command", required=True)
    logs_show = logs_commands.add_parser("show")
    logs_show.add_argument("--limit", type=int, default=200)
    _output(logs_show)
    logs_export = logs_commands.add_parser("export")
    logs_export.add_argument("destination")
    _output(logs_export)
    legacy = subparsers.add_parser("legacy", help="Preview or apply legacy aws_info.ini")
    legacy_commands = legacy.add_subparsers(dest="legacy_command", required=True)
    for command in ("preview", "import"):
        item = legacy_commands.add_parser(command)
        item.add_argument("source")
        item.add_argument("--profile-name", default="legacy-import")
        item.add_argument("--tunnel-name", default="legacy-rds")
        item.add_argument("--no-tunnel", action="store_true")
        if command == "import":
            item.add_argument("--confirm", action="store_true")
        _output(item)
    profile = subparsers.add_parser("profile", help="Manage protected AWS profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    _output(profile_commands.add_parser("list"))
    show = profile_commands.add_parser("show")
    show.add_argument("selector", nargs="?")
    _output(show)
    create = profile_commands.add_parser("create")
    _profile_fields(create)
    create.add_argument(
        "--credentials-stdin",
        action="store_true",
        help="Read Access Key and Secret Key from two stdin lines",
    )
    _output(create)
    update = profile_commands.add_parser("update")
    update.add_argument("selector")
    _profile_fields(update)
    update.add_argument("--replace-credentials", action="store_true")
    update.add_argument("--credentials-stdin", action="store_true")
    _output(update)
    delete = profile_commands.add_parser("delete")
    delete.add_argument("selector")
    delete.add_argument(
        "--stop-active",
        action="store_true",
        help="Stop every active EC2/RDS connection owned by the profile before deletion",
    )
    _output(delete)
    use = profile_commands.add_parser("use")
    use.add_argument("selector")
    _output(use)

    auth = subparsers.add_parser("auth", help="Validate and refresh AWS authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    for command in ("status", "validate"):
        item = auth_commands.add_parser(command)
        item.add_argument("--profile")
        _output(item)
    refresh = auth_commands.add_parser("refresh")
    refresh.add_argument("--profile")
    refresh.add_argument("--mfa-stdin", action="store_true")
    _output(refresh)
    ec2 = subparsers.add_parser("ec2", help="List and connect to SSM managed EC2")
    ec2_commands = ec2.add_subparsers(dest="ec2_command", required=True)
    ec2_list = ec2_commands.add_parser("list")
    ec2_list.add_argument("--profile")
    ec2_list.add_argument("--mfa-stdin", action="store_true")
    _output(ec2_list)
    connect = ec2_commands.add_parser("connect")
    connect.add_argument("instance_id")
    connect.add_argument("--profile")
    connect.add_argument("--mfa-stdin", action="store_true")
    _output(connect)
    rds = subparsers.add_parser("rds", help="Manage and start saved RDS tunnels")
    rds_commands = rds.add_subparsers(dest="rds_command", required=True)
    rds_session = rds_commands.add_parser("session", help="Manage saved tunnel sessions")
    session_commands = rds_session.add_subparsers(dest="session_command", required=True)
    session_list = session_commands.add_parser("list")
    session_list.add_argument("--profile")
    _output(session_list)
    session_show = session_commands.add_parser("show")
    session_show.add_argument("selector")
    session_show.add_argument("--profile")
    _output(session_show)
    session_create = session_commands.add_parser("create")
    _tunnel_fields(session_create)
    _output(session_create)
    session_update = session_commands.add_parser("update")
    session_update.add_argument("selector")
    _tunnel_fields(session_update)
    _output(session_update)
    session_clone = session_commands.add_parser("clone")
    session_clone.add_argument("selector")
    session_clone.add_argument("--name", required=True)
    session_clone.add_argument("--profile")
    _output(session_clone)
    session_delete = session_commands.add_parser("delete")
    session_delete.add_argument("selector")
    session_delete.add_argument("--profile")
    _output(session_delete)
    tunnel = rds_commands.add_parser("tunnel", help="Run a foreground tunnel")
    tunnel_commands = tunnel.add_subparsers(dest="tunnel_command", required=True)
    tunnel_start = tunnel_commands.add_parser("start")
    tunnel_start.add_argument("selector")
    tunnel_start.add_argument("--profile")
    tunnel_start.add_argument("--target-instance-id")
    tunnel_start.add_argument("--mfa-stdin", action="store_true")
    _output(tunnel_start)
    secrets = subparsers.add_parser("secrets", help="Get one Secrets Manager value safely")
    secrets_commands = secrets.add_subparsers(dest="secrets_command", required=True)
    secrets_list = secrets_commands.add_parser("list")
    secrets_list.add_argument("--profile")
    secrets_list.add_argument("--mfa-stdin", action="store_true")
    _output(secrets_list)
    secrets_get = secrets_commands.add_parser("get")
    secrets_get.add_argument("secret_id", help="Secret name or ARN")
    secrets_get.add_argument("--profile")
    secrets_get.add_argument("--mfa-stdin", action="store_true")
    secrets_get.add_argument(
        "--reveal",
        action="store_true",
        help="Explicitly print raw Secret values to this terminal",
    )
    _output(secrets_get)
    s3 = subparsers.add_parser("s3", help="Manage S3 locations, list objects and upload files")
    s3_commands = s3.add_subparsers(dest="s3_command", required=True)
    location = s3_commands.add_parser("location", help="Manage saved bucket/prefix locations")
    location_commands = location.add_subparsers(dest="location_command", required=True)
    location_list = location_commands.add_parser("list")
    location_list.add_argument("--profile")
    _output(location_list)
    location_show = location_commands.add_parser("show")
    location_show.add_argument("selector")
    location_show.add_argument("--profile")
    _output(location_show)
    for command in ("create", "update"):
        item = location_commands.add_parser(command)
        if command == "update":
            item.add_argument("selector")
        _s3_location_fields(item)
        _output(item)
    location_delete = location_commands.add_parser("delete")
    location_delete.add_argument("selector")
    location_delete.add_argument("--profile")
    _output(location_delete)
    s3_list = s3_commands.add_parser("list")
    s3_list.add_argument("--bucket", required=True)
    s3_list.add_argument("--prefix", default="")
    s3_list.add_argument("--query", help="Search file names recursively below the prefix")
    s3_list.add_argument("--profile")
    s3_list.add_argument("--mfa-stdin", action="store_true")
    _output(s3_list)
    rename = s3_commands.add_parser("rename")
    rename.add_argument("--bucket", required=True)
    rename.add_argument("--key", required=True)
    rename.add_argument("--name", required=True)
    rename.add_argument("--profile")
    rename.add_argument("--mfa-stdin", action="store_true")
    _output(rename)
    upload = s3_commands.add_parser("upload")
    upload.add_argument("sources", nargs="+")
    upload.add_argument("--bucket", required=True)
    upload.add_argument("--prefix", default="")
    upload.add_argument("--profile")
    upload.add_argument("--mfa-stdin", action="store_true")
    upload.add_argument("--confirm", action="store_true", help="Execute the displayed upload plan")
    upload.add_argument(
        "--overwrite", action="store_true", help="Allow confirmed replacement of existing objects"
    )
    _output(upload)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    services: ApplicationServices | None = None,
) -> int:
    """Run the CLI and return a process exit code."""

    _configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        try:
            print(render_doctor(build_doctor_service(), output=args.output))
            return 0
        except ApplicationError as error:
            mapped = map_error(error)
            print(error.message_code, file=sys.stderr)
            return mapped.exit_code
    output = str(args.output)
    app: ApplicationServices | None = services
    try:
        app = app or build_application_services()
        with execution_scope():
            payload = _execute(args, app)
            _record_cli_event(app, args, "succeeded", "cli.command.succeeded")
        print(render(payload, output=output))
        return 0
    except ApplicationError as error:
        if app is not None and not error.execution_logged:
            _record_cli_event(
                app,
                args,
                "failed",
                error.message_code,
                correlation_id=error.correlation_id,
            )
        mapped = map_error(error)
        rendered = (
            json.dumps(mapped.payload, ensure_ascii=False, sort_keys=True)
            if output == "json"
            else render_human_error(error)
        )
        print(rendered, file=sys.stderr)
        return mapped.exit_code
    except KeyboardInterrupt:
        if app is not None:
            _record_cli_event(app, args, "cancelled", "operation.cancelled")
        print("operation.cancelled", file=sys.stderr)
        return 130
    except Exception as error:
        mapped = map_unexpected(error)
        if app is not None:
            _record_cli_event(
                app,
                args,
                "failed",
                "internal.error",
                correlation_id=str(mapped.payload["error"]["correlation_id"]),
            )
        rendered = (
            json.dumps(mapped.payload, ensure_ascii=False, sort_keys=True)
            if output == "json"
            else (f"internal.error (correlation: {mapped.payload['error']['correlation_id']})")
        )
        print(rendered, file=sys.stderr)
        return mapped.exit_code


def _record_cli_event(
    app: ApplicationServices,
    args: argparse.Namespace,
    result: str,
    message_code: str,
    *,
    correlation_id: str | None = None,
) -> None:
    if app.activity_logs is None:
        return
    subcommands = (
        "settings_command",
        "logs_command",
        "legacy_command",
        "profile_command",
        "auth_command",
        "ec2_command",
        "rds_command",
        "session_command",
        "tunnel_command",
        "secrets_command",
        "s3_command",
        "location_command",
    )
    target = ".".join(
        str(getattr(args, name)) for name in subcommands if getattr(args, name, None) is not None
    )
    try:
        app.activity_logs.record(
            feature=f"cli.{args.command}",
            target=target or "command",
            result=result,
            message_code=message_code,
            correlation_id=correlation_id,
        )
    except Exception:
        # Audit logging is best effort, but its failure is still visible and must
        # never replace the outcome of the user's requested command.
        print("logs.write.failed", file=sys.stderr)


def _execute(args: argparse.Namespace, app: ApplicationServices) -> dict[str, Any]:
    if args.command == "dashboard":
        from dataclasses import asdict

        if app.dashboard is None:
            raise RuntimeError("Dashboard service is not configured")
        dashboard_result = asdict(app.dashboard.check_permissions(args.profile))
        dashboard_result["checked_at"] = dashboard_result["checked_at"].isoformat()
        return dashboard_result
    if args.command == "settings":
        if app.settings is None:
            raise RuntimeError("Settings service is not configured")
        from pathlib import Path

        from aws_connect.application.settings_service import UpdateSettingsRequest

        if args.settings_command == "show":
            value = app.settings.get()
        elif args.settings_command == "update":
            value = app.settings.update(
                UpdateSettingsRequest(
                    Path(args.log_directory) if args.log_directory else None,
                    args.log_level,
                )
            )
        else:
            value = app.settings.reset()
        return {"log_directory": str(value.log_directory), "log_level": value.log_level.value}
    if args.command == "logs":
        if args.logs_command == "show":
            if app.activity_logs is None:
                raise RuntimeError("Activity log service is not configured")
            return {
                "log_entries": [
                    {
                        "occurred_at": entry.occurred_at.isoformat(),
                        "feature": entry.feature,
                        "target": entry.target,
                        "result": entry.result,
                        "message_code": entry.message_code,
                        "correlation_id": entry.correlation_id,
                        "operation_id": entry.operation_id,
                    }
                    for entry in app.activity_logs.recent(args.limit)
                ]
            }
        if app.diagnostic_logs is None:
            raise RuntimeError("Diagnostic log service is not configured")
        from pathlib import Path

        export_result = app.diagnostic_logs.export(Path(args.destination))
        return {
            "path": str(export_result.path),
            "files_exported": export_result.files_exported,
        }
    if args.command == "legacy":
        if app.legacy_import is None:
            raise RuntimeError("Legacy import service is not configured")
        from pathlib import Path

        from aws_connect.application.legacy_import import LegacyImportRequest

        legacy_request = LegacyImportRequest(
            Path(args.source),
            args.profile_name,
            args.tunnel_name,
            not args.no_tunnel,
            bool(getattr(args, "confirm", False)),
        )
        if args.legacy_command == "preview":
            return legacy_preview_payload(app.legacy_import.preview(legacy_request))
        return legacy_import_payload(app.legacy_import.apply(legacy_request))
    if args.command == "profile":
        command = args.profile_command
        if command == "list":
            return profiles_payload(app.profiles.list())
        if command == "show":
            return profile_payload(app.profiles.show(args.selector))
        if command == "create":
            access_key, secret_key = _read_credentials(args.credentials_stdin)
            return profile_payload(
                create_profile(
                    app.profiles,
                    name=args.name,
                    region=args.region,
                    account_id=args.account_id,
                    user_id=args.user_id,
                    mfa_arn=args.mfa_arn,
                    mfa_enabled=args.mfa,
                    access_key=access_key,
                    secret_key=secret_key,
                )
            )
        if command == "update":
            current = app.profiles.resolve(args.selector)
            updated_access_key: str | None = None
            updated_secret_key: str | None = None
            if args.replace_credentials:
                updated_access_key, updated_secret_key = _read_credentials(args.credentials_stdin)
            from aws_connect.application.profile_service import SaveProfileRequest

            return profile_payload(
                app.profiles.update(
                    SaveProfileRequest(
                        profile_id=current.id,
                        name=args.name,
                        region=args.region,
                        account_id=args.account_id,
                        user_id=args.user_id,
                        mfa_arn=args.mfa_arn,
                        mfa_enabled=args.mfa,
                        access_key=updated_access_key,
                        secret_key=updated_secret_key,
                    )
                )
            )
        if command == "delete":
            if app.connection_lifecycle is None:
                raise RuntimeError("Profile connection lifecycle service is not configured")
            app.connection_lifecycle.delete(
                args.selector,
                stop_active=bool(args.stop_active),
            )
            return {"deleted": True}
        if command == "use":
            return profile_payload(app.profiles.use(args.selector))
    if args.command == "auth":
        if args.auth_command == "status":
            return auth_payload(app.authentication.status(args.profile))
        if args.auth_command == "validate":
            return auth_payload(app.authentication.validate(args.profile))
        if args.auth_command == "refresh":
            mfa_code = _read_mfa(args.mfa_stdin)
            result = refresh_auth(app.operations, args.profile, mfa_code)
            if result.error:
                raise result.error
            return operation_payload(result)
    if args.command == "ec2":
        ec2 = app.ec2
        if ec2 is None:
            raise RuntimeError("EC2 service is not configured")
        if args.ec2_command == "list":
            return targets_payload(
                _run_authenticated(
                    app,
                    args.profile,
                    lambda: ec2.list_targets(args.profile),
                    args.mfa_stdin,
                )
            )
        if args.ec2_command == "connect":
            return connection_payload(
                _run_authenticated_long(
                    app,
                    args.profile,
                    lambda context: ec2.connect(args.instance_id, args.profile, context=context),
                    args.mfa_stdin,
                )
            )
    if args.command == "rds":
        if app.tunnel_sessions is None or app.rds_tunnels is None:
            raise RuntimeError("RDS tunnel services are not configured")
        if args.rds_command == "session":
            command = args.session_command
            if command == "list":
                return tunnel_sessions_payload(app.tunnel_sessions.list(args.profile))
            if command == "show":
                return tunnel_session_payload(app.tunnel_sessions.show(args.selector, args.profile))
            if command in ("create", "update"):
                from aws_connect.application.rds_tunnel_service import SaveTunnelSessionRequest
                from aws_connect.domain.tunnel_session import TargetMode

                tunnel_request = SaveTunnelSessionRequest(
                    profile=args.profile,
                    name=args.name,
                    host=args.host,
                    remote_port=args.remote_port,
                    local_port=args.local_port,
                    target_mode=TargetMode(args.target_mode),
                    target_instance_id=args.target_instance_id,
                    tunnel_id=(
                        app.tunnel_sessions.show(args.selector, args.profile).require_id()
                        if command == "update"
                        else None
                    ),
                )
                saved = (
                    app.tunnel_sessions.create(tunnel_request)
                    if command == "create"
                    else app.tunnel_sessions.update(tunnel_request)
                )
                return tunnel_session_payload(saved)
            if command == "clone":
                return tunnel_session_payload(
                    app.tunnel_sessions.clone(args.selector, args.name, args.profile)
                )
            if command == "delete":
                app.tunnel_sessions.delete(args.selector, args.profile)
                return {"deleted": True}
        if args.rds_command == "tunnel" and args.tunnel_command == "start":
            from aws_connect.application.rds_tunnel_service import StartTunnelRequest

            started = _complete_authenticated_result(
                app.rds_tunnels.start(
                    StartTunnelRequest(args.selector, args.profile, args.target_instance_id)
                ),
                app.rds_tunnels.resume,
                app.rds_tunnels.cancel,
                args.mfa_stdin,
            )
            if started.error:
                raise started.error
            if started.state is OperationState.CANCELLED:
                raise KeyboardInterrupt
            if started.value is None:
                raise RuntimeError("Tunnel operation did not return a result")
            return tunnel_connection_payload(started.value)
    if args.command == "secrets":
        secret_service = app.secrets  # pragma: allowlist secret
        if secret_service is None:
            raise RuntimeError("Secrets service is not configured")
        if args.secrets_command == "list":  # pragma: allowlist secret
            return secrets_payload(
                _run_authenticated(
                    app,
                    args.profile,
                    lambda: secret_service.list(args.profile),  # pragma: allowlist secret
                    args.mfa_stdin,
                )
            )
        return secret_payload(
            _run_authenticated(
                app,
                args.profile,
                lambda: secret_service.get(  # pragma: allowlist secret
                    args.secret_id, args.profile
                ),
                args.mfa_stdin,
            ),
            reveal=bool(args.reveal),
        )
    if args.command == "s3":
        s3 = app.s3
        if app.s3_locations is None or s3 is None:
            raise RuntimeError("S3 services are not configured")
        if args.s3_command == "location":
            command = args.location_command
            if command == "list":
                return locations_payload(app.s3_locations.list(args.profile))
            selector = _selector(getattr(args, "selector", None))
            if command == "show":
                return location_payload(app.s3_locations.show(selector, args.profile))
            if command in ("create", "update"):
                from aws_connect.application.s3_service import SaveS3LocationRequest

                location_id = (
                    app.s3_locations.show(selector, args.profile).require_id()
                    if command == "update"
                    else None
                )
                request = SaveS3LocationRequest(
                    args.profile, args.name, args.bucket, args.prefix, location_id
                )
                saved_location = (
                    app.s3_locations.create(request)
                    if command == "create"
                    else app.s3_locations.update(request)
                )
                return location_payload(saved_location)
            app.s3_locations.delete(selector, args.profile)
            return {"deleted": True}
        if args.s3_command == "rename":
            return {
                "key": _run_authenticated(
                    app,
                    args.profile,
                    lambda: s3.rename_object(args.bucket, args.key, args.name, args.profile),
                    args.mfa_stdin,
                )
            }
        if args.s3_command == "list":
            return objects_payload(
                args.bucket,
                args.prefix,
                _run_authenticated(
                    app,
                    args.profile,
                    lambda: s3.list_objects(
                        args.bucket,
                        args.prefix,
                        args.profile,
                        **({"query": args.query} if args.query is not None else {}),
                    ),
                    args.mfa_stdin,
                ),
            )
        from pathlib import Path

        plan = _run_authenticated(
            app,
            args.profile,
            lambda: s3.prepare_upload(
                [Path(value) for value in args.sources], args.bucket, args.prefix, args.profile
            ),
            args.mfa_stdin,
        )
        preview = upload_plan_payload(plan)
        if not args.confirm:
            return preview
        progress: list[ProgressEvent] = []

        def report_progress(event: ProgressEvent) -> None:
            progress.append(event)
            if args.output == "text":
                print(render_upload_progress(event), file=sys.stderr, flush=True)

        upload_result = _run_authenticated_long_result(
            app,
            args.profile,
            lambda context: s3.upload(plan, overwrite=bool(args.overwrite), context=context),
            args.mfa_stdin,
            progress=report_progress,
        )
        if upload_result.value is None:
            raise RuntimeError("S3 upload did not return a summary")
        return upload_result_payload(
            upload_result.value, upload_result.operation_id, preview, progress
        )
    raise RuntimeError("Unreachable CLI command")


def _run_authenticated[T](
    app: ApplicationServices,
    selector: str | int | None,
    action: Callable[[], T],
    mfa_stdin: bool,
) -> T:
    """Render the shared resumable operation contract for an interactive CLI."""

    coordinator = app.authenticated_operations
    if coordinator is None:  # Backward-compatible seam for narrow presentation fakes.
        return action()
    result = _complete_authenticated_result(
        coordinator.start(selector, action),
        coordinator.resume,
        coordinator.cancel,
        mfa_stdin,
    )
    if result.value is None:
        raise RuntimeError("Authenticated operation did not return a result")
    return result.value


def _run_authenticated_long[T](
    app: ApplicationServices,
    selector: str | int | None,
    action: Callable[[OperationContext], T],
    mfa_stdin: bool,
    *,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> T:
    """Run a foreground operation with one shared identity/progress/cancel context."""

    result = _run_authenticated_long_result(app, selector, action, mfa_stdin, progress=progress)
    if result.value is None:
        raise RuntimeError("Authenticated operation did not return a result")
    return result.value


def _run_authenticated_long_result[T](
    app: ApplicationServices,
    selector: str | int | None,
    action: Callable[[OperationContext], T],
    mfa_stdin: bool,
    *,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> OperationResult[T]:
    """Complete a long operation while retaining its single final operation ID."""

    coordinator = app.authenticated_operations
    if coordinator is None:  # Backward-compatible seam for narrow presentation fakes.
        result = execute_operation(action, OperationContext(progress=progress))
    else:
        result = _complete_authenticated_result(
            coordinator.start_long(selector, action, OperationContext(progress=progress)),
            coordinator.resume,
            coordinator.cancel,
            mfa_stdin,
        )
    if result.state is OperationState.CANCELLED:
        raise KeyboardInterrupt
    if result.error is not None:
        raise result.error
    return result


def _complete_authenticated_result[T](
    result: OperationResult[T],
    resume: Callable[[str, str | None], OperationResult[T]],
    cancel: Callable[[str], None],
    mfa_stdin: bool,
) -> OperationResult[T]:
    """Complete one MFA challenge with consistent interactive CLI semantics."""

    if result.state is OperationState.MFA_REQUIRED:
        if not mfa_stdin and not sys.stdin.isatty():
            cancel(result.operation_id)
            raise CredentialValidationError(
                "auth.mfa_required",
                "MFA input requires an interactive terminal or --mfa-stdin",
            )
        result = resume(result.operation_id, _read_mfa(mfa_stdin))
    if result.state is OperationState.CANCELLED:
        raise KeyboardInterrupt
    if result.error is not None:
        raise result.error
    return result


def _profile_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--mfa-arn")
    parser.add_argument(
        "--mfa",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use MFA for temporary sessions (default for new profiles); use --no-mfa to disable",
    )


def _tunnel_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile")
    parser.add_argument("--name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-port", required=True, type=int)
    parser.add_argument("--local-port", required=True, type=int)
    parser.add_argument("--target-mode", choices=("fixed", "select"), required=True)
    parser.add_argument("--target-instance-id")


def _output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=("text", "json"), default="text")


def _s3_location_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile")
    parser.add_argument("--name", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="")


def _selector(value: str | None) -> str | int:
    if value is None:
        raise RuntimeError("Selector is required")
    return int(value) if value.isdigit() else value


def _read_credentials(from_stdin: bool) -> tuple[str, str]:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n"), sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass("Access Key: "), getpass.getpass("Secret Access Key: ")


def _read_mfa(from_stdin: bool) -> str | None:
    try:
        return sys.stdin.readline().rstrip("\r\n") if from_stdin else getpass.getpass("MFA code: ")
    except (EOFError, KeyboardInterrupt):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
