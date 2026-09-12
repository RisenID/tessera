; Inno Setup script for Tessera.
;
;   iscc packaging\windows\tessera.iss /DVersion=1.10.0
;
; Per-user by default: no elevation, so the installer runs the way the app
; does. Everything it writes is under the user's own AppData, which is also
; where the app keeps its settings, so an uninstall leaves nothing behind but
; the pairing (deliberately -- reinstalling should not mean pairing again).

#ifndef Version
  #define Version "1.10.0"
#endif

#define AppName "Tessera"
#define Publisher "Tessera contributors"
#define AppId "{{8A6E2C3F-4E5B-4C21-9B4B-2E7A1D9F5C10}"

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

[Files]
; The whole PyInstaller folder, Qt and all.
Source: "..\..\dist\Tessera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Tessera.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Tessera.exe"; Tasks: desktopicon

[Registry]
; The app manages this key itself from Settings; the task here is only the
; initial state, and uninstalling must not leave it pointing at nothing.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Tessera"; ValueData: """{app}\Tessera.exe"""; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\Tessera.exe"; Description: "Start Tessera"; Flags: nowait postinstall skipifsilent
