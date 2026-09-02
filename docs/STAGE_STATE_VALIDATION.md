# Stage-state and delivery regression validation — 2026-09-02

## Scope

Baseline: main commit `64fdabd` (business labels separated from length evidence).
This round fixes receipt completeness and extracts writer/admission helpers.
It does not change classification, denoising, support matching or CRS/GCP
thresholds. Docker setup remains deferred.

Completed changes:

- Stage contracts v2 hash full reader/feature state rather than counts alone.
  Topology's in-place feature mutations are included. Unknown objects are not
  silently stringified. Input/output fingerprint overhead is measured.
- Context binds the conversion snapshot, reader selection and modes. Missing
  production reader, plan-domain and artifact modules are included in the
  versioned implementation fingerprint.
- No result cache is enabled; receipts explicitly declare
  `cache_status: disabled_receipt_only` and `cacheable: false`.
- Final delivery hashes are recorded after QGIS style embedding. Source
  admission, reviewed spatial filtering, styled delivery, schema creation and
  segment/asset row writes have separate helper boundaries.
- A read-only exact feature-schema/row comparator detects label, geometry,
  length, source-lineage and CRS-ID regressions with unchanged feature counts.

## Evidence

Local Python suite: **383 passed, 6 skipped**, with three existing dependency
deprecation warnings. Ruff and compile checks pass. Skips are the two opt-in
complex-DWG cases and four unavailable historical Hutabohu evidence tests.
New tests cover state/context hashing, frozen receipts, post-style artifact
hashes, schema uniqueness, rollback and nonpublication on coordinate mismatch.

The isolated Windows `uv tool` environment was reinstalled non-editably from
the working package. `doctor --deep --strict --profile full` reports all four
profiles ready. It uses managed LibreDWG CLI 0.14 and pyramids-gis GDAL;
neither AutoCAD nor Conda is required. Claude Code's plugin MCP health check
reports Connected. Its account status is **not logged in**, so the evidence
below comes from the exact stdio MCP server, not a Claude model session.

| Real source / existing project pack | Baseline result | Candidate result |
| --- | --- | --- |
| Kletek | Delivery produced | Delivery produced; MCP audit PASS; all eight feature-layer schemas and all 263 rows exactly equal |
| Lamteh main | Rejected: declared `CGEOCS=WGS84.PseudoMercator`, observed `Indonesian1974.UTM-46N` | Same rejection before publication |
| Hutabohu | Rejected: reviewed source-inventory hash differs from current reader inventory | Same rejection before publication |

Kletek counts: SITE 1, BOITE 9, PTECH 33, IMB 167, INFRASTRUCTURE 9,
CABLE 33, ZPM 9, ZNRO 2. Its 210 nonempty business labels are preserved;
there are zero diagnostic pseudo-labels and zero nonempty labels lacking
provenance. Empty CABLE labels remain empty: no business identifier is invented
from CAD geometric length. Exact row comparison includes geometry BLOBs,
length metrics and lineage, not merely counts.

Local artifacts are retained under `validation/stage-state-20260902/`:
`mcp-candidate-kletek.json`, `mcp-kletek-equivalence.json`, and the baseline/
candidate Lamteh and Hutabohu failure reports. The earlier Kletek reference is
`validation/claude-code-cold-20260902/mcp-kletek-label-fix-run/delivery.gpkg`.
Generated drawings and run artifacts are not added to the repository.

## Remaining work and limits

- Lamteh and Hutabohu need source-bound project-pack reconciliation and review,
  not removal of CRS/inventory gates or blindly replacing expected hashes.
- Kletek remains CONDITIONAL: no independent surveyed GCP/check set proves
  absolute positional accuracy. Regression equivalence is not ground truth.
- A logged-in Claude Code model session is still required for the full
  natural-language/tool orchestration acceptance test.
- Large classification/topology functions still need further decomposition;
  a replay-safe result cache remains unimplemented. This round demonstrates
  behavior preservation, not a measured throughput improvement.
