from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.domain.execution_log import ErrorCategory, ExecutionPhase
from aws_connect.presentation.gui.errors import error_message
from aws_connect.presentation.gui.logs import _detail_text
from aws_connect.presentation.gui.view_models import build_activity_log_row_view_model


def _entry(**changes: object) -> ManagedLogEntry:
    return replace(
        ManagedLogEntry(
            datetime(2026, 10, 20, tzinfo=UTC),
            "rds",
            "example",
            "SUCCESS",
            "rds.start.completed",
            operation="start",
        ),
        **changes,
    )


@pytest.mark.parametrize(
    ("feature", "operation", "label"),
    [
        ("rds", "start", "연결 실행"),
        ("rds", "start_managed", "연결 실행"),
        ("rds", "stop", "연결 종료"),
        ("ec2", "list", "목록"),
        ("secrets", "list", "목록"),
        ("s3", "upload", "업로드"),
        ("ec2", "start_instance", "인스턴스 시작"),
    ],
)
def test_operation_and_completed_message_are_korean(
    feature: str, operation: str, label: str
) -> None:
    row = build_activity_log_row_view_model(
        _entry(
            feature=feature, operation=operation, message_code=f"{feature}.{operation}.completed"
        )
    )
    assert row.operation == label
    assert row.message == f"{label} 작업을 완료했습니다."
    assert row.phase == "완료"


@pytest.mark.parametrize(
    ("phase", "result", "message"),
    [
        (ExecutionPhase.STARTED, "SUCCESS", "연결 실행 작업을 시작했습니다."),
        (ExecutionPhase.PROGRESS, "WARNING", "연결 실행 작업을 진행 중입니다."),
        (ExecutionPhase.COMPLETED, "CANCELLED", "연결 실행 작업을 취소했습니다."),
        (ExecutionPhase.COMPLETED, "FAILURE", "연결 실행 작업에 실패했습니다."),
        (ExecutionPhase.COMPLETED, "WARNING", "연결 실행 작업 중 확인이 필요한 사항"),
    ],
)
def test_phase_and_result_are_distinguished(
    phase: ExecutionPhase, result: str, message: str
) -> None:
    row = build_activity_log_row_view_model(_entry(phase=phase, result=result))
    assert row.message.startswith(message)


def test_error_copy_reuses_catalog_and_preserves_diagnostic_fields() -> None:
    entry = _entry(
        result="FAILURE",
        message_code="rds.tunnel.local_port_in_use",
        error_category=ErrorCategory.CONFLICT,
        error_code="PortInUse",
        required_permission="ssm:StartSession",
        aws_request_id="request-example",
    )
    row = build_activity_log_row_view_model(entry)
    assert row.message == " ".join(error_message(entry.message_code) or ())
    assert row.error_category == "사용 중인 자원과 충돌"
    assert row.error_code == "PortInUse"
    assert row.required_permission == "ssm:StartSession"
    assert row.aws_request_id == "request-example"
    copied = _detail_text(entry)
    assert f"메시지: {row.message}" in copied
    assert "작업: 연결 실행" in copied
    assert "메시지 코드: rds.tunnel.local_port_in_use" in copied


def test_unknown_failure_has_korean_reason_and_recovery_guidance() -> None:
    row = build_activity_log_row_view_model(
        _entry(
            result="FAILURE",
            message_code="unknown.error",
            error_category=ErrorCategory.NETWORK,
            masked_detail="Technical transport failure",
        )
    )
    assert (
        row.message == "연결 실행 작업에 실패했습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요."
    )
    assert row.error_category == "네트워크 오류"


def test_legacy_code_and_korean_details_are_supported() -> None:
    row = build_activity_log_row_view_model(
        _entry(operation=None, message_code="s3.list.completed")
    )
    assert row.operation == "목록"
    assert row.message == "목록 작업을 완료했습니다."
    korean = _entry(masked_detail="터널이 종료되었습니다.")
    assert build_activity_log_row_view_model(korean).message == korean.masked_detail
    unknown = _entry(operation="new_action", message_code="new.action.completed")
    assert build_activity_log_row_view_model(unknown).message == "작업을 완료했습니다."


def test_session_end_and_expiration_explain_what_happened() -> None:
    ended = build_activity_log_row_view_model(
        _entry(feature="ec2", operation="session_end", message_code="ec2.session_end.completed")
    )
    assert ended.operation == "터미널 연결 종료"
    assert ended.message == "터미널 연결 종료 작업을 완료했습니다."
    expired = build_activity_log_row_view_model(
        _entry(
            operation="stop",
            message_code="rds.stop.completed",
            metadata_json='{"reason":"max_duration"}',
        )
    )
    assert expired.message == "설정한 세션 유지 시간이 만료되어 RDS 연결을 종료했습니다."
    for invalid in ('{"reason":', "[]"):
        assert (
            build_activity_log_row_view_model(
                _entry(operation="stop", metadata_json=invalid)
            ).message
            == "연결 종료 작업을 완료했습니다."
        )


@pytest.mark.parametrize(
    "code",
    [
        "credentials.session.expired",
        "aws.network.error",
        "settings.persistence.failed",
        "s3.download.destination.exists",
        "secret.saved.update.failed",
        "profile.create.failed",
    ],
)
def test_previously_generic_errors_have_specific_recovery_guidance(code: str) -> None:
    row = build_activity_log_row_view_model(
        _entry(operation=code, message_code=code, result="FAILURE")
    )
    copy = error_message(code)
    assert copy is not None
    assert row.message == " ".join(copy)
    assert row.operation != "작업"


def test_cli_nested_action_and_all_level_labels_are_translated() -> None:
    from aws_connect.presentation.gui.view_models import log_level_view_model

    row = build_activity_log_row_view_model(
        _entry(
            operation="cli.command.succeeded",
            message_code="cli.command.succeeded",
            target="session.clone",
        )
    )
    assert row.operation == "연결 설정 복제"
    for level, label in (
        ("DEBUG", "상세"),
        ("INFO", "정보"),
        ("WARNING", "경고"),
        ("ERROR", "오류"),
    ):
        assert log_level_view_model(level).text == label


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("auth.session.issued", "인증 세션을 발급했습니다."),
        ("auth.mfa_required", "추가 인증이 필요합니다. 인증 코드를 입력하세요."),
        ("auth.mfa.cancelled", "추가 인증을 취소했습니다."),
        ("ec2.connection.succeeded", "EC2 터미널 연결을 실행했습니다."),
        (
            "ec2.inventory.permission_fallback",
            "전체 인스턴스를 조회할 권한이 없어 접속 가능한 인스턴스만 표시했습니다.",
        ),
    ],
)
def test_special_messages_explain_the_actual_outcome(code: str, message: str) -> None:
    row = build_activity_log_row_view_model(_entry(message_code=code))
    assert row.message == message


def test_unclassified_failure_and_normal_stop_have_safe_specific_copy() -> None:
    failure = build_activity_log_row_view_model(
        _entry(result="FAILURE", message_code="future.failure", error_category=None)
    )
    assert failure.message == (
        "연결 실행 작업에 실패했습니다. 오류 코드와 연결 상태를 확인한 뒤 다시 시도하세요."
    )
    stopped = build_activity_log_row_view_model(
        _entry(
            operation="stop", message_code="rds.stop.completed", metadata_json='{"reason":"manual"}'
        )
    )
    assert stopped.message == "연결 종료 작업을 완료했습니다."
