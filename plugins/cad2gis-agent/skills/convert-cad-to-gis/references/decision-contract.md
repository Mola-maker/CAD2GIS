# Decision contract

## Immutable inputs

A decision pack binds exactly one source SHA-256 and one Evidence Graph
SHA-256. Every target and evidence reference must already exist in that graph.

## Execution boundary

- `off`: ignore model decisions.
- `observe`: validate and record the pack; apply nothing.
- `assist`: simulate, independently validate, and apply only registered
  operations that satisfy automatic-decision policy.

Models cannot submit coordinates, arbitrary geometry, WKT, inferred lengths,
CRS declarations, or unobserved entity identifiers.

## Required verification

All operations require independent geometry, topology, and native-length
reports. Georeference operations additionally require a CRS report, spatially
distributed controls, and independent check points. Missing reports quarantine
the operation; failed reports reject it.

## Delivery interpretation

GeoPackages are immutable run artifacts. The run manifest is authoritative for
source binding, artifact hashes, validation, decision execution, and run status.
