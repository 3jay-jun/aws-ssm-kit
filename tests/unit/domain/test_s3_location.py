import pytest

from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_location import S3Location


def test_location_normalizes_prefix_and_validates_bucket() -> None:
    value = S3Location(None, 1, " uploads ", "test-upload-bucket", "/reports/2026/")

    assert value.name == "uploads"
    assert value.prefix == "reports/2026/"

    with pytest.raises(ConfigurationError, match="s3.bucket.invalid"):
        S3Location(None, 1, "bad", "INVALID_BUCKET")


def test_location_requires_persisted_id() -> None:
    with pytest.raises(ConfigurationError, match="s3.location.id.required"):
        S3Location(None, 1, "uploads", "test-upload-bucket").require_id()


@pytest.mark.parametrize(
    ("profile_id", "name", "prefix", "code"),
    [
        (0, "uploads", "", "s3.location.profile_id.invalid"),
        (1, " ", "", "s3.location.name.required"),
        (1, "uploads", "bad\x00prefix", "s3.prefix.invalid"),
    ],
)
def test_location_rejects_invalid_identity_name_and_prefix(
    profile_id: int, name: str, prefix: str, code: str
) -> None:
    with pytest.raises(ConfigurationError, match=code):
        S3Location(None, profile_id, name, "test-upload-bucket", prefix)
