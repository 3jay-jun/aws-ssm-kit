from dataclasses import replace
from unittest.mock import Mock

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QPushButton
from tests.adapter.gui.test_shell import ImmediateTaskRunner

from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive
from aws_connect.presentation.gui.s3_drives import S3DrivesPage


def page() -> tuple[S3DrivesPage, Mock]:
    service = Mock()
    drive = S3Drive(1, 1, "Project", "example-assets", "brand", "S", "Files")
    service.list.return_value = [DriveStatus(drive, DriveState.DISCONNECTED)]
    service.available_letters.return_value = {"S", "T"}
    result = S3DrivesPage(service, None, ImmediateTaskRunner(), None, lambda *_: None)
    result.set_profile(1)
    result.table.setCurrentRow(0)
    return result, service


def test_polling_and_same_profile_refresh_preserve_unsaved_editor(qt_application):
    widget, service = page()
    widget.prefix.setText("draft")
    widget.refresh()
    widget.set_profile(1)
    assert widget.prefix.text() == "draft"
    assert widget.edit_button.isEnabled()
    assert widget.connect_button.isEnabled()
    assert "draft" in widget.preview.text()
    service.save.assert_not_called()


def test_editor_save_reuses_service_and_restores_selection(qt_application):
    widget, service = page()
    saved = replace(service.list.return_value[0].drive, prefix="changed")
    service.save.return_value = saved
    service.list.return_value = [DriveStatus(saved, DriveState.DISCONNECTED)]
    widget.prefix.setText("changed")
    widget.save()
    service.save.assert_called_once_with(saved)
    assert widget._selected() == saved
    assert not widget._dirty
    assert widget.connect_button.isEnabled()


def test_connected_editor_is_locked_but_disconnect_available(qt_application):
    widget, service = page()
    service.list.return_value = [replace(service.list.return_value[0], state=DriveState.CONNECTED)]
    widget.refresh()
    assert not widget.bucket.isEnabled()
    assert not widget.connect_button.isEnabled()
    assert not widget.connect_button.isHidden()
    assert not widget.disconnect_button.isHidden()
    widget.disconnect_selected()
    service.disconnect.assert_called_once_with(1)


def test_search_does_not_discard_editor_and_new_cancel_keeps_draft(qt_application, monkeypatch):
    widget, _ = page()
    widget.name.setText("Draft")
    widget.search.setText("missing")
    assert widget.table.item(0).isHidden()
    assert widget.name.text() == "Draft"
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    widget.edit(None)
    assert widget.name.text() == "Draft"
    widget.cancel()
    assert widget.name.text() == "Project"


def test_profile_change_rejects_late_bucket_catalog(qt_application):
    widget, _ = page()
    callbacks = []
    widget._catalog = lambda profile, callback, **kwargs: callbacks.append(callback)
    widget._load_buckets()
    widget.set_profile(2)
    callbacks[0](["old-profile-bucket"])
    assert widget.bucket.findText("old-profile-bucket") == -1


def test_empty_editor_saves_new_connection_without_example_bucket(qt_application):
    widget, service = page()
    widget.edit(None)
    assert widget.bucket.currentText() == ""
    widget.name.setText("New")
    widget.bucket.setEditText("example-new")
    service.save.return_value = S3Drive(2, 1, "New", "example-new", "", "S")
    service.list.return_value = [DriveStatus(service.save.return_value, DriveState.DISCONNECTED)]
    widget.save()
    assert service.save.call_args.args[0].id is None
    assert widget._selected().id == 2


def test_new_connect_saves_then_connects_and_actions_have_tooltips(qt_application):
    widget, service = page()
    widget.edit(None)
    widget.name.setText("New")
    widget.bucket.setEditText("example-new")
    saved = S3Drive(2, 1, "New", "example-new", "", "S")
    service.save.return_value = saved
    service.list.return_value = [DriveStatus(saved, DriveState.DISCONNECTED)]
    widget.connect_button.click()
    service.save.assert_called_once()
    assert service.connect.call_args.args[0] == 2
    for button, tooltip in (
        (widget.edit_button, "저장"),
        (widget.connect_button, "연결"),
        (widget.disconnect_button, "연결 해제"),
    ):
        assert button.text() == ""
        assert button.toolTip() == tooltip
        assert not button.icon().isNull()


def test_catalog_uses_shared_cache_auto_loads_missing_and_refreshes(qt_application):
    widget, _ = page()
    s3 = Mock()
    widget._s3 = s3
    s3.cached_buckets.return_value = ["cached-bucket"]
    widget._ensure_buckets()
    assert widget.bucket.findText("cached-bucket") >= 0
    s3.list_buckets.assert_not_called()
    widget.set_profile(2)
    assert widget.bucket.findText("cached-bucket") == -1
    s3.cached_buckets.return_value = None
    s3.list_buckets.return_value = ["new-bucket"]
    widget._ensure_buckets()
    s3.list_buckets.assert_called_once_with(2, refresh=False)
    widget.catalog_button.click()
    assert s3.list_buckets.call_args.kwargs == {"refresh": True}
    assert not widget._dirty


def test_bucket_auth_cancel_allows_manual_retry(qt_application):
    widget, _ = page()
    widget._s3 = Mock()
    authenticated = Mock()
    widget._authenticated = authenticated
    widget._load_buckets()
    assert widget._catalog_loading
    authenticated.submit.call_args.kwargs["on_cancel"]()
    assert not widget._catalog_loading
    widget._load_buckets()
    assert authenticated.submit.call_count == 2


def test_shutdown_during_new_save_does_not_start_mount(qt_application):
    widget, service = page()
    callbacks = []
    widget._runner = Mock()
    widget._runner.submit.side_effect = lambda operation, success, error: callbacks.append(success)
    saved = service.list.return_value[0].drive
    widget.prefix.setText("draft")
    widget.connect_selected()
    widget.shutdown(Mock(), Mock())
    callbacks[0](saved)
    service.connect.assert_not_called()


def test_explorer_opens_final_path_and_disconnect_keeps_values(qt_application, monkeypatch):
    widget, service = page()
    assert not widget.open_button.isEnabled()
    drive = service.list.return_value[0].drive
    service.list.return_value = [DriveStatus(drive, DriveState.CONNECTED)]
    service.connected_path.return_value = drive.windows_path
    opened = Mock(return_value=True)
    monkeypatch.setattr(QDesktopServices, "openUrl", opened)
    widget.refresh()
    assert widget.open_button.isEnabled()
    widget.open_button.click()
    assert opened.call_args.args[0].toLocalFile() == drive.windows_path.replace("\\", "/")
    service.list.return_value = [DriveStatus(drive, DriveState.DISCONNECTED)]
    widget.disconnect_button.click()
    assert widget.prefix.isEnabled()
    assert widget.prefix.text() == drive.prefix
    assert widget.local.text() == drive.local_prefix
    assert widget.preview.text() == "s3://example-assets/brand/ → S:\\Files"


def test_row_menu_clone_uses_clicked_row_and_delete_discards_deleted_editor(
    qt_application, monkeypatch
):
    widget, service = page()
    source = service.list.return_value[0].drive
    clone = replace(source, id=2, name="Copy", letter="T")
    service.clone.return_value = clone
    service.list.return_value.append(DriveStatus(clone, DriveState.DISCONNECTED))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Copy", True))
    widget._show_menu(1, QPushButton(widget))
    menu = widget.findChild(QMenu)
    actions = [action for action in menu.actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == ["이름 수정", "마운트 복제", "마운트 삭제"]
    actions[1].trigger()
    menu.close()
    service.clone.assert_called_once_with(1, "Copy")
    assert widget._selected() == clone
    widget.prefix.setText("unsaved")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    service.list.return_value = [DriveStatus(source, DriveState.DISCONNECTED)]
    widget.delete_selected(clone)
    service.delete.assert_called_once_with(2)
    assert widget._editing_id is None
    assert not widget._dirty
