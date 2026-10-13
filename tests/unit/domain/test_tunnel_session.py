from dataclasses import replace

import pytest

from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession


def session(**changes: object) -> TunnelSession:
    base = TunnelSession(
        1,
        2,
        "dev-db",
        "db.cluster.ap-northeast-2.rds.amazonaws.com",
        3306,
        13306,
        TargetMode.FIXED,
        "i-0123456789abcdef0",
    )
    return replace(base, **changes)


def test_fixed_and_select_target_rules() -> None:
    assert session().resolve_target(None) == "i-0123456789abcdef0"
    selectable = session(target_mode=TargetMode.SELECT, target_instance_id=None)
    assert selectable.resolve_target("i-abcdef01234567890") == "i-abcdef01234567890"

    with pytest.raises(ConfigurationError, match="rds.tunnel.target_required"):
        selectable.resolve_target(None)
    with pytest.raises(ConfigurationError, match="rds.session.select_target_must_be_empty"):
        session(target_mode=TargetMode.SELECT)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"name": " "}, "rds.session.name.required"),
        ({"host": "bad host"}, "rds.session.host.invalid"),
        ({"remote_port": 0}, "rds.session.remote_port.invalid"),
        ({"local_port": 65536}, "rds.session.local_port.invalid"),
        ({"profile_id": 0}, "rds.session.profile_id.invalid"),
        ({"target_instance_id": None}, "rds.session.target_instance_id.invalid"),
    ],
)
def test_rejects_invalid_saved_session(changes: dict[str, object], code: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        session(**changes)
    assert caught.value.message_code == code


def test_unsaved_session_has_no_persistent_identity() -> None:
    with pytest.raises(ConfigurationError, match="rds.session.id.required"):
        session(id=None).require_id()


def test_tunnel_duration_defaults_to_twelve_hours() -> None:
    assert session().session_duration_hours == 12


@pytest.mark.parametrize("hours", [1, 8, 12, 24])
def test_tunnel_duration_accepts_supported_hours(hours: int) -> None:
    assert session(session_duration_hours=hours).session_duration_hours == hours


@pytest.mark.parametrize("hours", [36, 0, -1, 2, True, 1.0, "12", None])
def test_tunnel_duration_rejects_unsupported_values(hours: object) -> None:
    with pytest.raises(ConfigurationError, match="rds.session.duration.invalid"):
        session(session_duration_hours=hours)
