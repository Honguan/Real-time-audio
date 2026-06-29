#define MyAppName "Realtime Audio Translator"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Local"
#define MyAppExeName "RealtimeAudioTranslator.exe"

[Setup]
AppId={{9A78A7C0-45E7-4D9D-8E60-A578EE020E95}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Realtime Audio Translator
DefaultGroupName={#MyAppName}
OutputDir=..\installer-output
OutputBaseFilename=RealtimeAudioTranslatorSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico

[Files]
Source: "..\dist\RealtimeAudioTranslator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他圖示："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "啟動 {#MyAppName}"; Flags: nowait postinstall skipifsilent
