# Real-drawing verification

Use this reference when testing an installed runtime against actual drawings,
comparing CAD and GIS visually, or diagnosing GCP and field discrepancies.

## Runtime and extraction evidence

Run a non-editable installation without checkout `PYTHONPATH` or an unintended
`CAD2GIS_BACKEND_PATH`. Retain the build identifier/wheel hash, actual imported
module paths, reader version, configured roots and an actual MCP stdio probe.
Matching version strings alone do not prove matching code. Keep original DWG
hashes before and after testing; write source snapshots and each rerun to new
directories.

An undecodable reader string must retain reversible code-unit/byte evidence.
Do not replace, truncate or guess text to make an export succeed. Compare
separate extractions when reader output is unstable: successful reversible
escaping within one extraction does not establish cross-run text determinism.
Retain differing records by handle and intermediate hashes. A source-binding
failure is not permission to edit the expected hash. Use a supported verified
snapshot input when available, or an explicitly selected and verified reader;
otherwise return the frozen source evidence and the blocked canonical status.

DWG insertion units are not sufficient evidence of the geographic coordinate
scale. Use observed GEODATA, current CRS candidates and source-bound reviewed
configuration. Do not copy another drawing's CRS or infer scale from a filename.

## Visual and numeric comparison

Compare three surfaces: original/native CAD rendering when available, the full
extracted source/expanded-instance plan, and the exported delivery. A vector
overview cannot validate every font, hatch, clipping boundary or proxy object.
State reader/renderer omissions and which surfaces were actually inspected.

For numeric comparison, bring delivery coordinates back into the source frame
with independently verified inverse CRS/GEODATA/registration operations. Report
the coordinate scale and curve-sampling tolerance. If the transform cannot be
inverted reliably, stop that metric rather than compare incompatible domains.
Never regenerate or modify delivery geometry with the audit script.

Retain both whole-drawing and business-extent overlays plus close-ups for
flagged differences. Decorative objects far away can make a full-extent image
look empty. Count raw records, expanded plan entities, renderable geometries,
business features and output segments separately; their counts are not equal
by definition. Reunite split cable segments by source identity before measuring
shape/length differences, and audit vertex/connectivity changes separately.

Distinguish numerical tolerance, declared derivation and unexplained change.
Support colocation and endpoint bridging can move features by metres; report
each operation and its displacement. Polygon validity repair can remove source
spikes or components: record area/vertex/boundary differences and require review
of the loss. Merely labelling a change as derived does not make it acceptable.
Inherited outputs must retain the original repair lineage, not claim identity.

For every source entity, distinguish mapped, annotation, graphic-only,
unsupported, unresolved and absent from the canonical evidence ledger. An
unmapped decorative line is not automatically a missing asset. An object absent
from both output and evidence cannot be dismissed using the delivery census.

Compare non-empty field values to selected source attributes/annotations and
their provenance. Report unexpected value differences, values without evidence,
blank values and intentional generated identifiers separately. Distinguish
parent cable lengths from split delivery segment lengths.

Nearby integer labels require their own source entity key, original value,
target frame key and independently checked distance. They are derived candidate
relations requiring review, even if the label looks plausible; never describe
them as direct block attributes. Token families sharing the same token count
may have different structures. Preserve separate source-observed families and
require complete, non-overlapping coverage instead of broadening a regex until
the validation passes.

## Web and manual GCP

Open the actual run's local review URL and verify its drawing SHA, layer counts
and live status. Historical demo pages do not verify the current run. The CAD
pane currently backtracks source geometry of delivered objects and can omit
derived regions and unmatched source objects; use the full-source audit for
completeness. The “CAD 视觉证据” control opens rendered CAD evidence.

The “03 空间配准” panel accepts training and independent check controls. Use
the current profile's minimum counts and spatial distribution gates, not a few
convenient coincident points. Ask for authoritative control files and CRS when
absolute accuracy is requested. Keep unknown coordinates unknown; never create
survey points from the output being tested.

Current web capture labels controls as relative OSM references. Its similarity
preview and translation export use different models; preview RMSE is not the
final independent-check result. Inspect the generated profile and a new
canonical run before claiming registration. The optional `gcp prepare` capture
operator may be unavailable even though web capture and canonical GCP profile
validation exist. Record the missing operator; do not silently install an old
unreviewed script. OSM overlap and embedded CRS do not prove absolute accuracy.

## Deliverable status

Return the actual GeoPackages/styles, review images and difference tables, an
index of source evidence and failures, and reproducible runtime/run bindings.
Keep artifact integrity, conversion status, geometry fidelity, field fidelity,
visual completeness and absolute accuracy as separate results. A successful
file write or `audit_status=PASS` does not erase unresolved or lossy cases.
