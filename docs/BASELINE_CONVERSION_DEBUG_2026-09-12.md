# Baseline conversion recovery and platform integration

The first nine-drawing batch completed eight conversions but did not reproduce
the reviewed QGIS baselines. A successful parser, conversion, or ordinary QGZ
package was insufficient evidence of correct baseline delivery. The recovery
uses immutable source-bound runs and preserves the original baselines.

## Observed failures and their causes

| Observed failure | Root cause | Fix and regression boundary |
| --- | --- | --- |
| Extra or missing business entities | Generic onboarding replaced existing reviewed mappings. Lamteh main counted IMB twice; Tinggar lost BOITE; SF jobs included non-business geometry. | Resolve reviewed projects by source SHA and complete inventory before new onboarding. Preserve the actual baseline project slug, mapping and partition configuration. Compare logical source identities separately from cable segments. |
| Onboarding approved a run that conversion rejected | Onboarding and conversion supplied different project identity, SF denoise and geometry-repair inputs. | Share canonical policy application, pass project_id and directory identity, record optional geometry_repairs expectations, and reject incompatible conversion modes before output. Existing profiles without the optional field remain compatible. |
| Displacements relative to the baseline | The batch selected nominal registration and discarded reviewed OSM correction profiles. | Replay pinned source-bound profiles with OSM matching enabled. Normalize both datasets into a common CRS before measuring differences; a different CRS identifier alone is not an offset. |
| Frozen road cache missed despite equivalent coordinates | Query bounds differed from the cached bounds by one floating-point rounding step. | Keep exact-key lookup first. Allow only a unique equivalent cache with every bbox edge within 1e-12 degrees, valid original bbox filename binding and unchanged road-content SHA. Reject ambiguous or corrupted caches. Matching thresholds are unchanged. |
| Manado appeared to contain extra entities | Independent EMR scopes were collapsed or counted against the main panel. | Preserve EMR28560 and EMR29619 directories and compare each scope with its corresponding baseline. |
| Unexpected output folders and missing self-contained SVG projects | Generic `project` naming changed slug-sensitive policies. Ordinary `package_delivery` was mistaken for the standard SVG/OSM publication. | Keep the baseline directory identity and distinguish ordinary portable QGZ+ZIP from six-file publication. Register the canonical matched publisher and independent QGIS verifier as MCP tools. |
| SVG evidence failed to load | CSV queue MIME/allow-list behavior differed on Windows; candidate evidence and final QGIS SVG bindings were not independently checked. | Serve only manifest-sealed PNG/SVG/CSV assets with explicit content types and tamper checks. Reopen both QGZ projects and verify source SVG bindings and actual rendered pixels. Unresolved point symbols remain visible. |
| The conversion review console was never opened | A static batch index and a prepared launch command were reported as a review workspace. | Register real localhost server lifecycle tools, verify source/run/manifest identity before readiness, and open the returned live URL in the host. Preparing a workspace alone does not start a service. |
| Newly installed behavior did not match cached tools | Package, plugin skill and host-injected tool schema can come from different installations. | Check actual stdio capabilities and schema digests, update the marketplace source with the official cachebuster flow, and reinstall. A wheel version alone does not prove the host loaded the current skill. |
| QGIS labels and titles appeared as square glyphs | The Windows headless QGIS process had an empty Qt font database. Nonempty map pixels did not prove readable text. | Load existing fonts into the application process, reject missing font coverage, and check the actual rendered label text and fonts. Preserve failed images and bind replacement evidence to the same QGZ hashes. |
| QGIS launched directly but stalled through MCP | The Windows virtual-environment launcher reinjected `PYTHONHOME`; bundled GDAL also changed inherited library/data paths. | Remove the foreign Python home and only discovered bundled GDAL paths from the QGIS child environment, isolate stdin from the MCP transport, and terminate owned probe trees on timeout. Parent and unrelated user settings stay intact. |
| A reachable review console could still have empty or wrong GIS layers | The lifecycle readiness check discarded the actual layers response. | Bind expected counts to the authoritative main/partition manifest and require unique actual layer names and exact feature counts before readiness. |
| A delivery card or ZIP could reference incomplete or changed files | Archive checksums alone did not preserve the canonical manifest and QGIS receipt bindings. | Check every expected file, archive member, canonical database hash, SVG asset hash, QGZ receipt and frozen OSM source; reject incomplete, extra, duplicated or changed outputs before reporting success. |

## Linux and Windows merge boundary

The merge includes the Linux repair chain `3f69e9c` (runtime payload integrity),
`eed53d5` (desktop chat/review), and `54c3411` (verified QGZ packaging and artifact
downloads), while retaining the Windows fixes already on main: `244f9eb`
(upgrade/uninstall guard), `cef39e2` (bound chat results), and `fc7acfd` (launcher
lifetime). The recovery changes were first committed as `d7f409a`.

The independent `review.html` from main supersedes the Linux branch's older
shared landing-page filtering workaround. The merged UI retains review ID,
view ID and server revision checks, as well as real delivery artifact links.
Both old database schemas migrate without losing existing result or artifact
records. Linux packaging rejects missing, changed, extra or invalid symlink
payload entries and excludes invalid terminfo aliases and bytecode caches.

## Registered delivery workflow

After the established inspect, source export, source-bound onboarding,
validation, conversion and run audit steps:

1. Use `package_qgis_delivery` for an ordinary portable QGZ+ZIP. It explicitly
   requires sidecar data and does not claim SVG application or rendered QA.
2. Use `publish_matched_delivery` for the standard six-file SVG/OSM output,
   supplying a frozen snapshot for every populated scope. Its canonical
   publisher rejects abstained, unavailable or coarse scopes.
3. For an explicitly uncorrected reference project, first use
   `build_source_svg_delivery` to add source-bound SVGs to the ordinary package,
   then use `build_qgis_review` with the actual hash-bound matching context.
   This does not turn a reference into a matched revision or change source coordinates.
4. Use `verify_qgis_review` to reopen and render each review QGZ in a separate
   QGIS process. Configure the local QGIS runtime with `CAD2GIS_QGIS_PYTHON`.
5. Use `audit_visual_conversion` for source-bound geometry, attribute and label
   comparisons, preserving the original source and run artifacts.
6. Use `start_review_server`, check the returned ready status, and open its URL.
   `get_review_server_status` and `stop_review_server` operate only on services
   owned by that MCP process. Browser opening remains a host action.

Every scope's standard directory contains `delivery.gpkg`, `delivery.qgz`,
`delivery.review.json`, `osm-basemap.gpkg`, `view-with-source-SVG.qgz`, and
`view-with-source-SVG.review.json`. The tools reject existing output artifacts
and preserve matching, source binding and conditional acceptance boundaries.

## Evidence and remaining interpretation limits

The recovered nine drawings contain eleven scopes and 3,704 total features.
All scopes match baseline logical entity keys, compared source/business fields,
and geometry after CRS normalization. The maximum baseline Hausdorff difference
is approximately 3.82e-9 metres. Twenty-two QGZ projects were independently
reopened and rendered, and all 66 copied standard delivery files passed hash
checks. These are baseline consistency results, not survey accuracy.

Four iteration candidates still have larger unresolved evidence counts than
the original generic batch: Manado, Taipa, Tinggar and Tinggede. Detailed
inspection identified 226 failed precise annotation attempts with later
successful pole-label assignments. Their successful targets are outside the
original family layer constraint, and some also exceed its distance limit.
The successful fallback therefore does not prove the original precise rule
was satisfied. Those attempts must remain auditable; neither warning deletion
nor an acceptance-gate relaxation is an appropriate fix.

Independent route ambiguity, endpoint tolerance, exact segment dimension and
device-number review items remain explicit. OSM authority stays relative and
`absolute_accuracy_verified=false`. No candidate is automatically accepted.
Private source files, local run artifacts and installation paths are not part
of this code publication.
