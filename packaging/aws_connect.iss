#ifndef PackageDir
  #error PackageDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef OutputName
  #define OutputName "aws-connect-" + AppVersion + "-windows-x64-Setup"
#endif
[Setup]
AppId={{9C63A8DF-02D4-4B50-A4F3-63C7E7C31D2D}
AppName=AWS-SSM-KIT
AppVersion={#AppVersion}
DefaultDirName={autopf}\AWS-SSM-KIT
DefaultGroupName=AWS-SSM-KIT
DisableDirPage=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\aws_connect.exe
SetupIconFile={#PackageDir}\_internal\aws_connect\presentation\gui\assets\logo.ico

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; GroupDescription: "바로가기"
Name: "winfspsetup"; Description: "S3 마운트용 WinFsp 설치 (없을 때만 설치)"; GroupDescription: "구성 요소"

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AWS-SSM-KIT"; Filename: "{app}\aws_connect.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AWS-SSM-KIT"; Filename: "{app}\aws_connect.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
var
  RuntimeMessage: String;

function AppInUse(): Boolean;
var
  Code: Integer;
  Root: String;
begin
  Root := ExpandConstant('{app}');
  StringChangeEx(Root, '''', '''''', True);
  // Ask Windows about file locks; never close user's terminals or mount processes.
  Code := 0;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "' +
    '$root=''' + Root + '''; ' +
    '$p=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ' +
    '($_.ExecutablePath.StartsWith($root+''\'',[StringComparison]::OrdinalIgnoreCase)) }; ' +
    'if($p){exit 1}else{exit 0}"', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := 1;
  Result := Code <> 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if AppInUse() then
    Result := '설치 폴더의 프로그램이 실행 중입니다. 트레이에서 완전 종료하고 EC2 터미널도 종료한 뒤 다시 시도하세요.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := not AppInUse();
  if not Result then
    MsgBox('프로그램과 연결이 실행 중입니다. 트레이에서 완전 종료한 뒤 제거하세요.', mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Args: String;
begin
  if CurStep = ssPostInstall then begin
    if not WizardIsTaskSelected('winfspsetup') then begin
      RuntimeMessage := '앱 설치 완료. S3 마운트가 필요하면 앱의 WinFsp 설치 버튼을 사용하세요.';
      Exit;
    end;
    Args := 'runtime winfsp install --output json';
    RuntimeMessage := '앱 설치 완료. S3 마운트의 환경 진단에서 WinFsp 상태를 확인하세요.';
    if Exec(ExpandConstant('{app}\aws_connect_cli.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
      if Code = 0 then RuntimeMessage := '앱과 S3 마운트 런타임 준비가 완료되었습니다.'
      else if Code = 3010 then RuntimeMessage := '앱 설치 완료. WinFsp 사용 전 Windows를 재부팅하세요.'
      else RuntimeMessage := '앱 설치 완료 / S3 마운트 구성 필요. 앱의 WinFsp 설치 버튼으로 재시도하세요.';
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then WizardForm.FinishedLabel.Caption := RuntimeMessage;
end;
