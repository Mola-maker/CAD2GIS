# Deep Dive Trace: comprehensively-understand-all-experiment

## Observed Result

User asked three questions about the CAD2GIS Python converter cluster (~12,000 lines, 10 scripts):
1. Can it be reused for other DWG files?
2. To what degree is it reusable?
3. Can all converters be consolidated into fewer programs, with the rest decoupled as components?

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | ~10% project-specific coupling is the main barrier; ~90% is FTTH-domain or domain-agnostic | High | Strong | Lane 1 quantified 1,200+ hardcoded values: 3.7% domain-agnostic, 54.2% FTTH, 10% project-specific, 32.1% configurable. The coupling is concentrated and extractable. |
| 2 | Architecture already separates concerns (config vs logic vs topology vs styling) but config is embedded as Python constants rather than externalized | High | Strong | Lane 2 mapped all 56 converter.py functions to 7 layers. 5 of 10 modules are genuinely standalone. Natural consolidation to 4-5 core programs exists. |
| 3 | Guides describe an idealized 9-agent DAG architecture that never existed in code; the implementation has diverged significantly but surpassed the guide in key areas | High | Strong | Lane 3 found 11/35 GeoFormer capabilities implemented, 12 capabilities beyond the guide. Structural divergence: 9-agent DAG vs single sequential pipeline. Verification guide most aligned. |

## Evidence Summary by Hypothesis

- **Hypothesis 1 (Code Coupling)**: Lane 1 quantified all 1,200+ hardcoded values across schema_config.py (2,680 lines), converter.py (3,416 lines), topology_builder.py (1,378 lines), domain_vocab.py (352 lines). Bucket C (project-specific: 120 items including DMPH label regex, Indonesia bounds, FDT_VALUE map, code prefixes) is concentrated in ~5 locations. LABEL_FAMILIES, LAYER_PATTERN_MAP, FRAGMENT_AGGREGATION_LAYERS, FDT_VALUE, and REGION_BOUNDS_WGS84 could all be extracted to a single JSON config file (~1 day of work). The 54.2% FTTH-domain surface is intentional — this IS an FTTH tool.

- **Hypothesis 2 (Architecture Surface)**: Lane 2 classified every function across all 10 modules into 7 architectural layers (L1 CAD Extraction → L7 Verification). converter.py spans all 7 layers (orchestrator). Five modules are genuinely standalone (legend_detector, layout_miner, evidence_ledger, style_builder, domain_vocab). Natural consolidation: schema_config + domain_vocab → single config module; converter split into core pipeline + FTTH plugins; topology_builder, evaluator, style_builder stay independent. The core "CAD read → classify → geometry → write" pipeline (L1-L3) is highly reusable for any DWG-to-GIS task.

- **Hypothesis 3 (Guide-Implementation Gap)**: Lane 3 produced a 35-capability feature matrix. 11 IMPLEMENTED, 14 NOT IMPLEMENTED (Kvisimine quadtree, LLM bridge, DAG orchestrator, 4/5 topology ops, GCP validation, STRtree caching), 7 PARTIALLY, 3 SUPERSEDED. 12 capabilities exist in code but not in guides (cable chaining, graded topology, BOITE fusion, Hungarian assignment, legend detection, 3-track styling, evidence ledger, layout mining, FDT domains, span extraction, ATTRIB reading, SITE snap). Verification guide maps well to evaluator.py (8/10 alignment). T_TOPOLOGY_REPAIR_ANALYSIS is the most accurate technical document. 10 engineering lessons extracted from the 15-spec trace→interview→implementation cycle.

## Evidence Against / Missing Evidence

- **Hypothesis 1**: The count of "project-specific" items (120) may be understated — some Bucket D items (field names like REF_SRO, NB_FIBRE_UTIL) carry implicit FTTH domain assumptions. Also, the DWG layer name regexes in LAYER_PATTERN_MAP encode implicit knowledge about Hutabohu CAD conventions that would need rewriting for any new project.
- **Hypothesis 2**: The architectural layers are descriptive, not enforced. converter.py imports from 7 other modules, creating tight coupling through mutable global state (_CRS_TRANSFORM, ANNOTATION_LEDGER, BOITE_FUSION_LEDGER). Refactoring into clean layers requires breaking these globals.
- **Hypothesis 3**: The guides' "NOT IMPLEMENTED" list includes genuinely valuable features (GCP validation, STRtree caching, checkpointing) that would improve robustness. Their absence limits reuse for production pipelines.

## Per-Lane Critical Unknowns

- **Lane 1 (Code-path coupling)**: How do DWG layer naming conventions vary across different FTTH projects and different CAD authors? The LAYER_PATTERN_MAP assumes specific regex patterns — if a new project's CAD uses completely different layer names, the entire Tier-1 classification fails silently.
- **Lane 2 (Config/architecture surface)**: Can the FTTH domain schemas (BOITE, CABLE, PTECH, etc.) be abstracted to a generic "feature class configuration" format that works across telecom sub-domains (FTTH vs FTTB vs mobile backhaul)? Or is each sub-domain fundamentally different?
- **Lane 3 (Guide-vs-implementation gap)**: Should the pipeline be refactored to match the guide's 9-agent DAG architecture, or should the guide be rewritten to document the actual sequential pipeline? The guides have value as architectural vision but mislead as documentation.

## Convergence / Separation Notes

All three lanes converge on the same core finding: **the converter IS reusable, with the main barrier being ~10% project-specific configuration that is concentrated and extractable**. The disagreement is on emphasis:

- Lane 1 emphasizes the quantitative extent of coupling
- Lane 2 emphasizes the architectural path to decoupling
- Lane 3 emphasizes the documentation/knowledge gap

These are complementary, not competing. The synthesis is: **extract project config → stabilize boundaries → update documentation**.

## Most Likely Explanation

The CAD2GIS Python converter cluster is **reusable at approximately 85-90% for other FTTH DWG files** and **reusable at approximately 55-60% for non-telecom DWG-to-GIS pipelines**. The 10% project-specific coupling (Bucket C: label regex, DWG layer names, coordinate bounds, CRS defaults, FDT values, code prefixes) is concentrated in ~5 well-defined locations and can be extracted to a single JSON project config file. The 54% FTTH-domain surface (Bucket B: schemas, vocabularies, topology rules) is correct and intentional — this is purpose-built for FTTH telecom.

The architecture already has good separation of concerns but needs boundary hardening:
1. converter.py's mutable global state must be encapsulated
2. topology_builder.py's internal helpers must be hidden behind a stable API
3. schema_config.py and domain_vocab.py should merge into one config module
4. Project-specific values must move to external config files

The guides are architecture documents, not living documentation. They should be clearly labeled as such, and a new living architecture document should be created based on the actual code.

## Critical Unknown

**How much do DWG layer naming conventions vary across different FTTH projects by different CAD authors?** The entire Tier-1 classification (LAYER_PATTERN_MAP in schema_config.py) depends on regex patterns that were reverse-engineered from one Hutabohu drawing. Without testing on 3-5 additional DWG files from different projects/authors, we cannot quantify the regex generalization failure rate.

## Recommended Discriminating Probe

Test the current converter (with only schema_config.py LAYER_PATTERN_MAP modified) on 3-5 other FTTH DWG files from different projects. Measure:
1. Tier-1 classification hit rate (entities matched to a known FC)
2. DWG layer name pattern coverage (% of non-NEGATIVE_EVIDENCE layers that match a LAYER_PATTERN_MAP entry)
3. Required LAYER_PATTERN_MAP additions per new DWG file

If ≥80% of entities classify without map changes across 3+ files, the regex approach generalizes. If each file requires 5+ new patterns, the classification layer needs a more adaptive approach (e.g., LLM-assisted layer name mapping).

## 10 Engineering Lessons from 15 Specs

1. **Guides written before code are aspirational, not descriptive** — the GeoFormer guide was never updated when the code diverged
2. **Empirical CRS discovery is mandatory** — never trust CRS assertions; probe with dwgread first
3. **Anonymous DWG blocks break regex-based classification** — always dump a block-name histogram first
4. **Trace-first investigation prevents wasted implementation** — probe before designing
5. **"Strict topology" ≠ "geometric snap"** — graded topology (snap/attr_only/floating) is more correct
6. **Gap-bridge without constraints causes systematic errors** — spatial joins need semantic constraints
7. **Legend/diagram content must be detected and excluded** — auto-detection needs confirmation loop
8. **Hungarian assignment outperforms greedy nearest-neighbor** for label-to-feature binding
9. **Paper-space layouts are evidence sources, not geometry sources**
10. **Evidence ledger systems enable self-verification** — build verification into the pipeline
