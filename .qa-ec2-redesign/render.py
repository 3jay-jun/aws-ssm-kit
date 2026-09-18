import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from datetime import datetime
from PySide6.QtWidgets import QApplication
from aws_connect.presentation.gui.ec2_rds import Ec2Page
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import configure_gui_typography
from aws_connect.application.ec2_service import Ec2Target
from tests.adapter.gui.test_ec2_rds import FakeEc2, ImmediateRunner
app=QApplication([])
configure_gui_typography(app)
app.setStyleSheet(APP_STYLE)
service=FakeEc2()
service.targets=[Ec2Target('i-0123456789abcdef0', 'example_web_application_01', '10.0.0.10', 'Linux', 'Online', favorite=True, last_connected_at=datetime.now().astimezone()), Ec2Target('i-abcdef01234567890', 'example_worker_02', '10.0.0.20', 'Linux','Offline',instance_state='stopped')]
page=Ec2Page(service,ImmediateRunner())
page.set_profile(1)
page.resize(1308,880)
page.show()
page.table.selectRow(0)
app.processEvents()
page.grab().save('.qa-ec2-redesign/ec2.png')
print([(i,page.table.columnWidth(i)) for i in range(8)])
page.resize(804,598)
app.processEvents()
page.grab().save('.qa-ec2-redesign/ec2-small.png')
print('small',page.width(),page.table.horizontalScrollBar().maximum())

page.table.horizontalScrollBar().setValue(page.table.horizontalScrollBar().maximum())
app.processEvents()
page.grab().save('.qa-ec2-redesign/ec2-small-actions.png')
from PySide6.QtWidgets import QPushButton
for row in range(page.table.rowCount()):
    cell=page.table.cellWidget(row,7)
    buttons=cell.findChildren(QPushButton)
    for button in buttons:
        assert cell.rect().contains(button.geometry()), (cell.rect(),button.geometry())
page.resize(1308,880)
page.table.horizontalScrollBar().setValue(0)
app.processEvents()
more=page.table.cellWidget(0,7).findChild(QPushButton,'ec2_more')
more.click()
app.processEvents()
from PySide6.QtWidgets import QMenu
menu=more.findChild(QMenu)
menu.grab().save('.qa-ec2-redesign/menu.png')
print('Action geometry and menu render passed')
page.close()
