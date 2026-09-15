"""GUI view-model mapping for the bootstrap system information use case."""

from dataclasses import dataclass

from aws_connect.application.system_info import SystemInfoService


@dataclass(frozen=True, slots=True)
class SystemInfoViewModel:
    """Display-ready values with no GUI framework dependency."""

    title: str
    status_text: str


def build_system_info_view_model(service: SystemInfoService) -> SystemInfoViewModel:
    """Map application output to the GUI shell model."""

    result = service.get()
    return SystemInfoViewModel(title=result.application_name, status_text=result.status)
