"""Render every GUI page for deterministic visual QA without real AWS access."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from PySide6.QtWidgets import QApplication

from aws_connect.bootstrap import build_application_services
from aws_connect.presentation.gui.typography import configure_gui_typography
from aws_connect.presentation.gui.window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1424)
    parser.add_argument("--height", type=int, default=894)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    services = build_application_services(output / "runtime")
    lifecycle = services.connection_lifecycle
    if lifecycle is None:
        raise RuntimeError("Connection lifecycle is required for visual QA")
    application = QApplication([])
    configure_gui_typography(application)
    window = MainWindow(
        services.profiles,
        services.authentication,
        services.operations,
        services.ec2,
        services.tunnel_sessions,
        services.rds_tunnels,
        services.secrets,
        services.s3_locations,
        services.s3,
        services.settings,
        services.activity_logs,
        services.diagnostic_logs,
        services.rds_endpoints,
        authenticated_operations=services.authenticated_operations,
        dashboard=services.dashboard,
        connection_lifecycle=lifecycle,
        auto_start=False,
    )
    window.resize(arguments.width, arguments.height)
    window.show()
    application.processEvents()
    names = ("dashboard", "ec2", "rds", "secrets", "s3", "logs")
    for index, name in enumerate(names):
        window.pages.setCurrentIndex(index)
        page_layout = window.pages.currentWidget().layout()
        if page_layout is not None:
            page_layout.activate()
        window.pages.currentWidget().updateGeometry()
        window.repaint()
        application.processEvents()
        application.processEvents()
        if not window.grab().save(str(output / f"{name}.png")):
            raise RuntimeError(f"Could not render {name}")
    window.profile_dialog.open()
    application.processEvents()
    if not window.profile_dialog.grab().save(str(output / "profiles.png")):
        raise RuntimeError("Could not render profiles")
    window._allow_close = True  # noqa: SLF001 - deterministic QA teardown
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
