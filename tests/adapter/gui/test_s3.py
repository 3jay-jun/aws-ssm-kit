from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox, QToolButton

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import (
    CancellationToken,
    OperationResult,
    OperationState,
)
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import UploadItem, UploadPlan, UploadSummary
from aws_connect.domain.errors import (
    ApplicationError,
    AwsPermissionError,
    CredentialValidationError,
)
from aws_connect.domain.s3_location import S3Location
from aws_connect.presentation.gui.s3 import S3Page, _confirm_upload
from aws_connect.presentation.gui.tasks import ApplicationTask


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class Handle:
    def __init__(self, token: CancellationToken) -> None:
        self.token = token

    def cancel(self) -> None:
        self.token.cancel()


class ImmediateRunner:
    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
    ) -> Handle:
        token = CancellationToken()
        try:
            on_success(operation())
        except ApplicationError as error:
            on_error(error)
        return Handle(token)

    def submit_cancellable(self, operation, on_success, on_error, on_progress) -> Handle:
        token = CancellationToken()
        try:
            on_success(operation(token, on_progress))
        except ApplicationError as error:
            on_error(error)
        return Handle(token)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 11, tzinfo=UTC)


class RefreshCoordinator:
    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.resume_calls: list[tuple[str, str | None]] = []

    def start_refresh(self, selector=None, *, discard_cached_session=False):
        assert discard_cached_session
        from aws_connect.application.operations import MfaChallenge

        challenge = MfaChallenge(
            "auth-op",
            int(selector),
            "arn:aws:iam::123456789012:mfa/developer",
            self.clock.now() + timedelta(minutes=5),
        )
        return OperationResult("auth-op", OperationState.MFA_REQUIRED, challenge=challenge)

    def resume(self, operation_id, code):
        self.resume_calls.append((operation_id, code))
        if code is None:
            return OperationResult(operation_id, OperationState.CANCELLED)
        return OperationResult(operation_id, OperationState.SUCCEEDED, object())

    def cancel(self, _operation_id):
        return None


def test_s3_page_browses_direct_location_and_updates_progress(tmp_path: Path) -> None:
    _app()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    locations = Mock()
    locations.list.return_value = [S3Location(1, 7, "reports", "test-upload-bucket", "incoming/")]
    s3 = Mock()
    s3.list_buckets.return_value = []
    s3.list_objects.return_value = [S3Object("incoming/report.txt", 7, None)]
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        (UploadItem(source, "test-upload-bucket", "incoming/report.txt", 7, False),),
    )
    s3.prepare_upload.return_value = plan

    def upload(_plan, *, overwrite, context):
        assert not overwrite
        assert not context.cancellation.is_cancellation_requested
        context.report(
            "uploading",
            "s3.upload.progress",
            completed=7,
            total=7,
            target=plan.items[0].uri,
        )
        return UploadSummary((plan.items[0].uri,), 7)

    s3.upload.side_effect = upload
    page = S3Page(locations, s3, ImmediateRunner(), lambda _parent, _plan: (True, False))  # type: ignore[arg-type]
    page.set_profile(7)
    page.location_list.setCurrentRow(0)
    page.list_objects()
    page.set_files([source])
    page.prepare_upload()

    assert page.bucket.text() == "test-upload-bucket"
    assert page.objects.item(0, 0).text() == "report.txt"
    assert page.objects.item(0, 1).text() == "text/plain"
    assert page.objects.columnCount() == 4
    assert page.progress.value() == 100
    assert page.upload_status.text() == "업로드 중: s3://test-upload-bucket/incoming/report.txt"
    s3.prepare_upload.assert_called_once()
    s3.upload.assert_called_once()


def test_s3_page_matches_mockup_header_toolbar_card_and_drop_order() -> None:
    _app()
    page = S3Page(Mock(), Mock(), ImmediateRunner())  # type: ignore[arg-type]
    card = page.findChild(QFrame, "s3_browser_card")

    assert page.findChild(QLabel, "page_title").text() == "S3 파일"
    assert page.findChild(QFrame, "s3_toolbar") is not None
    assert card is not None
    assert card.layout().indexOf(page.breadcrumb) < card.layout().indexOf(page.objects)
    assert card.layout().indexOf(page.objects) < card.layout().indexOf(page.drop_zone)
    assert page.upload.text() == "파일 업로드"
    assert page.findChild(type(page.upload), "s3_bucket_catalog_load") is None
    assert page.findChild(QFrame, "s3_saved_locations_panel") is not None


def test_bucket_catalog_success_selects_and_permission_denial_falls_back() -> None:
    _app()
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = ["alpha-bucket", "zeta-bucket"]
    s3.list_objects.return_value = []
    page = S3Page(locations, s3, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(7)
    page.bucket.setText("zeta-bucket")

    page.load_buckets()

    assert not page.bucket_catalog.isHidden()
    assert page.bucket.isHidden()
    assert page.bucket_catalog.currentText() == "zeta-bucket"
    page.bucket_catalog.setCurrentText("alpha-bucket")
    page.list_objects()
    s3.list_objects.assert_called_with("alpha-bucket", "", 7)

    notices: list[str] = []
    page.notice_raised.connect(notices.append)
    s3.list_buckets.side_effect = AwsPermissionError(
        "aws.permission.denied",
        "denied",
        aws_service="s3",
        aws_action="ListBuckets",
    )
    page.load_buckets()

    assert page.bucket_catalog.isHidden()
    assert not page.bucket.isHidden()
    assert "직접 입력" in notices[-1]


def test_breadcrumb_is_clickable_and_table_projects_folder_and_mime_without_head() -> None:
    _app()
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    s3.list_objects.return_value = [
        S3Object("reports/2026/archive/", 0, None, True),
        S3Object("reports/2026/data.json", 10, datetime(2026, 9, 1, tzinfo=UTC)),
    ]
    page = S3Page(locations, s3, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(7)
    page.bucket.setText("test-upload-bucket")
    page.prefix.setText("reports/2026/")

    page.list_objects()

    assert page.objects.horizontalHeaderItem(0).text() == "이름"
    assert page.objects.horizontalHeaderItem(1).text() == "유형"
    assert page.objects.item(0, 0).text() == "archive"
    assert page.objects.item(0, 1).text() == "폴더"
    assert page.objects.item(0, 2).text() == ""
    assert page.objects.item(1, 0).text() == "data.json"
    assert page.objects.item(1, 1).text() == "application/json"
    assert not hasattr(s3, "head_object") or not s3.head_object.called
    segment = page.breadcrumb.findChildren(QToolButton)[1]
    segment.click()
    assert page.prefix.text() == "reports/"


def test_selected_file_delete_requires_confirmation_and_refreshes_listing() -> None:
    _app()
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    selected = S3Object("reports/file.txt", 10, None)
    s3.list_objects.return_value = [selected]
    page = S3Page(
        locations,
        s3,
        ImmediateRunner(),  # type: ignore[arg-type]
        confirm_delete=lambda _parent, item: item is selected,
    )
    page.set_profile(7)
    page.bucket.setText("test-upload-bucket")
    page.list_objects()
    page.objects.selectRow(0)

    page.delete_object_button.click()

    s3.delete_object.assert_called_once_with("test-upload-bucket", "reports/file.txt", 7)
    assert s3.list_objects.call_count >= 2


def test_profile_change_cancels_owned_upload_and_clears_files(tmp_path: Path) -> None:
    _app()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    page = S3Page(locations, s3, ImmediateRunner())  # type: ignore[arg-type]
    token = CancellationToken()
    page._upload_task = Handle(token)  # type: ignore[assignment]
    page._upload_in_progress = True
    page.set_files([source])

    page.set_profile(None)

    assert token.is_cancellation_requested
    assert page._selected_files == []
    assert page.location_list.count() == 0


def test_prefix_navigation_and_existing_object_require_separate_overwrite_consent(
    monkeypatch, tmp_path: Path
) -> None:
    _app()
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    s3.list_objects.return_value = []
    page = S3Page(locations, s3, ImmediateRunner())  # type: ignore[arg-type]
    page._profile_id = 7
    page.bucket.setText("test-upload-bucket")
    page.prefix.setText("reports/2026/")

    page.navigate_up()

    assert page.prefix.text() == "reports/"
    s3.list_objects.assert_called_with("test-upload-bucket", "reports/", 7)

    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        (UploadItem(source, "test-upload-bucket", "reports/report.txt", 7, True),),
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.StandardButton.No
    )

    assert _confirm_upload(page, plan) == (False, True)


def test_pre_cancelled_cancellable_task_runs_cleanup_contract() -> None:
    token = CancellationToken()
    token.cancel()
    called: list[bool] = []
    task = ApplicationTask(
        lambda: called.append(token.is_cancellation_requested),
        token,
        emit_cancelled_result=True,
    )

    task.run()

    assert called == [True]


def test_shutdown_waits_for_owned_upload_completion() -> None:
    _app()
    page = S3Page(Mock(), Mock(), ImmediateRunner())  # type: ignore[arg-type]
    token = CancellationToken()
    page._upload_task = Handle(token)  # type: ignore[assignment]
    page._upload_in_progress = True
    completed: list[bool] = []

    page.shutdown(lambda: completed.append(True))

    assert token.is_cancellation_requested
    assert completed == []
    page._upload_cancelled()
    assert completed == [True]


def test_actual_s3_upload_resumes_after_mfa_with_same_plan_and_token(tmp_path: Path) -> None:
    _app()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        (UploadItem(source, "test-upload-bucket", "report.txt", 7, False),),
    )
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    s3.prepare_upload.return_value = plan
    s3.list_objects.return_value = []
    tokens: list[CancellationToken] = []
    upload_calls = 0

    operation_ids: list[str] = []

    def upload(upload_plan, *, overwrite, context):
        nonlocal upload_calls
        upload_calls += 1
        assert upload_plan is plan
        assert not overwrite
        tokens.append(context.cancellation)
        operation_ids.append(context.operation_id)
        if upload_calls == 1:
            raise CredentialValidationError("auth.mfa_required", "test")
        context.report(
            "uploading",
            "s3.upload.progress",
            completed=7,
            total=7,
            target=plan.items[0].uri,
        )
        return UploadSummary((plan.items[0].uri,), 7)

    s3.upload.side_effect = upload
    clock = FixedClock()
    refreshes = RefreshCoordinator(clock)
    authenticated = AuthenticatedOperationCoordinator(refreshes, clock)  # type: ignore[arg-type]
    page = S3Page(
        locations,
        s3,
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _plan: (True, False),
        authenticated,
        lambda _parent, _arn: "123456",
    )
    notices: list[str] = []
    page.notice_raised.connect(notices.append)
    page.set_profile(7)
    page.bucket.setText("test-upload-bucket")
    page.set_files([source])

    page.prepare_upload()

    assert upload_calls == 2
    assert len(tokens) == 2 and tokens[0] is tokens[1]
    assert operation_ids[1] == "auth-op"
    assert refreshes.resume_calls == [("auth-op", "123456")]
    assert page.progress.value() == 100
    assert notices == ["S3 업로드를 완료했습니다."]


def test_upload_mfa_cancel_clears_handle_and_shutdown_completes_immediately(
    tmp_path: Path,
) -> None:
    _app()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        (UploadItem(source, "test-upload-bucket", "report.txt", 7, False),),
    )
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    s3.prepare_upload.return_value = plan
    s3.upload.side_effect = CredentialValidationError("auth.mfa_required", "test")
    clock = FixedClock()
    authenticated = AuthenticatedOperationCoordinator(  # type: ignore[arg-type]
        RefreshCoordinator(clock), clock
    )
    page = S3Page(
        locations,
        s3,
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _plan: (True, False),
        authenticated,
        lambda _parent, _arn: None,
    )
    page.set_profile(7)
    page.bucket.setText("test-upload-bucket")
    page.set_files([source])

    page.prepare_upload()
    completed: list[bool] = []
    page.shutdown(lambda: completed.append(True))

    assert s3.upload.call_count == 1
    assert page._upload_task is None
    assert not page._upload_in_progress
    assert completed == [True]
