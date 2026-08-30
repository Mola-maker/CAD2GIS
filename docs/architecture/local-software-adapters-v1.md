# Local software adapters v1

## Decision

CAD2GIS keeps local desktop control behind the existing MCP server and one
canonical CLI. The plugin remains a package of workflow skills plus that MCP
server; it does not embed a second conversion engine.

```text
Codex / other MCP host
  -> CAD2GIS skill (workflow and safety constraints)
  -> cad2gis-agent MCP (typed tools, JSON results, project-root policy)
     -> canonical CAD2GIS CLI / Python services
        -> LibreDWG primary reader
        -> AutoCAD Core Console explicit fallback / parallel verifier
           -> read-only COM fallback only when explicitly enabled
        -> token-authenticated 127.0.0.1 QGIS bridge
           -> dedicated visible QGIS desktop session
```

This follows the useful intersection of two designs:

- OpenAI plugins combine skills and an optional MCP server. Local Codex clients
  support STDIO MCP processes and share host configuration. Server
  `instructions` carry cross-tool constraints.
- CLI-Anything starts from the real application's backend, exposes both
  scriptable one-shot commands and session state, emits machine-readable JSON,
  and verifies outputs with the real application instead of reimplementing it.

The Codex App Server is intentionally not in this data path. App Server embeds
Codex conversations, approvals, and events into another rich client. CAD2GIS
needs the inverse direction: Codex calling local engineering tools, for which
MCP is the stable boundary.

## CAD reader adapter

The CAD reader remains authoritative and read-only. Reader precedence is part
of the public contract, not a machine-local preference:

1. `inspect_source` / `export_source` enter through MCP or the canonical CLI.
2. LibreDWG is the default and primary source reader.
3. AutoCAD is selected explicitly only after a classified LibreDWG failure or
   for an explicitly requested parallel verification run. The adapter never
   changes reader silently.
4. Inside the AutoCAD fallback channel, Core Console is preferred and COM is a
   compatibility fallback only when explicitly enabled after a classified Core
   Console failure.
5. The reader returns immutable CAD records; later stages own semantics,
   topology, georeferencing, and delivery.
6. Entity inventory, hashes, curve facts, and output artifacts are verified.

`CAD2GIS_READER_BACKEND=autocad` is therefore a per-run recovery or comparison
override. It must not be stored as the plugin's normal default. Keeping the
LibreDWG failure visible preserves provenance and prevents a machine-dependent
reader switch from changing source facts without review.

AutoCAD profiles and COM Running Object Table entries are Windows-user scoped.
Codex desktop may run MCP commands under an isolated account instead of the
interactive desktop user. In that case the adapter must not pretend that an
interactive AutoCAD instance is attachable. Export a portable AutoCAD `.arg`
profile and set `CAD2GIS_AUTOCAD_PROFILE`; Core Console receives it through the
official `/p` switch. COM fallback resolves ProgIDs to CLSIDs and is attempted
only when explicitly enabled. A future interactive-user sidecar can use the
same typed loopback pattern as QGIS, but arbitrary UI command injection is not
part of this adapter.

For the one-time user-boundary handoff, the plugin ships
`scripts/export-autocad-profile.lsp`. The user loads it inside an already
initialized interactive AutoCAD session and runs `CAD2GIS_EXPORT_PROFILE`.
The helper obtains `Application.Preferences.Profiles`, reads `ActiveProfile`,
and calls Autodesk's `ExportProfile` ActiveX method to write a new `.arg` file.
It refuses to overwrite an existing target and contains no profile import,
profile switch, registry write, or drawing command. The resulting portable
file is then an explicit input to the sandbox-side Core Console adapter.

No UI click recorder is required for DWG reading, and no simplified Python CAD
parser silently replaces AutoCAD.

## QGIS desktop session adapter

QGIS is a downstream review surface. It may inspect a completed or candidate
GeoPackage, apply styles, toggle layers, zoom, and export a view for human
review. It is not a DWG reader, semantic compiler, topology repair engine, CRS
authority, or substitute for the canonical conversion pipeline. GUI changes
are treated as late-stage fine-tuning inputs and must be captured in a new
source-bound candidate run before they can affect a formal delivery.

`cad2gis qgis start` launches QGIS with an in-process bridge loaded by
`--code`. The bridge:

- binds only `127.0.0.1` on an ephemeral port;
- requires a random per-session token;
- stores the descriptor under an allowed CAD2GIS project root;
- redirects QGIS stdout/stderr away from the MCP STDIO channel;
- validates every open/load/export path against the configured roots;
- exposes fixed typed commands, never `eval`, `exec`, or arbitrary Python;
- controls a dedicated visible QGIS process, not an unrelated user session.

Supported commands are status, open project, load a CAD2GIS run, set layer
visibility, zoom to full extent, export the current view, and stop the managed
session. Status includes QGIS version, PID, active project, layer IDs/names,
visibility, providers, sources, validity, and canvas extent.

`load-run` finds the run's delivery GeoPackage, loads its sublayers through the
real QGIS providers, applies matching QML styles, zooms the canvas, and returns
structured evidence. `export-view` renders through QGIS and reports file size
and the PNG signature.

## Security and lifecycle

- MCP file access remains constrained by `CAD2GIS_PROJECT_ROOTS` or
  `CAD2GIS_PROJECT_ROOT`.
- `CAD2GIS_QGIS_EXECUTABLE` is an operator configuration value. Tool arguments
  cannot select an arbitrary executable.
- The executable must be named `qgis-bin.exe` or `qgis.exe`; otherwise launch
  is rejected.
- QGIS discovery checks explicit configuration, PATH, then installed Start Menu
  shortcuts.
- A session descriptor is reusable while its bridge responds. Stale descriptors
  do not grant control of another process.
- The token is never returned from MCP/CLI results.
- Streamable HTTP for the outer MCP server remains loopback-only unless an
  authenticated TLS reverse proxy is supplied separately.

## Verification contract

The adapter is accepted only when all of the following are observed:

1. `cad2gis doctor --deep --strict --json` reports primary LibreDWG readiness,
   AutoCAD fallback readiness where configured, and QGIS desktop-session
   discovery.
2. STDIO `tools/list` includes the typed QGIS tools alongside the CAD tools.
3. A real DWG is inspected with the primary reader. The AutoCAD fallback is
   separately exercised with a non-empty, hash-bound inventory.
4. A visible QGIS process is launched, a real GeoPackage is loaded, layers are
   inspected and manipulated, and QGIS exports a valid non-empty PNG.
5. Unit and integration tests validate the token protocol, executable allowlist,
   run artifact selection, and MCP registration.

If the MCP Windows account differs from the interactive AutoCAD account, item
3 additionally requires an exported ARG profile configured through
`CAD2GIS_AUTOCAD_PROFILE`; process discovery alone is not proof of read access.

## Live validation record (2026-08-30)

QGIS passed the real-application round trip on the local workstation:

- QGIS `4.0.3-Norrköping` started as a dedicated process using the adjacent
  OSGeo4W `qgis-bin.env` and an isolated profile directory.
- The typed bridge loaded six valid spatial layers from a real GeoPackage and
  reported two non-spatial audit tables as explicitly skipped.
- `source_blocks` visibility changed `true -> false -> true`, full extent was
  returned, and QGIS rendered a 17,235-byte PNG with signature
  `89504e470d0a1a0a`.
- The bridge reported `arbitrary_python: false`; the dedicated process was
  subsequently confirmed stopped.
- A separate content-level validation opened the official QGIS Training Data
  `release_3.44` `world.qgs` project. QGIS loaded the `continents` polygon layer
  through OGR as valid and rendered a recognizable 1,800 x 1,000 world map
  (554,664 bytes, PNG signature `89504e470d0a1a0a`). This replaces the earlier
  sparse native-CAD view as the QGIS rendering acceptance artifact; the sparse
  view remains only transport evidence for that unregistered CAD source.

AutoCAD subsequently passed the real-DWG acceptance item across the Windows
user boundary:

- The constrained `export-autocad-profile.lsp` helper was loaded in the
  interactive AutoCAD 2027 session. AutoCAD exported the active
  `<<Unnamed Profile>>` to a 105,665-byte ARG file under the allowed project
  root without modifying the blank drawing.
- `cad2gis doctor --deep --strict --json` reported `ready`, recognized the ARG
  as a portable profile, and reported both DWG ingest and QGIS desktop-session
  capabilities.
- Core Console inspected the real 127,744-byte
  `奈曼二节点机房--肉联厂基站.dwg` through the
  `autocad_core_console_bulk` backend. It parsed and returned all 530 protocol
  rows with zero skipped rows, 12 layers, 34 block instances, 140 annotation
  entities, and 336 entities carrying native-length and curve facts.
- The source was bound to SHA-256
  `0547b7b9a9f2e0a16fd08b968301d955c1f4af0abba00f5d1ac82774c45e9b8e`
  and the inventory to
  `f5302d234ff5e8594b7a9bbc6942ea1954e63ad1db0856d284e7a518cd7934ef`.
  A direct MCP STDIO `inspect_source` call reproduced those values using the
  installed personal plugin configuration.
- Inspection status remained `WATCH` because the drawing contains three
  non-blocking orphan block definitions. Conversion correctly remained gated
  until a source-bound mapping and CRS evidence are reviewed; reader success
  was not mislabeled as semantic or coordinate accuracy.

## Primary references

- OpenAI, Model Context Protocol: <https://developers.openai.com/codex/mcp/>
- OpenAI, Plugin architecture: <https://developers.openai.com/plugins/concepts/plugins>
- OpenAI, Codex App Server: <https://developers.openai.com/codex/app-server/>
- HKUDS, CLI-Anything HARNESS: <https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/HARNESS.md>
- CLI-Anything QGIS implementation: <https://github.com/opengeos/cli-anything-qgis>
- Autodesk, ExportProfile Method (ActiveX): <https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-LT-ActiveX-Reference/files/GUID-C68101BC-DB55-41CF-9B05-BB618DAF7AC3.htm>
- Autodesk, PreferencesProfiles Object (ActiveX): <https://help.autodesk.com/cloudhelp/2021/ENU/AutoCAD-ActiveX-Reference/files/GUID-EE065355-4343-4954-8C5F-04E2BA87891F.htm>
