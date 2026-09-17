"""aws-ssm-kit graphical entry point."""

from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from aws_connect import APPLICATION_NAME
from aws_connect.bootstrap import build_application_services
from aws_connect.presentation.gui.icons import gui_icon
from aws_connect.presentation.gui.typography import configure_gui_typography
from aws_connect.presentation.gui.window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Run the GUI shell backed by the shared Application Services."""

    app = QApplication(list(argv) if argv is not None else [])
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(APPLICATION_NAME)
    app.setWindowIcon(gui_icon("logo.ico"))
    configure_gui_typography(app)
    services = build_application_services()
    connection_lifecycle = services.connection_lifecycle
    if connection_lifecycle is None:
        raise RuntimeError("Profile connection lifecycle service is not configured")
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
        connection_lifecycle=connection_lifecycle,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
