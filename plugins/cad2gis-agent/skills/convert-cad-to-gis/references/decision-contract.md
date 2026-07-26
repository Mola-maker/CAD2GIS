# Decision contract

## Immutable inputs

A decision pack binds exactly one source SHA-256 and one Evidence Graph
SHA-256. Every target and evidence reference must be a node already present in
that graph.

## Execution boundary

- `off`: ignore model decisions.
- `observe`: validate and record the pack; apply nothing.
- `assist`: simulate, independently validate, and apply only registered
  operations that satisfy the automatic decision policy.

Executable operations are `attach_existing_label`, `register_style`,
`materialize_native_curve`, `join_observed_endpoints`,
`split_at_observed_intersection`, and `merge_collinear_fragments`. Geometry
repairs add derived network relations between observed CAD facts and do not
mutate source geometry or length. Direction and CRS operations remain
quarantined until a deterministic simulator and the required validators are
available.

## Required verification

All operations require independent geometry, topology, and native-length
reports. Georeference operations additionally require an independent CRS
report with check points and spatial coverage. Missing reports quarantine the
operation; failed reports reject it.

## Delivery interpretation

GeoPackage files are immutable run artifacts. The run manifest is authoritative
for source binding, artifact hashes, decision execution, validation, and
`run_status`.
