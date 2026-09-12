# Linux 0.7.0-1 installation rebuild

The v0.7.0 GitHub release already contained Linux DEB and portable assets. It is a prerelease; the repository has no stable latest release, so the GitHub `/releases/latest` API returned HTTP 404. README did not offer direct installer downloads. The README and Linux guide now link to the explicit version and revised Linux assets.

Inspecting the downloaded archive found 31 broken `runtime/share/terminfo` aliases: for example `1/1178` points to `../a/adm1178`, but the archive contains `A/adm1178`. This survived construction on a case insensitive filesystem. There were also 944 Python bytecode cache files outside the payload checksum manifest. The application started, so these findings do not establish that conversion or desktop startup was broken.

Runtime copying now consistently excludes optional terminal data and bytecode caches, including when rebuilding from an installed runtime. Linux packaging verifies the complete payload checksum inventory and rejects missing, changed, unlisted files and broken or escaping symlinks before writing an installer. Five new integrity cases and six existing archive/launcher cases passed.

The new package revision is **0.7.0-1**; the application remains **0.7.0**. It reuses the verified published application wheel (`c94e58d4233359c29224e811c8ace63edc6072ede5ea9969ac5d2dd121a93cdc`) and the original patched LibreDWG binaries built on Ubuntu 24.04. It is a repaired runtime package, not a fresh native compiler build. The upstream portable archive SHA256 is `a3de413b3aafc5d9d207a530c7e5ab418d3e355b91c7b978466b894792859332`.

## Validation

On Ubuntu 26.04.1 x86_64:

- Downloaded source archive and retained payload files verified against published SHA256 values.
- New portable archive: 12,279 payload file hashes verified, no broken symlinks, relocated CLI starts.
- Real dpkg installation and removal transactions completed in an isolated root under fakeroot. The isolated package database has no OS dependencies, so `--force-depends` was used; declared libraries, certificates and fonts were separately verified present on the host. This does not claim a system-wide apt installation test.
- The installed runtime passed native DWG roundtrip, GDAL/PROJ/GEOS, MCP, synthetic canonical OSM+SVG conversion, workbench authentication, upload/inspect/SVG, GCP preview/rematch and all three semantic extraction stages. No paid model or private DWG upload was used.
- Test projects remained after the isolated installation was removed. Original host installation was unchanged.
- Ruff and the 11 focused regression tests passed. Full general CI is not claimed by these local checks.

The original v0.7.0 release is immutable: GitHub rejected adding new attachments (HTTP 422), and no original assets were changed. The repaired assets are published in the separate v0.7.0-linux.1 prerelease. `SHA256SUMS-linux-0.7.0-1`, `linux-0.7.0-1-build.json` and `linux-0.7.0-1-verification.json` describe the revised artifacts; the original aggregate release manifest describes the original assets.
