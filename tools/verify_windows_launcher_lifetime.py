"""Verify desktop lifetime ownership with real Windows processes and DLL locks."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time


def verify(bundle: Path, output: Path):
    bundle, output = bundle.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE

    def exclusive(path):
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            return False
        kernel.CloseHandle(handle)
        return True

    def eventually(check, label, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(.05)
        raise AssertionError(label)

    def children(pid):
        command = f"@(Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq {int(pid)} | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress"
        raw = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                      creationflags=subprocess.CREATE_NO_WINDOW, text=True).strip()
        value = json.loads(raw) if raw else []
        return value if isinstance(value, list) else [value]

    def stopped(pid):
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, 10000) == 0
        finally:
            kernel.CloseHandle(handle)

    # A desktop nested under an ordinary supervisor Job must still own only
    # its own descendants, not its supervisor or that supervisor's siblings.
    outer = kernel.CreateJobObjectW(None, None)
    assert outer and kernel.AssignProcessToJobObject(outer, kernel.GetCurrentProcess()), ctypes.get_last_error()
    unrelated = subprocess.Popen([str(bundle / 'runtime/python.exe'), '-I', '-B', '-c',
                                  'import time; time.sleep(120)'], creationflags=subprocess.CREATE_NO_WINDOW)
    environment = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'CAD2GIS_BACKEND_PATH', 'CAD2GIS_LIBREDWG_CLI'):
        environment.pop(name, None)
    processes = []
    try:
        # Real backend: after SSL is imported, forcibly stop only its launcher.
        state, project = output / 'real-state', output / 'project'
        project.mkdir()
        launcher = bundle / 'bin/cad2gis-desktop.exe'
        process = subprocess.Popen([str(launcher), '--no-browser', '--state-dir', str(state), '--project-root', str(project)],
                                   env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(process)
        dll = bundle / 'runtime/DLLs/libcrypto-3-x64.dll'
        eventually(lambda: any('CAD2GIS: ' in log.read_text(encoding='utf-8-sig', errors='replace')
                                for log in (state / 'logs').glob('desktop-*.log')), 'Desktop did not become ready', 60)
        service_children = children(process.pid)
        assert service_children and not exclusive(dll)
        process.kill()  # Deliberately no /T: the launcher must own cleanup.
        process.wait(10)
        assert all(stopped(pid) for pid in service_children)
        eventually(lambda: exclusive(dll), 'Real Python DLL is still locked')
        assert unrelated.poll() is None

        # A fast runtime fixture starts a grandchild before it announces itself.
        # This exercises child inheritance and teardown during early startup.
        fixture = output / 'startup-fixture'
        (fixture / 'bin').mkdir(parents=True)
        (fixture / 'runtime').mkdir()
        (fixture / 'bin/cad2gis-desktop.exe').write_bytes(launcher.read_bytes())
        source = fixture / 'hold.cs'
        source.write_text('''using System; using System.Diagnostics; using System.IO; using System.Threading;
class Hold { static void Main(string[] args) {
 bool leaf = args.Length == 1 && args[0] == "--leaf";
 if (!leaf) Process.Start(new ProcessStartInfo(System.Reflection.Assembly.GetExecutingAssembly().Location, "--leaf") { UseShellExecute=false, CreateNoWindow=true });
 File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("CAD2GIS_LIFETIME_FIXTURE"), Process.GetCurrentProcess().Id+".pid"), leaf ? "leaf" : "python");
 Thread.Sleep(120000);
} }''')
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:' + str(fixture / 'runtime/python.exe'), str(source)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        for iteration in range(3):
            records = output / f'startup-{iteration}'
            records.mkdir()
            process = subprocess.Popen([str(fixture / 'bin/cad2gis-desktop.exe'), '--state-dir', str(records / 'state')],
                env={**environment, 'CAD2GIS_LIFETIME_FIXTURE': str(records)}, creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(process)
            eventually(lambda: len(list(records.glob('*.pid'))) == 2, 'Runtime descendants did not start')
            pids = [int(path.stem) for path in records.glob('*.pid')]
            assert not exclusive(fixture / 'runtime/python.exe')
            process.kill()
            process.wait(10)
            assert all(stopped(pid) for pid in pids)
            eventually(lambda: exclusive(fixture / 'runtime/python.exe'), 'Startup runtime stayed locked')
            assert unrelated.poll() is None
        report = {'status': 'passed', 'real_backend_terminated_with_launcher': True,
                  'libcrypto_released': True, 'startup_child_and_grandchild_repetitions': 3,
                  'nested_supervisor_job': 'passed', 'unrelated_python_preserved': True}
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        for process in processes:
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(10)
        kernel.CloseHandle(outer)  # The outer test Job has no kill-on-close flag.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.output)))
