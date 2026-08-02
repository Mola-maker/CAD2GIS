# CAD2GIS Portability

## Reader Selection

LibreDWG is the cross-platform default. AutoCAD Core Console is a maintained
Windows adapter:

```powershell
$env:CAD2GIS_READER_BACKEND = "libredwg"  # default
$env:CAD2GIS_READER_BACKEND = "autocad"   # Windows
```

Use `CAD2GIS_LIBREDWG_DLL` for an explicit LibreDWG library path or
`CAD2GIS_ACCORECONSOLE` for an explicit Core Console executable. Run
`cad2gis doctor --json` to inspect the selected runtime before conversion.

## Runtime

The supported GIS dependency stack is pinned in `env/environment.yml`.
System Python 3.14 is not the target GDAL/QGIS runtime.

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e .
cad2gis doctor --deep --strict
```

## Portability Tests

All tests use the canonical suite:

```powershell
python -m pytest tests/test_reader_capabilities.py -q
python -m pytest tests/test_canonical_cli.py -q
```

The real-DWG suite is external and capability-gated:

```powershell
$env:CAD2GIS_TEST_DATASET_ROOT = "E:\branch_CAD2GIS\APD_test"
python -m pytest tests/test_apd_test_compatibility.py -q
```

A missing reader runtime may skip capability-dependent cases; malformed
records, silent row loss, or source-hash mismatch must fail.
