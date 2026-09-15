import logging

from aws_connect.infrastructure.masking import REDACTED, MaskingFilter, mask, mask_text


def test_recursive_masking_redacts_keys_and_known_literals() -> None:
    value = {
        "password": "visible-before-mask",  # pragma: allowlist secret
        "nested": {"name": "safe", "SessionToken": "token-before-mask"},
        "message": "failure contained literal-to-hide",
    }

    masked = mask(value, known_secrets=["literal-to-hide"])

    assert masked["password"] == REDACTED
    assert masked["nested"]["SessionToken"] == REDACTED
    assert "literal-to-hide" not in masked["message"]


def test_logging_filter_masks_registered_values() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "value=%s", ("hide-me",), None)

    assert MaskingFilter(["hide-me"]).filter(record)
    assert "hide-me" not in record.args


def test_recursive_masking_handles_lists_tuples_scalars_and_empty_registered_values() -> None:
    access_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    value = {"items": [(access_key, 7), "replace-me"]}

    masked = mask(value, known_secrets=("", "replace-me"))

    assert masked == {"items": [(REDACTED, 7), REDACTED]}
    assert mask_text("safe", known_secrets=("",)) == "safe"


def test_logging_filter_accepts_records_without_arguments() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "safe", (), None)

    assert MaskingFilter().filter(record)
    assert record.msg == "safe"
    assert record.args == ()
