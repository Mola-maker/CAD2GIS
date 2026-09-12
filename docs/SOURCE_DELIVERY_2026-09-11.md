# Reviewable source and agent-plugin delivery

`tools/build_upgrade_sources.py` packages current worktree bytes, including uncommitted fixes and new source assets. It does not use `git archive HEAD`, install dependencies, change Git history, or modify baseline engineering data. Python 3.11/3.12 and Git are sufficient to build the archives; verification requires Python only.

## Selection policy

The source ZIP includes existing tracked application, Android, plugin, tool, test, documentation and project configuration files. It also includes non-ignored additions under `src`, `tests`, `docs`, `tools`, `packaging`, `apps`, the supported plugin, `.github`, `.agents` and `.claude-plugin`, plus recognised root project files. Current file bytes are read, rather than their last committed contents.

Explicit exclusions cover Git/IDE/agent state, virtual environments, dependencies, caches, temporary directories, output/build/distribution directories, verification products, compiled installers, original DWG/DXF/DGN input, machine environment files, credentials, private keys and signing keystores. Private-key content and probable literal API credentials in otherwise eligible files cause an error requiring review. Paths and reasons for excluded candidates are recorded. Git-ignored untracked files are not candidates; explicitly add a legitimate ignored source file to Git before building. The Git index is not otherwise changed by this tool.

Baseline delivery includes **tracked** project `config` files (including frozen OSM snapshots) and `view-with-source-SVG.qgz`. Standalone derived run directories, GeoPackage files and review receipts stay outside the source ZIP. The approved QGZ engineering projects are retained intact, including their embedded `delivery.gpkg`, `osm-basemap.gpkg` and style databases; those embedded datasets are therefore present in the source archive. No baseline project is unpacked or rewritten during packaging. Tracked `tests/data` fixtures remain included so the supplied tests have their versioned inputs.

The agent-plugin archive contains the supported plugin's actual bytes, including hidden `.codex-plugin`, `.claude-plugin` and `.mcp.json` registration files, scripts, skills, agents, clients and README. It requires the separately installed canonical CAD2GIS runtime.

Default limits are 25 MiB per included file and 250 MiB total. Exceeding either limit fails with the offending file; it never silently drops eligible source. Explicit `--max-file-bytes` and `--max-total-bytes` allow a reviewed larger package. Symlinks, submodules, case-colliding paths and unresolved Git conflicts fail instead of pulling files from outside the repository.

## Preview and final build

Run a read-only inventory before the final code freeze:

```powershell
python tools/build_upgrade_sources.py build --dry-run
python tools/build_upgrade_sources.py build --dry-run --report tmp/source-inventory.json
```

The optional report is exclusively created; choose a new path for a later report. Keep it under an ignored temporary directory or outside the repository.

After application code, assets, packaging and documentation are stable:

```powershell
python tools/build_upgrade_sources.py build
```

For version `0.7.0`, the default outputs are:

- `dist/source/CAD2GIS-0.7.0-source.zip`
- `dist/plugin/CAD2GIS-0.7.0-agent-plugin.zip`
- A `.manifest.json` and `.sha256` beside each ZIP.

The source ZIP has one `CAD2GIS-0.7.0/` root. The plugin ZIP has one `cad2gis-agent/` root. Existing delivery files are never replaced or deleted. Use fresh directories for a subsequent delivery:

```powershell
python tools/build_upgrade_sources.py build --source-dir dist/source-second --plugin-dir dist/plugin-second
```

Both archives are staged, independently verified and rebuilt a second time before publication. Repeat hashes must agree. The build also rechecks Git inventory and every included file before publishing so concurrent edits cannot silently escape the manifest. In an interrupted publication, retain completed files for review and choose new directories for a retry.

## Manifest and verification

`DELIVERY-MANIFEST.json` inside each archive records schema/version, base Git HEAD, branch, current dirty state and changed paths, selection/exclusion policy, plus every payload path, byte size, SHA-256 and fixed file mode. `tree_sha256` hashes the canonical ordered path/size/hash/mode records. The manifest itself is covered by the whole-ZIP SHA-256 sidecar; it is not recursively included in its own tree hash.

ZIP paths are sorted, timestamps fixed to 1980-01-01, and file modes fixed to Git executable status (new shell scripts are executable). ZIP encoding and compression settings do not include host paths or current timestamps. Identical source inventory and the same Python/zlib implementation produce identical ZIP hashes, regardless of output location or filesystem timestamps. A different dirty-state record or content change is a different delivery.

```powershell
python tools/build_upgrade_sources.py verify dist/source/CAD2GIS-0.7.0-source.zip
python tools/build_upgrade_sources.py verify dist/plugin/CAD2GIS-0.7.0-agent-plugin.zip
Get-FileHash dist/source/CAD2GIS-0.7.0-source.zip -Algorithm SHA256
```

Verification checks archive member coverage, duplicate/path safety, fixed metadata, total budgets, forbidden files and every content digest. Compare the returned whole-ZIP hash with the delivered `.sha256`; internal consistency alone is not an external release signature.

After extracting the source archive, a wheel can be built without a Git checkout:

```powershell
python -m pip install "setuptools>=75" wheel
python -m pip wheel ./CAD2GIS-0.7.0 --no-deps --no-build-isolation --wheel-dir ./wheels
```

The source archive includes all code and packaged UI assets, not downloaded GIS runtimes, SDKs, platform wheels or private DWG datasets. Runtime installation and platform build requirements remain documented in the repository's packaging tools and plugin README.

## Tests and release boundary

`tests/test_source_delivery.py` checks dirty source preservation, new-code and asset inclusion, hidden plugin files, baseline/config filtering, excluded keys/raw data, byte-identical repeat builds, refusal to replace an old delivery, limit failures, tamper rejection, late source changes and wheel construction from an extracted source archive without Git.

The initial workspace dry-run selected approximately 554 source files and 18 plugin files (about 30 MiB before compression); this was a planning inventory, not a release. Final counts and hashes come exclusively from the manifests generated after the complete upgrade is frozen. Formal source and plugin archives are intentionally not generated by a dry run.
