// Included in [Code]. Probe existing binaries without changing their contents.
// Python and native helpers do not reliably register with Restart Manager.
function RuntimeDirectoryAttributes(const Filename: String): Cardinal;
external 'GetFileAttributesW@kernel32.dll stdcall';

function RuntimeDirectoryError: Cardinal;
external 'GetLastError@kernel32.dll stdcall';

function DirectoryBlocker(const Directory: String): String;
var Attributes, ErrorCode: Cardinal;
begin
  Result := '';
  Attributes := RuntimeDirectoryAttributes(Directory);
  if Attributes = $FFFFFFFF then begin
    ErrorCode := RuntimeDirectoryError;
    if (ErrorCode <> 2) and (ErrorCode <> 3) then Result := Directory;
  end else if ((Attributes and $400) <> 0) or
    ((Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) then Result := Directory;
end;

function BusyRuntimeFile(const Directory: String): String;
var
  Entry: TFindRec;
  Filename, Extension: String;
  Probe: TFileStream;
begin
  Result := DirectoryBlocker(Directory);
  if Result <> '' then Exit;
  if not DirExists(Directory) then Exit;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Filename := AddBackslash(Directory) + Entry.Name;
          if (Entry.Attributes and $400) <> 0 then begin
            Result := Filename; { Do not follow reparse points outside the runtime. }
          end else if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
            Result := BusyRuntimeFile(Filename);
          end else begin
            Extension := Lowercase(ExtractFileExt(Filename));
            if (Extension = '.exe') or (Extension = '.dll') or (Extension = '.pyd') then begin
              try
                Probe := TFileStream.Create(Filename, fmOpenReadWrite or fmShareExclusive);
                Probe.Free;
              except
                Result := Filename;
              end;
            end;
          end;
          if Result <> '' then Exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end else Result := Directory;
end;

function UpgradeBlocker: String;
var Filename: String;
begin
  Filename := DirectoryBlocker(ExpandConstant('{app}'));
  if Filename = '' then Filename := BusyRuntimeFile(ExpandConstant('{app}\bin'));
  if Filename = '' then Filename := BusyRuntimeFile(ExpandConstant('{app}\runtime'));
  if Filename = '' then Filename := BusyRuntimeFile(ExpandConstant('{app}\libredwg'));
  Result := '';
  if Filename <> '' then begin
    Log('CAD2GIS upgrade blocked before changing files: ' + Filename);
    Result := 'CAD2GIS is still running, or its runtime cannot be replaced.'#13#10#13#10 +
      '请先在所有 CAD2GIS 窗口左下角点击“关闭程序”，等待当前任务结束，然后重试。仅关闭浏览器标签页不会停止后台服务。'#13#10#13#10 +
      'File: ' + Filename;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := UpgradeBlocker;
end;

function InitializeUninstall: Boolean;
var Blocker: String;
begin
  Blocker := UpgradeBlocker;
  Result := Blocker = '';
  if not Result then SuppressibleMsgBox(Blocker, mbError, MB_OK, IDOK);
end;
