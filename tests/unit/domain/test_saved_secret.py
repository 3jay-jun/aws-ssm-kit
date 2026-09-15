import pytest

from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.saved_secret import SavedSecret, SecretLookupMode


def test_saved_secret_normalizes_identifier_and_requires_persisted_id() -> None:
    saved = SavedSecret(None, 7, "  db/dev  ")

    assert saved.identifier == "db/dev"
    assert saved.value == ""
    assert saved.lookup_mode is SecretLookupMode.DIRECT
    assert saved.relay_instance_id is None
    with pytest.raises(ConfigurationError, match="secret.saved.id.required"):
        saved.require_id()


@pytest.mark.parametrize("profile_id, identifier", [(0, "db/dev"), (7, ""), (7, "bad\x00id")])
def test_saved_secret_rejects_invalid_identity(profile_id: int, identifier: str) -> None:
    with pytest.raises(ConfigurationError):
        SavedSecret(None, profile_id, identifier)


def test_saved_secret_never_exposes_value_or_lookup_context_in_repr() -> None:
    saved = SavedSecret(
        3,
        7,
        "db/dev",
        value="private-snapshot-value",  # pragma: allowlist secret
        lookup_mode=SecretLookupMode.VIA_EC2,
        relay_instance_id="i-private-relay",
    )

    rendered = repr(saved)

    assert "private-snapshot-value" not in rendered
    assert "via_ec2" not in rendered
    assert "i-private-relay" not in rendered


@pytest.mark.parametrize(
    ("lookup_mode", "relay_instance_id", "message_code"),
    [
        (SecretLookupMode.VIA_EC2, None, "secret.saved.relay.required"),
        (SecretLookupMode.DIRECT, "i-relay", "secret.saved.relay.unexpected"),
        ("unsupported", None, "secret.saved.lookup_mode.invalid"),
    ],
)
def test_saved_secret_enforces_lookup_mode_and_relay_invariant(
    lookup_mode: SecretLookupMode | str,
    relay_instance_id: str | None,
    message_code: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message_code):
        SavedSecret(
            None,
            7,
            "db/dev",
            lookup_mode=lookup_mode,  # type: ignore[arg-type]
            relay_instance_id=relay_instance_id,
        )
