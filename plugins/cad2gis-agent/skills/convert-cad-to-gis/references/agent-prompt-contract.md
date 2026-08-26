# CAD2GIS agent prompt contract v2

This contract keeps different MCP hosts consistent. The model plans and selects
evidence; the canonical Python service calculates and validates all geometry.

## Input order

1. Read `get_capabilities` and require `cad2gis.agent_prompt.v2`.
2. Inspect the exact source or run named by the user.
3. Page only the evidence needed for the current decision.
4. Read registered operations and candidate IDs before proposing a repair.

## Tool argument rules

- Send typed JSON arguments directly to MCP tools.
- Select only identifiers returned for this source: layer, block, entity,
  evidence, endpoint, network candidate, CRS candidate, and operation IDs.
- Keep geometry, topology, length, and coordinate accuracy as four independent
  claims. Passing one does not imply any other passed.
- Treat vector reader facts as primary and screenshots as secondary evidence.
- Leave ambiguous entities unresolved. Never fill missing facts with a likely
  value from another drawing, a legend, a filename, or general domain knowledge.

## Failure response

When a required ID, reader fact, source file, control point, or validation gate
is missing, stop the affected operation and report the exact missing evidence.
Do not synthesize coordinates, WKT, lengths, CRS identifiers, GCPs, labels,
layers, counts, or a successful run status.

## Delivery response

After conversion, call `inspect_run` and `audit_run`. Report artifact integrity,
physical GeoPackage layer census, source replay availability, and the four
accuracy claims separately. Only an `audit_status=PASS` run is deliverable.
