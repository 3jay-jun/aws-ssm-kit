"""Korean execution-history copy shared by logs and dashboard projections."""

import json
import re

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.domain.execution_log import ErrorCategory, ExecutionPhase, ExecutionResult
from aws_connect.presentation.gui.errors import error_message

_OPERATIONS = {
    "list": "목록",
    "list_targets": "인스턴스 목록",
    "list_targets_in_region": "리전별 인스턴스 목록",
    "list_buckets": "버킷 목록",
    "list_objects": "파일 목록",
    "list_fallback": "접속 가능한 인스턴스 목록",
    "start": "연결 실행",
    "start_managed": "연결 실행",
    "connect": "연결 실행",
    "connect_external": "외부 터미널 연결",
    "connection": "연결 실행",
    "stop": "연결 종료",
    "stop_external": "외부 터미널 종료",
    "session_end": "터미널 연결 종료",
    "start_instance": "인스턴스 시작",
    "reboot_instance": "인스턴스 재부팅",
    "get": "값 조회",
    "get_via_ec2": "EC2 경유 값 조회",
    "test_relay_connection": "EC2 경유 연결 확인",
    "download": "다운로드",
    "download_object": "파일 다운로드",
    "upload": "업로드",
    "delete_object": "파일 삭제",
    "rename": "이름 변경",
    "validate": "인증 확인",
    "refresh": "인증 갱신",
    "issue_session": "인증 세션 발급",
    "mfa": "추가 인증",
    "retry": "재시도",
    "load": "최근 작업 조회",
    "settings_update": "설정 저장",
    "settings_reset": "설정 초기화",
    "show": "상세 조회",
    "status": "상태 확인",
    "create": "새 항목 저장",
    "update": "항목 수정",
    "delete": "항목 삭제",
    "clone": "연결 설정 복제",
    "use": "프로필 선택",
    "reset": "설정 초기화",
    "export": "진단 로그 내보내기",
    "preview": "기존 설정 미리보기",
    "import": "기존 설정 가져오기",
    "save": "설정 저장",
    "run": "드라이브 연결 실행",
    "install": "드라이버 설치",
    "repair": "드라이버 복구",
    "doctor": "실행 환경 진단",
    "runtime": "실행 환경 관리",
    "dashboard": "대시보드 조회",
}

CATEGORY_LABELS = {
    ErrorCategory.PERMISSION: "권한 부족",
    ErrorCategory.AUTHENTICATION: "인증 오류",
    ErrorCategory.NETWORK: "네트워크 오류",
    ErrorCategory.TIMEOUT: "시간 초과",
    ErrorCategory.RESOURCE: "대상 리소스 오류",
    ErrorCategory.PROCESS: "프로그램 실행 오류",
    ErrorCategory.CONFLICT: "사용 중인 자원과 충돌",
    ErrorCategory.CONFIGURATION: "설정 오류",
    ErrorCategory.INTERNAL: "내부 오류",
}

_CATEGORY_GUIDANCE: dict[ErrorCategory | None, str] = {
    ErrorCategory.PERMISSION: "관리자에게 필요한 접근 권한을 요청하세요.",
    ErrorCategory.AUTHENTICATION: "인증정보를 확인하고 인증을 갱신한 뒤 다시 시도하세요.",
    ErrorCategory.NETWORK: "네트워크 연결을 확인한 뒤 다시 시도하세요.",
    ErrorCategory.TIMEOUT: "연결 상태를 확인한 뒤 다시 시도하세요.",
    ErrorCategory.RESOURCE: "대상이 존재하고 사용 가능한 상태인지 확인하세요.",
    ErrorCategory.PROCESS: "연결 프로그램의 설치 및 실행 상태를 확인하세요.",
    ErrorCategory.CONFLICT: "사용 중인 연결이나 포트를 확인하세요.",
    ErrorCategory.CONFIGURATION: "입력값과 저장된 설정을 확인하세요.",
    ErrorCategory.INTERNAL: "다시 시도해도 문제가 계속되면 진단 로그를 내보내 문의하세요.",
}

_SPECIAL_MESSAGES = {
    "auth.session.issued": "인증 세션을 발급했습니다.",
    "auth.mfa_required": "추가 인증이 필요합니다. 인증 코드를 입력하세요.",
    "auth.mfa.cancelled": "추가 인증을 취소했습니다.",
    "ec2.connection.succeeded": "EC2 터미널 연결을 실행했습니다.",
    "ec2.inventory.permission_fallback": (
        "전체 인스턴스를 조회할 권한이 없어 접속 가능한 인스턴스만 표시했습니다."
    ),
}


def log_message_code(entry: ManagedLogEntry) -> str:
    """Recover the stable code retained as action by older GUI/SQLite records."""
    if error_message(entry.message_code) is not None or entry.message_code in _SPECIAL_MESSAGES:
        return entry.message_code
    if entry.operation and error_message(entry.operation) is not None:
        return entry.operation
    return entry.message_code


def log_operation(entry: ManagedLogEntry) -> str:
    operation = entry.operation
    if label := _OPERATIONS.get(operation or ""):
        return label
    if not operation or operation == entry.message_code:
        command = entry.target.rsplit(".", 1)[-1]
        if command in _OPERATIONS:
            return _OPERATIONS[command]
    if not operation or "." in operation:
        parts = log_message_code(entry).split(".")
        operation = parts[1] if len(parts) >= 3 else ""
    if operation in _OPERATIONS:
        return _OPERATIONS[operation]
    # Shell-level failures have no action; name the affected area instead of an error suffix.
    return {
        "credentials": "인증정보 처리",
        "auth": "인증",
        "mfa": "추가 인증",
        "ec2": "EC2 작업",
        "rds": "RDS 연결 설정",
        "s3": "S3 파일 작업",
        "secret": "비밀정보 처리",  # nosec B105 - public UI label, not a credential
        "settings": "설정 변경",
        "logs": "작업 기록 처리",
        "diagnostics": "진단 로그 내보내기",
        "profile": "프로필 관리",
        "legacy": "기존 설정 가져오기",
        "drive": "드라이브 연결",
        "plugin": "연결 프로그램 실행",
        "session": "연결 세션 관리",
        "aws": "AWS 요청",
        "dpapi": "인증정보 보안 처리",
        "private_file_access": "파일 접근 보호",
        "runtime": "실행 환경 관리",
    }.get(log_message_code(entry).split(".", 1)[0], "작업")


def log_message(entry: ManagedLogEntry) -> str:
    """Use approved copy; never turn arbitrary technical causes into user instructions."""
    code = log_message_code(entry)
    known_error = error_message(code)
    if known_error is not None:
        return " ".join(known_error)
    if code in _SPECIAL_MESSAGES:
        return _SPECIAL_MESSAGES[code]
    operation = log_operation(entry)
    subject = operation if operation.endswith("작업") else f"{operation} 작업"
    result = ExecutionResult.from_legacy(entry.result)
    if result is ExecutionResult.FAILURE:
        guidance = _CATEGORY_GUIDANCE.get(
            entry.error_category, "오류 코드와 연결 상태를 확인한 뒤 다시 시도하세요."
        )
        return f"{subject}에 실패했습니다. {guidance}"
    if entry.feature == "rds" and log_operation(entry) == _OPERATIONS["stop"]:
        try:
            metadata = json.loads(entry.metadata_json)
        except (TypeError, ValueError):
            metadata = None  # Legacy diagnostics may lack valid metadata; retain the outcome.
        if isinstance(metadata, dict) and metadata.get("reason") == "max_duration":
            return "설정한 세션 유지 시간이 만료되어 RDS 연결을 종료했습니다."
    # Existing human-written Korean details remain useful, including legacy file entries.
    detail = entry.masked_detail or code
    if re.search("[가-힣]", detail):
        return detail
    if result is ExecutionResult.CANCELLED:
        return f"{subject}을 취소했습니다."
    if entry.phase is ExecutionPhase.STARTED:
        return f"{subject}을 시작했습니다."
    if entry.phase is ExecutionPhase.PROGRESS:
        return f"{subject}을 진행 중입니다."
    if result is ExecutionResult.WARNING:
        return f"{subject} 중 확인이 필요한 사항이 발생했습니다. 상세 정보를 확인하세요."
    return f"{subject}을 완료했습니다."
