# Verification Loop — AI Agent Prompts for FTTH GeoPackage Acceptance

**Recast (English) of the FiberHome acceptance standard**
`experiment/evaluation_standards/VERIFICATION_RULE.csv` + 7 layer schema CSVs
(BOITE / CABLE / INFRASTRUCTURE / PTECH / SITE / ZNRO / ZPM; the IMB field list is
embedded in VERIFICATION_RULE.csv row 4.1).

This document is a self-contained prompt pack. Each Rule Group section is an
independently executable agent prompt: it can be handed to an AI verification
agent (or a human operator driving scripted checks) without reading the rest of
the document, provided the Global Conventions (§2) are prepended.

---

## 0. Mission & Context

You are the **Verification Loop Agent** for the FiberHome FTTH CAD-to-GIS
pipeline. Upstream, a converter transforms engineering CAD drawings (DWG) into a
single GeoPackage (GPKG) containing eight FTTH feature layers plus metadata
tables. Your mission is to decide whether that GeoPackage is **acceptable as an
engineering deliverable** according to the company acceptance standard.

Core principles:

1. **Read-only.** You never modify the GeoPackage. You emit reports and verdicts.
2. **Evidence-first.** Every violation you report must carry the layer, feature
   identifier(s), the rule id, and the observed vs. expected values.
3. **No vacuous passes.** A check whose input set is empty (e.g., a referential
   check where all reference fields are NULL) must NOT report PASS. It must
   report FAIL or QUARANTINE with an explicit "empty evidence set" reason. This
   guards against the historical "hollow pass" defect where empty attributes
   caused isolation checks to succeed trivially.
4. **Three-state verdict.** Every finding resolves to PASS, FAIL, or QUARANTINE
   (human review required). See §4 Verdict Protocol.
5. **Nothing is hardcoded that the project can configure.** In particular, the
   expected CRS is an input parameter (default `EPSG:3857`); the CRS check is
   fundamentally a *consistency* check (project vs. layers), never a check
   against a fixed world-coordinate system.

The eight layers under acceptance:

| Layer | Meaning (company gloss) | Expected geometry |
|---|---|---|
| IMB | Survey location table — buildings / home points | Point |
| SITE | SRO / PM technical sites (optical distribution cabinets) | Point |
| BOITE | Optical boxes (BPE splice closures, PBO terminal boxes, BPI) | Point |
| CABLE | Optical cables | Line |
| PTECH | Technical points — poles and chambers | Point |
| INFRASTRUCTURE | Ducts / conduits | Line |
| ZNRO | NRO (OLT) coverage zone | Polygon |
| ZPM | PM (SRO) coverage zone — "ZASRO" | Polygon |

---

## 1. Inputs & Configuration

Every rule-group prompt receives this configuration block. All parameters are
overridable at invocation time; defaults shown.

```yaml
inputs:
  gpkg_path: <absolute path to the GeoPackage under test>   # required
  expected_crs: "EPSG:3857"        # configurable; the project's declared target CRS.
                                   # NEVER hardcode a world CRS inside a check —
                                   # always read this parameter.
  layer_suffixes:                  # naming keywords; a layer is identified by
    [IMB, SITE, BOITE, CABLE, PTECH, INFRASTRUCTURE, ZNRO, ZPM]   # suffix match
  quarantine_table: "quarantine_review"   # metadata table written by the converter
                                          # (over-tolerance topology, missing source
                                          # layers, other pre-flagged review items)
  tolerances:
    endpoint_tolerance: 0.05       # max distance (in units of the project CRS,
                                   # metres for metric CRS) for a box/site point
                                   # to count as coincident with a cable endpoint
    containment_tolerance: 0.01    # boundary buffer for within-polygon checks,
                                   # absorbs on-the-boundary float noise
    overlap_area_epsilon: 0.001    # polygon pairs whose intersection area is
                                   # below this are treated as shared-edge/touch,
                                   # not as an overlap violation
  report_path: <where to write verification_report.json>    # required
```

Optional context (consume if present, do not require):

- Metadata table `transform_record` in the GPKG — the coordinate transformation
  chain actually applied by the converter. Use it as supporting evidence in
  RG-2 findings (e.g., to explain a CRS mismatch), never as a substitute for
  reading the live layer CRS definitions.
- The converter's per-layer drop/filter counters, if exported — useful context
  for RG-3 (empty layer) root-cause narration.

---

## 2. Global Conventions (prepend to every rule-group prompt)

### 2.1 String and domain-value comparison — ALWAYS UPPERCASE

All comparisons of domain/enumeration values (`TYPE = 'PM'`, `TYPE = 'PBO'`,
`TYPE_CABLE = 'DISTRIBUTION'`, `STATUT` values, …) and all referential code
joins (`CABLE.ORIGINE = BOITE.CODE`, `BOITE.REF_PM = SITE.CODE`, …) MUST be
performed on **uppercased, whitespace-trimmed** values on both sides. This is a
confirmed correction from previous verification rounds: source data mixes
letter cases; case-sensitive comparison produces false negatives.

### 2.2 Field-name resolution — 10-character truncation (Shapefile legacy)

The company standard was authored against Shapefiles, whose DBF format truncates
attribute names to 10 characters. The GeoPackage under test may carry either the
full field name or its 10-character truncation. Resolve every field name in two
steps:

1. Look for the **exact full name** (case-insensitive) in the layer schema.
2. If absent and the standard name is longer than 10 characters, look for the
   **first 10 characters** of the name (case-insensitive).
3. If both lookups fail → `MISSING_FIELD` violation.

Example: standard name `NB_FIBRE_UTIL` → try `NB_FIBRE_UTIL`, then
`NB_FIBRE_U`. The full truncation map is in Appendix B.

### 2.3 Known corrections (authoritative, discovered in prior rounds)

These OVERRIDE the literal text of VERIFICATION_RULE.csv:

| # | Correction | Where it applies |
|---|---|---|
| K1 | The ZPM layer's PM-reference field is **`REF_SRO`**, not `REF_PM`. Rule row 4.11 lists `REF_PM`, but that field does not exist on ZPM; the schema (ZPM.csv, "OBJET: ZONE SRO") defines `REF_SRO` = PM name. Validate `REF_SRO`. | RG-4 (ZPM field list), RG-5/RG-6 wherever a PM reference on ZPM is needed |
| K2 | **`IMB.CODE_VOIE` does not exist** in the layer, although rule row 4.1 lists it. Exclude it from existence and non-null checks. Do NOT report it as `MISSING_FIELD`. | RG-4 (IMB field list) |
| K3 | All domain-value and code-join comparisons are **uppercase-normalized** (see §2.1). | All rule groups |
| K4 | Rule rows 4.5 / 4.7 / 4.9 contain a duplicated `CODE` entry (`"CODE,CODE,…"`). This is a source-document artifact: treat it as a single `CODE`. (The object schemas define a separate `NOM` field at that position; `NOM` is conditional and is NOT enforced non-null.) | RG-4 (CABLE, PTECH, INFRASTRUCTURE lists) |
| K5 | `ADRESSSE` (triple S) is the literal field name in the company schemas. Do not "fix" the spelling when matching. | RG-4 |

### 2.4 Violation record schema

Every violation you emit, in any rule group, uses this JSON shape:

```json
{
  "rule_id": "RG6-6.3",
  "source_rule": "6.3",
  "layer": "SITE",
  "fid": 42,
  "feature_code": "TNG01-BOK01",
  "severity": "FAIL",
  "message": "SITE (TYPE=PM) point lies outside its matching ZPM polygon",
  "observed": {"distance_outside": 12.4},
  "expected": "point within polygon (containment_tolerance=0.01)",
  "pre_quarantined": false
}
```

- `severity` ∈ `FAIL` | `QUARANTINE` | `WARN`.
- `pre_quarantined: true` marks features that already appear in the
  `quarantine_review` table for the same defect class — report them, but they
  contribute to the QUARANTINE verdict, not to FAIL (see §4.3).

### 2.5 Anti-vacuous-pass guard (mandatory in RG-5, RG-6, RG-7)

Before evaluating any referential or geometric rule, count the evidence set
(e.g., number of DISTRIBUTION cables with non-empty `ORIGINE`). If the evidence
set is empty while the population is non-empty (cables exist but all `ORIGINE`
are NULL), emit ONE aggregate violation with
`message: "empty evidence set — check cannot pass vacuously"`, severity FAIL,
and skip the per-feature loop. If the population itself is legitimately empty
and RG-3 already routed it to QUARANTINE, mark the check `SKIPPED_QUARANTINED`.

---

## 3. Rule Group Prompts

Each subsection is a standalone agent prompt. Concatenate §2 + the subsection
and execute. Rule ids reference the source rows in VERIFICATION_RULE.csv.

### RG-1 — File / Layer Completeness, Naming & Geometry Type (rules 1.1–1.9)

```
ROLE: Layer Census Agent — FTTH GeoPackage acceptance, Rule Group 1.

PURPOSE: Verify that the deliverable contains ALL EIGHT required feature layers,
that each layer's name follows the suffix naming convention, and that each
layer's geometry type matches the standard.

CONTEXT NOTE: The original company standard targets a "shape/" directory holding
eight Shapefile sets. For the GeoPackage deliverable, the equivalent requirement
is: one GPKG containing eight feature tables, one per layer keyword.

PROCEDURE:
  1. Open {gpkg_path} read-only. Enumerate all feature tables (ignore
     non-spatial metadata tables such as transform_record and
     {quarantine_table}).
  2. For each required keyword in [IMB, SITE, BOITE, CABLE, PTECH,
     INFRASTRUCTURE, ZNRO, ZPM]:
       a. Find candidate tables whose UPPERCASED name ENDS WITH the keyword
          (accept both bare names like "CABLE" and prefixed names like
          "HUTABOHU_CABLE"). Rule rows 1.2–1.9: file/layer names end with the
          layer keyword.
       b. Exactly one match expected. Zero matches → MISSING_LAYER (FAIL).
          Multiple matches → AMBIGUOUS_LAYER_NAME (FAIL, list all candidates).
  3. For each matched layer, read the declared geometry type and verify:
       IMB, SITE, BOITE, PTECH      → Point (accept MultiPoint)         [1.2,1.3,1.4,1.6]
       CABLE, INFRASTRUCTURE        → LineString (accept MultiLineString) [1.5,1.7]
       ZNRO, ZPM                    → Polygon (accept MultiPolygon)     [1.8,1.9]
     Mismatch → GEOMETRY_TYPE_MISMATCH (FAIL) with observed vs expected type.
  4. Rule 1.1 verdict: all 8 keywords resolved to exactly one table each,
     with correct geometry types.

VIOLATION OUTPUT: one record per problem, rule_id RG1-<row>, layer = the
keyword, feature_code = null (layer-level), severity FAIL.

VERDICT CONTRIBUTION: any MISSING_LAYER / AMBIGUOUS_LAYER_NAME /
GEOMETRY_TYPE_MISMATCH → rule group FAIL. Downstream rule groups still run for
the layers that DO exist (partial verification is more useful than an early
abort), but the overall verdict is already FAIL.

EXEMPTION HOOK: if a layer is absent AND {quarantine_table} contains an entry
declaring that the source drawing genuinely lacks that layer (known case: ZNRO
for the Hutabohu drawing), downgrade that single MISSING_LAYER to severity
QUARANTINE with pre_quarantined=true. All other absences remain FAIL.
```

### RG-2 — CRS Consistency (rule 2)

```
ROLE: CRS Consistency Agent — FTTH GeoPackage acceptance, Rule Group 2.

PURPOSE: Verify that the project and every checked layer agree on ONE coordinate
reference system, and that this system equals the configured expected CRS.

DESIGN RULE (non-negotiable): this check is a CONSISTENCY check. The expected
CRS arrives as the input parameter {expected_crs} (default "EPSG:3857").
Do NOT hardcode any specific world CRS inside the check logic. If the project
is delivered in a different legitimate CRS tomorrow, only the parameter
changes — not this prompt.

PROCEDURE:
  1. Open {gpkg_path} read-only. For each of the 8 feature layers found by
     RG-1, read the layer's declared CRS (its SRS definition / EPSG code).
  2. Pairwise consistency: all layers must share the SAME CRS. Any layer
     whose CRS differs from the majority → CRS_INCONSISTENT (FAIL), report
     per-layer observed codes.
  3. Expected-CRS conformance: the shared CRS must equal {expected_crs}
     (compare by authority code after normalization, e.g. "EPSG:3857" ==
     epsg id 3857; fall back to WKT equivalence if no authority code).
     Mismatch → CRS_UNEXPECTED (FAIL) with observed vs {expected_crs}.
  4. Undefined/blank CRS on any layer → CRS_UNDEFINED (FAIL) for that layer.
  5. Plausibility probe (supporting evidence, WARN only — never the primary
     signal): sample up to 100 feature centroids per layer and confirm the
     coordinate magnitudes are plausible for {expected_crs} (e.g., metric
     web-mercator coordinates are typically |x|,|y| in the 1e4..2e7 range;
     degree-like values in a metric CRS suggest a silent unit error).
     Implausible magnitudes with a formally matching CRS tag →
     CRS_PLAUSIBILITY_WARN (QUARANTINE, human review).
  6. If the metadata table transform_record exists, read it and attach the
     recorded transformation chain to your report as evidence. If the record
     claims "identity" while step 3 or 5 found anomalies, say so explicitly —
     the converter historically wrote identity records unconditionally.

VIOLATION OUTPUT: rule_id RG2-2, layer-level records, severity as above.

VERDICT CONTRIBUTION: CRS_INCONSISTENT / CRS_UNEXPECTED / CRS_UNDEFINED → FAIL.
CRS_PLAUSIBILITY_WARN alone → QUARANTINE.
```

### RG-3 — No Empty Layers (rule 3)

```
ROLE: Layer Population Agent — FTTH GeoPackage acceptance, Rule Group 3.

PURPOSE: Every one of the 8 layers must contain AT LEAST ONE feature.

PROCEDURE:
  1. For each of the 8 layers found by RG-1, count features.
  2. count >= 1 → pass for that layer.
  3. count == 0 → decide the route:
       a. Consult {quarantine_table}. If it contains an entry stating the
          SOURCE DRAWING lacks the corresponding objects (known, expected
          case: ZNRO — the Hutabohu source drawing contains no NRO-zone
          geometry at all), emit EMPTY_LAYER_SOURCE_MISSING with severity
          QUARANTINE and pre_quarantined=true. The deliverable is then
          conditionally acceptable pending human confirmation that the
          absence is a source-data reality, not a conversion loss.
       b. Otherwise emit EMPTY_LAYER with severity FAIL. An empty PTECH or
          SITE layer, for example, historically indicated classification
          misses in the converter, not missing source data — that is a
          pipeline defect, not an acceptable state.
  4. In both routes, if converter drop/filter counters are available, attach
     the per-layer drop counts as evidence (they distinguish "never
     extracted" from "extracted then filtered out").

VIOLATION OUTPUT: rule_id RG3-3, layer-level records.

VERDICT CONTRIBUTION: any EMPTY_LAYER → FAIL. Only
EMPTY_LAYER_SOURCE_MISSING entries → QUARANTINE.

CALIBRATION HOOK: for every EMPTY_LAYER, also emit a
CALIBRATION_RECOMMENDATION (see §4.4) naming the suspected upstream stage
(usually layer→feature-class mapping) and the CAD layer names that should have
fed the empty GIS layer.
```

### RG-4 — Field Existence, Non-Null Values & CODE Uniqueness (rules 4.1–4.16)

```
ROLE: Schema & Attribute Agent — FTTH GeoPackage acceptance, Rule Group 4.

PURPOSE: For each of the 8 layers verify that (a) every mandatory field EXISTS
(after 10-character truncation resolution, §2.2), (b) every mandatory field is
NON-NULL and non-empty-string on every feature, and (c) the CODE value is
UNIQUE within the layer.

MANDATORY FIELD LISTS (authoritative, corrections K1/K2/K4 applied):

  IMB   [4.1]  (list embedded in VERIFICATION_RULE.csv row 4.1; CODE_VOIE
               removed per K2 — it does not exist in the layer):
        CODE, REF_PLAQUE, REGION, PROVINCE, VILLE, COMMUNE, CODE_POSTAL,
        NUMERO_VOIE, TYPE_VOIE, TYPE_BATIMENT, TYPE_CLIENT, NB_LOC_RES,
        NB_LOC_PRO, NB_LOC_TOT, RACCORDEMENT, STATUT, NB_ETAGE,
        COL_MONTANTE, SOUS_SOL, SOUS_SOL_COMMUN, BPE_CODE, X, Y

  BOITE [4.3]:
        CODE, CODE_PTC, REF_PLAQUE, REF_NRO, REF_PM, TYPE, TYPE_STRUCTURE,
        MODE_POSE, CAPACITE, NB_LOGEMENT, NB_SPLICES, NB_FIBRE_UTIL,
        FABRIQUANT, REF_BPE, NB_CASSETTES_MAX, CABLE_AMONT, STATUT,
        PROPRIETAIRE, GESTIONNAIRE, ADRESSSE, VILLE, CODE_POSTAL, X, Y

  CABLE [4.5] (duplicate CODE deduplicated per K4):
        CODE, REF_PLAQUE, REF_NRO, REF_PM, CODE_INFRA, ORIGINE, EXTREMITE,
        TYPE_CABLE, DIAMETRE, MODE_POSE, CAPACITE, MODULO, FABRIQUANT,
        REF_PRODUIT, TYPE_FIBRE, NB_FIBRE_UTIL, NB_FIBRE_DISP, STATUT,
        PROPRIETAIRE, GESTIONNAIRE, TYPE_PROP, LONGUEUR

  PTECH [4.7] (duplicate CODE deduplicated per K4):
        CODE, REF_PLAQUE, TYPE, NATURE, HAUTEUR_APPUI, TYPE_APPUI,
        EFFORT_APPUI, NB_BOITIERS, STATUT, PROPRIETAIRE, GESTIONNAIRE,
        ADRESSSE, VILLE, CODE_POSTAL, X, Y

  INFRASTRUCTURE [4.9] (duplicate CODE deduplicated per K4):
        CODE, REF_PLAQUE, ORIGINE, EXTREMITE, COMPOSITION, TYPE, TYPE_LOG,
        STATUT, PROPRIETAIRE, GESTIONNAIRE, LONGUEUR

  ZPM   [4.11] (REF_PM corrected to REF_SRO per K1):
        CODE, REF_PLAQUE, REF_NRO, REF_SRO, STATUT, NB_PRISES

  ZNRO  [4.13]:
        CODE, REF_PLAQUE, REF_NRO, STATUT, NB_PRISES

  SITE  [4.15]:
        CODE, REF_PLAQUE, REF_NRO, TYPE, FABRIQUANT, REF_PRODUIT, MODE_POSE,
        STATUT, PROPRIETAIRE, GESTIONNAIRE, ADRESSSE, COMMUNE, CODE_POSTAL,
        X, Y

PROCEDURE (run per layer):
  1. FIELD EXISTENCE. For each mandatory field name, resolve it against the
     layer schema using §2.2 (exact match, then 10-char truncation). Record
     which physical name matched. Unresolvable → MISSING_FIELD (FAIL),
     one record per field.
  2. NON-NULL SCAN. For every feature, for every resolved mandatory field:
     value must not be NULL and, for text fields, not '' after trimming.
     Violation → NULL_MANDATORY_VALUE (FAIL) with fid, feature CODE, field.
     Aggregate: if a field is null on more than 20 features, you may emit one
     aggregated record carrying the count plus the first 20 fids as samples.
  3. CONDITIONAL-FIELD RELAXATION (PTECH and INFRASTRUCTURE only). The object
     schemas mark some fields conditional:
       PTECH.HAUTEUR_APPUI / TYPE_APPUI / EFFORT_APPUI apply when
       TYPE = POTEAU (uppercase comparison); for non-pole features the schema
       prescribes 0 / empty.
       INFRASTRUCTURE.COMPOSITION applies when the feature is a conduit.
     For features where the condition does NOT hold, downgrade a null in
     these specific fields from FAIL to QUARANTINE (human confirms schema
     intent). Everywhere else the strict rule applies.
  4. CODE UNIQUENESS [rows 4.2, 4.4, 4.6, 4.8, 4.10, 4.12, 4.14, 4.16].
     Within the layer, group features by UPPERCASED trimmed CODE. Any group
     with size > 1 → DUPLICATE_CODE (FAIL) listing all fids sharing the code.
     NULL codes were already caught by step 2 — do not double-report.

VIOLATION OUTPUT: rule_id RG4-<row>, per-field or per-feature records as
above.

VERDICT CONTRIBUTION: any MISSING_FIELD, NULL_MANDATORY_VALUE, or
DUPLICATE_CODE at severity FAIL → rule group FAIL. Only conditional-field
QUARANTINE records → QUARANTINE.
```

### RG-5 — Isolation / Referential Integrity (rules 5.1–5.4)

```
ROLE: Referential Integrity Agent — FTTH GeoPackage acceptance, Rule Group 5.

PURPOSE: Verify bidirectional correspondence between layers that reference each
other by CODE. These checks are PRECONDITIONS for the geometry checks 6.3–6.6:
a broken reference here makes the corresponding geometry check undecidable.

NORMALIZATION: all joins on UPPERCASED, trimmed values (§2.1). Apply the
anti-vacuous-pass guard (§2.5) to every sub-rule.

SUB-RULES:

  5.1  SITE(PM) <-> ZPM, bidirectional. [precondition of 6.3]
       Forward:  for every SITE feature with TYPE = PM, there must exist a
                 ZPM feature with ZPM.CODE = SITE.CODE.
       Reverse:  for every ZPM feature, there must exist a SITE feature with
                 TYPE = PM and SITE.CODE = ZPM.CODE.
       Note (K1): ZPM also carries REF_SRO (the PM name; in this dataset it
       normally equals ZPM.CODE). If forward matching by ZPM.CODE fails but
       ZPM.REF_SRO matches, report REFERENCE_VIA_ALTERNATE_KEY with severity
       QUARANTINE instead of a hard FAIL — the pairing exists but the key
       discipline is off.

  5.2  SITE(PM) <-> BOITE(PBO), master-detail bidirectional. [precondition of 6.4]
       Forward:  every BOITE with TYPE = PBO must have REF_PM equal to some
                 SITE.CODE (SITE.TYPE = PM).
       Reverse:  every SITE with TYPE = PM must be referenced by at least one
                 BOITE(PBO).REF_PM.

  5.3  SITE(PM) <-> CABLE(DISTRIBUTION), master-detail bidirectional.
       [precondition of 6.5]
       Forward:  every CABLE with TYPE_CABLE = DISTRIBUTION must have REF_PM
                 equal to some SITE.CODE (SITE.TYPE = PM).
       Reverse:  every SITE with TYPE = PM must be referenced by at least one
                 CABLE(DISTRIBUTION).REF_PM.

  5.4  CABLE endpoints <-> node layers, bidirectional. [precondition of 6.6]
       (a) Forward: for every CABLE with TYPE_CABLE = DISTRIBUTION, each of
           ORIGINE and EXTREMITE must match the CODE of a BOITE feature whose
           TYPE is BPE or PBO. If no BOITE matches, fall back to SITE features
           with TYPE = PM (a cable may legitimately start at the PM cabinet).
           No match in either layer → DANGLING_ENDPOINT_REF (FAIL).
       (b) Reverse-1: every SITE(PM).CODE must appear AT LEAST ONCE across the
           ORIGINE/EXTREMITE values of DISTRIBUTION cables.
           Unreferenced PM → UNCONNECTED_PM (FAIL).
       (c) Reverse-2: every BOITE feature with TYPE in {BPE, PBO} — and every
           SITE(PM) — must have its CODE appear at least once in the
           ORIGINE/EXTREMITE values of DISTRIBUTION cables.
           Unreferenced box → UNCONNECTED_NODE (FAIL).

QUARANTINE INTERPLAY: features listed in {quarantine_table} for topology
reasons (e.g., endpoint assigned by nearest-node fallback beyond snap
tolerance) still participate in these checks; if they violate, mark the
violation pre_quarantined=true so it contributes to QUARANTINE, not FAIL —
the converter already flagged them for human review.

VIOLATION OUTPUT: rule_id RG5-<subrule> (e.g., RG5-5.4b), with the referencing
layer/fid/code, the referenced key value, and the layers searched.

VERDICT CONTRIBUTION: any non-pre-quarantined FAIL record → rule group FAIL.
An empty evidence set on any sub-rule (all reference fields NULL while the
population is non-empty) → FAIL per §2.5 — never a silent pass.
```

### RG-6 — Geometry Checks (rules 6.1–6.6)

```
ROLE: Geometry Auditor Agent — FTTH GeoPackage acceptance, Rule Group 6.

PURPOSE: Verify spatial invariants: zone polygons do not overlap, points and
cables lie inside their owning PM zone, and cable endpoints coincide with the
optical boxes they claim to connect.

PREREQS: run RG-5 first. Where an RG-5 precondition failed for a feature, mark
the dependent RG-6 check for that feature UNDECIDABLE_PRECONDITION_FAILED
(severity QUARANTINE) rather than guessing. All comparisons uppercase (§2.1);
distances in units of the project CRS.

SUB-RULES:

  6.1  ZNRO internal non-overlap.
       For every pair of ZNRO polygons: interiors must be disjoint. Shared
       edges and shared vertices (touching) are ALLOWED. Compute pairwise
       intersection; if the intersection has positive area greater than
       {overlap_area_epsilon} → ZONE_OVERLAP (FAIL) with both codes and the
       overlap area. Use a spatial index; only test bbox-intersecting pairs.

  6.2  ZPM internal non-overlap.
       Identical procedure and semantics on the ZPM layer.

  6.3  SITE(PM) within its ZPM. [depends on 5.1]
       For every SITE with TYPE = PM: find the ZPM with ZPM.CODE = SITE.CODE
       (per 5.1; REF_SRO alternate key per K1 if applicable). The SITE point
       must lie within that polygon, tolerating {containment_tolerance}
       (i.e., point.distance(polygon) <= tolerance counts as inside for
       boundary cases). Outside → PM_OUTSIDE_ZONE (FAIL) with the distance.

  6.4  BOITE(PBO) within its owning ZPM. [depends on 5.2]
       Ownership chain: BOITE.REF_PM = SITE.CODE = ZPM.CODE.
       For every BOITE with TYPE = PBO: resolve its ZPM via the chain; the
       point must lie within the polygon ({containment_tolerance} as in 6.3).
       Outside → PBO_OUTSIDE_ZONE (FAIL).

  6.5  CABLE(DISTRIBUTION) fully within its owning ZPM. [depends on 5.3]
       Ownership chain: CABLE.REF_PM = SITE.CODE = ZPM.CODE.
       For every CABLE with TYPE_CABLE = DISTRIBUTION: EVERY vertex of the
       polyline (endpoints AND intermediate bend points) must lie within the
       owning ZPM polygon ({containment_tolerance}). Any vertex outside →
       CABLE_ESCAPES_ZONE (FAIL) with the vertex index and distance.

  6.6  Box-on-endpoint coincidence (connectivity geometry). [depends on 5.4]
       For every CABLE with TYPE_CABLE = DISTRIBUTION:
       (a) ORIGINE and EXTREMITE values must differ (self-loop forbidden).
           Equal → SELF_LOOP_CABLE (FAIL).
       (b) Resolve ORIGINE and EXTREMITE to node features (BOITE BPE/PBO,
           fallback SITE PM — same resolution as 5.4a). Each resolved node's
           point must coincide with ONE of the two endpoints of the cable
           polyline within {endpoint_tolerance}. The check is
           DIRECTION-AGNOSTIC: ORIGINE may sit on either the first or the
           last vertex (drawing direction is not normative), as long as
           ORIGINE and EXTREMITE occupy DIFFERENT endpoints.
           Node farther than {endpoint_tolerance} from both endpoints →
           NODE_OFF_ENDPOINT (FAIL) with the measured distances.

QUARANTINE INTERPLAY: cables/nodes flagged in {quarantine_table} as
over-tolerance (attribute-only assignment without geometric snapping) will
typically trip 6.6b. Mark those violations pre_quarantined=true — they roll up
to QUARANTINE, not FAIL. Newly discovered offenders (not in the table) remain
FAIL.

VIOLATION OUTPUT: rule_id RG6-<subrule>, with feature codes, fids, measured
distances/areas, and tolerance values used.

VERDICT CONTRIBUTION: non-pre-quarantined FAIL records → rule group FAIL.
UNDECIDABLE_PRECONDITION_FAILED and pre-quarantined records → QUARANTINE.
```

### RG-7 — Capacity / Port Balance (rules 7.1–7.2)

```
ROLE: Capacity Auditor Agent — FTTH GeoPackage acceptance, Rule Group 7.

PURPOSE: Verify port-count arithmetic: no optical terminal box is oversubscribed
and no PM's downstream box capacity exceeds the fibre count actually leaving
the PM.

NUMERIC PARSING: CAPACITE and fibre-count fields may be stored as text or carry
non-numeric decoration. Extract the NUMERIC PART (leading integer after
stripping non-digit prefixes/suffixes, e.g. "144" from "144P"). Unparseable
value → UNPARSEABLE_CAPACITY (QUARANTINE) for that feature; exclude it from
sums but report it. Apply the anti-vacuous-pass guard (§2.5).

SUB-RULES:

  7.1  PBO oversubscription.
       For every BOITE with TYPE = PBO:
         NB_FIBRE_UTIL (designed homes covered by the box)
           must be <= numeric part of CAPACITE (max usable ports of the box).
       Violation → PBO_OVERSUBSCRIBED (FAIL) with both numbers.
       (Field-name note: NB_FIBRE_UTIL resolves to NB_FIBRE_U in truncated
       schemas — use §2.2 resolution, never assume either spelling.)

  7.2  PM-level port balance.
       For every SITE with TYPE = PM:
         (1) sum_pbo = SUM of numeric CAPACITE over all BOITE features with
             TYPE = PBO and REF_PM = SITE.CODE
             (the PBO population of this PM's coverage).
         (2) sum_cable = SUM of numeric CAPACITE over all CABLE features with
             ORIGINE = SITE.CODE AND TYPE_CABLE = DISTRIBUTION
             (fibre strands leaving the PM).
         (3) REQUIRE sum_pbo <= sum_cable.
       Violation → PM_PORT_IMBALANCE (FAIL) with sum_pbo, sum_cable, and the
       contributing feature codes on both sides.
       If either population is empty for a PM, do not treat the comparison as
       trivially satisfied: cross-check against RG-5 results (an empty PBO or
       cable set for a PM already violates 5.2/5.3) and mark this PM
       UNDECIDABLE_PRECONDITION_FAILED (QUARANTINE) here.

VIOLATION OUTPUT: rule_id RG7-<subrule>, per-feature (7.1) or per-PM (7.2)
records with the full arithmetic shown.

VERDICT CONTRIBUTION: PBO_OVERSUBSCRIBED / PM_PORT_IMBALANCE → FAIL.
UNPARSEABLE_CAPACITY / UNDECIDABLE_PRECONDITION_FAILED → QUARANTINE.
```

---

## 4. Verdict Protocol

### 4.1 Three-state semantics

| Verdict | Meaning | Exit code |
|---|---|---|
| **PASS** | Every rule group passed. No FAIL-severity violations anywhere; no open QUARANTINE items (pre-acknowledged, human-signed-off exemptions recorded in `quarantine_review` with a resolution mark do not block PASS). | **0** |
| **FAIL** | At least one FAIL-severity violation (not pre-quarantined) in any rule group. The deliverable is rejected; the report is the rework order. | **1** |
| **QUARANTINE** | No hard FAILs, but at least one item requires human review: source-missing layers (RG-3 ZNRO path), over-tolerance topology features, alternate-key references, conditional-field ambiguities, unparseable capacities, plausibility warnings. The deliverable is conditionally acceptable pending review. | **2** |

Precedence: **FAIL > QUARANTINE > PASS.** A single non-pre-quarantined FAIL
record makes the run exit 1 regardless of how many quarantine items exist.

### 4.2 Final report

Write `{report_path}` (`verification_report.json`):

```json
{
  "gpkg_path": "...",
  "expected_crs": "EPSG:3857",
  "tolerances": { "endpoint_tolerance": 0.05, "containment_tolerance": 0.01,
                  "overlap_area_epsilon": 0.001 },
  "rule_groups": {
    "RG1": {"status": "PASS|FAIL|QUARANTINE", "violations": 0},
    "RG2": {"status": "...", "violations": 0},
    "RG3": {"status": "...", "violations": 1},
    "RG4": {"status": "...", "violations": 0},
    "RG5": {"status": "...", "violations": 3},
    "RG6": {"status": "...", "violations": 2},
    "RG7": {"status": "...", "violations": 0}
  },
  "violations": [ /* all records, §2.4 schema */ ],
  "quarantine_items": [ /* subset with severity QUARANTINE or pre_quarantined */ ],
  "calibration_recommendations": [ /* §4.4 records */ ],
  "verdict": "PASS|FAIL|QUARANTINE",
  "exit_code": 0
}
```

### 4.3 Consuming the `quarantine_review` table

The converter writes a `quarantine_review` metadata table into the GPKG for
items it already knows need human eyes (typical columns: `layer`, `fid`,
`feature_code`, `reason`, `source_stage`, `details`). The verifier's contract
with that table:

1. **Read it first.** Build an index of (layer, fid/code, defect class).
2. **Do not double-punish.** When a rule-group check trips on a feature that
   the table already flags for the same defect class, set
   `pre_quarantined=true` on the violation. It then contributes to the
   QUARANTINE verdict, not FAIL (§4.1). The converter did its job by flagging;
   the human review is the pending action.
3. **Do fail on novelty.** Violations NOT covered by the table remain FAIL —
   the converter missed them, which is exactly what this loop must surface.
4. **Echo everything.** Copy all table rows into `quarantine_items` in the
   final report (marked `origin: "converter"`), alongside verifier-discovered
   quarantine records (`origin: "verifier"`), so the human reviewer has ONE
   consolidated review list.
5. **Never write to the table.** The verifier is read-only; its output is the
   report file.

### 4.4 CALIBRATION_RECOMMENDATION output (descriptive feedback, no auto-loop)

When violations reveal a systematic upstream cause (not a one-off data typo),
emit a calibration recommendation. This is descriptive advice for the pipeline
operator; nothing is fed back automatically.

```json
{
  "event": "CALIBRATION_RECOMMENDATION",
  "rule_id": "RG3-3",
  "observation": "PTECH is empty while the source drawing contains 216 pole
                  block references on NEW POLE / EXISTING POLE layers",
  "suspected_stage": "classification | topology | crs | schema_mapping | extraction",
  "recommended_action": "extend the CAD-layer-to-feature-class mapping so that
                         NEW POLE 7-*/EXISTING POLE/POLE * route to PTECH",
  "evidence_samples": ["<fids, codes, CAD layer names, coordinates>"],
  "confidence": "high | medium | low"
}
```

Guidelines: one recommendation per root cause, not per violation; name the
suspected stage; quote concrete evidence; state the action in terms of a
parameter, mapping entry, or tolerance — something an operator can change.

---

## Appendix A — Full Layer Field Tables

Legend for the Req column (from the company schemas): **O** = mandatory,
**C** = conditional, **N** = optional. The RG-4 mandatory lists in §3 are the
authoritative acceptance lists; the tables below are the complete schemas for
reference, including fields (like `COMMENT`, `NOM`) that acceptance does not
enforce. Descriptions are translated from the company French/Chinese sources.

### A.1 IMB (survey building/home points)

No standalone schema CSV exists for IMB; the field list comes from
VERIFICATION_RULE.csv row 4.1. Types/lengths were not specified there.

| Field | Description | Note |
|---|---|---|
| CODE | Building/home point code | unique within layer |
| REF_PLAQUE | Parent plaque code | |
| REGION | Region | |
| PROVINCE | Province | |
| VILLE | City | |
| COMMUNE | Commune | |
| CODE_POSTAL | Postal code | truncates to CODE_POSTA |
| NUMERO_VOIE | Street number | truncates to NUMERO_VOI |
| TYPE_VOIE | Street type | |
| CODE_VOIE | Street code | **does not exist in the layer (K2) — excluded from checks** |
| TYPE_BATIMENT | Building type | truncates to TYPE_BATIM |
| TYPE_CLIENT | Client type | truncates to TYPE_CLIEN |
| NB_LOC_RES | Number of residential units | exactly 10 chars, no truncation |
| NB_LOC_PRO | Number of professional units | exactly 10 chars |
| NB_LOC_TOT | Total units | exactly 10 chars |
| RACCORDEMENT | Connection mode/state | truncates to RACCORDEME |
| STATUT | Deployment status | |
| NB_ETAGE | Number of floors | |
| COL_MONTANTE | Riser column | truncates to COL_MONTAN |
| SOUS_SOL | Basement | |
| SOUS_SOL_COMMUN | Shared basement | truncates to SOUS_SOL_C |
| BPE_CODE | Serving box code | |
| X | X coordinate attribute | |
| Y | Y coordinate attribute | |

### A.2 SITE (technical site — SRO/PM) — from SITE.csv, "OBJET: SITE TECHNIQUE"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Technical site code (e.g. TNG01-BOK01) | Text 20 | O |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| REF_NRO | Parent NRO code | Text 50 | O |
| TYPE | Site type (list l_site_type, e.g. PM) | Text 50 | O |
| FABRIQUANT | Manufacturer reference | Text 50 | O |
| REF_PRODUIT | Product reference (e.g. ADR_28U) | Text 50 | O |
| MODE_POSE | Site installation mode (list l_site_type_phy) | Text 50 | O |
| STATUT | Deployment status (list l_statut) | Text 30 | O |
| PROPRIETAIRE | Owner | Text 50 | O |
| GESTIONNAIRE | Manager | Text 50 | O |
| ADRESSSE | Nearby address (spelling per K5) | Text 50 | C |
| COMMUNE | Commune | Text 50 | O |
| CODE_POSTAL | Postal code | Int 5 | C |
| X | Geographic X attribute (auto-computed per source spec) | Double | O |
| Y | Geographic Y attribute (auto-computed per source spec) | Double | O |
| COMMENT | Comments | Text 50 | C |

### A.3 BOITE (optical box) — from BOITE.csv, "OBJET: BOITIER"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Optical box code (PBO-/BPE-/BPI- prefixed) | Text 30 | O |
| CODE_PTC | Code of the hosting technical point | Text 30 | O |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| REF_NRO | Parent NRO code | Text 50 | N (schema) — enforced by rule 4.3 |
| REF_PM | Parent PM code | Text 50 | O |
| TYPE | Box type: BPE, BPI, PBO (list l_bpe_type) | Text | O |
| TYPE_STRUCTURE | Network tier: Transport / Distribution | Text | (blank in schema) — enforced by rule 4.3 |
| MODE_POSE | Installation mode: Façade, Chambre, Aerien (list l_bpe_mode_pose) | Text 30 | O |
| CAPACITE | Total splice capacity of the box | Int 3 | O |
| NB_LOGEMENT | Connectable homes count | Len 10 | (blank) — enforced by rule 4.3 |
| NB_SPLICES | Splice count | | (blank) — enforced by rule 4.3 |
| NB_FIBRE_UTIL | Total fibres (homes + reserves) | Int 3 | O |
| FABRIQUANT | Manufacturer | Text 50 | O |
| REF_BPE | Product reference | Text 50 | O |
| NB_CASSETTES_MAX | Max cassette capacity | Int 3 | O |
| CABLE_AMONT | Upstream / incoming cable reference | Text 20 | O |
| STATUT | Deployment status (list l_statut) | Text 50 | O |
| PROPRIETAIRE | Owner | Text 50 | O |
| GESTIONNAIRE | Manager | Text 50 | O |
| ADRESSSE | Nearby address (K5) | Text 50 | C |
| VILLE | City | Text 50 | O |
| CODE_POSTAL | Postal code | Int 5 | C |
| X | Geographic X attribute | Double | O |
| Y | Geographic Y attribute | Double | O |
| COMMENT | Comments | Text 50 | C |

### A.4 CABLE — from CABLE.csv, "OBJET: CABLE"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Cable code | Text 30 | O |
| NOM | Field (terrain) name | Text 30 | C |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| REF_NRO | Parent NRO code | Text 50 | O |
| REF_PM | Parent PM code | Text 50 | O |
| CODE_INFRA | Code of the hosting infrastructure | Text 20 | O |
| ORIGINE | Code of the upstream box/site | Text 20 | O |
| EXTREMITE | Code of the downstream box/site | Text 20 | O |
| TYPE_CABLE | Cable type (list l_cable_type, e.g. DISTRIBUTION) | Text 15 | O |
| DIAMETRE | Diameter | Int 2 | O |
| MODE_POSE | Laying mode: SOUTERRAIN, AERIEN, FACADE (list l_mode_pose) | Text 30 | O |
| CAPACITE | Fibre capacity (2, 6, 12, 24, 36, 48, 72, …) | Int 3 | O |
| MODULO | Modularity (2, 6, 12) | Int 2 | O |
| FABRIQUANT | Cable manufacturer | Text 50 | O |
| REF_PRODUIT | Product reference | Text 50 | O |
| TYPE_FIBRE | Fibre type (list l_fibre_type, e.g. G657A2) | Text 10 | (blank) — enforced by rule 4.5 |
| NB_FIBRE_UTIL | Fibres used | Int 3 | O |
| NB_FIBRE_DISP | Fibres available | Int 3 | O |
| STATUT | Deployment status (list l_statut) | Text 50 | O |
| PROPRIETAIRE | Owner | Text 50 | O |
| GESTIONNAIRE | Manager | Text 50 | O |
| TYPE_PROP | Ownership type (list l_type_prop) | Text 30 | (blank) — enforced by rule 4.5 |
| LONGUEUR | Cable length in metres (in the retained projection) | Double 10 | O |
| COMMENT | Comments | Text 50 | C |

### A.5 PTECH (technical point) — from PTECH.csv, "OBJET: POINT TECHNIQUE"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Technical point identifier | Text 30 | O |
| NOM | Name on the ground / in owner databases | Text 50 | O (schema; not in rule 4.7 list) |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| TYPE | Technical point type: POTEAU, CHAMBRE (list l_ptc_type) | Text 30 | O |
| NATURE | Technical point nature: PBOI, L2T, PNS3 (list l_ptc_nature) | Text 30 | O |
| HAUTEUR_APPUI | Pole height in metres | Double 10 | C — if TYPE=POTEAU, else 0 |
| TYPE_APPUI | Pole support type (Simple, Moisé, Haubané, Couple, …) | Double 10 | C — if TYPE=POTEAU, else empty |
| EFFORT_APPUI | Nominal pole effort in daN | Double 10 | O if TYPE=POTEAU, else empty |
| NB_BOITIERS | Number of boxes present at the point (0,1,2) | | (blank) — enforced by rule 4.7 |
| STATUT | Deployment status (list l_statut) | Text 30 | O |
| PROPRIETAIRE | Owner | Text 50 | O |
| GESTIONNAIRE | Manager | Text 50 | O |
| ADRESSSE | Nearby address (K5) | Text 50 | C |
| VILLE | City | Text 50 | O |
| CODE_POSTAL | Postal code | Int 5 | C |
| X | Geographic X attribute | Double | O |
| Y | Geographic Y attribute | Double | O |
| COMMENT | Comments | Text 50 | C |

### A.6 INFRASTRUCTURE — from INFRASTRUCTURE.csv, "OBJET: INFRASTRUCTURE"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Infrastructure code | Text 30 | O |
| NOM | Field name (code if none on the ground) | Text 30 | C |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| ORIGINE | Code of the origin technical point | Text 50 | (blank) — enforced by rule 4.9 |
| EXTREMITE | Code of the end technical point | Text 50 | (blank) — enforced by rule 4.9 |
| COMPOSITION | Duct bundle composition (e.g. "3 PEHD 33/40 \| 6 PVC 60") | Text 50 | C — if conduit |
| TYPE | Infrastructure type: AERIEN, SOUTERRAIN (list l_mode_pose) | Text 50 | O |
| TYPE_LOG | Logical type (list l_type_log, e.g. DISTRIBUTION) | Text 50 | O |
| STATUT | Deployment status (list l_statut) | Text 50 | O |
| PROPRIETAIRE | Owner | Text 50 | O |
| GESTIONNAIRE | Manager | Text 50 | O |
| LONGUEUR | Length in metres (in the retained projection) | Double 10 | O |
| COMMENT | Comments | Text 50 | C |

### A.7 ZNRO (NRO zone) — from ZNRO.csv, "OBJET: ZONE NRO"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Deployment zone code | Text 30 | O |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| REF_NRO | NRO name | Text 30 | O |
| STATUT | Deployment status (list l_statut) | Text 50 | O |
| NB_PRISES | Total number of ports | Int 10 | O |
| COMMENT | Comments | Text 50 | C |

### A.8 ZPM (PM/SRO zone) — from ZPM.csv, "OBJET: ZONE SRO"

| Field | Description | Type/Len | Req |
|---|---|---|---|
| CODE | Deployment zone code | Text 30 | O |
| REF_PLAQUE | Parent plaque code | Text 50 | O |
| REF_NRO | NRO name | Text 30 | O |
| REF_SRO | PM name (**this is the PM-reference field — K1; there is no REF_PM on ZPM**) | Text 30 | O |
| STATUT | Deployment status (list l_statut) | Text 50 | O |
| NB_PRISES | Total number of homes | Int 10 | O |
| COMMENT | Comments | Text 50 | C |

> Note on X/Y attribute columns: the company schemas describe them as
> geographic coordinates (WGS84 in the source documents). For acceptance they
> are validated only for existence and non-null (RG-4). The authoritative CRS
> check for the layer **geometries** is RG-2, driven solely by the
> configurable `expected_crs` parameter.

---

## Appendix B — 10-Character Truncated Field-Name Map (Shapefile legacy)

Rule (§2.2): any standard field name longer than 10 characters may appear in
the data under its first 10 characters. Match the full name first, then the
truncation. Names of exactly 10 characters or fewer never truncate.

| Layer | Full standard name | Truncated (first 10 chars) |
|---|---|---|
| IMB | CODE_POSTAL | CODE_POSTA |
| IMB | NUMERO_VOIE | NUMERO_VOI |
| IMB | TYPE_BATIMENT | TYPE_BATIM |
| IMB | TYPE_CLIENT | TYPE_CLIEN |
| IMB | RACCORDEMENT | RACCORDEME |
| IMB | COL_MONTANTE | COL_MONTAN |
| IMB | SOUS_SOL_COMMUN | SOUS_SOL_C |
| BOITE | TYPE_STRUCTURE | TYPE_STRUC |
| BOITE | NB_LOGEMENT | NB_LOGEMEN |
| BOITE | NB_FIBRE_UTIL | NB_FIBRE_U |
| BOITE | NB_CASSETTES_MAX | NB_CASSETT |
| BOITE | CABLE_AMONT | CABLE_AMON |
| BOITE | PROPRIETAIRE | PROPRIETAI |
| BOITE | GESTIONNAIRE | GESTIONNAI |
| BOITE | CODE_POSTAL | CODE_POSTA |
| CABLE | REF_PRODUIT | REF_PRODUI |
| CABLE | NB_FIBRE_UTIL | NB_FIBRE_U |
| CABLE | NB_FIBRE_DISP | NB_FIBRE_D |
| CABLE | PROPRIETAIRE | PROPRIETAI |
| CABLE | GESTIONNAIRE | GESTIONNAI |
| PTECH | HAUTEUR_APPUI | HAUTEUR_AP |
| PTECH | EFFORT_APPUI | EFFORT_APP |
| PTECH | NB_BOITIERS | NB_BOITIER |
| PTECH | PROPRIETAIRE | PROPRIETAI |
| PTECH | GESTIONNAIRE | GESTIONNAI |
| PTECH | CODE_POSTAL | CODE_POSTA |
| INFRASTRUCTURE | COMPOSITION | COMPOSITIO |
| INFRASTRUCTURE | PROPRIETAIRE | PROPRIETAI |
| INFRASTRUCTURE | GESTIONNAIRE | GESTIONNAI |
| SITE | REF_PRODUIT | REF_PRODUI |
| SITE | PROPRIETAIRE | PROPRIETAI |
| SITE | GESTIONNAIRE | GESTIONNAI |
| SITE | CODE_POSTAL | CODE_POSTA |
| ZNRO | — (no name exceeds 10 characters) | — |
| ZPM | — (no name exceeds 10 characters) | — |

Boundary cases that do NOT truncate (exactly 10 characters): `REF_PLAQUE`,
`NB_SPLICES`, `FABRIQUANT`, `NB_LOC_RES`, `NB_LOC_PRO`, `NB_LOC_TOT`,
`CODE_INFRA`, `TYPE_CABLE`, `TYPE_FIBRE`, `TYPE_APPUI`.

Note: the company source document (row 4.1 remark) enumerates only the IMB
truncations `CODE_POSTAL, NUMERO_VOIE, TYPE_BATIMENT, RACCORDEMENT,
COL_MONTANTE, SOUS_SOL_COMMUN`; the table above additionally derives
`TYPE_CLIENT` (11 chars) and all other layers' >10-char names by applying the
same stated rule ("names over 10 characters: match by the first 10
characters"). No two truncated names collide within any single layer
(`NB_FIBRE_UTIL` → `NB_FIBRE_U` and `NB_FIBRE_DISP` → `NB_FIBRE_D` remain
distinct).

---

## Appendix C — Traceability Matrix (source rule → prompt section)

| Source rule (VERIFICATION_RULE.csv) | Content | Covered by |
|---|---|---|
| 1.1 | All 8 layers present | RG-1 step 2 |
| 1.2–1.9 | Naming suffix + geometry type per layer | RG-1 steps 2–3 |
| 2 | Project/layer CRS consistency | RG-2 (configurable expected CRS) |
| 3 | No empty layers | RG-3 (with ZNRO source-missing QUARANTINE path) |
| 4.1 | IMB mandatory fields (embedded list) | RG-4 (K2 applied) |
| 4.2 / 4.4 / 4.6 / 4.8 / 4.10 / 4.12 / 4.14 / 4.16 | CODE uniqueness per layer | RG-4 step 4 |
| 4.3 | BOITE mandatory fields | RG-4 |
| 4.5 | CABLE mandatory fields | RG-4 (K4 applied) |
| 4.7 | PTECH mandatory fields | RG-4 (K4 + conditional relaxation) |
| 4.9 | INFRASTRUCTURE mandatory fields | RG-4 (K4 + conditional relaxation) |
| 4.11 | ZPM mandatory fields | RG-4 (K1 applied: REF_SRO) |
| 4.13 | ZNRO mandatory fields | RG-4 |
| 4.15 | SITE mandatory fields | RG-4 |
| 5.1 | SITE(PM) ↔ ZPM bidirectional | RG-5 5.1 |
| 5.2 | SITE(PM) ↔ BOITE(PBO) bidirectional | RG-5 5.2 |
| 5.3 | SITE(PM) ↔ CABLE(DISTRIBUTION) bidirectional | RG-5 5.3 |
| 5.4 | Cable endpoints ↔ BOITE/SITE, forward + two reverse checks | RG-5 5.4 a/b/c |
| 6.1 | ZNRO non-overlap (shared edges allowed) | RG-6 6.1 |
| 6.2 | ZPM non-overlap (shared edges allowed) | RG-6 6.2 |
| 6.3 | SITE(PM) within its ZPM | RG-6 6.3 |
| 6.4 | BOITE(PBO) within owning ZPM | RG-6 6.4 |
| 6.5 | CABLE(DISTRIBUTION) vertices within owning ZPM | RG-6 6.5 |
| 6.6 | ORIGINE≠EXTREMITE; boxes coincide with cable endpoints (direction-agnostic) | RG-6 6.6 |
| 7.1 | PBO NB_FIBRE_UTIL ≤ numeric CAPACITE | RG-7 7.1 |
| 7.2 | Σ PBO CAPACITE per PM ≤ Σ outgoing DISTRIBUTION cable CAPACITE | RG-7 7.2 |
