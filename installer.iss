; PC-karukaru-YYY インストーラー定義 (Inno Setup)
; ビルド: "C:\Users\yuuma\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer.iss

#define MyAppName "PC-karukaru-YYY"
#define MyAppVersion "1.1.1"
#define MyAppExeName "PC-karukaru-YYY.exe"
#define MyAppSourceExe "dist\PC-karukaru-YYY.exe"

[Setup]
AppId={{6F2E8C1A-9D4B-4E7A-8F3C-2B6A1D9E5C4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=yuuma
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_dist
OutputBaseFilename=PC-karukaru-YYY-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
SetupIconFile=assets\pc_karukaru_yyy.ico
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppSourceExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
