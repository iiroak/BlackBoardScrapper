#define AppName "Campus Archive"
#define AppVersion "1.0.0"
#define AppPublisher "iiroak"
#define AppExeName "BlackBoardScrapper.exe"

[Setup]
AppId={{B2A7A6E5-50E4-4AC6-AF0B-0D75C8D8C9B4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Campus Archive
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=Campus-Archive-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
Uninstallable=yes
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
WizardStyle=modern

[Files]
Source: "..\dist\BlackBoardScrapper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
