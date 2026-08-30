"""Tests for optional LibreDWG reader capability discovery."""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cad2gis.reader.contracts import ReaderCapability, ReaderUnavailableError


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_reader_capability_is_frozen_and_serializable():
    capability = ReaderCapability(
        backend="libredwg",
        available=False,
        detail="bindings missing",
        remediation="Install LibreDWG or select CAD2GIS_READER_BACKEND=autocad.",
    )

    assert dataclasses.is_dataclass(capability)
    assert capability.to_dict() == {
        "backend": "libredwg",
        "available": False,
        "detail": "bindings missing",
        "remediation": "Install LibreDWG or select CAD2GIS_READER_BACKEND=autocad.",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        capability.available = True


def test_libredwg_module_imports_when_bindings_are_missing(tmp_path: Path):
    missing_python_path = tmp_path / "missing-python"
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        "CAD2GIS_LIBREDWG_PYTHON_PATH": str(missing_python_path),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; import cad2gis.reader.libredwg as r; "
            "print(json.dumps(r.libredwg_capability().to_dict()))",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    capability = json.loads(completed.stdout)
    assert capability["backend"] == "libredwg"
    assert capability["available"] is False
    assert "Python bindings" in capability["detail"]
    assert capability["remediation"]


def test_libredwg_import_does_not_load_native_library():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes; ctypes.CDLL = lambda *_args, **_kwargs: "
            "(_ for _ in ()).throw(AssertionError('native load')); "
            "import cad2gis.reader.libredwg; print('imported')",
        ],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "imported"


def test_capability_honors_explicit_discovery_overrides(tmp_path: Path):
    exports = {
        "Dwg_Data": "object",
        "Dwg_Object_Array_getitem": "object",
        "DWG_SUPERTYPE_ENTITY": "1",
        "DWG_TYPE_BLOCK_HEADER": "2",
        "DWG_TYPE_LAYER": "3",
        "DWG_TYPE_LINE": "4",
        "DWG_TYPE_LWPOLYLINE": "5",
        "DWG_TYPE_CIRCLE": "6",
        "DWG_TYPE_ARC": "7",
        "DWG_TYPE_TEXT": "8",
        "DWG_TYPE_MTEXT": "9",
        "DWG_TYPE_INSERT": "10",
        "DWG_TYPE_POINT": "11",
        "DWG_TYPE_DIMENSION_ALIGNED": "12",
        "DWG_TYPE_DIMENSION_LINEAR": "13",
        "DWG_TYPE_DIMENSION_ANG2LN": "14",
        "DWG_TYPE_DIMENSION_ANG3PT": "15",
        "DWG_TYPE_DIMENSION_DIAMETER": "16",
        "DWG_TYPE_DIMENSION_ORDINATE": "17",
        "DWG_TYPE_DIMENSION_RADIUS": "18",
        "DWG_TYPE_DIMENSION_r11": "19",
        "DWG_TYPE_HATCH": "20",
        "DWG_TYPE_SPLINE": "21",
        "DWG_TYPE_ELLIPSE": "22",
        "DWG_TYPE_POLYLINE_2D": "23",
        "DWG_TYPE_POLYLINE_3D": "24",
        "DWG_TYPE_SEQEND": "25",
        "new_Dwg_Object_Array": "object",
        "dwg_read_file": "object",
    }
    binding_path = tmp_path / "LibreDWG.py"
    binding_path.write_text(
        "\n".join(f"{name} = {value}" for name, value in exports.items()),
        encoding="utf-8",
    )
    library_path = tmp_path / "libredwg-test.so"
    library_path.write_bytes(b"test")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; import cad2gis.reader.libredwg as r; "
            "print(json.dumps(r.libredwg_capability().to_dict()))",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(SRC),
            "CAD2GIS_LIBREDWG_PYTHON_PATH": str(tmp_path),
            "CAD2GIS_LIBREDWG_LIBRARY": str(library_path),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    capability = json.loads(completed.stdout)
    assert capability["available"] is False
    assert "shared library" in capability["detail"].lower()


@pytest.mark.parametrize(
    "environment_name", ["CAD2GIS_LIBREDWG_LIBRARY", "CAD2GIS_LIBREDWG_DLL"]
)
def test_bare_explicit_library_override_is_passed_to_cdll(
    monkeypatch: pytest.MonkeyPatch, environment_name: str
):
    import cad2gis.reader.libredwg as libredwg

    class FakeFunction:
        argtypes = None
        restype = None

    class FakeLibrary:
        def __getattr__(self, name):
            function = FakeFunction()
            setattr(self, name, function)
            return function

    calls: list[object] = []

    def fake_cdll(name):
        calls.append(name)
        return FakeLibrary()

    monkeypatch.delenv("CAD2GIS_LIBREDWG_LIBRARY", raising=False)
    monkeypatch.delenv("CAD2GIS_LIBREDWG_DLL", raising=False)
    monkeypatch.setenv(environment_name, "custom-redwg.dll")
    monkeypatch.setattr(libredwg.os, "name", "nt")
    monkeypatch.setattr(
        libredwg.ctypes.util, "find_library", lambda _name: "unrelated-system-redwg"
    )
    monkeypatch.setattr(libredwg.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(libredwg, "_load_python_bindings", lambda: object())
    monkeypatch.setattr(libredwg, "_libdwg", None)
    monkeypatch.setattr(libredwg, "_libc", None)

    libredwg._init_libredwg()

    assert calls == ["custom-redwg.dll", "msvcrt"]


def test_windows_default_library_uses_bare_path_candidate(
    monkeypatch: pytest.MonkeyPatch,
):
    import cad2gis.reader.libredwg as libredwg

    monkeypatch.delenv("CAD2GIS_LIBREDWG_LIBRARY", raising=False)
    monkeypatch.delenv("CAD2GIS_LIBREDWG_DLL", raising=False)
    monkeypatch.setattr(libredwg.os, "name", "nt")
    monkeypatch.setattr(libredwg.Path, "is_file", lambda _path: False)
    monkeypatch.setattr(libredwg.ctypes.util, "find_library", lambda _name: None)

    assert libredwg._discover_library() == "libredwg.dll"


def test_concurrent_capability_initialization_reuses_one_cached_handle_pair(
    monkeypatch: pytest.MonkeyPatch,
):
    import cad2gis.reader.libredwg as libredwg

    class FakeFunction:
        argtypes = None
        restype = None

    class FakeLibrary:
        def __getattr__(self, name):
            function = FakeFunction()
            setattr(self, name, function)
            return function

    counter_lock = threading.Lock()
    binding_installs = 0
    binding_cache = None
    opened_libraries: list[FakeLibrary] = []
    close_calls: list[FakeLibrary] = []

    def fake_load_bindings():
        nonlocal binding_cache, binding_installs
        if binding_cache is not None:
            return binding_cache
        with counter_lock:
            binding_installs += 1
        time.sleep(0.05)
        binding_cache = object()
        return binding_cache

    def fake_cdll(_name):
        library = FakeLibrary()
        with counter_lock:
            opened_libraries.append(library)
        time.sleep(0.05)
        return library

    def track_release(handle):
        if handle is not None:
            close_calls.append(handle)

    monkeypatch.setattr(libredwg, "_load_python_bindings", fake_load_bindings)
    monkeypatch.setattr(libredwg, "_discover_library", lambda: "libredwg-test")
    monkeypatch.setattr(libredwg, "_allocator_library", lambda: "allocator-test")
    monkeypatch.setattr(libredwg.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(libredwg, "_release_native_handle", track_release)
    monkeypatch.setattr(libredwg, "_libdwg", None)
    monkeypatch.setattr(libredwg, "_libc", None)

    with ThreadPoolExecutor(max_workers=5) as executor:
        capabilities = list(executor.map(lambda _item: libredwg.libredwg_capability(), range(5)))

    assert all(capability.available for capability in capabilities)
    assert binding_installs == 1
    assert len(opened_libraries) == 2
    assert libredwg._libdwg is opened_libraries[0]
    assert libredwg._libc is opened_libraries[1]
    libredwg._init_libredwg()
    assert len(opened_libraries) == 2
    assert close_calls == []


def test_capability_rejects_loadable_library_without_required_symbols(
    monkeypatch: pytest.MonkeyPatch,
):
    import _ctypes

    import cad2gis.reader.libredwg as libredwg

    class WrongLibrary:
        def __init__(self, handle):
            self._handle = handle

    native_library = WrongLibrary(101)
    allocator_library = WrongLibrary(202)

    def fake_cdll(name):
        return native_library if name == "wrong-libredwg" else allocator_library

    monkeypatch.setattr(libredwg, "_load_python_bindings", lambda: object())
    monkeypatch.setattr(
        libredwg, "_discover_library", lambda: "wrong-libredwg"
    )
    monkeypatch.setattr(libredwg.os, "name", "nt")
    monkeypatch.setattr(libredwg.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(libredwg.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(_ctypes, "FreeLibrary", lambda _handle: None)
    monkeypatch.setattr(libredwg, "_libdwg", None)
    monkeypatch.setattr(libredwg, "_libc", None)

    capability = libredwg.libredwg_capability()

    assert capability.available is False
    assert "required" in capability.detail.lower()
    assert libredwg._libdwg is None
    assert libredwg._libc is None


def test_native_initialization_releases_handles_on_symbol_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    import _ctypes

    import cad2gis.reader.libredwg as libredwg

    class WrongLibrary:
        def __init__(self, handle):
            self._handle = handle

    native_library = WrongLibrary(301)
    allocator_library = WrongLibrary(302)
    close_calls: list[int] = []

    def fake_cdll(name):
        return native_library if name == "wrong-libredwg" else allocator_library

    monkeypatch.setattr(libredwg, "_load_python_bindings", lambda: object())
    monkeypatch.setattr(
        libredwg, "_discover_library", lambda: "wrong-libredwg"
    )
    monkeypatch.setattr(libredwg.os, "name", "nt")
    monkeypatch.setattr(libredwg.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(libredwg.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(_ctypes, "FreeLibrary", close_calls.append)
    monkeypatch.setattr(libredwg, "_libdwg", None)
    monkeypatch.setattr(libredwg, "_libc", None)

    with pytest.raises(Exception):
        libredwg._init_libredwg()

    assert close_calls.count(301) == 1
    assert close_calls.count(302) == 1
    assert libredwg._libdwg is None
    assert libredwg._libc is None


def test_explicit_python_path_blocks_global_binding_fallback(tmp_path: Path):
    global_path = tmp_path / "global"
    global_path.mkdir()
    missing_override = tmp_path / "missing-explicit"
    exports = {
        "Dwg_Data": "object",
        "Dwg_Object_Array_getitem": "object",
        "DWG_SUPERTYPE_ENTITY": "1",
        "DWG_TYPE_BLOCK_HEADER": "2",
        "DWG_TYPE_LAYER": "3",
        "DWG_TYPE_LINE": "4",
        "DWG_TYPE_LWPOLYLINE": "5",
        "DWG_TYPE_CIRCLE": "6",
        "DWG_TYPE_ARC": "7",
        "DWG_TYPE_TEXT": "8",
        "DWG_TYPE_MTEXT": "9",
        "DWG_TYPE_INSERT": "10",
        "DWG_TYPE_POINT": "11",
        "DWG_TYPE_DIMENSION_ALIGNED": "12",
        "DWG_TYPE_DIMENSION_LINEAR": "13",
        "DWG_TYPE_DIMENSION_ANG2LN": "14",
        "DWG_TYPE_DIMENSION_ANG3PT": "15",
        "DWG_TYPE_DIMENSION_DIAMETER": "16",
        "DWG_TYPE_DIMENSION_ORDINATE": "17",
        "DWG_TYPE_DIMENSION_RADIUS": "18",
        "DWG_TYPE_DIMENSION_r11": "19",
        "DWG_TYPE_HATCH": "20",
        "DWG_TYPE_SPLINE": "21",
        "DWG_TYPE_ELLIPSE": "22",
        "DWG_TYPE_POLYLINE_2D": "23",
        "DWG_TYPE_POLYLINE_3D": "24",
        "DWG_TYPE_SEQEND": "25",
        "new_Dwg_Object_Array": "object",
        "dwg_read_file": "object",
    }
    (global_path / "LibreDWG.py").write_text(
        "\n".join(f"{name} = {value}" for name, value in exports.items()),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; import cad2gis.reader.libredwg as r; "
            "cap = r.libredwg_capability(); "
            "print(json.dumps({'cap': cap.to_dict(), 'loaded': r._python_bindings is not None}))",
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(global_path), str(SRC))),
            "CAD2GIS_LIBREDWG_PYTHON_PATH": str(missing_override),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["loaded"] is False
    assert result["cap"]["available"] is False
    assert "Python bindings" in result["cap"]["detail"]


def test_dimension_type_maps_keep_canonical_dimension_name(
    monkeypatch: pytest.MonkeyPatch,
):
    import cad2gis.reader.libredwg as libredwg

    dimension_names = (
        "DWG_TYPE_DIMENSION_ALIGNED",
        "DWG_TYPE_DIMENSION_LINEAR",
        "DWG_TYPE_DIMENSION_ANG2LN",
        "DWG_TYPE_DIMENSION_ANG3PT",
        "DWG_TYPE_DIMENSION_DIAMETER",
        "DWG_TYPE_DIMENSION_ORDINATE",
        "DWG_TYPE_DIMENSION_RADIUS",
        "DWG_TYPE_DIMENSION_r11",
    )
    for value, name in enumerate(dimension_names, start=100):
        monkeypatch.setattr(libredwg, name, value)

    libredwg._rebuild_type_maps()
    try:
        assert {libredwg._DWG_TYPE_NAME_MAP[value] for value in range(100, 108)} == {
            "DIMENSION"
        }
        assert {
            libredwg._DIM_STRUCT_NAMES[value] for value in range(100, 108)
        } == {
            "DIMENSION_ALIGNED",
            "DIMENSION_LINEAR",
            "DIMENSION_ANG2LN",
            "DIMENSION_ANG3PT",
            "DIMENSION_DIAMETER",
            "DIMENSION_ORDINATE",
            "DIMENSION_RADIUS",
            "DIMENSION_r11",
        }
    finally:
        monkeypatch.undo()
        libredwg._rebuild_type_maps()


def test_windows_native_initialization_uses_msvcrt_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
):
    import cad2gis.reader.libredwg as libredwg

    class FakeFunction:
        argtypes = None
        restype = None

    class FakeLibrary:
        def __getattr__(self, name):
            function = FakeFunction()
            setattr(self, name, function)
            return function

    calls: list[object] = []

    def fake_cdll(name):
        calls.append(name)
        if name == "msvcrt":
            raise OSError("allocator unavailable")
        return FakeLibrary()

    monkeypatch.setattr(libredwg, "_load_python_bindings", lambda: object())
    monkeypatch.setattr(libredwg, "_discover_library", lambda: "libredwg-test")
    monkeypatch.setattr(libredwg.os, "name", "nt")
    monkeypatch.setattr(libredwg.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(libredwg.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(libredwg, "_libdwg", None)
    monkeypatch.setattr(libredwg, "_libc", None)

    with pytest.raises(OSError, match="allocator unavailable"):
        libredwg._init_libredwg()

    assert calls == ["libredwg-test", "msvcrt"]
    assert libredwg._libdwg is None
    assert libredwg._libc is None


def test_missing_runtime_fails_only_when_extraction_is_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import cad2gis.reader.libredwg as libredwg

    monkeypatch.setattr(
        libredwg,
        "libredwg_capability",
        lambda: ReaderCapability(
            backend="libredwg",
            available=False,
            detail="bindings missing",
            remediation="Install LibreDWG or select CAD2GIS_READER_BACKEND=autocad.",
        ),
    )

    with pytest.raises(ReaderUnavailableError, match="CAD2GIS_READER_BACKEND=autocad"):
        libredwg.extract_dwg_records(tmp_path / "drawing.dwg")


def test_autocad_default_extraction_timeout_scales_with_source_size(
    tmp_path: Path,
) -> None:
    import cad2gis.reader.autocad as autocad

    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"AC1032")

    seconds, origin = autocad._resolve_extraction_timeout(source, environ={})

    assert (
        seconds
        == autocad.DEFAULT_ACCORECONSOLE_TIMEOUT
        + autocad.ACCORECONSOLE_TIMEOUT_PER_MIB
    )
    assert origin == "adaptive_size_default"

    seconds, origin = autocad._resolve_extraction_timeout(
        source,
        environ={autocad.ACCORECONSOLE_TIMEOUT_ENV: "42"},
    )

    assert seconds == 42
    assert origin == "environment"


def test_autocad_completion_marker_is_strict(tmp_path: Path) -> None:
    import cad2gis.reader.autocad as autocad

    marker = tmp_path / "entities.tsv.complete"
    marker.write_text(
        f"{autocad.BULK_COMPLETION_SCHEMA}\t17\n",
        encoding="ascii",
    )
    assert autocad._read_bulk_completion_marker(marker) == 17

    marker.write_text(
        f"{autocad.BULK_COMPLETION_SCHEMA}\tnot-a-count\n",
        encoding="ascii",
    )
    assert autocad._read_bulk_completion_marker(marker) is None

    marker.write_text("untrusted-schema\t17\n", encoding="ascii")
    assert autocad._read_bulk_completion_marker(marker) is None


def test_autocad_profile_accepts_exported_arg_and_builds_profile_command(
    tmp_path: Path,
) -> None:
    import cad2gis.reader.autocad as autocad

    profile = tmp_path / "cad2gis.arg"
    profile.write_text("profile", encoding="utf-8")
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"AC1032")
    script = tmp_path / "extract.scr"
    script.write_text("_.QUIT\n", encoding="utf-8")

    value, origin = autocad._configured_autocad_profile(
        environ={autocad.AUTOCAD_PROFILE_ENV: str(profile)}
    )
    command = autocad._core_console_command(
        tmp_path / "accoreconsole.exe",
        source,
        script,
        profile=value,
    )

    assert value == str(profile.resolve())
    assert origin == "environment_arg"
    assert command[1:3] == ["/p", str(profile.resolve())]
    assert command[3:] == [
        "/readonly",
        "/i",
        str(source.resolve()),
        "/s",
        str(script.resolve()),
    ]


def test_autocad_profile_rejects_missing_or_non_arg_path(tmp_path: Path) -> None:
    import cad2gis.reader.autocad as autocad

    with pytest.raises(RuntimeError, match="does not exist"):
        autocad._configured_autocad_profile(
            environ={autocad.AUTOCAD_PROFILE_ENV: str(tmp_path / "missing.arg")}
        )
    with pytest.raises(ValueError, match="must end in .arg"):
        autocad._configured_autocad_profile(
            environ={autocad.AUTOCAD_PROFILE_ENV: str(tmp_path / "profile.txt")}
        )


def test_autocad_export_completion_is_independent_from_process_exit(
    tmp_path: Path,
) -> None:
    import cad2gis.reader.autocad as autocad

    marker = tmp_path / "entities.tsv.complete"
    code = (
        "from pathlib import Path; import time; "
        f"Path({str(marker)!r}).write_text("
        f"{(autocad.BULK_COMPLETION_SCHEMA + chr(9) + '23' + chr(10))!r},"
        "encoding='ascii'); "
        "time.sleep(30)"
    )

    result = autocad._run_until_bulk_export_complete(
        [sys.executable, "-c", code],
        completion_path=marker,
        timeout_seconds=5,
        exit_grace_seconds=0.1,
    )

    assert result["completion_rows"] == 23
    assert result["forced_after_export"] is True
    assert result["completed"].returncode != 0
    assert result["export_elapsed_seconds"] < 3


def test_autocad_process_timeout_without_completion_marker_fails(
    tmp_path: Path,
) -> None:
    import cad2gis.reader.autocad as autocad

    with pytest.raises(subprocess.TimeoutExpired):
        autocad._run_until_bulk_export_complete(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            completion_path=tmp_path / "missing.complete",
            timeout_seconds=0.1,
            exit_grace_seconds=0.1,
        )
