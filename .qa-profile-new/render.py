import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication, QWidget
from aws_connect.presentation.gui.window import ProfileDialog
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import configure_gui_typography
from tests.adapter.gui.test_shell import FakeProfiles
app = QApplication([])
configure_gui_typography(app)
app.setStyleSheet(APP_STYLE)
parent = QWidget()
dialog = ProfileDialog(parent)
dialog.set_profiles(FakeProfiles().items, 1)
dialog.show()
app.processEvents()
dialog.grab().save('.qa-profile-new/profile.png')
print(dialog.width(), dialog.height())
