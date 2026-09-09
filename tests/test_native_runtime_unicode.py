from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from cad2gis import native_runtime


@pytest.mark.parametrize("already_loaded", [False, True])
def test_pyramids_windows_data_paths_preserve_unicode_and_environment(
    monkeypatch: pytest.MonkeyPatch, already_loaded: bool
) -> None:
    values = {
        "GDAL_DATA": r"C:\安装目录 空格\gdal_data",
        "PROJ_DATA": r"C:\用户指定\proj;D:\另一个目录\proj",
        "PROJ_LIB": r"C:\旧配置\proj",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    configured: dict[str, str] = {}
    gdal = SimpleNamespace(
        GetConfigOptions=lambda: dict(configured),
        SetConfigOption=lambda key, value: configured.__setitem__(key, value),
    )
    monkeypatch.setitem(sys.modules, "osgeo", SimpleNamespace(gdal=gdal))
    monkeypatch.setitem(sys.modules, "pyramids", SimpleNamespace())
    spec = SimpleNamespace(origin=r"C:\安装目录\pyramids\_vendor\osgeo\__init__.py")
    specs = iter([spec] if already_loaded else [None, spec])
    monkeypatch.setattr(native_runtime.importlib.util, "find_spec", lambda _: next(specs))
    monkeypatch.setattr(native_runtime, "sys_platform", lambda: "win32")

    assert native_runtime.ensure_osgeo_runtime()["provider"] == "pyramids-gis"
    assert configured == values
    assert {key: os.environ[key] for key in values} == values


def test_data_path_bootstrap_keeps_explicit_gdal_config_and_absent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_runtime, "sys_platform", lambda: "win32")
    monkeypatch.setenv("PROJ_DATA", r"C:\环境\proj")
    monkeypatch.setenv("GDAL_DATA", "")
    monkeypatch.delenv("PROJ_LIB", raising=False)
    configured = {"PROJ_DATA": r"D:\显式设置\proj"}
    writes = []
    gdal = SimpleNamespace(
        GetConfigOptions=lambda: dict(configured),
        SetConfigOption=lambda key, value: writes.append((key, value)),
    )
    monkeypatch.setitem(sys.modules, "osgeo", SimpleNamespace(gdal=gdal))

    native_runtime._configure_pyramids_windows_paths()

    assert writes == [("GDAL_DATA", "")]
    assert configured == {"PROJ_DATA": r"D:\显式设置\proj"}


@pytest.mark.parametrize(
    ("platform", "origin"),
    [("linux", "/site-packages/pyramids/_vendor/osgeo/__init__.py"),
     ("win32", r"C:\system\osgeo\__init__.py")],
)
def test_data_path_fix_does_not_initialize_linux_or_system_gdal(
    monkeypatch: pytest.MonkeyPatch, platform: str, origin: str
) -> None:
    monkeypatch.setattr(native_runtime, "sys_platform", lambda: platform)
    monkeypatch.setattr(
        native_runtime.importlib.util, "find_spec", lambda _: SimpleNamespace(origin=origin)
    )
    # Accessing a fake GDAL module here would fail: these paths need no setters.
    monkeypatch.setitem(sys.modules, "osgeo", SimpleNamespace())

    assert native_runtime.ensure_osgeo_runtime()["available"] is True
