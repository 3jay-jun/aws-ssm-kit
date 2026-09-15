from aws_connect.application.operations import CancellationToken, new_operation_id


def test_cancellation_token_and_operation_ids() -> None:
    token = CancellationToken()
    assert not token.is_cancellation_requested

    token.cancel()

    assert token.is_cancellation_requested
    assert new_operation_id() != new_operation_id()
