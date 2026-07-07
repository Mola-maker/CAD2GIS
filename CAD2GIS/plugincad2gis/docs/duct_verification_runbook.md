# Duct Verification Runbook

The current duct per-feature verification baseline is honest but incomplete: only single-CIRCLE
duct symbols are independently shape-confirmed. `gc013*` symbols and route/line ducts must not be
credited merely because topology propagation classified them as ducts.

## Verification Contract

Input features:

- `feature_class == "duct"`
- `SourceRef.handle`, `SourceRef.block`, `SourceRef.layer`, `SourceRef.entity_type`
- `_map_evidence`, parsed duct attributes, block fingerprint, and propagation metadata when present

Independent evidence sources:

- Hand-labeled stratified subset keyed by source handle.
- Node/duct coordinate table export keyed by source handle or by a deterministic spatial join.
- Design detail/schedule table cross-reference that can be tied back to a source handle.

Output verdicts:

- `verified`: external evidence says this source handle is a duct.
- `unverified`: feature is a duct output, but no independent confirmation is available.
- `not_verifiable`: no appropriate independent evidence source exists for that feature class or subset.
- `negative`: external evidence says the source handle is not a duct.

Rule: topology propagation is classification evidence, not verification evidence. It cannot verify itself.

## Reviewed Label Format

`verify_per_feature(..., reviewed_labels=...)` accepts a dictionary shaped like this:

```json
{
  "duct": {
    "5C123": {
      "class": "duct",
      "evidence": "hand-reviewed DS-06 duct schedule row 14"
    },
    "5C124": {
      "class": "paving",
      "evidence": "negative control from reviewed sheet"
    }
  }
}
```

Only entries whose reviewed class is exactly `duct` add to the verified duct set. Negative entries
are useful audit evidence but do not verify the feature.

## Hand-Labeled Subset Workflow

1. Export candidate ducts with source handle, block, layer, geometry, matched label, parsed duct spec, and `_map_evidence`.
2. Stratify by source block: at minimum `gc170`, `gc013a`, `gc013b`, `gc013c`, and route/line ducts with no source block.
3. Review candidates against the source drawing/detail sheet.
4. Save reviewed labels keyed by `SourceRef.handle`.
5. Pass the reviewed labels into `verify_per_feature()`.
6. Report the uplift separately from the baseline fingerprint-only score.

## Regression Rules

- The baseline fingerprint verifier must still verify `gc170` single-CIRCLE ducts.
- A topology-propagated `gc013*` duct with no reviewed label must remain unverified.
- A reviewed non-duct label must not count as verified.
- Per-feature correctness must stay separate from the weighted six-dimension score unless a formal benchmark definition is added.

