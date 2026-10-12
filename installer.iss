#ifndef BuildDir
  #define BuildDir "dist\MontageMaker"
#endif
#ifndef SetupOutput
  #define SetupOutput "dist"
#endif
#ifdef TestBuild
  #define InstallerId "{1965430A-A768-49E5-9F30-0E4415F9B231}"
  #define InstallerName "Montage Maker Studio QA"
#else
  #define InstallerId "{A76EBCED-E029-4BE5-B586-4835E5FAB624}"
  #define InstallerName "Montage Maker Studio"
#endif

[Setup]
AppId={{#InstallerId}
AppName={#InstallerName}
AppVersion=4.1
AppPublisher=Montage Maker Studio
DefaultDirName={autopf}\Montage Maker Studio
DefaultGroupName=Montage Maker Studio 4
UninstallDisplayIcon={app}\MontageMaker.exe
SetupIconFile=assets\montage.ico
OutputDir={#SetupOutput}
OutputBaseFilename=MontageMaker-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop shortcut / اختصار سطح المكتب"; GroupDescription: "Shortcuts / الاختصارات"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Montage Maker Studio 4"; Filename: "{app}\MontageMaker.exe"; WorkingDir: "{app}"; IconFilename: "{app}\MontageMaker.exe"
Name: "{autodesktop}\Montage Maker Studio 4"; Filename: "{app}\MontageMaker.exe"; WorkingDir: "{app}"; IconFilename: "{app}\MontageMaker.exe"; Tasks: desktopicon
Name: "{group}\Uninstall Montage Maker Studio"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\MontageMaker.exe"; Description: "Open Montage Maker Studio / فتح البرنامج"; Flags: nowait postinstall skipifsilent
