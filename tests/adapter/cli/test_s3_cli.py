import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from aws_connect.application.operations import (
    MfaChallenge,
    OperationCancelled,
    OperationResult,
    OperationState,
)
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import UploadItem, UploadPlan, UploadSummary
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import main
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_location import S3Location


def _services() -> ApplicationServices:
    return ApplicationServices(Mock(), Mock(), Mock(), Mock(), s3_locations=Mock(), s3=Mock())


def test_location_crud_and_direct_list_do_not_require_bucket_catalog(capsys) -> None:
    app = _services()
    location = S3Location(3, 7, "reports", "test-upload-bucket", "reports/")
    app.s3_locations.list.return_value = [location]
    app.s3.list_objects.return_value = [S3Object("reports/2026/", 0, None, True)]

    assert (
        main(["s3", "location", "list", "--profile", "dev", "--output", "json"], services=app) == 0
    )
    assert json.loads(capsys.readouterr().out)["s3_locations"][0]["bucket"] == "test-upload-bucket"
    assert (
        main(
            [
                "s3",
                "list",
                "--bucket",
                "test-upload-bucket",
                "--prefix",
                "reports/",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["objects"][0]["is_prefix"] is True
    app.s3.list_objects.assert_called_with("test-upload-bucket", "reports/", None)


def test_upload_is_preview_only_until_explicit_confirmation(capsys, tmp_path: Path) -> None:
    app = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        (UploadItem(source, "test-upload-bucket", "incoming/report.txt", 7, True),),
    )
    app.s3.prepare_upload.return_value = plan

    argv = [
        "s3",
        "upload",
        str(source),
        "--bucket",
        "test-upload-bucket",
        "--prefix",
        "incoming",
        "--output",
        "json",
    ]
    assert main(argv, services=app) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (
        preview["upload_preview"][0]["target_uri"] == "s3://test-upload-bucket/incoming/report.txt"
    )
    assert preview["requires_overwrite_confirmation"] is True
    app.s3.upload.assert_not_called()

    def upload(_plan, *, overwrite, context):
        assert overwrite
        context.report(
            "uploading",
            "s3.upload.progress",
            completed=7,
            total=7,
            target=plan.items[0].uri,
        )
        return UploadSummary((plan.items[0].uri,), 7)

    app.s3.upload.side_effect = upload
    assert main([*argv[:-2], "--confirm", "--overwrite", "--output", "json"], services=app) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["state"] == "SUCCEEDED"
    assert completed["progress"] == [
        {
            "completed": 7,
            "message_code": "s3.upload.progress",
            "operation_id": completed["operation_id"],
            "phase": "uploading",
            "target_uri": "s3://test-upload-bucket/incoming/report.txt",
            "total": 7,
        }
    ]
    app.s3.upload.assert_called_once()


def test_human_upload_streams_progress_to_stderr_before_completion(capsys, tmp_path: Path) -> None:
    app = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    item = UploadItem(source, "test-upload-bucket", "incoming/report.txt", 7, False)
    plan = UploadPlan(7, "ap-northeast-2", (item,))
    app.s3.prepare_upload.return_value = plan
    observed_during_upload: list[str] = []

    def upload(_plan, *, overwrite, context):
        assert not overwrite
        context.report("uploading", "s3.upload.progress", completed=4, total=7, target=item.uri)
        observed_during_upload.append(capsys.readouterr().err)
        context.report("uploading", "s3.upload.progress", completed=7, total=7, target=item.uri)
        return UploadSummary((item.uri,), 7)

    app.s3.upload.side_effect = upload

    assert (
        main(
            [
                "s3",
                "upload",
                str(source),
                "--bucket",
                "test-upload-bucket",
                "--prefix",
                "incoming",
                "--confirm",
            ],
            services=app,
        )
        == 0
    )

    assert observed_during_upload == [
        "s3.upload.progress s3://test-upload-bucket/incoming/report.txt: 4/7 bytes\n"
    ]
    captured = capsys.readouterr()
    assert "7/7 bytes" in captured.err
    assert "SUCCEEDED: 7 bytes" in captured.out


def test_json_upload_keeps_progress_off_streaming_stderr(capsys, tmp_path: Path) -> None:
    app = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    item = UploadItem(source, "test-upload-bucket", "report.txt", 7, False)
    plan = UploadPlan(7, "ap-northeast-2", (item,))
    app.s3.prepare_upload.return_value = plan

    def upload(_plan, *, overwrite, context):
        context.report("uploading", "s3.upload.progress", completed=7, total=7, target=item.uri)
        return UploadSummary((item.uri,), 7)

    app.s3.upload.side_effect = upload
    exit_code = main(
        [
            "s3",
            "upload",
            str(source),
            "--bucket",
            "test-upload-bucket",
            "--confirm",
            "--output",
            "json",
        ],
        services=app,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["progress"][0]["completed"] == 7


def test_overwrite_refusal_uses_stable_typed_exit(capsys, tmp_path: Path) -> None:
    app = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7, "ap-northeast-2", (UploadItem(source, "test-upload-bucket", "report.txt", 7, True),)
    )
    app.s3.prepare_upload.return_value = plan
    app.s3.upload.side_effect = ConfigurationError(
        "s3.upload.overwrite_confirmation_required", "private file content must not appear"
    )

    exit_code = main(
        [
            "s3",
            "upload",
            str(source),
            "--bucket",
            "test-upload-bucket",
            "--confirm",
            "--output",
            "json",
        ],
        services=app,
    )

    assert exit_code == 10
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "s3.upload.overwrite_confirmation_required"
    assert "private file content" not in json.dumps(error)


def test_cancelled_upload_uses_standard_cli_cancellation_exit(capsys, tmp_path: Path) -> None:
    app = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7, "ap-northeast-2", (UploadItem(source, "test-upload-bucket", "report.txt", 7, False),)
    )
    app.s3.prepare_upload.return_value = plan
    app.s3.upload.side_effect = OperationCancelled

    exit_code = main(
        [
            "s3",
            "upload",
            str(source),
            "--bucket",
            "test-upload-bucket",
            "--confirm",
        ],
        services=app,
    )

    assert exit_code == 130
    assert capsys.readouterr().err.strip() == "operation.cancelled"


def test_upload_mfa_resume_returns_one_flat_operation_result(monkeypatch, capsys, tmp_path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    item = UploadItem(source, "test-upload-bucket", "report.txt", 7, False)
    plan = UploadPlan(7, "ap-northeast-2", (item,))
    upload = UploadSummary((item.uri,), 7)
    coordinator = Mock()
    app = ApplicationServices(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        s3_locations=Mock(),
        s3=Mock(),
        authenticated_operations=coordinator,
    )
    coordinator.start.return_value = OperationResult("prepare-op", OperationState.SUCCEEDED, plan)
    coordinator.start_long.return_value = OperationResult(
        "auth-op",
        OperationState.MFA_REQUIRED,
        challenge=MfaChallenge(
            "auth-op",
            7,
            "arn:aws:iam::123456789012:mfa/developer",
            datetime.now(UTC) + timedelta(minutes=5),
        ),
    )
    coordinator.resume.return_value = OperationResult("auth-op", OperationState.SUCCEEDED, upload)
    monkeypatch.setattr("sys.stdin", io.StringIO("123456\n"))

    assert (
        main(
            [
                "s3",
                "upload",
                str(source),
                "--bucket",
                "test-upload-bucket",
                "--confirm",
                "--mfa-stdin",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "SUCCEEDED"
    assert payload["operation_id"] == "auth-op"
    assert payload["uploaded"] == ["s3://test-upload-bucket/report.txt"]
    coordinator.start_long.assert_called_once()
    coordinator.resume.assert_called_once_with("auth-op", "123456")
