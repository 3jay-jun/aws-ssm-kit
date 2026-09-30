import io
from unittest.mock import Mock

from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import main
from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive


def test_drive_cli_save_list_and_foreground_commands(capsys, monkeypatch):
    drives = Mock()
    saved = S3Drive(1, 7, "project", "test-bucket", "", "S")
    drives.save.return_value = saved
    drives.get.return_value = saved
    drives.list.return_value = [DriveStatus(saved, DriveState.DISCONNECTED)]
    app = ApplicationServices(Mock(), Mock(), Mock(), Mock(), s3_drives=drives)
    app.profiles.resolve.return_value.require_id.return_value = 7
    assert (
        main(
            [
                "s3-drive",
                "save",
                "--profile",
                "dev",
                "--name",
                "project",
                "--bucket",
                "test-bucket",
                "--letter",
                "S",
            ],
            services=app,
        )
        == 0
    )
    assert main(["s3-drive", "list", "--output", "json"], services=app) == 0
    assert '"s3_drives"' in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("status\ndisconnect 1\nquit\n"))
    assert main(["s3-drive", "run"], services=app) == 0
    drives.disconnect.assert_called_once_with(1)
    drives.shutdown.assert_called_once()
