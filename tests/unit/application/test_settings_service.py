from collections.abc import Mapping
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from aws_connect.application.settings_service import SettingsService, UpdateSettingsRequest
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.errors import ConfigurationError


class MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_setting(self, key: str) -> str | None:
        return self.values.get(key)

    def put_setting(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete_setting(self, key: str) -> None:
        self.values.pop(key, None)

    def replace_settings(self, values: Mapping[str, str | None]) -> None:
        for key, value in values.items():
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value


def test_settings_validate_apply_and_persist_as_single_authority(tmp_path: Path) -> None:
    store = MemorySettings()
    configurator = Mock()
    service = SettingsService(store, tmp_path / "default logs", configurator)

    result = service.update(UpdateSettingsRequest(tmp_path / "한국어 로그", "debug"))

    assert result == AppSettings(tmp_path / "한국어 로그", LogLevel.DEBUG)
    configurator.apply.assert_called_once_with(result)
    assert store.values == {
        "log.directory": str(tmp_path / "한국어 로그"),
        "log.level": "DEBUG",
    }


def test_invalid_level_is_rejected_before_persistence(tmp_path: Path) -> None:
    store = MemorySettings()
    service = SettingsService(store, tmp_path / "logs", Mock())

    with pytest.raises(ConfigurationError) as failure:
        service.update(UpdateSettingsRequest(log_level="trace"))

    assert failure.value.message_code == "settings.log_level.invalid"
    assert store.values == {}


def test_reset_applies_defaults_and_removes_overrides(tmp_path: Path) -> None:
    store = MemorySettings()
    store.values.update({"log.directory": "old", "log.level": "ERROR"})
    configurator = Mock()
    service = SettingsService(store, tmp_path / "logs", configurator)

    result = service.reset()

    assert result == AppSettings(tmp_path / "logs", LogLevel.INFO)
    assert store.values == {}
    configurator.apply.assert_called_once_with(result)


def test_persistence_failure_restores_previous_logging_configuration(tmp_path: Path) -> None:
    store = MemorySettings()
    store.values.update({"log.directory": str(tmp_path / "old"), "log.level": "ERROR"})
    store.replace_settings = Mock(side_effect=OSError("disk detail"))  # type: ignore[method-assign]
    configurator = Mock()
    service = SettingsService(store, tmp_path / "default", configurator)

    with pytest.raises(ConfigurationError) as failure:
        service.update(UpdateSettingsRequest(tmp_path / "new", "DEBUG"))

    assert failure.value.message_code == "settings.persistence.failed"
    assert failure.value.technical_cause == "OSError"
    assert configurator.apply.call_args_list == [
        call(AppSettings(tmp_path / "new", LogLevel.DEBUG)),
        call(AppSettings(tmp_path / "old", LogLevel.ERROR)),
    ]
    assert store.values == {"log.directory": str(tmp_path / "old"), "log.level": "ERROR"}


def test_empty_log_directory_is_rejected_before_apply(tmp_path: Path) -> None:
    configurator = Mock()
    service = SettingsService(MemorySettings(), tmp_path / "default", configurator)

    with pytest.raises(ConfigurationError) as failure:
        service.update(UpdateSettingsRequest("", "INFO"))

    assert failure.value.message_code == "settings.log_directory.required"
    configurator.apply.assert_not_called()
