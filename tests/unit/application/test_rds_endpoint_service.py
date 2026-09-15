from unittest.mock import Mock

from aws_connect.application.ports import RdsEndpoint
from aws_connect.application.rds_endpoint_service import RdsEndpointService
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials


def test_rds_endpoint_catalog_uses_profile_session_and_gateway() -> None:
    profile = AwsProfile(
        7,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"a",
        b"b",
    )
    profiles = Mock()
    profiles.resolve.return_value = profile
    sessions = Mock()
    credentials = PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "fixture-token")
    sessions.require_credentials.return_value = credentials
    gateway = Mock()
    gateway.list_endpoints.return_value = [
        RdsEndpoint("orders", "orders.example.internal", 5432, "postgres")
    ]
    service = RdsEndpointService(profiles, sessions, gateway)

    assert service.list("dev")[0].host == "orders.example.internal"
    sessions.require_credentials.assert_called_once_with(7)
    gateway.list_endpoints.assert_called_once_with(credentials, "ap-northeast-2")
