import json

from aws_connect.application.system_info import StaticSystemInfoService
from aws_connect.presentation.cli.system_info import render_system_info


def test_render_system_info_as_text() -> None:
    service = StaticSystemInfoService()

    assert render_system_info(service, output="text") == "aws-ssm-kit: ready"


def test_render_system_info_as_json() -> None:
    service = StaticSystemInfoService()

    assert json.loads(render_system_info(service, output="json")) == {
        "application_name": "aws-ssm-kit",
        "status": "ready",
    }
