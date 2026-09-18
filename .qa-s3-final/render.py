import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from datetime import datetime, UTC
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt
from aws_connect.application.ports import S3Object
from aws_connect.presentation.gui.s3 import S3Page
from aws_connect.presentation.gui.s3_search import S3SearchDialog
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import configure_gui_typography
from tests.adapter.gui.test_s3 import ImmediateRunner

app=QApplication.instance() or QApplication([])
configure_gui_typography(app)
app.setStyleSheet(APP_STYLE)
locations=Mock(); locations.list.return_value=[]
service=Mock(); service.list_buckets.return_value=['demo-assets-bucket']
service.list_objects.return_value=[S3Object('images/banners/'+name, size, datetime(2026,9,18,11,9,12,tzinfo=UTC), folder) for name,size,folder in [('2024/',0,True),('2025/',0,True),('banner_main.png',2516582,False),('banner_event.jpg',1153433,False),('logo_white.png',545690,False),('readme.txt',1228,False)]]
page=S3Page(locations,service,ImmediateRunner())
page.setObjectName('s3_page')
page.set_profile(7)
page.prefix.setText('images/banners/')
page.list_objects()
directory=Path('.qa-s3-final').resolve()
image=QImage(100,70,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.darkCyan); image.save(str(directory/'01.png'))
for name in ['document.pdf','failed.png','application.log']:
    (directory/name).write_bytes(b'preview fixture')
page.add_sources([directory/n for n in ['01.png','document.pdf','failed.png','application.log']])
for entry,state in zip(page._queue.values(),['성공','성공','실패','대기']):
    entry.state=state
    entry.target=f's3://demo-assets-bucket/images/{entry.source.name}'
    entry.started='2026-09-18 11:09:12' if state!='대기' else '-'
    entry.percent=100 if state=='성공' else 0
page._render_queue_state()
for width,height,name in [(1204,790,'desktop'),(804,616,'small')]:
    page.resize(width,height); page.show(); app.processEvents(); app.processEvents()
    page.grab().save(str(directory/f'{name}.png'))
page.resize(1204,790); app.processEvents()
page.open_object_menu(2); app.processEvents(); app.activePopupWidget().grab().save(str(directory/'object-menu.png')); app.activePopupWidget().close()
page.open_queue_menu(); app.processEvents(); app.activePopupWidget().grab().save(str(directory/'queue-menu.png')); app.activePopupWidget().close()
dialog=S3SearchDialog(page,'demo-assets-bucket','images/banners',['demo-assets-bucket'],[('demo-assets-bucket','images/banners'),('demo-assets-bucket','images/icons'),('demo-assets-bucket','backup/2025')])
dialog.show(); app.processEvents(); dialog.grab().save(str(directory/'search.png'))
dialog.tabs.setCurrentIndex(1); app.processEvents(); dialog.grab().save(str(directory/'search-files.png'))
dialog.close(); page.close()
from tests.adapter.gui.test_shell import _window
from PySide6.QtWidgets import QPushButton
window, _, _ = _window(auto_start=False)
window.reload_profiles()
placeholder = window.pages.widget(4)
window.pages.removeWidget(placeholder)
placeholder.deleteLater()
window.pages.insertWidget(4, page)
window.pages.setCurrentIndex(4)
page.objects.selectRow(2)
for button in window.findChildren(QPushButton):
    if button.text() == 'S3 파일':
        button.click()
for width,height,name in [(1424,894,'window-desktop'),(1024,720,'window-small')]:
    window.resize(width,height); window.show(); app.processEvents(); app.processEvents()
    window.grab().save(str(directory/f'{name}.png'))
window._allow_close=True
window.close()
