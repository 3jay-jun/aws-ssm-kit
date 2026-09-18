from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QToolButton

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import (
    CancellationToken,
    OperationResult,
    OperationState,
)
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import (
    DownloadItem,
    DownloadPlan,
    DownloadSummary,
    UploadConflictPolicy,
    UploadItem,
    UploadPlan,
    UploadSummary,
)
from aws_connect.domain.errors import (
    ApplicationError,
    AwsPermissionError,
    CredentialValidationError,
)
from aws_connect.domain.s3_location import S3Location
from aws_connect.presentation.gui.s3 import S3Page
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

    def upload(_plan, *, policy, context):
        assert policy is UploadConflictPolicy.SKIP_EXISTING
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
    page = S3Page(
        locations,
        s3,
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _plan: UploadConflictPolicy.SKIP_EXISTING,
    )
    page.set_profile(7)
    page.location_list.setCurrentRow(0)
    page.list_objects()
    page.set_files([source])
    page.prepare_upload()

    assert page.bucket.text() == "test-upload-bucket"
    assert page.objects.item(0, 1).text() == "report.txt"
    assert page.objects.item(0, 2).text() == "파일"
    assert page.objects.columnCount() == 6
    assert page.progress.value() == 100
    assert page.upload_status.text() == "업로드 중: s3://test-upload-bucket/incoming/report.txt"
    s3.prepare_upload.assert_called_once()
    s3.upload.assert_called_once()
    assert s3.list_objects.call_count == 2


def test_s3_page_matches_mockup_header_toolbar_card_and_drop_order() -> None:
    _app()
    page = S3Page(Mock(), Mock(), ImmediateRunner())  # type: ignore[arg-type]
    card = page.findChild(QFrame, "s3_browser_card")

    assert page.findChild(QLabel, "page_title").text() == "S3 파일"
    assert page.findChild(QFrame, "s3_toolbar") is None
    assert card is not None
    assert page.findChild(QFrame, "s3_queue_card") is not None
    assert page.prefix.isHidden()
    assert page.upload.text() == "업로드"
    assert page.upload.toolTip() == page.upload.accessibleName() == "업로드"
    assert page.findChild(type(page.upload), "s3_file_choose") is None
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
    page.bucket_catalog.activated.emit(0)
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

    assert page.bucket_catalog.isEditable()
    assert page.bucket.isHidden()
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

    assert page.objects.horizontalHeaderItem(1).text() == "이름"
    assert page.objects.horizontalHeaderItem(2).text() == "유형"
    assert page.objects.item(0, 1).text() == "archive"
    assert page.objects.item(0, 2).text() == "폴더"
    assert page.objects.item(0, 3).text() == "-"
    assert page.objects.item(1, 1).text() == "data.json"
    assert page.objects.item(1, 2).text() == "파일"
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


def test_selected_files_and_folders_download_to_one_selected_root(tmp_path: Path) -> None:
    _app()
    locations = Mock()
    locations.list.return_value = []
    s3 = Mock()
    s3.list_buckets.return_value = []
    selected_file = S3Object("reports/file.txt", 10, None)
    selected_folder = S3Object("archive/", 0, None, True)
    s3.list_objects.return_value = [selected_file, selected_folder]
    plan = DownloadPlan(
        7,
        "ap-northeast-2",
        (DownloadItem("test-upload-bucket", selected_file.key, tmp_path / selected_file.key, 10),),
    )
    s3.prepare_download.return_value = plan
    s3.download.return_value = DownloadSummary((plan.items[0].destination,), 10)
    page = S3Page(
        locations,
        s3,
        ImmediateRunner(),  # type: ignore[arg-type]
        download_destination=lambda _parent: tmp_path,
    )
    page.set_profile(7)
    page.bucket.setText("test-upload-bucket")
    page.list_objects()
    page.objects.selectAll()

    assert not page.delete_object_button.isEnabled()
    page.open_object_button.click()

    s3.prepare_download.assert_called_once_with(
        (selected_file, selected_folder), tmp_path, "test-upload-bucket", 7
    )
    s3.download.assert_called_once()
    assert page.open_object_button.toolTip() == "다운로드"


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


def test_prefix_navigation_and_existing_object_use_selected_conflict_policy(
    tmp_path: Path,
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
    chosen: list[UploadConflictPolicy] = []
    s3.upload.return_value = UploadSummary((), 0, (plan.items[0].uri,))
    page._confirm_upload = lambda _parent, _plan: (
        chosen.append(  # type: ignore[method-assign]
            UploadConflictPolicy.SKIP_EXISTING
        )
        or UploadConflictPolicy.SKIP_EXISTING
    )
    page._upload_prepared(plan)
    assert chosen == [UploadConflictPolicy.SKIP_EXISTING]


def test_upload_sources_accumulate_remove_clear_and_switch_single_action(tmp_path: Path) -> None:
    _app()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    page = S3Page(Mock(), Mock(), ImmediateRunner())  # type: ignore[arg-type]
    page._profile_id = 7

    page.add_sources([source, folder, source])

    assert page._selected_files == [source.resolve(), folder.resolve()]
    assert page.upload_sources.count() == 2
    assert page.file_summary.text() == "업로드 파일 (2개)"
    assert page.upload.text() == "업로드"
    assert page.upload.toolTip() == page.upload.accessibleName() == "업로드"
    assert page.upload.property("variant") == "primary"
    assert page.upload.isEnabled()

    page.upload_sources.selectRow(0)
    page.remove_selected_sources()
    assert page._selected_files == [folder.resolve()]
    page._upload_in_progress = True
    page._sync_upload_action()
    assert page.upload.text() == "업로드 취소"
    assert page.upload.toolTip() == page.upload.accessibleName() == "업로드 취소"
    assert page.upload.property("variant") == "danger"
    page._upload_in_progress = False
    page.clear_sources()
    assert page.upload_sources.count() == 0
    assert not page.upload.isEnabled()


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

    def upload(upload_plan, *, policy, context):
        nonlocal upload_calls
        upload_calls += 1
        assert upload_plan is plan
        assert policy is UploadConflictPolicy.SKIP_EXISTING
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
        lambda _parent, _plan: UploadConflictPolicy.SKIP_EXISTING,
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
    assert notices == ["S3 업로드를 완료했습니다. (1개)"]


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
        lambda _parent, _plan: UploadConflictPolicy.SKIP_EXISTING,
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


@pytest.mark.parametrize(
    "policy", [None, UploadConflictPolicy.OVERWRITE, UploadConflictPolicy.SKIP_EXISTING]
)
def test_download_confirmation_cancel_or_policy(tmp_path: Path, policy) -> None:
    _app()
    service = Mock()
    service.download.return_value = DownloadSummary((), 0)
    page = S3Page(Mock(), service, ImmediateRunner(), confirm_download=lambda *_: policy)  # type: ignore[arg-type]
    plan = DownloadPlan(
        7,
        "ap-northeast-2",
        (DownloadItem("test-bucket", "file.txt", tmp_path / "file.txt", 2, True),),
    )
    page._download_prepared(plan)
    if policy is None:
        service.download.assert_not_called()
    else:
        assert service.download.call_args.kwargs["policy"] is policy


def test_upload_cards_preview_and_blank_area_opens_picker(tmp_path: Path) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest

    from aws_connect.presentation.gui.upload_sources import UploadSourcesList

    _app()
    chosen: list[bool] = []
    cards = UploadSourcesList(
        lambda *_: None, lambda *_: None, lambda: chosen.append(True), ImmediateRunner()
    )  # type: ignore[arg-type]
    cards.resize(500, 184)
    cards.show()
    QTest.mouseClick(cards.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(420, 80))
    assert chosen == [True]
    source = tmp_path / "a-very-long-image-name-that-needs-to-be-shortened-for-the-card.png"
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    image.save(str(source))
    from aws_connect.presentation.gui.upload_sources import UploadQueueEntry

    cards.add_entry(UploadQueueEntry(source), "image/png", lambda *_: None, lambda *_: None)
    assert cards.item(0, 2).text() == source.name
    assert cards.cellWidget(0, 1).pixmap().toImage().pixelColor(1, 1).red() == 255
    assert "B" in cards.item(0, 3).text()
    assert cards.columnCount() == 9
    cards.close()


def test_upload_picker_accepts_files_and_folders_together(tmp_path: Path) -> None:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QFileSystemModel

    from aws_connect.presentation.gui.upload_sources import UploadSourceDialog

    _app()
    source = tmp_path / "file.txt"
    source.write_text("local", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    parent = S3Page(Mock(), Mock(), ImmediateRunner())  # type: ignore[arg-type]
    dialog = UploadSourceDialog(parent)
    dialog.setDirectory(str(tmp_path))
    dialog.show()
    QTest.qWait(100)
    view = next(
        view
        for view in dialog.findChildren(QAbstractItemView)
        if view.isVisible() and isinstance(view.model(), QFileSystemModel)
    )
    for path in (source, folder):
        view.selectionModel().select(
            view.model().index(str(path)),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    dialog.accept()
    assert set(dialog.paths) == {source, folder}
