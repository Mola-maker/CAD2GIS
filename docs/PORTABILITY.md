# CAD2GIS Portability Guide

## Cross-Platform Reader

The robustness workspace uses LibreDWG as the primary reader, making the
conversion pipeline available on Linux, Windows, and macOS without requiring
AutoCAD.

### Backend Selection

Set the `CAD2GIS_READER_BACKEND` environment variable:

- `libredwg` (default): Cross-platform LibreDWG reader
- `autocad`: Windows-only AutoCAD fallback (requires AutoCAD Core Console)

```bash
# Linux / macOS
export CAD2GIS_READER_BACKEND=libredwg

# Windows (AutoCAD fallback)
set CAD2GIS_READER_BACKEND=autocad
```

### LibreDWG Installation

#### Linux

```bash
# System-wide install (expected at /usr/local/lib/libredwg.so)
sudo ldconfig
```

#### Windows

Place `libredwg.dll` in a directory on the system `PATH`, or set
`CAD2GIS_LIBREDWG_DLL` to the explicit DLL path.

#### macOS

```bash
brew install libredwg
```

### AutoCAD Fallback

The AutoCAD reader is retained for legacy Windows deployments.  It requires:

- AutoCAD Core Console (`accoreconsole.exe`)
- `CAD2GIS_ACCORECONSOLE` environment variable pointing to the executable
- Windows OS (`os.name == "nt"`)

The AutoCAD reader is **deprecated** and will be removed in a future release.
All new development should target the LibreDWG reader.

### Verification

Run the portability test suite:

```bash
PYTHONPATH=src pytest verify/portability/ -q
```

This verifies OS detection, ctypes library loading, and output schema
consistency across platforms.
