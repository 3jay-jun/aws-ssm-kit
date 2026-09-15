"""Read-only RDS endpoint catalog use case."""

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.ports import RdsEndpoint, RdsEndpointGateway
from aws_connect.application.profile_service import ProfileService


class RdsEndpointService:
    def __init__(
        self,
        profiles: ProfileService,
        sessions: SessionGuard,
        gateway: RdsEndpointGateway,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._gateway = gateway

    def list(self, profile: str | int | None = None) -> list[RdsEndpoint]:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        return self._gateway.list_endpoints(credentials, selected.region)
