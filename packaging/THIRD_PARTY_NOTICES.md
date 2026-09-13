# CAD2GIS application bundle — third-party components

Original CAD2GIS source declares `PolyForm-Noncommercial-1.0.0` in pyproject.toml.
See the accompanying LICENSE.md and NOTICE.md for its noncommercial terms,
copyright notices and separate commercial-authorization process. Third-party
components retain their existing licenses; this notice does not relicense them.

This development installer includes separate third-party components. Their original
license files are retained in `runtime/` (Python), installed `*.dist-info/licenses/`
directories, `libredwg/`, and Web UI asset directories. Consult
`THIRD_PARTY_COMPONENTS.json` for the exact installed versions and license locations,
`requirements.lock` for dependency hashes, and `bundle-manifest.json` for file hashes.

- CPython / python-build-standalone: Python Software Foundation license and bundled
  third-party notices. https://github.com/astral-sh/python-build-standalone
- LibreDWG 0.14: GNU GPL version 3 or later. The unmodified corresponding upstream
  source archive is distributed beside the installers in `third-party-sources/`.
  https://www.gnu.org/software/libredwg/
- pyramids-gis 0.58.1: GNU GPL version 3. Its source archive is distributed beside
  the installers in `third-party-sources/`. Its vendored native libraries have their
  own licenses; see the wheel's bundled notices. https://github.com/Serapieum-of-alex/pyramids
- GDAL/OGR, PROJ, GEOS, NumPy, Shapely, ezdxf, FastAPI, Uvicorn and MCP SDK:
  see their installed license files and exact package metadata in the inventory.
- OpenLayers 10.6.1: BSD 2-Clause. Its license is included with the local Web UI assets.
- Android client: Android SDK build tools are used only to build the APK; the APK
  uses the operating system WebView. The SDK/JDK build tools are not redistributed
  inside the CAD2GIS installer.

These are local development/test packages, not a declaration that the combined
proprietary/GPL distribution has been legally cleared for public redistribution.
A public commercial release requires resolving the combined licensing terms and
providing complete corresponding source/build materials as applicable. Code-signing
certificates and Android production signing keys are also separate release inputs.

Network services are not bundled: OSM lookup/tiles and optional AI onboarding require
network access. Offline OSM snapshots can be selected through the canonical CLI.
