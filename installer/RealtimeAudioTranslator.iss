#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef ReleaseTag
  #error ReleaseTag is required
#endif

[Setup]
AppId={{45F42C97-0F7E-48D8-9F71-1894BD340C25}
AppName=Realtime Audio Translator
AppVersion={#AppVersion}
AppPublisher=Honguan
AppPublisherURL=https://github.com/Honguan/Real-time-audio
AppSupportURL=https://github.com/Honguan/Real-time-audio/issues
DefaultDirName={localappdata}\Programs\RealtimeAudioTranslator
DefaultGroupName=Realtime Audio Translator
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=RealtimeAudioTranslator-{#ReleaseTag}-setup
SetupIconFile={#RepoRoot}\assets\icon.ico
UninstallDisplayIcon={app}\RealtimeAudioTranslator.exe
LicenseFile={#RepoRoot}\LICENSE
InfoBeforeFile={#RepoRoot}\THIRD_PARTY_NOTICES.md
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchiveExtraction=enhanced/nopassword
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=Honguan
VersionInfoDescription=Realtime Audio Translator Installer
VersionInfoProductName=Realtime Audio Translator

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "zhTW"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"

[Types]
Name: "recommended"; Description: "建議安裝（App 與偵測到的 runtime）"
Name: "app"; Description: "只安裝 App"
Name: "custom"; Description: "自訂安裝"; Flags: iscustom

[Components]
Name: "runtime"; Description: "從 Faster-Whisper-XXL 上游下載語音辨識 runtime"; Types: recommended
Name: "runtime\core"; Description: "CPU / CUDA 共用 runtime 核心（約 1.4 GB）"; Types: recommended
Name: "runtime\cuda"; Description: "NVIDIA CUDA 12 DLL（約 0.85 GB）"; Types: recommended; Check: IsCudaAvailable

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; Flags: unchecked
Name: "vbcableguide"; Description: "安裝後開啟 VB-CABLE 官方下載說明（不會自動安裝驅動程式）"; Flags: unchecked; Check: VBCableMissing

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\SBOM.cdx.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\docs\README_QUICK_START_zh-TW.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\scripts\detect_installer_hardware.ps1"; Flags: dontcopy
Source: "{#RepoRoot}\scripts\normalize_installer_runtime.ps1"; Flags: dontcopy
Source: "{#RuntimeCoreUrl}"; DestDir: "{%USERPROFILE}\.realtime-audio\runtime\cuda12"; DestName: "runtime-core.7z"; ExternalSize: {#RuntimeCoreSize}; Hash: "{#RuntimeCoreHash}"; Components: runtime\core; Flags: external download extractarchive ignoreversion recursesubdirs createallsubdirs uninsneveruninstall
Source: "{#RuntimeCudaUrl}"; DestDir: "{%USERPROFILE}\.realtime-audio\runtime\cuda12"; DestName: "runtime-cuda.7z"; ExternalSize: {#RuntimeCudaSize}; Hash: "{#RuntimeCudaHash}"; Components: runtime\cuda; Flags: external download extractarchive ignoreversion recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{autoprograms}\Realtime Audio Translator"; Filename: "{app}\RealtimeAudioTranslator.exe"
Name: "{autodesktop}\Realtime Audio Translator"; Filename: "{app}\RealtimeAudioTranslator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RealtimeAudioTranslator.exe"; Description: "啟動 Realtime Audio Translator"; Flags: nowait postinstall skipifsilent
Filename: "https://vb-audio.com/Cable/"; Description: "開啟 VB-CABLE 官方下載頁"; Tasks: vbcableguide; Flags: shellexec postinstall skipifsilent unchecked

[Code]
var
  CudaAvailable: Boolean;
  VbCableDetected: Boolean;
  HardwareData: TArrayOfString;
  RemoveRuntimeData: Boolean;
  RemoveModelData: Boolean;
  RemoveSettingsData: Boolean;
  RemoveLogData: Boolean;
  HardwarePage: TOutputMsgWizardPage;

function HardwareValue(const Key: String): String;
var
  I: Integer;
  Prefix: String;
begin
  Result := '';
  Prefix := Key + '=';
  for I := 0 to GetArrayLength(HardwareData) - 1 do
    if Pos(Prefix, HardwareData[I]) = 1 then begin
      Result := Copy(HardwareData[I], Length(Prefix) + 1, MaxInt);
      Exit;
    end;
end;

function IsCudaAvailable: Boolean;
begin
  Result := CudaAvailable;
end;

function VBCableMissing: Boolean;
begin
  Result := not VbCableDetected;
end;

procedure InitializeWizard;
var
  DetectScript: String;
  OutputPath: String;
  Params: String;
  ResultCode: Integer;
  Summary: String;
  VbStatus: String;
begin
  ExtractTemporaryFile('detect_installer_hardware.ps1');
  ExtractTemporaryFile('normalize_installer_runtime.ps1');
  DetectScript := ExpandConstant('{tmp}\detect_installer_hardware.ps1');
  OutputPath := ExpandConstant('{tmp}\hardware.txt');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + AddQuotes(DetectScript) +
    ' -OutputPath ' + AddQuotes(OutputPath) + ' -UseSystemDetection';
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) or
    (not LoadStringsFromFile(OutputPath, HardwareData)) then begin
    SetArrayLength(HardwareData, 4);
    HardwareData[0] := 'runtime=cpu';
    HardwareData[1] := 'gpu_count=0';
    HardwareData[2] := 'vram_gb=0';
    HardwareData[3] := 'vb_cable=false';
  end;
  CudaAvailable := HardwareValue('runtime') = 'cuda';
  VbCableDetected := HardwareValue('vb_cable') = 'true';
  if Lowercase(ExpandConstant('{param:TYPE|}')) <> 'app' then begin
    if CudaAvailable then
      WizardSelectComponents('runtime,runtime\core,runtime\cuda')
    else
      WizardSelectComponents('runtime,runtime\core');
  end;
  if VbCableDetected then
    VbStatus := '已偵測'
  else
    VbStatus := '未偵測';
  Summary := '偵測到 GPU 數量：' + HardwareValue('gpu_count') + #13#10 +
    '最大 VRAM：約 ' + HardwareValue('vram_gb') + ' GB' + #13#10 +
    '建議 runtime：' + Uppercase(HardwareValue('runtime')) + #13#10 +
    'VB-CABLE：' + VbStatus + #13#10#13#10 +
    'Runtime 會在您選取組件後直接從上游下載並驗證 SHA-256；CPU 模式不下載 CUDA DLL。' + #13#10 +
    '模型因上游未完整明示成品再散布條款，請在 App 內直接向 Argos 下載。';
  HardwarePage := CreateOutputMsgPage(wpWelcome, '硬體與選用元件',
    '安裝程式不會靜默安裝第三方驅動程式', Summary);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  ResultCode: Integer;
  Device: String;
begin
  if (CurStep = ssPostInstall) and WizardIsComponentSelected('runtime\core') then begin
    if WizardIsComponentSelected('runtime\cuda') then
      Device := 'cuda'
    else
      Device := 'cpu';
    Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
      AddQuotes(ExpandConstant('{tmp}\normalize_installer_runtime.ps1')) +
      ' -RuntimeRoot ' + AddQuotes(ExpandConstant('{%USERPROFILE}\.realtime-audio\runtime\cuda12')) +
      ' -Device ' + Device;
    if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      RaiseException('Runtime 安裝驗證失敗。請重試，或改用 App 的手動匯入功能。');
  end;
end;

function AskRemove(const Description: String): Boolean;
begin
  Result := SuppressibleMsgBox(Description + #13#10 + '選擇「否」會保留資料。', mbConfirmation,
    MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
end;

function InitializeUninstall: Boolean;
begin
  RemoveRuntimeData := AskRemove('是否移除下載的 runtime？');
  RemoveModelData := AskRemove('是否移除下載的模型？');
  RemoveSettingsData := AskRemove('是否移除設定與詞彙表？');
  RemoveLogData := AskRemove('是否移除紀錄、匯出檔與快取？');
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserRoot: String;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  UserRoot := ExpandConstant('{%USERPROFILE}\.realtime-audio');
  if RemoveRuntimeData then
    DelTree(UserRoot + '\runtime', True, True, True);
  if RemoveModelData then
    DelTree(UserRoot + '\models', True, True, True);
  if RemoveSettingsData then begin
    DelTree(UserRoot + '\config', True, True, True);
    DeleteFile(UserRoot + '\commands.json');
    DeleteFile(UserRoot + '\glossary.json');
  end;
  if RemoveLogData then begin
    DelTree(UserRoot + '\logs', True, True, True);
    DelTree(UserRoot + '\exports', True, True, True);
    DelTree(UserRoot + '\cache', True, True, True);
  end;
  RemoveDir(UserRoot);
end;
