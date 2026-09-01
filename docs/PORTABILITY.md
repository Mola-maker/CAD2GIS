# CAD2GIS Portability

## Product runtime

The supported agent runtime is an ordinary Python 3.11/3.12 tool installation:

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install
cad2gis doctor --deep --strict --json
```

On Windows, stop CAD2GIS MCP clients and any `cad2gis review` process before a
forced reinstall. A running Python process locks the uv tool directory and can
make replacement fail with `os error 32`; restart clients after installation.

The `agent` extra installs MCP/review dependencies, ezdxf, and a platform wheel
that bundles GDAL/OGR. `cad2gis runtime install` installs the DWG reader without
using a repository path:

- Windows x64: checksum-pinned official LibreDWG 0.14 release in the user cache;
- macOS/Linux with Homebrew: the bottled `libredwg` formula;
- other POSIX environments: rootless build of the checksum-pinned official
  LibreDWG 0.14 source release into the user cache (requires a C compiler and
  `make`).

AutoCAD and Conda are not required. `env/environment.yml` remains an optional,
reproducible native-development profile rather than the plugin runtime.

## Reader selection

The default `libredwg` selection tries the direct Python binding and then the
official LibreDWG CLI adapter. It never falls through to AutoCAD silently.

```powershell
$env:CAD2GIS_READER_BACKEND = "libredwg"      # default portable resolver
$env:CAD2GIS_READER_BACKEND = "libredwg-cli"  # force CLI adapter
$env:CAD2GIS_READER_BACKEND = "autocad"       # explicit Windows-only fallback
```

Use `CAD2GIS_LIBREDWG_CLI` for a non-standard `dwg2dxf` executable. The older
`CAD2GIS_LIBREDWG_DLL` and `CAD2GIS_LIBREDWG_PYTHON_PATH` overrides remain for
direct bindings. Use `CAD2GIS_ACCORECONSOLE` only for an explicitly selected
AutoCAD adapter.

## Workspace roots

Client templates do not contain a developer drive or checkout path. Claude Code
uses `CLAUDE_PROJECT_DIR`; Cursor/VS Code use `${workspaceFolder}`; Codex starts
the server in the current project. `CAD2GIS_PROJECT_ROOTS` is an optional
path-separated override for intentionally shared data outside that workspace.
The server remains fail-closed.

## Portability tests

```shell
python -m pytest tests/test_portable_runtime.py -q
python -m pytest tests/test_reader_capabilities.py -q
python -m pytest tests/test_canonical_cli.py -q
```

The real-DWG suite is external and capability-gated:

```powershell
$env:CAD2GIS_TEST_DATASET_ROOT = "D:\cad-test-data"
python -m pytest tests/test_apd_test_compatibility.py -q
```

A missing reader runtime may skip capability-dependent cases; malformed
records, silent row loss, or source-hash mismatch must fail.
