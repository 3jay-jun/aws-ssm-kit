"""Pixel-level visual contract mirrored from the approved HTML mockup."""

from aws_connect.presentation.gui.icons import gui_asset_path

# ruff: noqa: E501

APP_STYLE = """
* { color: #172033; font-family: 'Malgun Gothic'; font-size: 13px; }
QMainWindow, QWidget#prototype_background { background: #e9eef5; }
QFrame#app_shell { background: #f8fafc; border: 1px solid #d4dce8; border-radius: 18px; }
QFrame#authentication_header { background: #ffffff; border: 0; border-bottom: 1px solid #dce3ed; }
QWidget#brand_panel { background: transparent; }
QLabel#brandmark { min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px; border-radius: 11px; background: #ff9900; color: #ffffff; font-size: 16px; font-weight: 800; qproperty-alignment: AlignCenter; }
QLabel#brand { color: #172033; font-size: 15px; font-weight: 700; }
QWidget#profile_summary { background: transparent; border-left: 1px solid #e2e8f0; }
QLabel#profile_name { font-size: 15px; font-weight: 700; }
QLabel#header_caption, QLabel#page_subtitle, QLabel#feature_subtitle, QLabel#feature_description, QLabel#helper_text, QLabel#profile_sequence, QLabel#active_tunnel_summary, QLabel#session_meta { color: #64748b; }
QLabel#header_caption { font-size: 12px; }
QLabel#auth_state { color: #067647; font-size: 13px; font-weight: 700; }
QPushButton#profile_button, QPushButton#refresh_button { min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; padding: 0; border: 1px solid #d7dee9; border-radius: 11px; background: #ffffff; font-size: 20px; }
QPushButton#profile_button:hover, QPushButton#refresh_button:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }
QFrame#navigation { background: #f1f5f9; border: 0; border-right: 1px solid #dde4ee; }
QLabel#navigation_label { color: #64748b; font-size: 12px; font-weight: 600; letter-spacing: 2px; }
QFrame#navigation QPushButton { min-height: 40px; padding: 0 13px; border: 0; border-radius: 10px; background: transparent; color: #475569; text-align: left; font-weight: 400; }
QFrame#navigation QPushButton:hover { background: #e2e8f0; }
QFrame#navigation QPushButton:checked { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
QStackedWidget#workspace { background: #f7fafe; border: 0; }
QWidget#dashboard, QWidget#ec2_page, QWidget#rds_page, QWidget#secrets_page, QWidget#s3_page, QWidget#logs_page { background: #f7fafe; }
QLabel#page_title { color: #172033; font-size: 25px; font-weight: 700; }
QLabel#section_title, QLabel#feature_heading, QLabel#profile_editor_title, QLabel#managed_log_detail_title { font-size: 17px; font-weight: 700; }
QFrame#feature_card, QFrame#tunnel_summary, QFrame#content_card, QFrame#session_list_card, QFrame#editor_card, QFrame#catalog_card, QFrame#result_card, QFrame#managed_log_card, QFrame[role="card"] { background: #ffffff; border: 1px solid #dce3ed; border-radius: 14px; }
QFrame#feature_card { min-height: 178px; }
QLabel#feature_icon { min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; border-radius: 12px; background: #eff6ff; color: #1d4ed8; font-weight: 800; qproperty-alignment: AlignCenter; }
QLabel#feature_description { font-size: 13px; }
QFrame#status_dot { min-width: 9px; max-width: 9px; min-height: 9px; max-height: 9px; border-radius: 4px; background: #18a66a; }
QLabel[status="success"] { color: #087443; background: #e8f8ef; border-radius: 99px; padding: 5px 8px; font-size: 12px; font-weight: 700; }
QLabel[status="danger"] { color: #b42318; background: #feeceb; border-radius: 99px; padding: 5px 8px; font-size: 12px; font-weight: 700; }
QLabel[status="error"] { color: #b42318; font-weight: 700; }
QPushButton { min-height: 38px; padding: 0 14px; border: 1px solid #cbd5e1; border-radius: 9px; background: #ffffff; color: #172033; font-weight: 600; }
QPushButton:hover { border-color: #3b82f6; color: #1d4ed8; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; border-color: #e2e8f0; }
QPushButton[role="primary"], QPushButton[variant="primary"] { border-color: #2563eb; background: #2563eb; color: #ffffff; }
QPushButton[role="danger"], QPushButton[variant="danger"] { background: #ffffff; color: #b42318; }
QPushButton[size="small"] { min-height: 28px; max-height: 30px; padding: 0 10px; font-size: 12px; }
QPushButton#ec2_row_action { min-width: 80px; max-width: 86px; padding: 0 3px; font-size: 11px; }
QPushButton[icon_only="true"] { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; border: 0; background: transparent; }
QPushButton[icon_only="true"]:hover { background: #eff6ff; border-radius: 8px; }
QLineEdit, QComboBox, QSpinBox { min-height: 38px; max-height: 38px; padding: 0 11px; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; selection-background-color: #dbeafe; }
QComboBox { padding-right: 38px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 34px; border: 0; border-left: 1px solid #e2e8f0; border-top-right-radius: 8px; border-bottom-right-radius: 8px; background: #f8fafc; }
QComboBox::down-arrow { image: url(__COMBO_ARROW__); width: 10px; height: 8px; }
QComboBox QAbstractItemView { padding: 6px; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; color: #172033; selection-background-color: #dbeafe; selection-color: #1d4ed8; outline: 0; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 2px solid #3b82f6; }
QLineEdit[application_error="true"], QComboBox[application_error="true"], QSpinBox[application_error="true"] { border: 2px solid #d92d20; }
QLabel#field_label { color: #526074; font-size: 12px; font-weight: 600; }
QListWidget, QTableWidget { background: #ffffff; border: 0; gridline-color: transparent; selection-background-color: #eff6ff; selection-color: #172033; outline: 0; }
QListWidget::item { padding: 13px; border-radius: 10px; }
QListWidget::item:selected { background: #eff6ff; color: #172033; }
QListWidget#dashboard_active_tunnels::item { padding: 0; border-radius: 0; }
QHeaderView::section { background: #ffffff; color: #526074; font-size: 12px; font-weight: 600; padding: 11px; border: 0; border-bottom: 1px solid #e5eaf1; }
QTableWidget::item { padding: 11px; border-bottom: 1px solid #e5eaf1; }
QTableWidget#secret_fields { min-height: 260px; padding: 17px; border-radius: 10px; background: #f1f5f9; color: #263344; font-family: 'Consolas'; font-size: 13px; }
QFrame#notice, QLabel#notice, QFrame#managed_log_detail, QLabel#secret_notice, QLabel#managed_log_notice { background: #eff8ff; color: #175cd3; border: 0; border-radius: 10px; }
QLabel#notice { padding: 12px 14px; }
QLabel#secret_notice, QLabel#managed_log_notice { padding: 12px 14px; }
QLabel#codebox { min-height: 260px; padding: 17px; border-radius: 10px; background: #f1f5f9; color: #263344; font-family: 'Consolas'; font-size: 13px; }
QLabel#s3_drop_zone { min-height: 38px; padding: 24px; border: 1px dashed #93a4ba; border-radius: 12px; background: #ffffff; color: #64748b; font-weight: 700; }
QProgressBar { min-height: 8px; max-height: 8px; border: 0; border-radius: 4px; background: #e3e9f1; }
QProgressBar::chunk { border-radius: 4px; background: #2563eb; }
QScrollArea#rds_editor_details_scroll, QWidget#rds_editor_details { border: 0; background: #ffffff; }
QWidget#s3_breadcrumb { margin-bottom: 14px; }
QWidget#s3_breadcrumb QToolButton { min-height: 22px; padding: 0; border: 0; background: transparent; color: #2563eb; font-weight: 400; }
QWidget#s3_breadcrumb QLabel { color: #2563eb; }
QDialog#profile_dialog { background: #ffffff; border: 1px solid #dce3ed; }
QFrame#profile_dialog_header { background: #ffffff; border: 0; border-bottom: 1px solid #e2e8f0; }
QFrame#profile_list_panel { min-width: 270px; max-width: 270px; background: #f8fafc; border: 0; border-right: 1px solid #e2e8f0; }
QWidget#profile_editor { background: #ffffff; }
QLabel#profile_notice { padding: 12px 14px; border-radius: 10px; background: #eff8ff; color: #175cd3; }
QLabel#toast { padding: 12px 15px; border-radius: 10px; background: #172033; color: #ffffff; font-size: 13px; }
""".replace("__COMBO_ARROW__", gui_asset_path("chevron-down.svg"))
