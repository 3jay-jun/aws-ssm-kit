from aws_connect.application.system_info import StaticSystemInfoService
from aws_connect.presentation.gui.system_info import build_system_info_view_model


def test_gui_view_model_uses_application_service() -> None:
    view_model = build_system_info_view_model(StaticSystemInfoService(application_name="Test"))

    assert view_model.title == "Test"
    assert view_model.status_text == "ready"
