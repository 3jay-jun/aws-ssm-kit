from dataclasses import dataclass

from aws_connect.application.operations import ProgressEvent
from aws_connect.application.system_info import SystemInfo
from aws_connect.presentation.cli.s3 import render_upload_progress
from aws_connect.presentation.cli.system_info import render_system_info
from aws_connect.presentation.gui.system_info import build_system_info_view_model
from aws_connect.presentation.gui.view_models import build_s3_progress_view_model


@dataclass
class FakeSystemInfoService:
    calls: int = 0

    def get(self) -> SystemInfo:
        self.calls += 1
        return SystemInfo(application_name="Shared Fake", status="ready")


def test_cli_and_gui_consume_the_same_application_contract() -> None:
    service = FakeSystemInfoService()

    cli_output = render_system_info(service, output="text")
    gui_output = build_system_info_view_model(service)

    assert cli_output == "Shared Fake: ready"
    assert gui_output.title == "Shared Fake"
    assert gui_output.status_text == "ready"
    assert service.calls == 2


def test_cli_and_gui_project_the_same_s3_progress_event() -> None:
    event = ProgressEvent(
        "operation-1",
        "uploading",
        4096,
        8192,
        "s3.upload.progress",
        "s3://test-upload-bucket/reports/file.bin",
    )

    cli_output = render_upload_progress(event)
    gui_output = build_s3_progress_view_model(event)

    assert "s3://test-upload-bucket/reports/file.bin" in cli_output
    assert "4096/8192 bytes" in cli_output
    assert gui_output.operation_id == event.operation_id
    assert gui_output.target_uri == event.target
    assert (gui_output.completed, gui_output.total) == (event.completed, event.total)
    assert gui_output.percent == 50
