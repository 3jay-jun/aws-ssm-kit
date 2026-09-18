from pathlib import Path
from datetime import UTC, datetime
from unittest.mock import Mock
from tests.adapter.gui.test_logs import _app, ImmediateTaskRunner
from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.presentation.gui.logs import LogsSettingsPage
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import configure_gui_typography
app = _app()
configure_gui_typography(app)
settings = Mock()
settings.get.return_value = AppSettings(Path('.'), LogLevel.INFO)
activity = Mock()
activity.recent.return_value = [ManagedLogEntry(datetime.now(UTC), 'rds', '(DEV) example', 'SUCCESS', '터널이 정상적으로 종료되었습니다.', level='INFO', operation='stop', operation_id='operation-example', correlation_id='correlation-example', aws_service='ssm', aws_action='TerminateSession') for _ in range(20)]
page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())
page.setStyleSheet(APP_STYLE)
page.resize(1204,780)
page.refresh()
page.show()
app.processEvents()
page.grab().save('.qa-log-compact/expanded.png')
print('expanded rows', page.entries.viewport().height() // page.entries.rowHeight(0))
page.detail_toggle.click()
app.processEvents()
page.grab().save('.qa-log-compact/collapsed.png')
print('collapsed rows', page.entries.viewport().height() // page.entries.rowHeight(0))
page.close()
