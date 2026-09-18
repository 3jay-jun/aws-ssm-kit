from tests.adapter.gui.test_shell import _app, _window
from tests.unit.application.test_dashboard_service import build
from aws_connect.presentation.gui.typography import configure_gui_typography
app = _app()
configure_gui_typography(app)
window, _, _ = _window(auto_start=False)
service, *_ = build()
window.dashboard_page._service = service
window.dashboard_page.set_profile(1)
window.dashboard_page.refresh_permissions()
window.show()
app.processEvents()
window.grab().save('.qa-dashboard/desktop.png')
print('desktop', window.size(), window.dashboard_page.horizontalScrollBar().maximum(), window.dashboard_page.verticalScrollBar().maximum())
window.resize(1024,720)
app.processEvents()
window.grab().save('.qa-dashboard/small.png')
print('small', window.size(), window.dashboard_page.horizontalScrollBar().maximum(), window.dashboard_page.verticalScrollBar().maximum())
window.close()
