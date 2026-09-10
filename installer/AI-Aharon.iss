#define MyAppName "AI Aharon"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.4"
#endif
#define MyAppPublisher "AI Aharon"
#define MyAppExeName "AI-Aharon.exe"

[Setup]
AppId={{7FD6A2B8-4F6A-4F18-8D84-7A5D59C62710}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AI Aharon
DefaultGroupName=AI Aharon
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=AI-Aharon-Setup-{#MyAppVersion}
SetupIconFile=..\assets\ai-aharon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

[Files]
Source: "..\release-payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{commonappdata}\AgentWindowsAI"
Name: "{commonappdata}\AgentWindowsAI\data"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "autostart"; Description: "Start AI Aharon when I sign in"; GroupDescription: "Startup:"; Flags: checkedonce

[Icons]
Name: "{group}\AI Aharon"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\AI Aharon"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\AI Aharon"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--minimized"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AI Aharon"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -File ""{app}\scripts\installer-remove.ps1"" -InstallRoot ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAgentWindowsService"

[Code]
procedure RunAgentConfiguration;
var
  ResultCode: Integer;
  Params: String;
begin
  Params := '-NoProfile -NonInteractive -File "' +
    ExpandConstant('{app}\scripts\installer-apply.ps1') +
    '" -InstallRoot "' + ExpandConstant('{app}') +
    '" -AllowServiceFailure';
  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    RaiseException('AI Aharon could not start its configuration step.');
  end;
  if ResultCode <> 0 then
  begin
    RaiseException('AI Aharon configuration failed (exit code ' + IntToStr(ResultCode) + '). Check C:\ProgramData\AgentWindowsAI\install.log.');
  end;

  if FileExists(ExpandConstant('{commonappdata}\AgentWindowsAI\service-install-error.txt')) then
  begin
    MsgBox(
      'AI Aharon was installed and can run locally, but the background service could not start.' + #13#10 + #13#10 +
      'The desktop app will still open. Service diagnostics were saved to:' + #13#10 +
      ExpandConstant('{commonappdata}\AgentWindowsAI\install.log'),
      mbInformation,
      MB_OK
    );
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunAgentConfiguration;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop AgentWindowsAI', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
