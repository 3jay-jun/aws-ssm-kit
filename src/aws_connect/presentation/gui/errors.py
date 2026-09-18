"""Framework-neutral, central GUI error mapping contract."""

from dataclasses import dataclass
from enum import StrEnum

from aws_connect.domain.errors import (
    APPLICATION_ERROR_TYPES,
    ApplicationError,
    AwsPermissionError,
)


class GuiErrorPresentation(StrEnum):
    TOAST = "toast"
    DIALOG = "dialog"
    FIELD = "field"


@dataclass(frozen=True, slots=True)
class GuiErrorViewModel:
    message_code: str
    correlation_id: str
    retryable: bool
    title: str
    message: str
    presentation: GuiErrorPresentation
    field_name: str | None = None


def map_error(error: ApplicationError) -> GuiErrorViewModel:
    """Translate typed failures without exposing credentials or technical causes."""

    title, message = _MESSAGES.get(
        error.message_code,
        ("작업을 완료하지 못했습니다", "잠시 후 다시 시도하거나 실행 로그를 확인하세요."),
    )
    if (
        isinstance(error, AwsPermissionError)
        and error.aws_service == "ssm"
        and error.aws_action == "SendCommand"
    ):
        title = "SSM Run Command 권한이 없습니다"
        message = (
            "앱의 자동 경유 조회에는 ssm:SendCommand 권한이 필요합니다. "
            "권한 추가가 어렵다면 EC2 접속에서 같은 인스턴스의 터미널을 연 뒤 "
            "aws secretsmanager get-secret-value 명령을 실행하세요."
        )
    if (
        isinstance(error, AwsPermissionError)
        and error.aws_service == "ssm"
        and error.aws_action == "StartSession"
    ):
        title = "EC2 터미널을 열 권한이 없습니다"
        message = (
            "터미널 대체 조회에는 ssm:StartSession 권한과 대상 인스턴스의 SSM Online 상태가 "
            "필요합니다. 관리자에게 권한을 요청하거나 권한이 있는 환경에서 같은 AWS CLI 명령을 "
            "직접 실행하세요."
        )
    if (
        isinstance(error, AwsPermissionError)
        and error.aws_service is not None
        and error.aws_action is not None
    ):
        message = f"{message} 필요한 IAM 권한: {error.aws_service}:{error.aws_action}"
    field_name = _FIELD_BY_CODE.get(error.message_code)
    return GuiErrorViewModel(
        message_code=error.message_code,
        correlation_id=error.correlation_id,
        retryable=error.retryable,
        title=title,
        message=message,
        presentation=(
            GuiErrorPresentation.FIELD
            if field_name is not None
            else GuiErrorPresentation.TOAST
            if error.retryable
            else GuiErrorPresentation.DIALOG
        ),
        field_name=field_name,
    )


def mapped_error_types_for_gui() -> frozenset[type[ApplicationError]]:
    """Declare mapper completeness without duplicating the central error list."""

    return frozenset(APPLICATION_ERROR_TYPES)


_MESSAGES: dict[str, tuple[str, str]] = {
    "s3.rename.changed": (
        "파일 상태가 변경되었습니다",
        "대상 파일이 생겼거나 원본이 변경되었습니다. 목록을 새로고침해 확인하세요.",
    ),
    "s3.rename.invalid": (
        "파일 이름을 확인하세요",
        "폴더가 아닌 파일의 이름만 입력하세요. 경로 구분자는 사용할 수 없습니다.",
    ),
    "s3.rename.exists": (
        "같은 이름의 파일이 있습니다",
        "다른 이름을 입력하세요. 기존 파일은 덮어쓰지 않습니다.",
    ),
    "s3.rename.too_large": (
        "파일이 너무 큽니다",
        "이름 변경은 5 GiB 이하 파일을 지원합니다. 원본은 유지됩니다.",
    ),
    "s3.rename.partial": (
        "복사 후 원본 삭제에 실패했습니다",
        "새 이름의 복사본과 원본이 남아 있을 수 있습니다. 목록을 새로고침해 확인하세요.",
    ),
    "profile.name.required": (
        "프로필 ID를 입력하세요",
        "고유한 사용자 지정 프로필 ID가 필요합니다.",
    ),
    "profile.name.duplicate": ("이미 사용 중인 프로필 ID입니다", "다른 프로필 ID를 입력하세요."),
    "profile.region.invalid": ("Region 형식이 올바르지 않습니다", "예: ap-northeast-2"),
    "profile.account_id.invalid": ("Account ID가 올바르지 않습니다", "12자리 숫자를 입력하세요."),
    "profile.user_id.invalid": ("IAM 사용자가 올바르지 않습니다", "IAM 사용자명을 확인하세요."),
    "profile.mfa_arn.invalid": (
        "MFA 장치 ARN이 올바르지 않습니다",
        "현재 계정의 MFA ARN을 입력하세요.",
    ),
    "profile.not_found": ("프로필이 없습니다", "프로필 관리에서 AWS 인증정보를 등록하세요."),
    "profile.credentials.required": (
        "인증정보를 입력하세요",
        "Access Key와 Secret Access Key가 모두 필요합니다.",
    ),
    "profile.credentials.incomplete": (
        "인증정보가 완전하지 않습니다",
        "키를 변경하려면 Access Key와 Secret Access Key를 모두 입력하세요.",
    ),
    "credentials.access_key.invalid": (
        "Access Key 형식이 올바르지 않습니다",
        "Access Key ID를 확인하세요.",
    ),
    "credentials.secret_key.invalid": (
        "Secret Access Key 형식이 올바르지 않습니다",
        "Secret Access Key를 확인하세요.",
    ),
    "credentials.identity_mismatch": (
        "AWS 사용자 정보가 일치하지 않습니다",
        "Account ID와 IAM 사용자, Access Key를 확인하세요.",
    ),
    "mfa.code.invalid": ("MFA 코드가 올바르지 않습니다", "6자리 숫자를 입력하세요."),
    "mfa.code.rejected": ("MFA 인증에 실패했습니다", "새 MFA 코드를 발급받아 다시 시도하세요."),
    "mfa.challenge.expired": ("MFA 요청이 만료되었습니다", "토큰 재발급을 다시 시작하세요."),
    "mfa.operation.not_resumable": (
        "이미 처리된 요청입니다",
        "토큰 재발급을 다시 시작하세요.",
    ),
    "auth.operation.not_resumable": (
        "이미 처리된 요청입니다",
        "원래 작업을 다시 시작하세요.",
    ),
    "auth.operation.invalid_state": (
        "인증 작업 상태가 올바르지 않습니다",
        "원래 작업을 다시 시작하세요.",
    ),
    "auth.operation.capacity_exceeded": (
        "대기 중인 인증 요청이 너무 많습니다",
        "열려 있는 MFA 요청을 완료하거나 취소한 뒤 다시 시도하세요.",
    ),
    "gui.task.unexpected": (
        "예상하지 못한 오류가 발생했습니다",
        "앱은 계속 실행됩니다. 실행 로그를 확인한 뒤 다시 시도하세요.",
    ),
    "ec2.target.not_online": (
        "EC2가 온라인 상태가 아닙니다",
        "목록을 새로고침한 뒤 SSM Online 인스턴스를 선택하세요.",
    ),
    "ec2.filter.status.unsupported": (
        "지원하지 않는 인스턴스 상태입니다",
        "현재 버전은 SSM Online 대상만 지원합니다.",
    ),
    "rds.tunnel.target_not_online": (
        "중계 EC2가 온라인 상태가 아닙니다",
        "온라인 중계 인스턴스를 선택한 뒤 다시 시도하세요.",
    ),
    "rds.tunnel.local_port_in_use": (
        "로컬 포트를 사용할 수 없습니다",
        "다른 로컬 포트를 선택하거나 해당 포트를 사용하는 프로그램을 종료하세요.",
    ),
    "plugin.not_found": (
        "Session Manager Plugin이 없습니다",
        "Portable ZIP에 session-manager-plugin.exe가 포함되어 있는지 확인하세요.",
    ),
    "plugin.start.failed": (
        "세션 프로세스를 시작하지 못했습니다",
        "Windows Terminal과 Session Manager Plugin 설치 상태를 확인하세요.",
    ),
    "session.operation.not_found": (
        "세션 상태를 찾을 수 없습니다",
        "목록을 새로고침한 뒤 다시 연결하세요.",
    ),
    "aws.permission.denied": (
        "AWS 권한이 부족합니다",
        "관리자에게 이 작업에 필요한 최소 권한을 요청하세요.",
    ),
    "secret.not_found": (
        "Secret을 찾을 수 없습니다",
        "Secret 이름 또는 ARN과 Region을 확인하세요.",
    ),
    "secret.value.unavailable": (
        "Secret 값을 조회할 수 없습니다",
        "Secret 상태와 KMS 복호화 권한을 확인하세요.",
    ),
    "secret.binary.unsupported": (
        "Binary Secret은 지원하지 않습니다",
        "현재 버전에서는 SecretString 형식만 조회할 수 있습니다.",
    ),
    "secret.rds_endpoint.invalid": (
        "RDS endpoint 형식이 아닙니다",
        "JSON 최상위에 유효한 host와 port가 있는지 확인하세요.",
    ),
    "secret.id.remote.invalid": (
        "Secret 식별자를 확인하세요",
        "EC2 경유 조회에는 유효한 Secret 이름 또는 ARN만 사용할 수 있습니다.",
    ),
    "secret.relay.confirmation.required": (
        "EC2 경유 조회 동의가 필요합니다",
        "원문이 SSM 명령 출력에 일시 남을 수 있음을 확인한 뒤 다시 실행하세요.",
    ),
    "secret.relay.instance.required": (
        "중계 EC2를 선택하세요",
        "SSM Online 상태인 EC2 인스턴스 한 대를 선택하세요.",
    ),
    "secret.relay.instance.not_online": (
        "중계 EC2가 온라인 상태가 아닙니다",
        "목록을 새로고침하고 SSM Online 인스턴스를 선택하세요.",
    ),
    "secret.relay.platform.unsupported": (
        "지원하지 않는 EC2 플랫폼입니다",
        "Linux 또는 Windows SSM 관리형 인스턴스를 선택하세요.",
    ),
    "secret.relay.unavailable": (
        "EC2 경유 조회를 사용할 수 없습니다",
        "앱 구성을 확인하거나 직접 조회 방식을 사용하세요.",
    ),
    "secret.relay.instance_role.permission_denied": (
        "중계 EC2 역할의 권한이 부족합니다",
        "인스턴스 역할에 Secrets Manager GetSecretValue 권한을 추가하세요.",
    ),
    "secret.relay.output.invalid": (
        "Secret 응답을 읽을 수 없습니다",
        "중계 EC2의 AWS CLI 버전과 명령 출력 상태를 확인하세요.",
    ),
    "secret.relay.command.failed": (
        "EC2 경유 조회 명령이 실패했습니다",
        "SSM Agent, AWS CLI와 인스턴스 역할 권한을 확인하세요.",
    ),
    "secret.relay.command.status_unknown": (
        "SSM 명령 상태를 확인할 수 없습니다",
        "실행 로그를 확인한 뒤 새 조회를 시작하세요.",
    ),
    "secret.relay.timeout": (
        "EC2 경유 조회 시간이 초과되었습니다",
        "SSM 연결 상태를 확인한 뒤 다시 시도하세요.",
    ),
    "secret.saved.identifier.invalid": (
        "Secret 이름이 올바르지 않습니다",
        "저장할 Secret 이름 또는 ARN을 확인하세요.",
    ),
    "secret.saved.identifier.duplicate": (
        "이미 저장된 Secret입니다",
        "왼쪽 저장 목록에서 기존 항목을 선택하거나 수정하세요.",
    ),
    "secret.saved.not_found": (
        "저장된 Secret을 찾을 수 없습니다",
        "목록을 새로고침한 뒤 다시 시도하세요.",
    ),
    "s3.location.not_found": (
        "S3 저장 위치를 찾을 수 없습니다",
        "저장 위치를 새로고침하거나 Bucket과 Prefix를 직접 입력하세요.",
    ),
    "s3.bucket.invalid": (
        "Bucket 이름이 올바르지 않습니다",
        "S3 Bucket 이름을 확인하세요.",
    ),
    "s3.upload.source.invalid": (
        "업로드 파일을 읽을 수 없습니다",
        "로컬 파일이 존재하고 읽을 수 있는지 확인하세요.",
    ),
    "s3.upload.overwrite_confirmation_required": (
        "덮어쓰기 확인이 필요합니다",
        "기존 S3 객체를 교체하려면 덮어쓰기에 명시적으로 동의하세요.",
    ),
    "s3.upload.cancelled": (
        "S3 업로드를 취소했습니다",
        "multipart 정리를 완료했습니다.",
    ),
    "s3.download.destination.invalid": (
        "파일 저장 위치를 사용할 수 없습니다",
        "존재하는 로컬 폴더를 선택하세요.",
    ),
    "s3.download.object.required": (
        "열 파일을 선택하세요",
        "폴더가 아닌 S3 파일 한 개를 선택하세요.",
    ),
    "s3.open.failed": (
        "다운로드한 파일을 열 수 없습니다",
        "저장된 파일을 Windows 탐색기에서 직접 여세요.",
    ),
    "s3.transfer.failed": (
        "S3 파일 전송에 실패했습니다",
        "네트워크와 대상 Bucket의 GetObject 또는 PutObject 권한을 확인하세요.",
    ),
}


_FIELD_BY_CODE: dict[str, str] = {
    "profile.name.required": "profile_name_input",
    "profile.name.duplicate": "profile_name_input",
    "profile.region.invalid": "profile_region_input",
    "profile.account_id.invalid": "profile_account_input",
    "profile.user_id.invalid": "profile_user_input",
    "profile.credentials.required": "profile_access_key_input",
    "profile.credentials.incomplete": "profile_access_key_input",
    "credentials.access_key.invalid": "profile_access_key_input",
    "credentials.secret_key.invalid": "profile_secret_key_input",
    "secret.id.remote.invalid": "secret_id",
    "secret.saved.identifier.invalid": "secret_id",
}
