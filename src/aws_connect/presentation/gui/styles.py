"""Pixel-level visual contract mirrored from the approved HTML mockup."""

from aws_connect.presentation.gui.icons import gui_asset_path
from aws_connect.presentation.gui.typography import (
    BODY_FONT_SIZE,
    PAGE_TITLE_FONT_SIZE,
    SECTION_TITLE_FONT_SIZE,
)

# ruff: noqa: E501

NAVIGATION_WIDTH = 220

APP_STYLE = """
* { color: #172033; font-family: 'Malgun Gothic'; font-size: __BODY_SIZE__px; }
QMainWindow, QWidget#prototype_background { background: #e9eef5; }
QFrame#app_shell { background: #f8fafc; border: 0; border-radius: 0; }
QFrame#authentication_header { background: #ffffff; border: 0; border-bottom: 1px solid #dce3ed; }
QWidget#brand_panel { background: transparent; }
QLabel#brand_logo { background: transparent; }
QWidget#profile_summary { background: transparent; border-left: 1px solid #e2e8f0; }
QLabel#profile_name { font-size: 15px; font-weight: 700; }
QLabel#header_caption, QLabel#page_subtitle, QLabel#feature_subtitle, QLabel#feature_description, QLabel#helper_text, QLabel#profile_sequence, QLabel#active_tunnel_summary, QLabel#session_meta { color: #64748b; }
QLabel#header_caption { font-size: 12px; }
QLabel#auth_state { color: #067647; font-size: 13px; font-weight: 700; }
QPushButton#profile_button, QPushButton#refresh_button { min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; padding: 0; border: 1px solid #d7dee9; border-radius: 11px; background: #ffffff; font-size: 20px; }
QPushButton#profile_button:hover, QPushButton#refresh_button:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }
QFrame#navigation { background: #f1f5f9; border: 0; border-right: 1px solid #dde4ee; }
QLabel#navigation_label { color: #64748b; font-size: 12px; font-weight: 600; letter-spacing: 2px; }
QFrame#navigation QPushButton { min-height: 36px; padding: 0 11px; border: 2px solid transparent; border-radius: 10px; background: transparent; color: #475569; text-align: left; font-weight: 400; outline: 0; }
QFrame#navigation QPushButton:hover { background: #e2e8f0; }
QFrame#navigation QPushButton:checked { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
QFrame#navigation QPushButton:focus { border-color: #2563eb; }
QStackedWidget#workspace { background: #f7fafe; border: 0; }
QWidget#dashboard, QWidget#ec2_page, QWidget#rds_page, QWidget#secrets_page, QWidget#s3_page, QWidget#logs_page, QWidget#secret_page_content, QWidget#s3_page_content { background: #f7fafe; }
QLabel#section_title, QLabel#feature_heading, QLabel#profile_editor_title, QLabel#managed_log_detail_title, QLabel#rds_editor_title, QLabel#s3_section_title { font-size: __SECTION_TITLE_SIZE__px; font-weight: 700; }
QLabel#page_title { color: #172033; font-size: __PAGE_TITLE_SIZE__px; font-weight: 700; }
QFrame#feature_card, QFrame#tunnel_summary, QFrame#content_card, QFrame#session_list_card, QFrame#editor_card, QFrame#catalog_card, QFrame#result_card, QFrame#managed_log_card, QFrame[role="card"] { background: #ffffff; border: 1px solid #dce3ed; border-radius: 14px; }
QFrame#feature_card { min-height: 178px; }
QLabel#feature_icon { min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; border-radius: 12px; background: #eff6ff; color: #1d4ed8; font-weight: 800; qproperty-alignment: AlignCenter; }
QLabel#feature_description { font-size: 13px; }
QFrame#status_dot { min-width: 9px; max-width: 9px; min-height: 9px; max-height: 9px; border-radius: 4px; background: #18a66a; }
QLabel[status="success"] { color: #087443; background: #e8f8ef; border-radius: 99px; padding: 5px 8px; font-size: 12px; font-weight: 700; }
QWidget#icon_text[status="success"] { background: #e8f8ef; border-radius: 99px; }
QWidget#icon_text[status="warning"] { background: #fff4e5; border-radius: 99px; }
QWidget#icon_text[status="failure"] { background: #feeceb; border-radius: 99px; }
QLabel[status="danger"] { color: #b42318; background: #feeceb; border-radius: 99px; padding: 5px 8px; font-size: 12px; font-weight: 700; }
QLabel[status="error"] { color: #b42318; font-weight: 700; }
QLabel[status="warning"] { color: #b54708; font-weight: 700; }
QLabel[status="failure"] { color: #b42318; font-weight: 700; }
QLabel[level="info"] { color: #175cd3; }
QLabel[level="warning"] { color: #b54708; }
QLabel[level="error"] { color: #b42318; }
QPushButton { min-height: 38px; padding: 0 14px; border: 1px solid #cbd5e1; border-radius: 9px; background: #ffffff; color: #172033; font-weight: 600; }
QPushButton:hover { border-color: #3b82f6; color: #1d4ed8; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; border-color: #e2e8f0; }
QPushButton[role="primary"], QPushButton[variant="primary"] { border-color: #2563eb; background: #2563eb; color: #ffffff; }
QPushButton[role="danger"], QPushButton[variant="danger"] { background: #ffffff; color: #b42318; }
QPushButton[size="small"] { min-height: 28px; max-height: 30px; padding: 0 10px; font-size: 12px; }
QPushButton[icon_only="true"] { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; border: 0; background: transparent; }
QPushButton[icon_only="true"]:hover { background: #eff6ff; border-radius: 8px; }
QLineEdit, QComboBox, QSpinBox { min-height: 38px; max-height: 38px; padding: 0 11px; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; selection-background-color: #dbeafe; }
QComboBox { padding-right: 38px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 34px; border: 0; border-left: 1px solid #e2e8f0; border-top-right-radius: 8px; border-bottom-right-radius: 8px; background: #f8fafc; }
QComboBox::down-arrow { image: url(__COMBO_ARROW__); width: 10px; height: 8px; }
QComboBox QAbstractItemView { padding: 6px; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; color: #172033; selection-background-color: #dbeafe; selection-color: #1d4ed8; outline: 0; }
QCheckBox::indicator, QAbstractItemView::indicator, QMenu::indicator { width: 16px; height: 16px; border: 1px solid #cbd5e1; border-radius: 4px; background: #ffffff; }
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked, QMenu::indicator:checked { background: #2563eb; border-color: #2563eb; image: url(__CHECK_MARK__); }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 2px solid #3b82f6; }
QLineEdit[application_error="true"], QComboBox[application_error="true"], QSpinBox[application_error="true"] { border: 2px solid #d92d20; }
QLabel#field_label { color: #526074; font-size: 12px; font-weight: 600; }
QListWidget, QTableWidget { background: #ffffff; border: 0; gridline-color: transparent; selection-background-color: #eff6ff; selection-color: #172033; outline: 0; }
QListWidget::item { padding: 13px; border-radius: 10px; }
QListWidget::item:selected { background: #eff6ff; color: #172033; }
QListWidget::item:focus { background: #eff6ff; }
QListWidget#dashboard_active_tunnels::item { padding: 0; border-radius: 0; }
QHeaderView::section { background: #ffffff; color: #526074; font-size: 12px; font-weight: 600; padding: 11px; border: 0; border-bottom: 1px solid #e5eaf1; }
QTableWidget::item:selected { background: #eff6ff; color: #172033; }
QTableWidget::item { padding: 11px; border-bottom: 1px solid #e5eaf1; }
QTableWidget#s3_objects QHeaderView::section { padding: 6px 10px; }
QTableWidget#s3_objects::item { padding: 4px 10px; }
QTableWidget#secret_fields { min-height: 260px; padding: 17px; border-radius: 10px; background: #f1f5f9; color: #263344; font-family: 'Consolas'; font-size: 13px; }
QFrame#notice, QLabel#notice, QFrame#managed_log_detail, QLabel#secret_notice, QLabel#managed_log_notice { background: #eff8ff; color: #175cd3; border: 0; border-radius: 10px; }
QLabel#managed_log_detail_message { padding: 10px 12px; border-radius: 8px; background: #eaf4ff; color: #172033; }
QLabel#managed_log_detail_metadata { color: #172033; }
QScrollArea#managed_log_detail_body, QWidget#managed_log_detail_content { background: transparent; border: 0; }
QToolButton#managed_log_detail_toggle { padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 6px; background: white; }
QFrame#managed_log_detail_separator { background: #d7e3f4; min-width: 1px; max-width: 1px; border: 0; }
QLabel#notice { padding: 12px 14px; }
QLabel#secret_notice, QLabel#managed_log_notice { padding: 12px 14px; }
QLabel#codebox { min-height: 260px; padding: 17px; border-radius: 10px; background: #f1f5f9; color: #263344; font-family: 'Consolas'; font-size: 13px; }
QLabel#s3_drop_zone { min-height: 28px; padding: 10px; border: 1px dashed #93a4ba; border-radius: 12px; background: #ffffff; color: #64748b; font-weight: 700; }
QScrollArea#s3_browser_scroll { background: transparent; border: 0; }
QTableWidget#s3_upload_sources::item { padding: 4px; }
QTableWidget#s3_upload_sources QHeaderView::section { padding: 8px 4px; }
QTableWidget#s3_objects QPushButton[action_button="true"], QPushButton#rds_session_more, QPushButton#ec2_more { min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; border: 0; background: transparent; padding: 0; }
QPushButton#s3_upload { min-width: 106px; min-height: 40px; font-weight: 700; }
QPushButton#s3_upload[variant="danger"] { color: white; background: #d92d20; }
QTableWidget#s3_upload_sources QPushButton[action_button="true"] { min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; padding: 0; border-radius: 6px; }
QTableWidget#s3_upload_sources QProgressBar { min-height: 12px; max-height: 12px; margin: 14px 4px; font-size: 10px; text-align: center; }
QTableWidget#s3_upload_sources QProgressBar::chunk { background: #00a35c; }
QFrame#s3_queue_card > QProgressBar { min-height: 16px; max-height: 16px; text-align: center; }
QMenu#s3_action_menu { background: white; border: 1px solid #d7e5f7; padding: 6px; }
QMenu#s3_action_menu::item { padding: 9px 14px; }
QMenu#s3_action_menu::item:selected { background: #eff6ff; }
QPushButton#s3_menu_item { min-height: 34px; max-height: 34px; border: 0; text-align: left; padding: 0 14px; background: transparent; }
QPushButton#s3_menu_item[danger="true"] { color: #ff2638; }
QPushButton#s3_menu_item:hover, QPushButton#s3_menu_item:checked { background: #eff6ff; }
QDialog#s3_search_dialog { background: white; }
QLabel#s3_dialog_title { font-size: 25px; font-weight: 700; color: #0b1643; }
QLabel#s3_current_location { background: #f3f7fd; color: #52688f; padding: 14px; border-radius: 8px; }
QDialog#s3_search_dialog QTabBar::tab { padding: 12px 36px; background: #f4f7fc; color: #64748b; border-radius: 8px; margin-right: 6px; font-size: 17px; font-weight: 700; }
QDialog#s3_search_dialog QTabBar::tab:selected { background: #dbe7ff; color: #0055ff; }
QPushButton#s3_recent_path { text-align: left; border: 0; color: #52688f; }
QWidget#s3_sources_header { background: transparent; }
QProgressBar { min-height: 8px; max-height: 8px; border: 0; border-radius: 4px; background: #e3e9f1; }
QProgressBar::chunk { border-radius: 4px; background: #2563eb; }
QScrollArea#rds_editor_details_scroll, QWidget#rds_editor_details { border: 0; background: #ffffff; }
QWidget#s3_breadcrumb { background: white; }
QWidget#s3_breadcrumb QToolButton { min-height: 22px; padding: 0; border: 0; background: transparent; color: #2563eb; font-weight: 400; }
QWidget#s3_breadcrumb QLabel { color: #2563eb; }
QDialog#profile_dialog { background: #ffffff; border: 1px solid #dce3ed; }
QFrame#profile_dialog_header { background: #ffffff; border: 0; border-bottom: 1px solid #e2e8f0; }
QFrame#profile_list_panel { min-width: 270px; max-width: 270px; background: #f8fafc; border: 0; border-right: 1px solid #e2e8f0; }
QListWidget#profile_list { background: #f8fafc; }
QListWidget#profile_list::item, QListWidget#rds_sessions::item, QListWidget#secret_catalog::item { padding: 0; border-radius: 0; }
QFrame#compact_list_row { background: transparent; border: 0; border-bottom: 1px solid #e2e8f0; }
QFrame#compact_list_row[last_row="true"] { border-bottom: 0; }
QLabel#compact_row_title { background: transparent; font-weight: 700; }
QLabel#compact_row_metadata { background: transparent; color: #64748b; font-size: 12px; }
QPushButton[action_button="true"] { min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; padding: 0; background: #ffffff; color: #172033; }
QPushButton[action_button="true"][variant="primary"] { background: #2563eb; border-color: #2563eb; color: #ffffff; }
QPushButton[action_button="true"][variant="danger"] { background: #d92d20; border-color: #d92d20; color: #ffffff; }
QPushButton[action_button="true"][variant="danger"]:hover { background: #b42318; border-color: #b42318; }
QPushButton[action_button="true"]:disabled, QPushButton[action_button="true"][variant="primary"]:disabled, QPushButton[action_button="true"][variant="danger"]:disabled { background: #f1f5f9; border-color: #e2e8f0; color: #94a3b8; }
QWidget#profile_editor { background: #ffffff; }
QLabel#profile_notice { padding: 12px 14px; border-radius: 10px; background: #eff8ff; color: #175cd3; }
QLabel#toast { padding: 12px 15px; border-radius: 10px; background: #172033; color: #ffffff; font-size: 13px; }
""".replace("__COMBO_ARROW__", gui_asset_path("chevron-down.svg")).replace(
    "__CHECK_MARK__", gui_asset_path("common-check.svg")
)

APP_STYLE += """
QWidget#ec2_page QFrame#content_card { border-color: #d7e5f7; border-radius: 12px; }
QPushButton#ec2_refresh { min-width: 46px; max-width: 46px; min-height: 46px; max-height: 46px; }
QCheckBox#ec2_favorites_only { min-height: 40px; padding: 0 4px; }
QWidget#ec2_page QComboBox::drop-down { border: 0; background: transparent; }
QWidget#ec2_page QLineEdit, QWidget#ec2_page QComboBox, QTableWidget#ec2_targets { font-size: 14px; }
QWidget#icon_text[status="danger"] { background: #feeceb; border-radius: 5px; }
QWidget#icon_text[status="success"] QLabel { color: #087443; background: transparent; }
QWidget#icon_text[status="danger"] QLabel { color: #b42318; background: transparent; }
QFrame#ec2_selection_summary { background: #f9fcff; border: 1px solid #d7e5f7; border-radius: 9px; }
QLabel#ec2_session_state { background: transparent; color: #475d87; }
QTableWidget#ec2_targets::item { padding: 0 8px; border-bottom: 1px solid #dce8f8; }
QTableWidget#ec2_targets QHeaderView::section { padding: 12px 8px; }
QMenu#ec2_target_menu { background: #ffffff; border: 1px solid #d7e5f7; border-radius: 8px; padding: 6px; }
QMenu#ec2_target_menu::item { padding: 10px 14px; }
QMenu#ec2_target_menu::item:selected { background: #eff6ff; }
""".replace("__CHECK_MARK__", gui_asset_path("common-check.svg"))

APP_STYLE += """
QWidget#rds_page QFrame#status_dot[active="false"] { background: #7b88a5; }
QListWidget#rds_sessions QFrame#compact_list_row { padding: 4px 0; }
QListWidget#rds_sessions QFrame#compact_list_row[connected="true"] { background: #edf7ff; }
QLabel#rds_connection_label { font-weight: 700; background: transparent; }
QFrame#rds_connection_card { border: 0; border-radius: 10px; background: #edf7ff; }
QWidget#rds_running_notice { background: #edf9f3; border-radius: 10px; }
QWidget#rds_running_notice QLabel { background: transparent; color: #426476; }
QLabel#rds_copy_notice { color: #64748b; padding-top: 6px; }
QToolButton#field_help { border: 0; background: transparent; padding: 0; }
QToolButton#rds_copy_address { padding: 10px 32px 10px 12px; border: 1px solid #cbd5e1; border-radius: 9px; background: #ffffff; font-weight: 600; }
QToolButton#rds_copy_address::menu-button { width: 26px; border-left: 1px solid #dce3ed; }
QToolButton#rds_copy_address::menu-arrow { image: url(__COMBO_ARROW__); width: 12px; height: 12px; }
QToolButton#rds_copy_address:disabled { color: #94a3b8; background: #f1f5f9; }
QMenu#rds_session_menu { background: white; border: 1px solid #d7e5f7; padding: 6px; }
QMenu#rds_session_menu::item { padding: 10px 14px; }
QMenu#rds_session_menu::item:selected { background: #eff6ff; }
QPushButton#rds_menu_delete { color: #d92d20; border: 0; text-align: left; padding: 0 14px; }
QWidget#rds_page QLineEdit:disabled, QWidget#rds_page QComboBox:disabled, QWidget#rds_page QSpinBox:disabled { background: #f5f7fa; color: #64748b; }
""".replace("__COMBO_ARROW__", gui_asset_path("chevron-down.svg"))

APP_STYLE += """
QFrame#secret_catalog_card, QFrame#secret_result_card { border-color: #d7e5f7; }
QWidget#secret_relay_notice { background: #edf7ff; border-radius: 10px; }
QWidget#secret_relay_notice QLabel { background: transparent; color: #526d94; }
QLabel#secret_relay_status { color: #087443; font-weight: 600; }
QLabel#secret_saved_count { padding: 3px 9px; background: #edf1f7; border-radius: 12px; color: #64748b; }
QListWidget#secret_catalog::item:selected { border-left: 3px solid #2563eb; background: #eff6ff; }
QLabel#secret_summary { font-weight: 600; }
QTableWidget#secret_fields { min-height: 66px; padding: 0; background: white; border: 1px solid #e2e8f0; border-radius: 8px; font-family: 'Malgun Gothic'; }
QTableWidget#secret_fields::item { padding: 0 8px; }
QTableWidget#secret_fields QHeaderView::section { background: #f5f8fc; padding: 8px; }
QTabWidget#secret_detail_tabs::pane { border: 0; }
QTabWidget#secret_detail_tabs QTabBar::tab { padding: 10px 14px; background: transparent; color: #526d94; }
QTabWidget#secret_detail_tabs QTabBar::tab:selected { color: #2563eb; border-bottom: 2px solid #2563eb; }
QToolButton#secret_copy_all { padding: 10px 32px 10px 12px; border: 1px solid #cbd5e1; border-radius: 9px; background: white; font-weight: 600; }
QToolButton#secret_copy_all::menu-button { width: 26px; border-left: 1px solid #dce3ed; }
QToolButton#secret_copy_all::menu-arrow { image: url(__COMBO_ARROW__); width: 12px; height: 12px; }
QMenu#secret_saved_menu { background: white; border: 1px solid #d7e5f7; padding: 6px; }
QMenu#secret_saved_menu::item { padding: 10px 14px; }
QMenu#secret_saved_menu::item:selected { background: #eff6ff; }
QPushButton#secret_menu_delete { color: #d92d20; border: 0; text-align: left; padding: 0 14px; }
""".replace("__COMBO_ARROW__", gui_asset_path("chevron-down.svg"))

APP_STYLE += """
QScrollArea#secret_page_scroll, QWidget#secret_page_content { border: 0; background: #f7fafe; }
""".replace("__CHECK_MARK__", gui_asset_path("common-check.svg"))

APP_STYLE += 'QLabel#secret_relay_status[unavailable="true"] { color: #d92d20; }'

APP_STYLE += """
QListWidget#profile_list QFrame#compact_list_row { padding: 7px 0; }
QListWidget#profile_list QFrame#compact_list_row[connected="true"] { background: #edf7ff; }
QListWidget#profile_list QFrame#status_dot[active="false"] { background: #8b96ae; }
QPushButton#profile_row_more { min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0; border: 0; background: transparent; }
QPushButton#profile_row_more:hover { background: #dbeafe; }
QWidget#profile_notice { background: #eff8ff; border-radius: 10px; padding: 10px; }
QWidget#profile_notice QLabel { color: #175cd3; background: transparent; }
QMenu#profile_row_menu { background: white; border: 1px solid #d7e5f7; padding: 6px; }
QMenu#profile_row_menu::item { padding: 10px 14px; }
QMenu#profile_row_menu::item:selected { background: #eff6ff; }
QPushButton#profile_menu_delete { color: #d92d20; border: 0; text-align: left; padding: 0 14px; }
QPushButton#profile_menu_delete:hover { background: #fff1f0; }
"""

APP_STYLE = (
    APP_STYLE.replace("__BODY_SIZE__", str(BODY_FONT_SIZE))
    .replace("__PAGE_TITLE_SIZE__", str(PAGE_TITLE_FONT_SIZE))
    .replace("__SECTION_TITLE_SIZE__", str(SECTION_TITLE_FONT_SIZE))
)

APP_STYLE += """
QComboBox#ec2_region { min-height: 22px; max-height: 22px; padding: 8px 30px 8px 10px; }
QComboBox#ec2_region QLineEdit { min-height: 0; padding: 0; border: 0; background: transparent; }
QPushButton#ec2_row_action { min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0; }
QPushButton#ec2_row_action[variant="primary"] { background: #111111; border-color: #111111; }
QPushButton#ec2_row_action[variant="primary"]:hover { background: #303030; border-color: #303030; }
QPushButton#ec2_row_action:disabled { background: #f1f5f9; border-color: #e2e8f0; }
"""

APP_STYLE += """
QComboBox#s3_bucket_catalog { min-height: 22px; max-height: 22px; padding: 8px 24px 8px 6px; }
QComboBox#s3_bucket_catalog QLineEdit { min-height: 0; padding: 0; border: 0; background: transparent; }
"""
