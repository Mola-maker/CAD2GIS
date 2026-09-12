# Shared application semantic extraction and molamaker feedback

## Delivered behavior

Windows and Linux desktop browsers and the Android WebView use the same `/_app/` shell. The project panel, welcome area and tools menu now expose **提取语义**. A DWG and a new output directory are sufficient; an AI provider, project classification, or assumed geographic CRS is not required.

The service owns one supervised job with these stages:

1. `cad2gis export-source INPUT.dwg --run-dir OUTPUT/source --json`
2. `cad2gis semantic prepare --source-run OUTPUT/source --output-dir OUTPUT/candidates --json`
3. Canonical source snapshot and semantic prepare binding verification through `semantic_stage._bound_context`.

Progress counts completed stages, rather than inventing a duration or percentage. A zero exit status alone does not publish success. Failed or mismatched manifests produce a failed job. Cancellation stays `cancelling` until the worker exits; duplicate jobs remain blocked during that interval. A fresh directory is reserved atomically, and existing output is never overwritten.

Successful jobs provide entity/candidate counts, two JSON manifests, and two databases (`source.gpkg`, `candidate_index.sqlite3`). Download requests recheck bindings and approved paths, including after application restart; the existing database browser remains read only. No semantic store is initialized, no classification is committed or compiled, and source geometry, baseline projects and GCP data are not modified.

The application status exposes `semantic_extraction.available` and `automatic_commit: false`. Older servers leave the action disabled. Authenticated review pages link to the shared workspace; the standalone Web demo does not pretend to run extraction.

## Interaction and assets

The molamaker avatar remains the central visual identity. `molamaker-actions-v1.png` is a 3-column by 2-row sheet, ordered idle, press, working, success, error, cancelled. The image is preloaded before inserting the cropped image sheet; the existing avatar remains visible if loading fails. Button presses are transient reactions, and task completion feedback follows actual job state.

Pointer, touch, Enter and Space follow native button semantics. Disabled buttons are silent, pending handlers prevent repeated submissions, and keyboard focus stays visible. Reduced-motion mode removes movement while preserving text and distinct state images. The explanatory `molamaker-semantic-layers-v1.png` lives inside a collapsed “这一步提取什么？” section and is clearly illustrative.

The action styling uses the existing blue engineering palette with small pink avatar accents. The local `toolbar` class was renamed `action-toolbar` to avoid Framework7's translucent navigation toolbar overlay blurring ordinary action buttons. No additional UI framework or semantic engine was introduced.

## Brainstorm choices implemented

- **Source-bound handoff**: the “交给 AI 审阅” button fills a review-only prompt with exact source/prepare paths. It does not send the prompt, approve decisions, or trigger commits. Acceptance: the user can inspect the draft before sending.
- **Capability-aware availability**: the extraction action is enabled only when the connected service advertises the feature. Acceptance: an older service cannot give a simulated success.
- **Recoverable output selection**: failed/cancelled phases keep their files; “换一个新结果目录” selects a unique location for a retry. Acceptance: previous artifacts are preserved and an existing output path is rejected.

The source plugin README and conversion skill now describe this workflow, require actual MCP capabilities, and stop at prepare when the request is extraction or review only. Skill instructions cover all client registrations through the canonical MCP server.

## Verification

`tests/test_application.py`, `tests/test_application_semantic.py`, and `tests/test_plugin_contracts.py` exercise shared job orchestration, real source/prepare artifact validation with controlled source records, failed subprocesses, tampered/missing artifacts, cancellation while validation is running, repeat-job rejection, authenticated downloads, approved-path checks, plugin contracts and the companion state model. Result: 38 passed, 1 skipped. One existing Windows symlink case is skipped because the current account cannot create symlinks.

Browser checks use bundled Playwright and local Edge 152.0.4191.66. They exercise login deep linking, Enter/Space, repeated clicks producing exactly one job, a real reader rejection of an invalid DWG, absence of success artifacts after failure, a 600 ms delayed native double submit without duplicate jobs or navigation, new output selection, reduced motion, touch input and 390-pixel viewport overflow. No page errors were observed. Evidence is in `output/playwright/semantic-browser-result.json` and `semantic-{desktop,desktop-failure,mobile,explainer}.png`.

The browser check found and fixed native Window timer receiver binding and login hash cleanup dropping the semantic deep link. These were not observable in the isolated Node state test.

Boundary: these UI checks establish shared browser behavior and orchestration. They do not establish Android device installation, Linux packaging, or geospatial accuracy. Real DWG compatibility, baseline/GCP validation, OSM precision and package delivery are tracked by the broader upgrade verification.
