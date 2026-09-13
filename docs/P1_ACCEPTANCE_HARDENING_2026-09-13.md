# P1 acceptance and native-reader hardening

The nine-drawing review showed that a readable delivery could still have
unverified engineering decisions. This change keeps file integrity, declared
engineering acceptance and independent coordinate accuracy distinct.

## Measurement and reviewer gates

`validation.measurements` no longer starts with `passed: true`. It reports
`status: not_verified` and `passed: null` when dimensions, complete coverage or
an explicitly declared measurement tolerance are missing. An explicit applicable
tolerance is evaluated against the actual diagnostics. Numerical segment length
closure remains a separate invariant; it is not a surveyed measurement check.
The old compatibility path no longer invents a `1e-6` span-error tolerance.

Empty reviewer gate lists or delivery-count declarations report that they are
unverified. Mandatory source conservation, semantic checks and geometry policy
checks continue to run. Reviewer acceptance and pending engineering decisions
are exposed separately, and missing acceptance evidence keeps a usable result
`CONDITIONAL` rather than promoting it to `VERIFIED`.

Recorded support collocation, cable endpoint bridging and boundary repairs do
not become approved merely because lineage exists. Likewise, selecting an
annotation through a later rule does not erase a failed precise association.
This change preserves geometry, source profiles and unresolved evidence; it
does not assert that the prior baseline's engineering decisions were wrong.

## LibreDWG bindings

LibreDWG 0.14 return codes are bitmasks. Critical bits, including missing
sections/classes, invalid DWG, I/O errors and out-of-memory errors, stop extraction
before traversal. Unknown codes also stop extraction. Low-bit warnings preserve
inspectable records and diagnostics but do not establish a complete inventory.
Completeness requires a zero native status and traversal without skipped rows.

The actual native status, flag names and reader failures are retained in the
diagnostic receipt. A partially decoded drawing cannot enter authoritative
ingest merely because the native process returned some records.

## Verification boundary

Regression coverage uses synthetic native-binding responses and synthetic GIS
projects. The earlier real nine-drawing results are preserved, and conversion
of those drawings remains paused. A source-key/geometry match to baseline is
not engineering acceptance or independent survey accuracy. Hosted CI evidence
must refer to the actual submitted commit; a GitHub Actions budget rejection
does not count as a test pass.
