; Inno Setup script for Tessera.
;
;   iscc packaging\windows\tessera.iss /DVersion=1.10.0
;
; Per user, no elevation. The app is self-contained; the tools it drives are
; installed afterwards with winget (adb's licence rules out bundling).

#ifndef Version
  #define Version "1.10.0"
#endif

#define AppName "Tessera"
#define Publisher "Tessera contributors"
#define AppId "{{8A6E2C3F-4E5B-4C21-9B4B-2E7A1D9F5C10}"
#define AppUserModelID "dev.tessera.Tessera"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#Version}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=Tessera-{#Version}-setup
SetupIconFile=tessera.ico
UninstallDisplayIcon={app}\Tessera.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Tessera when I log in"; GroupDescription: "Startup:"; Flags: unchecked
Name: "tools"; Description: "Install the tools Tessera uses, with winget"; GroupDescription: "Tools:"
Name: "tools\screen"; Description: "scrcpy and adb: screen mirroring, app windows, the adb clipboard"
Name: "tools\camera"; Description: "FFmpeg: the phone's camera as a webcam"

[Files]
Source: "..\..\dist\Tessera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The app id matches the one the app sets, so notifications are named Tessera.
Name: "{group}\{#AppName}"; Filename: "{app}\Tessera.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Tessera.exe"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; The app manages this value from Settings; uninstalling removes it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Tessera"; ValueData: """{app}\Tessera.exe"""; \
    Flags: uninsdeletevalue; Tasks: startup
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; \
    ValueType: string; ValueName: "DisplayName"; ValueData: "{#AppName}"; \
    Flags: uninsdeletekey

[Run]
Filename: "{app}\Tessera.exe"; Description: "Start Tessera"; Flags: nowait postinstall skipifsilent

[Code]
const
  Agreements = ' --accept-source-agreements --disable-interactivity';

var
  Missed: String;

{ winget is an app execution alias under the user's WindowsApps. }
function WingetPath(): String;
begin
  Result := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
  if not FileExists(Result) then
    Result := 'winget.exe';
end;

function Winget(const Params: String): Integer;
var
  Code: Integer;
begin
  if Exec(WingetPath(), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Result := -1;
end;

{ Install one package unless winget already lists it. }
procedure Need(const Id, Name: String);
begin
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.StatusLabel.Caption := 'Checking for ' + Name + '...';
  if Winget('list --exact --id ' + Id + Agreements) = 0 then
    Exit;
  WizardForm.StatusLabel.Caption := 'Installing ' + Name + ' with winget...';
  if Winget('install --exact --id ' + Id + ' --silent --accept-package-agreements' + Agreements) <> 0 then
    Missed := Missed + #13#10 + '    winget install --exact --id ' + Id;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  Missed := '';
  if WizardIsTaskSelected('tools\screen') then
  begin
    Need('Genymobile.scrcpy', 'scrcpy');
    Need('Google.PlatformTools', 'adb');
  end;
  if WizardIsTaskSelected('tools\camera') then
    Need('Gyan.FFmpeg', 'FFmpeg');
  if Missed <> '' then
    SuppressibleMsgBox(
      'Tessera is installed, but winget could not install everything:' + #13#10 +
      Missed + #13#10#13#10 +
      'Run those in a terminal to try again.',
      mbInformation, MB_OK, IDOK);
end;
