from aws_connect.application.system_info import StaticSystemInfoService, SystemInfo


def test_static_system_info_is_deterministic() -> None:
    service = StaticSystemInfoService(application_name="Test Connect")

    assert service.get() == SystemInfo(application_name="Test Connect", status="ready")
