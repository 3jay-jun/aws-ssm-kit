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


def error_message(message_code: str) -> tuple[str, str] | None:
    """Share Korean error copy with execution history without recreating exceptions."""
    return _MESSAGES.get(message_code)


def map_error(error: ApplicationError) -> GuiErrorViewModel:
    """Translate typed failures without exposing credentials or technical causes."""

    title, message = error_message(error.message_code) or (
        "작업을 완료하지 못했습니다",
        "잠시 후 다시 시도하거나 작업 기록을 확인하세요.",
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
    "runtime.winfsp.unsupported": (
        "WinFsp 버전을 확인하세요",
        "지원하는 WinFsp 2.x 설치 상태를 확인하세요.",
    ),
    "runtime.winfsp.confirm_required": (
        "교체 확인이 필요합니다",
        "공용 WinFsp를 사용하는 마운트를 해제하고 업데이트/복구를 확인하세요.",
    ),
    "runtime.winfsp.payload_missing": (
        "설치 파일을 열 수 없습니다",
        "동봉된 prerequisites 폴더를 확인하거나 전체 배포 파일을 다시 받으세요.",
    ),
    "runtime.winfsp.verification_failed": (
        "설치 파일 검증에 실패했습니다",
        "공식 배포 파일을 다시 받으세요. 설치는 실행하지 않았습니다.",
    ),
    "runtime.winfsp.observe_failed": (
        "WinFsp 상태를 확인할 수 없습니다",
        "환경 진단을 다시 실행하고 Windows 설치 상태를 확인하세요.",
    ),
    "runtime.winfsp.start_failed": (
        "WinFsp 설치를 시작할 수 없습니다",
        "Windows 관리자 승인 및 설치 권한을 확인하세요.",
    ),
    "drive.runtime.rclone_missing": (
        "rclone 실행 파일이 없습니다",
        "배포 폴더의 rclone.exe가 필요합니다. "
        "소스 실행 시 scripts/setup-drive-runtime.ps1을 실행하세요.",
    ),
    "drive.runtime.winfsp_missing": (
        "WinFsp 설치가 필요합니다",
        "Windows 드라이브 연결에 필요한 WinFsp가 설치되지 않았습니다. "
        "공식 WinFsp Core를 관리자 권한으로 설치한 뒤 다시 연결하세요.",
    ),
    "drive.runtime.missing": (
        "드라이브 실행 환경이 없습니다",
        "환경 진단에서 rclone과 WinFsp 설치 상태를 확인하세요.",
    ),
    "drive.letter.busy": (
        "드라이브 문자가 사용 중입니다",
        "다른 드라이브 문자를 선택한 뒤 다시 저장하거나 연결하세요.",
    ),
    "drive.letter.invalid": ("드라이브 문자를 확인하세요", "D:부터 Z: 사이의 문자를 선택하세요."),
    "drive.prefix.invalid": (
        "드라이브 경로를 확인하세요",
        "상대 경로를 입력하세요. 예약 이름이나 상위 경로(..)는 사용할 수 없습니다.",
    ),
    "drive.cache.pending": (
        "아직 안전하게 해제할 수 없습니다",
        "열린 파일을 닫고 네트워크·인증을 확인한 뒤 재시도하세요. 미전송 데이터는 보존됩니다. "
        "오류 후에는 재연결해 전송을 복구하세요.",
    ),
    "drive.cache.full": (
        "캐시 공간이 부족합니다",
        "로컬 디스크의 여유 공간을 확보하세요. 미전송 캐시 파일은 삭제하지 마세요.",
    ),
    "drive.transfer.failed": (
        "파일 전송에 실패했습니다",
        "네트워크와 S3 권한을 확인하세요. 토큰이 만료되었다면 인증 갱신 / 재연결을 선택하세요.",
    ),
    "drive.control.failed": (
        "연결 상태를 확인할 수 없습니다",
        "드라이브와 캐시를 보존했습니다. 잠시 후 재시도하거나 환경 진단을 확인하세요.",
    ),
    "drive.process.failed": (
        "드라이브 연결이 종료되었습니다",
        "캐시는 보존됩니다. 재연결하여 미전송 파일을 복구하세요.",
    ),
    "drive.observe.failed": (
        "Windows 드라이브 상태가 일치하지 않습니다",
        "연결을 해제한 뒤 재시도하세요. 오류가 지속되면 환경 진단을 확인하세요.",
    ),
    "drive.owned_elsewhere": (
        "다른 앱에서 사용 중인 연결입니다",
        "이 드라이브를 연결한 AWS-SSM-KIT 창 또는 CLI에서 해제하세요.",
    ),
    "drive.active": (
        "연결 중인 드라이브입니다",
        "먼저 안전하게 연결을 해제한 뒤 설정을 수정하세요.",
    ),
    "drive.storage.failed": (
        "드라이브 설정을 저장하지 못했습니다",
        "같은 이름의 연결이 있는지, 선택한 프로필이 존재하는지 확인하세요.",
    ),
    "drive.not_found": (
        "저장된 드라이브가 없습니다",
        "목록을 새로고침하고 연결 설정을 다시 선택하세요.",
    ),
    "drive.not_connected": ("연결되지 않은 마운트입니다", "연결한 뒤 탐색기를 열어 주세요."),
    "drive.explorer.failed": ("탐색기를 열 수 없습니다", "연결 상태와 Windows 경로를 확인하세요."),
    "drive.closing": ("앱을 종료하는 중입니다", "연결 정리가 끝날 때까지 기다려 주세요."),
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
    "rds.session.duration.invalid": (
        "세션 유지 시간이 올바르지 않습니다",
        "1시간, 8시간, 12시간 또는 24시간을 선택하세요.",
    ),
    "profile.session_duration.invalid": (
        "인증 유지 시간이 올바르지 않습니다",
        "1시간, 8시간, 12시간, 24시간 또는 36시간을 선택하세요.",
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
    "credentials.shared_file.write_failed": (
        "AWS 공용 인증정보를 저장하지 못했습니다",
        "%USERPROFILE%\\.aws\\credentials 파일 권한과 형식을 확인한 뒤 다시 인증하세요.",
    ),
    "credentials.shared_file.profile.invalid": (
        "AWS 공용 프로필을 만들 수 없습니다",
        "프로필을 다시 저장한 뒤 인증을 갱신하세요.",
    ),
    "credentials.shared_file.session_token.required": (
        "임시 인증정보가 완전하지 않습니다",
        "토큰을 다시 발급받으세요.",
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
        "앱은 계속 실행됩니다. 작업 기록을 확인한 뒤 다시 시도하세요.",
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
        "작업 기록을 확인한 뒤 새 조회를 시작하세요.",
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
    "aws.network.error": (
        "AWS에 연결할 수 없습니다",
        "인터넷 연결과 VPN, 방화벽 설정을 확인한 뒤 다시 시도하세요.",
    ),
    "aws.request.failed": (
        "AWS 요청이 처리되지 않았습니다",
        "대상 리소스와 요청 설정을 확인하고 오류 코드 및 요청 ID로 원인을 확인하세요.",
    ),
    "credentials.access_key.inactive": (
        "사용할 수 없는 접근 키입니다",
        "키가 활성 상태인지 확인하거나 유효한 인증정보로 프로필을 수정하세요.",
    ),
    "credentials.invalid": (
        "AWS 인증정보가 올바르지 않습니다",
        "접근 키와 비밀 키가 같은 계정의 유효한 키인지 확인하세요.",
    ),
    "credentials.session.expired": (
        "인증 세션이 만료되었습니다",
        "인증을 갱신한 뒤 다시 실행하세요.",
    ),
    "credentials.session_token.invalid": (
        "세션 토큰이 올바르지 않습니다",
        "유효한 임시 인증정보로 인증을 갱신하세요.",
    ),
    "credentials.expiration.invalid": (
        "인증 만료 시간을 확인할 수 없습니다",
        "Windows 시간 설정을 확인하고 인증을 갱신하세요.",
    ),
    "credentials.decrypt.failed": (
        "저장된 인증정보를 읽을 수 없습니다",
        "저장 당시의 Windows 사용자로 실행하거나 인증정보를 다시 등록하세요.",
    ),
    "credentials.encrypt.failed": (
        "인증정보를 안전하게 저장하지 못했습니다",
        "Windows 사용자 및 저장 폴더 권한을 확인하고 다시 저장하세요.",
    ),
    "dpapi.entropy.decrypt_failed": (
        "암호화 보조 정보를 읽을 수 없습니다",
        "저장 당시의 Windows 사용자로 실행하고 앱 데이터 폴더를 확인하세요.",
    ),
    "dpapi.entropy.invalid": (
        "암호화 보조 정보가 손상되었습니다",
        "기존 데이터를 보존하고 진단 로그를 내보내 문의하세요.",
    ),
    "dpapi.entropy.write_failed": (
        "암호화 보조 정보를 저장하지 못했습니다",
        "앱 데이터 폴더의 쓰기 권한과 디스크 공간을 확인하세요.",
    ),
    "dpapi.windows_required": (
        "Windows 보안 저장 기능이 필요합니다",
        "지원되는 Windows 환경에서 실행하세요.",
    ),
    "diagnostics.destination.inside_logs": (
        "로그 폴더 안에는 내보낼 수 없습니다",
        "로그 폴더 밖의 저장 위치를 선택하세요.",
    ),
    "diagnostics.destination.zip_required": (
        "진단 파일은 ZIP 형식으로 저장해야 합니다",
        "확장자가 .zip인 저장 파일을 선택하세요.",
    ),
    "diagnostics.export.failed": (
        "진단 로그를 내보내지 못했습니다",
        "저장 경로의 쓰기 권한과 디스크 공간을 확인하세요.",
    ),
    "drive.owner.invalid": (
        "연결할 프로필을 확인할 수 없습니다",
        "드라이브를 저장한 프로필을 선택한 뒤 다시 연결하세요.",
    ),
    "drive.windows.required": (
        "Windows 드라이브 연결 기능이 필요합니다",
        "지원되는 Windows 환경에서 실행하세요.",
    ),
    "internal.error": (
        "예상하지 못한 오류가 발생했습니다",
        "다시 시도해도 문제가 계속되면 진단 로그를 내보내 문의하세요.",
    ),
    "legacy.import.confirmation.required": (
        "기존 설정 가져오기에 동의가 필요합니다",
        "가져올 파일과 항목을 확인하고 가져오기를 승인하세요.",
    ),
    "legacy.ini.aws.required": (
        "기존 파일에 AWS 설정이 없습니다",
        "AWS 설정이 있는 INI 파일을 선택하세요.",
    ),
    "legacy.ini.extension.invalid": (
        "가져올 파일 형식이 올바르지 않습니다",
        "기존 설정의 .ini 파일을 선택하세요.",
    ),
    "legacy.ini.not_found": (
        "가져올 설정 파일이 없습니다",
        "파일 경로를 확인하고 다시 선택하세요.",
    ),
    "legacy.ini.read.failed": (
        "기존 설정 파일을 읽을 수 없습니다",
        "파일 형식과 읽기 권한을 확인하세요.",
    ),
    "legacy.profile.name.conflict": (
        "가져올 프로필 이름이 중복됩니다",
        "다른 프로필 이름을 지정하고 다시 가져오세요.",
    ),
    "legacy.rds.incomplete": (
        "기존 RDS 설정이 불완전합니다",
        "호스트와 포트 등 필수 항목을 확인하세요.",
    ),
    "legacy.rds.port.invalid": (
        "기존 RDS 포트가 올바르지 않습니다",
        "1부터 65535 사이의 포트 번호로 설정을 수정하세요.",
    ),
    "logs.limit.invalid": (
        "기록 조회 개수가 올바르지 않습니다",
        "조회 개수는 1부터 10000 사이로 지정하세요.",
    ),
    "logs.metadata.invalid": (
        "작업 기록의 부가 정보 형식이 올바르지 않습니다",
        "작업을 다시 실행하고 문제가 계속되면 진단 로그를 내보내 문의하세요.",
    ),
    "logs.repository.required": (
        "작업 기록 저장소가 준비되지 않았습니다",
        "앱을 다시 시작하고 데이터 폴더의 접근 권한을 확인하세요.",
    ),
    "logs.sanitizer.required": (
        "작업 기록의 민감정보 보호 기능이 준비되지 않았습니다",
        "앱을 다시 시작하고 문제가 계속되면 문의하세요.",
    ),
    "logs.storage.failed": (
        "작업 기록을 저장하거나 불러오지 못했습니다",
        "데이터 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "logs.time.invalid": (
        "작업 기록의 시간 정보가 올바르지 않습니다",
        "Windows 날짜와 시간 설정을 확인하고 앱을 다시 시작하세요.",
    ),
    "logs.write.failed": (
        "작업 기록을 남기지 못했습니다",
        "로그 폴더의 쓰기 권한과 디스크 공간을 확인하세요.",
    ),
    "logs.directory.open_failed": (
        "로그 폴더를 열 수 없습니다",
        "폴더 경로와 접근 권한을 확인하세요.",
    ),
    "plugin.environment.invalid": (
        "연결 프로그램 실행 환경이 올바르지 않습니다",
        "배포 파일을 모두 압축 해제했는지 확인하고 앱을 다시 시작하세요.",
    ),
    "plugin.execute.failed": (
        "연결 프로그램 실행에 실패했습니다",
        "연결 프로그램 설치 상태와 실행 권한을 확인하세요.",
    ),
    "plugin.exit.nonzero": (
        "연결 프로그램이 오류로 종료되었습니다",
        "네트워크와 대상 인스턴스 상태를 확인한 뒤 다시 연결하세요.",
    ),
    "plugin.launch.failed": (
        "연결 프로그램을 시작하지 못했습니다",
        "동봉된 연결 프로그램 파일과 실행 권한을 확인하세요.",
    ),
    "plugin.status.failed": (
        "연결 프로그램 상태를 확인하지 못했습니다",
        "작업 기록을 확인하고 연결 상태를 다시 확인하세요.",
    ),
    "plugin.stop.failed": (
        "연결 프로그램을 종료하지 못했습니다",
        "현재 연결 상태를 확인한 뒤 종료를 다시 시도하세요.",
    ),
    "plugin.version.unsupported": (
        "연결 프로그램 버전이 지원되지 않습니다",
        "앱에 동봉된 지원 버전의 연결 프로그램을 사용하세요.",
    ),
    "plugin.wait.failed": (
        "연결 프로그램 종료를 확인하지 못했습니다",
        "연결 상태를 확인하고 종료를 다시 시도하세요.",
    ),
    "private_file_access.current_user_failed": (
        "현재 Windows 사용자를 확인하지 못했습니다",
        "현재 사용자 계정 상태를 확인하고 앱을 다시 시작하세요.",
    ),
    "private_file_access.restrict_failed": (
        "데이터 파일 접근 권한을 제한하지 못했습니다",
        "앱 데이터 폴더의 소유자와 파일 권한을 확인하세요.",
    ),
    "private_file_access.windows_required": (
        "Windows 파일 접근 보호 기능이 필요합니다",
        "지원되는 Windows 환경에서 실행하세요.",
    ),
    "profile.connections.active": (
        "이 프로필에 실행 중인 연결이 있습니다",
        "연결을 모두 종료한 뒤 프로필을 변경하거나 삭제하세요.",
    ),
    "profile.create.failed": (
        "프로필을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "profile.id.required": (
        "프로필을 선택해야 합니다",
        "사용할 프로필을 선택한 뒤 다시 실행하세요.",
    ),
    "profile.update.failed": (
        "프로필 변경사항을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "rds.session.create.failed": (
        "RDS 연결 설정을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "rds.session.host.invalid": (
        "RDS 호스트 주소가 올바르지 않습니다",
        "도메인 이름 또는 IP 주소를 입력하세요.",
    ),
    "rds.session.id.required": (
        "저장된 RDS 연결을 선택해야 합니다",
        "연결 목록에서 사용할 항목을 선택하세요.",
    ),
    "rds.session.local_port.invalid": (
        "내 컴퓨터의 연결 포트가 올바르지 않습니다",
        "1부터 65535 사이의 포트 번호를 입력하세요.",
    ),
    "rds.session.name.duplicate": (
        "이미 사용 중인 RDS 연결 이름입니다",
        "다른 연결 이름을 입력하세요.",
    ),
    "rds.session.name.required": ("RDS 연결 이름이 필요합니다", "저장할 연결 이름을 입력하세요."),
    "rds.session.not_found": (
        "저장된 RDS 연결을 찾을 수 없습니다",
        "목록을 새로고침하고 사용할 연결을 다시 선택하세요.",
    ),
    "rds.session.persistence.invalid": (
        "저장된 RDS 연결 설정을 읽을 수 없습니다",
        "데이터를 보존하고 진단 로그를 내보내 문의하세요.",
    ),
    "rds.session.profile_id.invalid": (
        "RDS 연결의 프로필이 올바르지 않습니다",
        "유효한 프로필을 선택하고 연결 설정을 저장하세요.",
    ),
    "rds.session.remote_port.invalid": (
        "RDS 서버 포트가 올바르지 않습니다",
        "1부터 65535 사이의 포트 번호를 입력하세요.",
    ),
    "rds.session.select_target_must_be_empty": (
        "중계 인스턴스 선택 방식과 저장값이 맞지 않습니다",
        "매번 선택하는 방식에서는 고정 인스턴스 값을 비우세요.",
    ),
    "rds.session.target_instance_id.invalid": (
        "중계 인스턴스 ID가 올바르지 않습니다",
        "목록에서 인스턴스를 선택하거나 유효한 인스턴스 ID를 입력하세요.",
    ),
    "rds.session.update.failed": (
        "RDS 연결 변경사항을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "rds.tunnel.managed_runner.unavailable": (
        "RDS 연결 관리 기능이 준비되지 않았습니다",
        "앱을 다시 시작하고 배포 파일이 모두 있는지 확인하세요.",
    ),
    "rds.tunnel.operation.not_found": (
        "실행 중인 RDS 연결을 찾을 수 없습니다",
        "연결 상태를 새로고침하고 종료 여부를 확인하세요.",
    ),
    "rds.tunnel.target_required": (
        "중계할 EC2 인스턴스가 필요합니다",
        "접속 가능한 중계 인스턴스를 선택하세요.",
    ),
    "secret.field.not_found": (
        "요청한 비밀정보 항목이 없습니다",
        "조회한 값의 항목 이름을 확인하세요.",
    ),
    "secret.field.update.unsupported": (
        "선택한 항목을 수정할 수 없습니다",
        "지원되는 항목인지 확인한 뒤 수정하세요.",
    ),
    "secret.id.required": (
        "조회할 비밀정보를 지정해야 합니다",
        "비밀정보 이름 또는 ARN을 입력하세요.",
    ),
    "secret.lookup.mode.invalid": (
        "비밀정보 조회 방식이 올바르지 않습니다",
        "직접 조회 또는 EC2 경유 조회를 선택하세요.",
    ),
    "secret.saved.create.failed": (
        "비밀정보 조회 설정을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "secret.saved.id.required": (
        "저장된 비밀정보를 선택해야 합니다",
        "저장 목록에서 조회할 항목을 선택하세요.",
    ),
    "secret.saved.lookup_mode.invalid": (
        "저장된 비밀정보의 조회 방식이 올바르지 않습니다",
        "직접 조회 또는 EC2 경유 조회로 설정을 수정하세요.",
    ),
    "secret.saved.persistence.invalid": (
        "저장된 비밀정보 설정을 읽을 수 없습니다",
        "데이터를 보존하고 진단 로그를 내보내 문의하세요.",
    ),
    "secret.saved.profile.invalid": (
        "비밀정보의 프로필이 올바르지 않습니다",
        "유효한 프로필을 선택하고 다시 저장하세요.",
    ),
    "secret.saved.relay.required": (
        "EC2 경유 조회에 중계 인스턴스가 필요합니다",
        "중계할 EC2 인스턴스를 선택하세요.",
    ),
    "secret.saved.relay.unexpected": (
        "직접 조회에는 중계 인스턴스를 지정할 수 없습니다",
        "중계 인스턴스를 비우거나 EC2 경유 조회로 변경하세요.",
    ),
    "secret.saved.unavailable": (
        "저장된 비밀정보 기능을 사용할 수 없습니다",
        "앱을 다시 시작하고 데이터 저장소 상태를 확인하세요.",
    ),
    "secret.saved.update.failed": (
        "비밀정보 설정 변경사항을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "secret.saved.upsert.failed": (
        "비밀정보 조회 결과를 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "secret.saved.value.invalid": (
        "저장할 비밀정보 값의 형식이 올바르지 않습니다",
        "값을 문자열 형식으로 입력하세요.",
    ),
    "session.cancel.cleanup_failed": (
        "취소 후 연결 정리를 완료하지 못했습니다",
        "남아 있는 연결 상태를 확인한 뒤 종료를 다시 시도하세요.",
    ),
    "session.end.failed": (
        "AWS 연결 세션을 종료하지 못했습니다",
        "네트워크와 세션 상태를 확인한 뒤 종료를 다시 시도하세요.",
    ),
    "settings.log_directory.required": (
        "로그 저장 폴더가 필요합니다",
        "로그를 저장할 폴더를 선택하세요.",
    ),
    "settings.log_directory.unwritable": (
        "로그 폴더에 기록할 수 없습니다",
        "쓰기 가능한 폴더를 선택하고 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "settings.log_level.invalid": (
        "로그 기록 수준이 올바르지 않습니다",
        "설정에서 지원되는 기록 수준을 선택하세요.",
    ),
    "settings.persistence.failed": (
        "설정을 저장하지 못했습니다",
        "데이터 폴더의 쓰기 권한과 디스크 공간을 확인하세요.",
    ),
    "settings.rollback.failed": (
        "기존 설정으로 되돌리지 못했습니다",
        "현재 설정을 확인하고 앱을 다시 시작하세요.",
    ),
    "ec2.favorite.persistence.invalid": (
        "인스턴스 즐겨찾기를 읽을 수 없습니다",
        "데이터를 보존하고 진단 로그를 내보내 문의하세요.",
    ),
    "ec2.favorite.unavailable": (
        "인스턴스 즐겨찾기 기능을 사용할 수 없습니다",
        "앱을 다시 시작하고 저장소 상태를 확인하세요.",
    ),
    "ec2.filter.instance_state.unsupported": (
        "지원하지 않는 인스턴스 상태입니다",
        "상태 필터에서 지원되는 항목을 선택하세요.",
    ),
    "ec2.instance.not_found": (
        "대상 인스턴스를 찾을 수 없습니다",
        "프로필과 리전을 확인하고 목록을 새로고침하세요.",
    ),
    "ec2.instance_id.required": (
        "EC2 인스턴스를 선택해야 합니다",
        "목록에서 작업할 인스턴스를 선택하세요.",
    ),
    "ec2.power.unavailable": (
        "인스턴스 전원 기능을 사용할 수 없습니다",
        "앱을 다시 시작하고 실행 환경을 확인하세요.",
    ),
    "s3.delete.object.required": (
        "삭제할 파일을 선택해야 합니다",
        "폴더가 아닌 파일 한 개를 선택하세요.",
    ),
    "s3.download.cancelled": (
        "다운로드를 취소했습니다",
        "파일을 다시 받으려면 다운로드를 실행하세요.",
    ),
    "s3.download.destination.duplicate": (
        "여러 파일의 저장 경로가 겹칩니다",
        "파일 이름과 저장 위치를 확인하세요.",
    ),
    "s3.download.destination.exists": (
        "저장 위치에 같은 이름의 파일이 있습니다",
        "기존 파일을 확인하고 덮어쓸지 선택하세요.",
    ),
    "s3.download.destination.unavailable": (
        "다운로드 파일을 저장할 수 없습니다",
        "저장 폴더의 쓰기 권한과 디스크 공간을 확인하세요.",
    ),
    "s3.download.key.invalid": (
        "다운로드할 파일 경로가 올바르지 않습니다",
        "목록을 새로고침하고 파일을 다시 선택하세요.",
    ),
    "s3.download.selection.empty": (
        "선택한 위치에 다운로드할 파일이 없습니다",
        "다른 파일이나 폴더를 선택하세요.",
    ),
    "s3.download.selection.required": (
        "다운로드할 항목을 선택해야 합니다",
        "다운로드할 파일이나 폴더를 선택하세요.",
    ),
    "s3.location.create.failed": (
        "S3 저장 위치를 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "s3.location.id.required": (
        "저장된 S3 위치를 선택해야 합니다",
        "저장 위치 목록에서 항목을 선택하세요.",
    ),
    "s3.location.name.duplicate": (
        "이미 사용 중인 S3 저장 위치 이름입니다",
        "다른 저장 위치 이름을 입력하세요.",
    ),
    "s3.location.name.required": (
        "S3 저장 위치 이름이 필요합니다",
        "저장할 위치 이름을 입력하세요.",
    ),
    "s3.location.persistence.invalid": (
        "저장된 S3 위치를 읽을 수 없습니다",
        "데이터를 보존하고 진단 로그를 내보내 문의하세요.",
    ),
    "s3.location.profile_id.invalid": (
        "S3 저장 위치의 프로필이 올바르지 않습니다",
        "유효한 프로필을 선택하고 다시 저장하세요.",
    ),
    "s3.location.update.failed": (
        "S3 저장 위치 변경사항을 저장하지 못했습니다",
        "저장 폴더의 접근 권한과 디스크 공간을 확인하세요.",
    ),
    "s3.multipart.configuration.invalid": (
        "분할 업로드 설정이 올바르지 않습니다",
        "진단 로그를 내보내 업로드 설정을 문의하세요.",
    ),
    "s3.prefix.invalid": (
        "S3 폴더 경로가 올바르지 않습니다",
        "폴더 경로의 잘못된 문자를 확인하고 다시 입력하세요.",
    ),
    "s3.upload.conflict_policy.conflicting": (
        "업로드 중복 처리 설정이 서로 충돌합니다",
        "덮어쓰기 또는 건너뛰기 중 하나를 선택하세요.",
    ),
    "s3.upload.conflict_policy.invalid": (
        "업로드 중복 처리 방식이 올바르지 않습니다",
        "지원되는 덮어쓰기 또는 건너뛰기 방식을 선택하세요.",
    ),
    "s3.upload.conflict_policy.required": (
        "동일한 파일이 있을 때의 처리 방식을 선택해야 합니다",
        "기존 파일을 덮어쓸지 건너뛸지 선택하세요.",
    ),
    "s3.upload.source.required": ("업로드할 파일이 필요합니다", "올릴 파일이나 폴더를 선택하세요."),
    "s3.upload.source.symlink": (
        "바로가기 또는 연결된 파일은 업로드할 수 없습니다",
        "연결 대상의 실제 파일이나 폴더를 선택하세요.",
    ),
    "s3.upload.source.unreadable": (
        "업로드할 파일을 읽을 수 없습니다",
        "파일이 존재하는지와 읽기 권한을 확인하세요.",
    ),
    "s3.upload.target.invalid": (
        "업로드 대상 경로가 올바르지 않습니다",
        "버킷과 폴더 경로를 확인하세요.",
    ),
    "s3.upload.target.duplicate": (
        "여러 업로드 파일의 대상 경로가 겹칩니다",
        "파일 이름과 대상 폴더를 확인하세요.",
    ),
    "drive.id.invalid": (
        "드라이브 번호가 올바르지 않습니다",
        "저장된 드라이브 목록에서 유효한 항목을 선택하세요.",
    ),
}


_FIELD_BY_CODE: dict[str, str] = {
    "profile.name.required": "profile_name_input",
    "profile.name.duplicate": "profile_name_input",
    "profile.region.invalid": "profile_region_input",
    "profile.account_id.invalid": "profile_account_input",
    "profile.user_id.invalid": "profile_user_input",
    "profile.session_duration.invalid": "profile_session_duration",
    "rds.session.duration.invalid": "rds_session_duration",
    "profile.credentials.required": "profile_access_key_input",
    "profile.credentials.incomplete": "profile_access_key_input",
    "credentials.access_key.invalid": "profile_access_key_input",
    "credentials.secret_key.invalid": "profile_secret_key_input",
    "secret.id.remote.invalid": "secret_id",
    "secret.saved.identifier.invalid": "secret_id",
}
