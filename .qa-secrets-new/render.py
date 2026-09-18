import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from aws_connect.presentation.gui.secrets import SecretsPage
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import configure_gui_typography
from tests.adapter.gui.test_secrets import FakeSecrets, ImmediateRunner
app=QApplication([])
configure_gui_typography(app)
app.setStyleSheet(APP_STYLE)
service=FakeSecrets()
page=SecretsPage(service,ImmediateRunner())
page.set_profile(1)
page.get_secret()
page.lookup_mode.setCurrentIndex(1)
page.resize(1308,880)
page.show()
app.processEvents()
page.grab().save('.qa-secrets-new/relay.png')
page.lookup_mode.setCurrentIndex(0)
app.processEvents()
page.grab().save('.qa-secrets-new/direct.png')
page.lookup_mode.setCurrentIndex(1)
page.resize(804,598)
app.processEvents()
page.grab().save('.qa-secrets-new/small.png')
print('page size',page.width(),page.height())
page.close()
