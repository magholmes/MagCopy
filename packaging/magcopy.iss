; The Windows download. One file, and opening it installs MagCopy.
;
; Built by build.ps1 -Folder, which compiles this against the PyInstaller folder build in
; dist\MagCopy and then zips the single Setup.exe it produces.
;
; Why a real installer rather than the exe on its own: a one-file PyInstaller binary unpacks a
; Python runtime into a temp directory and runs it from there, which is structurally what a
; dropper does - so scanners flag the bootloader and Chrome reports the download as a virus. The
; payload here is an ordinary folder of files that never self-extracts, and an Inno Setup stub is
; one of the most widely seen executables on Windows, so it carries reputation this project never
; could on its own.
;
; Nothing needs administrator rights: it installs per-user, in the same place the app already put
; itself, so there is no elevation prompt to talk anyone through.

#ifndef MyVersion
  #define MyVersion "0.0"
#endif

[Setup]
AppId={{DDA5AB15-C12A-55B6-8816-7A115693430E}
AppName=MagCopy
AppVersion={#MyVersion}
AppPublisher=magholmes
AppPublisherURL=https://github.com/magholmes/MagCopy
AppSupportURL=https://github.com/magholmes/MagCopy/issues
DefaultDirName={localappdata}\Programs\MagCopy
DefaultGroupName=MagCopy
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MagCopy-Setup
SetupIconFile=..\icon.ico
; Without this, Add/Remove Programs lists it as "MagCopy version 1.4", which reads like a typo.
UninstallDisplayName=MagCopy
UninstallDisplayIcon={app}\MagCopy.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Nothing here is worth asking about: there is one sensible location, one program group and no
; components. The only screen is the one offering a desktop shortcut.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
; An upgrade over a running copy would otherwise fail on locked files, or demand a restart.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\MagCopy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MagCopy"; Filename: "{app}\MagCopy.exe"
Name: "{autodesktop}\MagCopy"; Filename: "{app}\MagCopy.exe"; Tasks: desktopicon

[Registry]
; MagCopy writes this itself when "start with Windows" is on. ValueType none creates nothing at
; install time; the flag only clears it on uninstall, so removing the program does not leave
; Windows trying to launch something that is no longer there.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "MagCopy"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MagCopy.exe"; Description: "Start MagCopy"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Settings live in %APPDATA%\MagCopy and saved captures live in Pictures. Neither is touched.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM MagCopy.exe /F"; Flags: runhidden; RunOnceId: "StopMagCopy"
