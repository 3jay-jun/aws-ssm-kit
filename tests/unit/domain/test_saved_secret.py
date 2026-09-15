import pytest

from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.saved_secret import SavedSecret


def test_saved_secret_normalizes_identifier_and_requires_persisted_id() -> None:
    saved = SavedSecret(None, 7, "  db/dev  ")

    assert saved.identifier == "db/dev"
    with pytest.raises(ConfigurationError, match="secret.saved.id.required"):
        saved.require_id()


@pytest.mark.parametrize("profile_id, identifier", [(0, "db/dev"), (7, ""), (7, "bad\x00id")])
def test_saved_secret_rejects_invalid_identity(profile_id: int, identifier: str) -> None:
    with pytest.raises(ConfigurationError):
        SavedSecret(None, profile_id, identifier)
