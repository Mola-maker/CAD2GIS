"""Exercise the real Windows command-launcher timeout boundary."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from cad2gis import match_delivery


def _running(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        if ctypes.get_last_error() == 87:  # No process with this PID remains.
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return status == 258
    finally:
        kernel.CloseHandle(handle)


def _terminate_tree(pid):
    subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows .cmd process ownership boundary")
def test_timed_out_cmd_probe_terminates_owned_children_and_preserves_unrelated_process(tmp_path, monkeypatch):
    child = tmp_path / "owned_sleeper.py"
    child.write_text(
        "import json, os, pathlib, subprocess, sys, time\n"
        "root = pathlib.Path(sys.argv[2])\n"
        "mode = sys.argv[1]\n"
        "(root / (mode + '.json')).write_text(json.dumps({'pid': os.getpid(), 'parent_pid': os.getppid()}))\n"
        "if mode == 'child':\n"
        "    subprocess.Popen([sys.executable, '-I', __file__, 'grandchild', str(root)])\n"
        "deadline = time.monotonic() + 60\n"
        "while time.monotonic() < deadline and not (root / 'stop-sleepers').exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "qgis probe.cmd"
    launcher.write_text(f'@echo off\n"{sys.executable}" -I "{child}" child "{tmp_path}"\n', encoding="utf-8")
    control = subprocess.Popen(
        [sys.executable, "-I", str(child), "control", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    probes = []

    def track(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        probes.append(process)
        return process

    # Patch this module's namespace, never global subprocess.POpen used by SDKs.
    proxy = SimpleNamespace(**{name: getattr(subprocess, name) for name in dir(subprocess)})
    proxy.Popen = track
    monkeypatch.setattr(match_delivery, "subprocess", proxy)
    observed = {}
    try:
        ready = time.monotonic() + 5
        while not (tmp_path / "control.json").exists() and time.monotonic() < ready:
            time.sleep(0.02)
        assert (tmp_path / "control.json").exists(), "unrelated control process did not start"
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            match_delivery._probe_qgis_python(launcher, timeout=2)
        elapsed = time.monotonic() - started
        assert elapsed < 8, f"probe timeout blocked for {elapsed:.2f}s"
        assert len(probes) == 1 and probes[0].poll() is not None
        for mode in ("child", "grandchild", "control"):
            observed[mode] = json.loads((tmp_path / f"{mode}.json").read_text())
        owned = {probes[0].pid, observed["child"]["pid"], observed["child"]["parent_pid"],
                 observed["grandchild"]["pid"], observed["grandchild"]["parent_pid"]}
        deadline = time.monotonic() + 3
        while any(_running(pid) for pid in owned) and time.monotonic() < deadline:
            time.sleep(0.02)
        states = {str(pid): _running(pid) for pid in sorted(owned)}
        assert not any(states.values()), f"owned probe process tree survived: {states}"
        assert control.poll() is None and _running(observed["control"]["pid"])
        (tmp_path / "timeout-receipt.json").write_text(json.dumps({
            "schema": "cad2gis.qgis-probe-timeout-verification.v1", "launcher": str(launcher),
            "timeout_seconds": 2, "elapsed_seconds": elapsed, "owned_processes_alive_after_timeout": states,
            "observed_receipts": observed, "unrelated_control_survived": True,
        }, indent=2), encoding="utf-8")
    finally:
        # A private stop flag also cleans up when Windows denies taskkill in a
        # restricted runner, without masking the actual assertion failure.
        (tmp_path / "stop-sleepers").write_text("stop", encoding="utf-8")
        try:
            control.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for process in probes:
            if process.poll() is None:
                _terminate_tree(process.pid)
        # On failure, also clean up identified sleepers if their launcher exited.
        for mode in ("grandchild", "child"):
            receipt = tmp_path / f"{mode}.json"
            if receipt.exists():
                pid = json.loads(receipt.read_text())["pid"]
                if _running(pid):
                    _terminate_tree(pid)
        if control.poll() is None:
            _terminate_tree(control.pid)
        control.wait(timeout=5)
