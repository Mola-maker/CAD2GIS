# Deep Dive Trace: complete-validation-geoformer-ftth

## Observed Result
The user needs to produce a working verification implementation in `/official/validation/` that reconciles three data sources: (1) the GeoFormer_FiberHome_P2_AgentPrompts.md 9-agent pipeline spec (Agent 8 Quality Sentinel), (2) the FTTH_GIS_Technical_Standards.md 7-rule-group verification spec, and (3) the 8 original CSV domain specification files in `/official/evaluation_standards/`. The `/official/validation/` directory is currently empty. The existing `plugincad2gis/src/cad2gis/verify/` subpackage targets a different (Dongxi/China) domain and cannot be directly reused.

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | The Technical Standards document has internal inconsistencies AND discrepancies against the authoritative CSV source files — the verification script must be anchored to the CSVs as source of truth, with Agent 8 as the implementation scope | High | Strong | 7 concrete discrepancies found between CSV↔TechnicalStandards, plus internal TS contradictions (BOITE.MODE_POSE, ZPM field naming). The CSVs are the original, untranslated domain specification. |
| 2 | The Technical Standards Part VI skeleton implements only ~30% of the combined rule set from Agent 8 + Technical Standards — the implementation gap is systematic, not incidental | High | Strong | Skeleton covers Rules 1.1, 2.0, 3.0, CODE uniqueness, 7.1 only. Missing: 1.2-1.9 (geometry type enforcement), all Rule 4 field non-null checks, all Rule 5 isolation checks, all Rule 6 geometric checks, Rule 7.2, domain vocab validation (Q6), and Agent 8 Q1-Q5 metrics not represented in TS rules at all. |
| 3 | Agent 8 (Quality Sentinel) defines checks (Q1-Q5, FK1-FK2, FK5-FK6) that have no corresponding Technical Standard rule, while TS Rules 1.2-1.9 and 2.0 have no corresponding Agent 8 check — the two documents cover partially overlapping but distinct validation domains | Medium | Strong | 11 Agent 8 checks without TS rules; 10 TS rules without Agent 8 checks. The union is the complete verification surface. |

## Evidence Summary by Hypothesis

### Hypothesis 1: CSV↔TechnicalStandards Discrepancies (7 confirmed)

**D1 — ZPM field naming: REF_SRO vs REF_PM**
- CSV `ZPM.csv` line 11: field name is `REF_SRO` (PM 名称 / Nom du PM)
- Technical Standards Part II §2.7: field name is `REF_SRO`
- Technical Standards Rule 4.11: field name is `REF_PM`
- Technical Standards Part VII matrix: ZPM column has no REF_PM or REF_SRO entry
- GeoFormer Agent 6 §ZPM schema: `REF_SRO`
- **Verdict**: TS Rule 4.11 is wrong. Source of truth (CSV + TS Part II + GeoFormer A6) = `REF_SRO`. This is a copy-paste error from BOITE/CABLE schema where REF_PM is correct.

**D2 — BOITE.MODE_POSE domain values: internal TS contradiction**
- CSV `BOITE.csv` line 18: domain = `Façade, Chambre, Aerien` (3 values)
- Technical Standards Part II §2.1 BOITE.MODE_POSE: `Façade \| Chambre \| Aerien` (3 values)
- Technical Standards Part V §5.4: `SOUTERRAIN \| AERIEN \| FACADE \| IMMEUBLE \| COLONNE MONTANTE` (5 values, uppercase)
- GeoFormer Agent 6 §Domain: `SOUTERRAIN, AERIEN, FACADE, IMMEUBLE, COLONNE MONTANTE` (5 values)
- **Verdict**: Part V was written for CABLE.MODE_POSE and incorrectly applied to all MODE_POSE fields. BOITE.MODE_POSE has only 3 values (Façade, Chambre, Aerien). CABLE.MODE_POSE has 5 values (SOUTERRAIN, AERIEN, FACADE, IMMEUBLE, COLONNE MONTANTE). The verification script must use per-layer domain vocabularies.

**D3 — BOITE.TYPE missing PTO in CSV**
- CSV `BOITE.csv` line 16: TYPE domain = `BPE, BPI, PBO` (3 values, no PTO)
- Technical Standards Part II §2.1: `BPE \| PBO \| BPI \| PTO` (4 values)
- GeoFormer Agent 6 §Domain: `BPE, PBO, BPI, PTO` (4 values)
- **Verdict**: CSV is the authoritative source and was written before PTO was added as a box type. The TS and GeoFormer documents added PTO later. Verification should accept all 4 values (CSV is authoritative but may be incomplete for current deployment).

**D4 — CSV field list duplication errors**
- `BOITE.csv` Rule 4.3 field list: CODE appears once (correct)
- `CABLE.csv` Rule 4.5 field list: `CODE,CODE,...` — CODE listed twice
- `PTECH.csv` Rule 4.7 field list: `CODE,CODE,...` — CODE listed twice
- `INFRASTRUCTURE.csv` Rule 4.9 field list: `CODE,CODE,...` — CODE listed twice
- **Verdict**: Data entry errors in the CSV VERIFICATION_RULE column. The TS correctly deduplicates these. The verification script should use the TS field lists (deduplicated) but cross-reference CSV for field existence.

**D5 — IMB.CODE_VOIE: CSV says it doesn't exist**
- CSV VERIFICATION_RULE.csv Rule 4.1 note: "图层中不存在的字段名：CODE_VOIE" (field does not exist in layer)
- Technical Standards Rule 4.1: "CODE_VOIE does not exist in the layer schema (not added per Rule 4.1 footnote)"
- Both agree: CODE_VOIE is listed in the mandatory field enumeration but does NOT actually exist in the IMB layer schema. The verification script should NOT check for CODE_VOIE.

**D6 — INFRASTRUCTURE.TYPE domain value typo**
- CSV `INFRASTRUCTURE.csv` line 14: TYPE domain = `AERIEN, SOUTEREAIN` (note: "SOUTEREAIN" is a typo for "SOUTERRAIN")
- Technical Standards Part II §2.4: `AERIEN \| SOUTERRAIN`
- GeoFormer Agent 6: TYPE values not explicitly enumerated for INFRA
- **Verdict**: CSV has a French spelling error. TS corrected it. Verification should use `SOUTERRAIN`.

**D7 — CSV vs TS vs GeoFormer: CABLE domain vocabulary language**
- CSV BOITE.MODE_POSE: French mixed-case (`Façade, Chambre, Aerien`)
- TS Part V MODE_POSE: French UPPERCASE (`SOUTERRAIN, AERIEN, FACADE, IMMEUBLE, COLONNE MONTANTE`)
- GeoFormer A6: French UPPERCASE
- **Verdict**: The CSV uses mixed-case French as entered by domain experts. TS and GeoFormer standardized to UPPERCASE. The verification script should normalize to UPPERCASE for comparison, matching GeoFormer convention.

### Hypothesis 2: Implementation Gap (TS Part VI Skeleton Coverage)

**What the skeleton implements (5 rule groups, partial):**
| Rule | Status | Notes |
|------|--------|-------|
| 1.1 | IMPLEMENTED | Layer presence check (8 layers) |
| 2.0 | IMPLEMENTED | CRS EPSG:4326 check |
| 3.0 | IMPLEMENTED | Empty layer check |
| 4.x CODE | IMPLEMENTED | CODE uniqueness per layer (8 layers) |
| 7.1 | IMPLEMENTED | PBO NB_FIBRE_UTIL ≤ CAPACITE |

**What the skeleton does NOT implement (extracted from TS document itself):**
| Rule | Status | Description |
|------|--------|-------------|
| 1.2-1.9 | MISSING | Geometry type + naming convention per layer |
| 4.1 | MISSING | IMB mandatory field non-null (22 fields) |
| 4.3 | MISSING | BOITE mandatory field non-null (24 fields) |
| 4.5 | MISSING | CABLE mandatory field non-null (22 fields) |
| 4.7 | MISSING | PTECH mandatory field non-null (15 fields) |
| 4.9 | MISSING | INFRASTRUCTURE mandatory field non-null (11 fields) |
| 4.11 | MISSING | ZPM mandatory field non-null (6 fields, uses REF_SRO not REF_PM) |
| 4.13 | MISSING | ZNRO mandatory field non-null (5 fields) |
| 4.15 | MISSING | SITE mandatory field non-null (16 fields) |
| 5.1 | MISSING | SITE(PM)↔ZPM bidirectional isolation |
| 5.2 | MISSING | SITE(PM)↔BOITE(PBO) master-slave isolation |
| 5.3 | MISSING | SITE(PM)↔CABLE(DISTRIBUTION) master-slave isolation |
| 5.4 | MISSING | CABLE endpoints↔BOITE/SITE isolation (4 sub-checks) |
| 6.1 | MISSING | ZNRO polygon non-overlap |
| 6.2 | MISSING | ZPM polygon non-overlap |
| 6.3 | MISSING | SITE(PM) within ZPM containment |
| 6.4 | MISSING | BOITE(PBO) within ZPM containment |
| 6.5 | MISSING | CABLE(DISTRIBUTION) within ZPM containment |
| 6.6a | MISSING | CABLE self-loop check (ORIGINE ≠ EXTREMITE) |
| 6.6b | MISSING | CABLE endpoint-to-node coincidence |
| 7.2 | MISSING | PM capacity sum check |

**What Agent 8 requires that has NO Technical Standard rule at all:**
| Agent 8 Check | TS Rule? | Description |
|---------------|----------|-------------|
| Q1 | None | Geometric completeness (features_out / entities_valid_in ≥ 95%) |
| Q2a | None | CABLE endpoint snap to BOITE/PTECH/SITE within 0.0001° |
| Q2b | None | Floating CABLE detection (>0.001° from all nodes) |
| Q2c | ≈6.3 partial | ZNRO contains ≥1 SITE — only partial coverage |
| Q2d | None | IMB within 0.001° of CABLE/INFRA (service coverage) |
| Q2e | None | INFRASTRUCTURE continuity (no endpoint gaps >0.001°) |
| Q3 | None | GCP haversine residual ≤ 1×10⁻⁵° |
| Q4 | None | Semantic coverage (text linkage rate ≥ 70%) |
| Q5 | None | Schema conformance (mapped/total ≥ 80%) |
| Q6 | None | Domain vocabulary compliance (≥95%) — partially covered by field domain checks |
| FK1 | None | CABLE.CODE_INFRA → INFRASTRUCTURE.CODE |
| FK2 | None | BOITE.REF_NRO → SITE(TYPE=NRO).CODE |
| FK5 | None | ZPM.REF_NRO → ZNRO.REF_NRO |
| FK6 | None | SITE.REF_NRO → ZNRO.REF_NRO |
| D1 | 7.1 | PBO NB_FIBRE_UTIL ≤ CAPACITE — covered |
| D2 | 7.2 | PM capacity sum — covered |
| D3 | 6.6a | Self-loop — covered |
| D4 | 4.x | CODE uniqueness — covered |
| B1 | None | Automation rate ≥ 90% gate |
| B2 | None | GCP precision gate |

### Hypothesis 3: Document Consistency / Traceability Matrix

**Complete cross-walk of all checks across all three sources:**

| Check ID | Source | CSV Rule | TS Rule | Agent 8 | Verdict |
|----------|--------|----------|---------|---------|---------|
| Layer presence (8 layers) | All | 1.1 | 1.1 | — | Aligned |
| Layer geom type + naming | CSV+TS | 1.2-1.9 | 1.2-1.9 | — | Aligned (CSV↔TS) |
| CRS consistency | CSV+TS | 2 | 2.0 | Agent 2 | Aligned (but in Agent 2, not 8) |
| Empty layer | CSV+TS | 3 | 3.0 | — | Aligned |
| IMB mandatory non-null | CSV+TS | 4.1 | 4.1 | — | Aligned (note CODE_VOIE exclusion) |
| IMB CODE unique | CSV+TS | 4.2 | 4.2 | D4 partial | Aligned |
| BOITE mandatory non-null | CSV+TS | 4.3 | 4.3 | — | Aligned |
| BOITE CODE unique | CSV+TS | 4.4 | 4.4 | D4 partial | Aligned |
| CABLE mandatory non-null | CSV+TS | 4.5 | 4.5 | — | Aligned |
| CABLE CODE unique | CSV+TS | 4.6 | 4.6 | D4 partial | Aligned |
| PTECH mandatory non-null | CSV+TS | 4.7 | 4.7 | — | Aligned |
| PTECH CODE unique | CSV+TS | 4.8 | 4.8 | D4 partial | Aligned |
| INFRA mandatory non-null | CSV+TS | 4.9 | 4.9 | — | Aligned |
| INFRA CODE unique | CSV+TS | 4.10 | 4.10 | D4 partial | Aligned |
| ZPM mandatory non-null | CSV+TS | 4.11 | 4.11 | — | DISCREPANCY: REF_SRO vs REF_PM |
| ZPM CODE unique | CSV+TS | 4.12 | 4.12 | D4 partial | Aligned |
| ZNRO mandatory non-null | CSV+TS | 4.13 | 4.13 | — | Aligned |
| ZNRO CODE unique | CSV+TS | 4.14 | 4.14 | D4 partial | Aligned |
| SITE mandatory non-null | CSV+TS | 4.15 | 4.15 | — | Aligned |
| SITE CODE unique | CSV+TS | 4.16 | 4.16 | D4 partial | Aligned |
| SITE(PM)↔ZPM isolation | CSV+TS | 5.1 | 5.1 | FK3 partial | Aligned (CSV↔TS) |
| SITE(PM)↔BOITE(PBO) isolation | CSV+TS | 5.2 | 5.2 | FK3 partial | Aligned |
| SITE(PM)↔CABLE(DIST) isolation | CSV+TS | 5.3 | 5.3 | FK4 partial | Aligned |
| CABLE endpoints↔BOITE/SITE | CSV+TS | 5.4 | 5.4 | FK7+FK8 partial | Aligned |
| ZNRO non-overlap | CSV+TS | 6.1 | 6.1 | — | Aligned |
| ZPM non-overlap | CSV+TS | 6.2 | 6.2 | — | Aligned |
| SITE(PM) within ZPM | CSV+TS | 6.3 | 6.3 | — | Aligned |
| BOITE(PBO) within ZPM | CSV+TS | 6.4 | 6.4 | — | Aligned |
| CABLE(DIST) within ZPM | CSV+TS | 6.5 | 6.5 | — | Aligned |
| CABLE self-loop | CSV+TS | 6.6.1 | 6.6a | D3 | Aligned |
| CABLE endpoint coincidence | CSV+TS | 6.6.2 | 6.6b | Q2a partial | Aligned |
| PBO capacity check | CSV+TS | 7.1 | 7.1 | D1 | Aligned |
| PM capacity sum | CSV+TS | 7.2 | 7.2 | D2 | Aligned |
| Q1 Geometric completeness | Agent 8 | — | — | Q1 | Agent 8 only |
| Q2 Topological integrity | Agent 8 | — | — | Q2a-Q2e | Agent 8 only (partial overlap with 6.3-6.6) |
| Q3 Coordinate precision | Agent 8 | — | — | Q3 | Agent 8 only |
| Q4 Semantic coverage | Agent 8 | — | — | Q4 | Agent 8 only |
| Q5 Schema conformance | Agent 8 | — | — | Q5 | Agent 8 only |
| Q6 Domain vocab compliance | Agent 8 | — | — | Q6 | Agent 8 only (partial: field domain checks) |
| FK1 CABLE.CODE_INFRA→INFRA.CODE | Agent 8 | — | — | FK1 | Agent 8 only |
| FK2 BOITE.REF_NRO→SITE(NRO) | Agent 8 | — | — | FK2 | Agent 8 only |
| FK5 ZPM.REF_NRO→ZNRO.REF_NRO | Agent 8 | — | — | FK5 | Agent 8 only |
| FK6 SITE.REF_NRO→ZNRO.REF_NRO | Agent 8 | — | — | FK6 | Agent 8 only |
| B1 Automation gate | Agent 8 | — | — | B1 | Agent 8 only |
| B2 Precision gate | Agent 8 | — | — | B2 | Agent 8 only |

## Evidence Against / Missing Evidence

### Hypothesis 1:
- The 7 discrepancies are well-documented but some may be intentional design decisions (e.g., PTO addition, UPPERCASE standardization) rather than errors
- The CSV files may themselves be outdated relative to current deployment requirements
- Without access to the actual DWG source files and a produced GeoPackage, we cannot verify which domain values actually appear in real data

### Hypothesis 2:
- The TS Part VI skeleton is explicitly labeled "outline" — it was never intended to be complete
- Some missing checks (Q1-Q5) are pipeline-level metrics that can only be computed with access to intermediate agent outputs (tile JSONL files), not from the final GeoPackage alone
- The skeleton's purpose was illustrative, showing the pattern for how rules should be implemented

### Hypothesis 3:
- The Agent 8 checks without TS rules (Q1-Q5, FK1-FK2, FK5-FK6) may be intentionally scoped to pipeline-internal quality metrics rather than deliverable-acceptance criteria
- The TS rules without Agent 8 checks (1.2-1.9, 2.0) are file-format-level checks handled by Agent 2 (CRS) and Agent 7 (Assembly), not Agent 8
- A complete verification script may need to operate in two modes: (a) GeoPackage-only validation (TS rules) and (b) full-pipeline validation (Agent 8 Q-metrics requiring intermediate data)

## Per-Lane Critical Unknowns

- **Lane 1 (Implementation gap)**: What is the exact scope boundary for `/official/validation/` — should the verification script validate only the final GeoPackage (TS rules, self-contained), or should it also compute Agent 8 pipeline-level Q-metrics that require intermediate tile JSONL and agent reports as input?

- **Lane 2 (Schema reconciliation)**: Which document is authoritative when CSV, TS, and GeoFormer disagree? The CSVs are the original domain expert specification, the TS is a derived engineering document, and GeoFormer is the pipeline implementation spec. For ZPM.REF_SRO vs REF_PM, the CSV+TS Part II agree on REF_SRO — but does the existing Shape reference data in `/official/Shape/` use REF_SRO or REF_PM as the actual field name?

- **Lane 3 (Document consistency)**: Do the Agent 8 Q1-Q5 metrics (geometric completeness, topological integrity, coordinate precision, semantic coverage, schema conformance) need to be implemented as automated checks in the verification script, or are they informational metrics computed from agent reports that the verification script only aggregates and displays?

## Lane 3 Misplacement / SoT Ownership Scope

No MOVE candidates were discovered — this is a greenfield implementation in an empty directory, not a misplacement of existing artifacts. All three specification sources (CSV, TS, GeoFormer) are correctly located in their respective directories and serve distinct roles in the specification hierarchy.

## Rebuttal Round

**Best rebuttal to Hypothesis 1 (leader)**: Hypothesis 2 argues that the implementation gap is more critical than the schema discrepancies — even with perfectly reconciled schemas, ~70% of the verification logic remains unwritten. The schema discrepancies (7 items) can be resolved with a single reconciliation document; the implementation gap requires writing hundreds of lines of validation logic.

**Why leader held**: Hypothesis 1 leads because schema discrepancies, if unresolved, would cause the verification script to produce incorrect results even for the 30% of rules already implemented. A verification script that checks the wrong field names or wrong domain values is worse than no script at all — it provides false confidence. The schema reconciliation MUST happen first, and it directly informs the implementation scope of Hypothesis 2.

**Convergence note**: Hypotheses 1 and 3 partially converge — both identify the CSV files as the authoritative source of truth. The schema reconciliation (H1) is a prerequisite for the completeness audit (H3), and both feed into the implementation scope (H2). The three lanes are sequential dependencies, not competing explanations.

## Convergence / Separation Notes

All three hypotheses converge on a single root cause: the three specification artifacts (CSV, TS, GeoFormer) were produced at different times by different authors for different purposes, and no formal reconciliation was performed before the implementation phase. The CSV files are the original domain expert specification (French/Chinese bilingual). The Technical Standards document translated and structured the CSV rules into an engineering format but introduced copy-paste errors (ZPM.REF_PM, BOITE.MODE_POSE domain). The GeoFormer AgentPrompts further adapted the rules for a pipeline architecture context, adding Agent 8 metrics (Q1-Q6) that go beyond the CSV/TS rule set.

## Most Likely Explanation

The verification implementation in `/official/validation/` must:
1. **Use CSV files as the authoritative source of truth** for field names, mandatory constraints, and domain vocabularies — the CSVs are the original, untranslated domain expert specification
2. **Use the Technical Standards document as the implementation structure** — its 7-rule-group organization and Python skeleton provide the engineering framework
3. **Use GeoFormer Agent 8 as the extended scope** — Q1-Q6 metrics, FK1-FK6 referential integrity, and benchmark gates B1-B2 are the production quality requirements
4. **Reconcile the 7 identified discrepancies** before writing any verification code, with CSV taking precedence except where clearly erroneous (D4: field duplication, D6: typo)
5. **Implement the full 7 rule groups** (not just the ~30% skeleton) as the verification engine, with Agent 8 Q-metrics as an optional extended mode requiring intermediate pipeline data

## Critical Unknown

Does the verification script need to operate exclusively on the final GeoPackage (self-contained, no pipeline dependency), or does it also need to ingest intermediate agent outputs (tile JSONL, agent reports) to compute Agent 8 Q1-Q5 and benchmark gates? This determines whether the script has one mode or two modes, and affects ~40% of the implementation scope.

## Recommended Discriminating Probe

Check whether the existing Shape reference data in `/official/Shape/` uses `REF_SRO` or `REF_PM` as the ZPM field name. This single probe resolves the most impactful schema discrepancy (D1) and establishes the precedent for CSV-vs-TS authority: if the Shape data uses REF_SRO, the CSV is confirmed as authoritative and all other discrepancies follow the same resolution pattern.
