from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMenu, QPushButton
from tests.adapter.gui.test_s3 import ImmediateRunner, _app

from aws_connect.application.operations import ProgressEvent
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import (
    UploadConflictPolicy,
    UploadItem,
    UploadPlan,
    UploadSummary,
)
from aws_connect.domain.errors import S3TransferError
from aws_connect.presentation.gui.s3 import S3Page
from aws_connect.presentation.gui.s3_search import S3SearchDialog


def make_page() -> tuple[S3Page, Mock]:
    _app()
    service, locations = Mock(), Mock()
    locations.list.return_value = []
    service.list_buckets.return_value = ["test-upload-bucket"]
    service.list_objects.return_value = []
    page = S3Page(
        locations, service, ImmediateRunner(), lambda *_: UploadConflictPolicy.SKIP_EXISTING
    )
    page.set_profile(7)
    return page, service


def test_checks_never_enable_batch_or_prefix_delete_and_queue_clear_is_local(
    tmp_path: Path,
) -> None:
    page, service = make_page()
    page._objects_loaded(
        [
            S3Object("one.txt", 1, None),
            S3Object("two.txt", 2, None),
            S3Object("folder/", 0, None, True),
        ]
    )
    page.objects.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert page.delete_object_button.isEnabled()
    page.objects.item(1, 0).setCheckState(Qt.CheckState.Checked)
    assert not page.delete_object_button.isEnabled()
    page.delete_selected_object()
    page._select_object_row(2)
    assert not page.delete_object_button.isEnabled()
    file = tmp_path / "file.txt"
    file.write_text("sample")
    page.add_sources([file])
    page.clear_sources()
    assert page.upload_sources.rowCount() == 0
    service.delete_object.assert_not_called()


def test_partial_failure_retry_keeps_success_and_original_target(tmp_path: Path) -> None:
    page, service = make_page()
    sources = [tmp_path / "one.txt", tmp_path / "two.txt"]
    for source in sources:
        source.write_text("one")
    plan = UploadPlan(
        7,
        "ap-northeast-2",
        tuple(
            UploadItem(source, "test-upload-bucket", f"original/{source.name}", 3, False)
            for source in sources
        ),
    )
    service.prepare_upload.return_value = plan

    def upload(_plan, *, policy, context):
        for item in plan.items:
            context.report("starting", "s3.upload.item.started", target=item.uri)
            if item.source == sources[1]:
                raise S3TransferError("s3.transfer.failed", "fixture")
            context.report(
                "completed", "s3.upload.item.completed", completed=3, total=3, target=item.uri
            )

    service.upload.side_effect = upload
    page.add_sources(sources)
    page.prepare_upload()
    assert [e.state for e in page._queue.values()] == ["성공", "실패"]
    page._set_failed_only(True)
    assert page.upload_sources.isRowHidden(0)
    assert not page.upload_sources.isRowHidden(1)
    assert page.upload_sources.cellWidget(1, 8).findChild(QPushButton, "upload_retry").isEnabled()
    page.prefix.setText("different/")
    service.prepare_upload.return_value = UploadPlan(7, "ap-northeast-2", (plan.items[1],))
    service.upload.side_effect = None
    service.upload.return_value = UploadSummary((plan.items[1].uri,), 3)
    page.retry_source(sources[1])
    service.prepare_upload.assert_called_with((sources[1],), "test-upload-bucket", "original", 7)
    assert [e.state for e in page._queue.values()] == ["성공", "성공"]
    assert page.progress.value() == 100


def test_queue_mutations_blocked_during_upload_and_stale_events_ignored(tmp_path: Path) -> None:
    page, service = make_page()
    source = tmp_path / "file.txt"
    source.write_text("one")
    page.add_sources([source])
    page._upload_in_progress = True
    page.clear_sources()
    page.remove_source(source)
    assert len(page._queue) == 1
    page.set_profile(8)
    page._progressed(
        ProgressEvent(
            "old", "completed", 3, 3, "s3.upload.item.completed", "s3://test-upload-bucket/file.txt"
        )
    )
    assert not page._queue
    page._upload_cancelled()


def test_search_dialog_moves_or_searches_and_keeps_cancel_non_mutating() -> None:
    page, service = make_page()

    def accept_search(dialog):
        dialog.prefix.setText("images/banners")
        dialog.tabs.setCurrentIndex(1)
        dialog.query.setText("logo")
        return QDialog.DialogCode.Accepted

    with patch.object(S3SearchDialog, "exec", accept_search):
        page.open_search()
    service.list_objects.assert_called_with(
        "test-upload-bucket", "images/banners/", 7, query="logo"
    )
    previous = service.list_objects.call_count
    with patch.object(S3SearchDialog, "exec", return_value=QDialog.DialogCode.Rejected):
        page.open_search()
    assert service.list_objects.call_count == previous
    page._navigate_to_prefix("")
    service.list_objects.assert_called_with("test-upload-bucket", "", 7)


def test_object_menu_and_rename_call_shared_service() -> None:
    page, service = make_page()
    page._objects_loaded([S3Object("images/old.png", 5, None)])
    page.open_object_menu(0)
    menu = page.findChildren(QMenu)[-1]
    assert [action.text() for action in menu.actions()] == [
        "다운로드",
        "이름 변경",
        "복사 경로",
        "삭제",
    ]
    menu.close()
    with patch(
        "aws_connect.presentation.gui.s3.QInputDialog.getText", return_value=("new.png", True)
    ):
        page.rename_selected_object()
    service.rename_object.assert_called_once_with(
        "test-upload-bucket", "images/old.png", "new.png", 7
    )


def test_old_listing_cannot_replace_newer_navigation() -> None:
    page, service = make_page()
    pending = []

    class DeferredRunner:
        def submit(self, operation, on_success, on_error):
            pending.append(on_success)

    page._runner = DeferredRunner()
    page.list_objects()
    page._navigate_to_prefix("new/")
    pending[1]([S3Object("new/right.txt", 1, None)])
    pending[0]([S3Object("old.txt", 1, None)])
    assert page.objects.item(0, 1).text() == "right.txt"


def test_upload_local_path_mtime_and_centered_actions(tmp_path):
    import os
    from datetime import datetime

    from aws_connect.presentation.gui.styles import APP_STYLE

    page, _ = make_page()
    page.setStyleSheet(APP_STYLE)
    page.resize(1250, 850)
    page.show()
    source = tmp_path / "photo.png"
    source.write_bytes(b"fixture")
    timestamp = 1700000000
    os.utime(source, (timestamp, timestamp))
    page.add_sources([source])
    page._queue[source].target = "s3://test-upload-bucket/elsewhere/photo.png"
    page._queue[source].state = "실패"
    page._render_queue_state()
    _app().processEvents()
    table = page.upload_sources
    assert table.horizontalHeaderItem(4).text() == "로컬 경로"
    assert table.item(0, 4).text() == str(source)
    assert table.item(0, 4).toolTip() == str(source)
    assert table.item(0, 5).text() == datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    buttons = table.cellWidget(0, 8).findChildren(QPushButton)
    assert len({(b.width(), b.height(), b.y()) for b in buttons}) == 1
    assert all(b.y() >= 0 and b.geometry().bottom() < table.rowHeight(0) for b in buttons)
    page.close()


def test_download_status_keeps_more_button_and_separates_failure(tmp_path):
    from aws_connect.application.s3_service import DownloadItem, DownloadPlan

    page, service = make_page()
    page._download_destination = lambda _: tmp_path
    objects = [S3Object("images/first.txt", 3, None), S3Object("images/second.txt", 3, None)]
    page._objects_loaded(objects)
    for row in range(2):
        page.objects.item(row, 0).setCheckState(Qt.CheckState.Checked)
    items = tuple(
        DownloadItem("test-upload-bucket", obj.key, tmp_path / obj.key.split("/")[-1], 3, False)
        for obj in objects
    )
    service.prepare_download.return_value = DownloadPlan(7, "ap-northeast-2", items)

    def download(plan, context, *, policy):
        for index, item in enumerate(plan.items):
            context.report("starting", "s3.download.item.started", target=item.uri)
            assert page.objects.cellWidget(index, 5).accessibleName() == "다운로드 중"
            assert isinstance(page.objects.cellWidget(index, 6), QPushButton)
            if index:
                raise S3TransferError("s3.transfer.failed", "fixture")
            context.report("completed", "s3.download.item.completed", target=item.uri)

    service.download.side_effect = download
    page.download_selected_objects()
    assert page.objects.cellWidget(0, 5).accessibleName() == "성공"
    assert page.objects.cellWidget(1, 5).accessibleName() == "실패"
    assert not page._download_spinner.isActive()
    page.set_profile(8)
    assert not page._download_states
    page.close()


def test_object_and_queue_menus_share_uniform_item_geometry():
    from aws_connect.presentation.gui.styles import APP_STYLE

    page, _ = make_page()
    page.setStyleSheet(APP_STYLE)
    page.show()
    page._objects_loaded([S3Object("file.txt", 1, None)])
    page.open_object_menu(0)
    menu = page.findChildren(QMenu)[-1]
    _app().processEvents()
    object_sizes = [action.defaultWidget().height() for action in menu.actions()]
    assert len(set(object_sizes)) == 1
    assert menu.actions()[-1].defaultWidget().property("danger") is True
    menu.close()
    page.open_queue_menu()
    menu = page.findChildren(QMenu)[-1]
    _app().processEvents()
    assert all(action.defaultWidget().height() == object_sizes[0] for action in menu.actions())
    menu.actions()[0].trigger()
    assert page._hide_completed
    menu.close()
    page.close()
