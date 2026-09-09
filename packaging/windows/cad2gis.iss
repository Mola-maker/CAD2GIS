#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif

[Setup]
AppId={{FACCB633-6461-443A-90B2-F7AC74343244}
AppName=CAD2GIS
AppVersion={#AppVersion}
AppPublisher=CAD2GIS contributors
DefaultDirName={localappdata}\Programs\CAD2GIS
DefaultGroupName=CAD2GIS
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=CAD2GIS-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=molamaker.ico
UninstallDisplayIcon={app}\bin\cad2gis-desktop.exe
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "addpath"; Description: "Add CAD2GIS commands to the user PATH (for agent plugins)"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove obsolete application metadata during 0.4 -> 0.5 upgrades. Leaving two
; dist-info versions makes importlib.metadata report the old package identity.
Type: filesandordirs; Name: "{app}\runtime\Lib\site-packages\cad2gis-0.4.0.dist-info"

[Icons]
Name: "{group}\CAD2GIS"; Filename: "{app}\bin\cad2gis-desktop.exe"
Name: "{group}\Uninstall CAD2GIS"; Filename: "{uninstallexe}"
Name: "{userdesktop}\CAD2GIS"; Filename: "{app}\bin\cad2gis-desktop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\cad2gis-desktop.exe"; Description: "Open CAD2GIS"; Flags: nowait postinstall skipifsilent

[Code]
function HasPath(const Paths, Target: String): Boolean;
begin
  Result := Pos(';' + Lowercase(Target) + ';', ';' + Lowercase(Paths) + ';') > 0;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Paths, Target: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addpath') then begin
    Target := ExpandConstant('{app}\bin');
    RegQueryStringValue(HKCU, 'Environment', 'Path', Paths);
    if not HasPath(Paths, Target) then begin
      if Paths = '' then Paths := Target else Paths := Paths + ';' + Target;
      RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Paths);
      RegWriteStringValue(HKCU, 'Software\CAD2GIS', 'AddedPath', Target);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Paths, Target, ResultPath, Part: String; Index: Integer;
begin
  if CurUninstallStep = usPostUninstall then begin
    Target := ExpandConstant('{app}\bin');
    if RegQueryStringValue(HKCU, 'Software\CAD2GIS', 'AddedPath', Part) and (Part = Target) then begin
      RegQueryStringValue(HKCU, 'Environment', 'Path', Paths);
      { Keep empty entries, trailing separators and unrelated edits byte-for-byte. }
      ResultPath := ';' + Paths + ';';
      Index := Pos(';' + Lowercase(Target) + ';', Lowercase(ResultPath));
      while Index > 0 do begin
        Delete(ResultPath, Index, Length(Target) + 1);
        Index := Pos(';' + Lowercase(Target) + ';', Lowercase(ResultPath));
      end;
      if Length(ResultPath) > 1 then ResultPath := Copy(ResultPath, 2, Length(ResultPath) - 2)
      else ResultPath := '';
      RegWriteExpandStringValue(HKCU, 'Environment', 'Path', ResultPath);
      RegDeleteValue(HKCU, 'Software\CAD2GIS', 'AddedPath');
    end;
  end;
end;
