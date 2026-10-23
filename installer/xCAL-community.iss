; xCAL Community unsigned beta installer.
; Build with build.ps1 so the tested PyInstaller output and Community OAuth
; client are created before this installer is compiled.

#ifndef MyAppVersion
#define MyAppVersion "0.2.2"
#endif

#define MyAppName "xCAL Community"
#define MyAppPublisher "Excalibur Racing"
#define MyAppExeName "xCAL Community.exe"
#define AppSource "..\dist\xCAL-community"

[Setup]
AppId={{7D519E09-CC65-45DE-A10B-CC83446E60F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://www.excaliburracing.com/xcal-community
AppSupportURL=https://github.com/loganbateman/xCAL-community/issues
AppUpdatesURL=https://github.com/loganbateman/xCAL-community/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
UsePreviousAppDir=no
DisableDirPage=yes
DefaultGroupName={#MyAppName}
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=xCAL-Community-Setup-{#MyAppVersion}
SetupIconFile=..\src\assets\logo.ico
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\docs\BETA_QUICKSTART.md"; DestDir: "{app}"; DestName: "START-HERE.md"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "ExcaliburRacing.xCALCommunity"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "ExcaliburRacing.xCALCommunity"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))),
      RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{localappdata}\Programs\xCAL Community')))) <> 0 then
    Result := 'Install xCAL Community in its separate Community folder. The regular xCAL folder cannot be used.';
end;
