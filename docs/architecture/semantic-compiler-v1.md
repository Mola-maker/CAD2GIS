# CAD2GIS source-bound semantic compiler v2

## Purpose

This stage converts the immutable CAD fact package into reviewable semantic
layers without performing topology repair, length inference, CRS conversion,
GCP registration, or final delivery publication.

```text
source.gpkg
  -> deterministic assemblies, labels, endpoint graph and legend-style evidence
  -> layout / CAD-role / source-table batches
  -> host AI selects observed IDs only
  -> deterministic compiler
  -> semantic.gpkg + semantic_manifest.json
```

## Authority boundary

The model may select:

- an existing `assembly_id` and its observed source entity keys;
- one registered semantic class and an optional descriptive subtype;
- an existing candidate text entity as the display label;
- one of the four terminal states;
- evidence strings and confidence.
- observed 24-character evidence IDs attached to the candidate.

The model cannot provide or modify geometry, coordinates, native length,
style, CRS, GCPs, identifiers, or label text. The compiler copies those facts
from the digest-bound `source.gpkg`.

Every feature decision must cite at least one observed evidence ID. Network
route and segment decisions must additionally cite the source relationship
evidence ID, originate in `source_lines`, and carry a finite positive CAD
native length. Free-text explanations never satisfy these gates.

## Relationship evidence graph

Line candidates expose three independent facts without modifying geometry:

- exact source endpoint equality and separately reported proximity;
- endpoint-to-block/point distances with original source entity IDs;
- model-line matches to style-legend entities using layer identity or shared
  layer tokens plus resolved CAD style properties.

Connectivity tolerances and proximity radii are derived per layout/CAD-role
partition and recorded in the prepare manifest. `PROXIMATE_ONLY` is evidence,
not a snap or topology repair.

## Four-state source ledger

Every source entity appears exactly once in `semantic_entity_ledger`:

- `CONSUMED_BY_FEATURE`
- `RETAINED_AS_REFERENCE`
- `EXCLUDED_AS_DOCUMENTATION`
- `UNRESOLVED`

Unmentioned entities remain unresolved. Coverage is never inflated by forced
classification, and a batch decision may never create business features.

## Composite objects and batches

INSERT records are assembled with source records whose AutoCAD owner handle
matches the INSERT handle in the same layout and CAD role. Nearby labels are
candidate evidence only. Assemblies are grouped by layout, CAD role, and
source materialization table so model-space business candidates can be
reviewed independently from block definitions, legends, frames, title blocks,
and paper-space layouts.

## Reproducibility

Decision packs are bound to both the source SHA-256 and candidates JSONL
SHA-256. The compiler rejects unknown IDs, stale candidate packs, invented
labels, duplicate entity assignments, non-registered classes, and semantic
fields on non-feature terminal states.

`semantic.gpkg` retains every `source_*` table and adds:

- `semantic_features`
- `semantic_entity_ledger`
- `semantic_candidate_evidence`
- `semantic_manifest`
- geometry-bearing `semantic_<class>_<source-kind>` views

The stage is valid only when source count, ledger count, distinct ledger keys,
terminal-state totals, and SQLite integrity all close exactly.
Network validation also requires every compiled route/segment to retain a
positive `source_lines.native_length`; the audit reports feature count and the
sum of those unmodified source lengths.
