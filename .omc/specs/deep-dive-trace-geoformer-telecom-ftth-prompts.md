# Deep Dive Trace: geoformer-telecom-ftth-prompts

## Observed Result
User requests integrated agent prompts that combine GeoFormer architecture, /official telecom FTTH domain knowledge, and /plugincad2gis methodologies. GeoFormer demo output has known failures: EPSG mismatch, coordinate precision 65km residual, Chinese landscape domain incompatible with Francophone telecom.

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength |
|------|------------|------------|-------------------|
| 1 | GeoFormer stages map cleanly to telecom domain with targeted prompt adaptations | High | Strong |
| 2 | plugincad2gis methodologies fill critical gaps in GeoFormer's implementation | High | Strong |
| 3 | /official CSV domain dictionaries directly transform to prompt vocabularies | High | Strong |
| 4 | GeoFormer has 10 OMIT-class incompatibilities requiring explicit documentation | High | Strong |

## Lane 1: GeoFormer → Telecom Mapping
- Agent 2 (CRS Detective): OMIT entirely — /official uses EPSG:4326 directly
- Agent 3 (Normalizer): ADAPT — identity transform, no pyproj chain
- Agent 4 (Topology): REUSE algorithms, ADAPT rules for FTTH connectivity
- Agent 5 (Semantic Weaver): ADAPT — French telecom abbreviations replace Chinese
- Agent 6 (Schema Alchemist): ADAPT — 8 FTTH feature classes replace 13 Chinese landscape classes
- Agent 7 (Assembler): ADAPT — EPSG:4326, multi-DWG merge

## Lane 2: plugincad2gis Methodologies
9 reusable patterns identified:
1. SourceRef provenance on every feature (file/layer/block/handle/entity_type)
2. Two-tier classification (rules-first + block-code table with text-evidence gating)
3. Negative evidence gates (paving veto, annotation-only layer regex)
4. Guarded geometry repair (area-delta ≤25%, class-constrained dedup, 95% sub-segment test)
5. Multi-pass refinement (demote→snap→propagate labels from topology)
6. Parsimony in spatial fit (prefer 4-param over 6-param unless RMSE gain ≥50%)
7. Scale-aware tolerances (chord height as 0.1% of extent)
8. Full audit trail (CorrectionPatch ledger, RunReport per-stage, SHA256 of source)
9. GCP auto-discovery from X=/Y= coordinate labels in DWG text

## Lane 3: Domain Label Integration
- 14 CSV dictionaries map to 8 feature classes with explicit attribute targets
- Cross-cutting domains: STATUT (all 8 FCs), MODE_POSE (CABLE, BOITE, SITE)
- Topology constraints: CABLE.ORIGINE→PTECH.CODE, BOITE.REF_NRO→ZNRO.REF_NRO, etc.
- All domains usable as Agent 5 vocabulary tables and Agent 6 schema validation rules

## Lane 4: Critical Gap Analysis
10 OMIT items: hardcoded offsets, UTM48N→3857 chain, regime hypothesis bbox, Chinese schema, Chinese topology rules, Chinese vocab, Chinese LLM_JUDGE, EPSG:3857 hardcode, Chinese attribute schema, Chinese quality metrics
6 ADD items: multi-DWG merge, EPSG:4326 geographic topology, 14 domain vocabularies, FTTH network connectivity graph, CRS passthrough mode, shapefile output
7 ADAPT items: CRS stage, normalizer, GCPs, semantic weaver, schema, topology config, assembler CRS

Root cause of 65km residual: hardcoded offsets from visual Tianditu matching (not survey-grade GCPs), per-layer survey benchmark divergence, no DWG geodetic datum. Approach CANNOT be salvaged for /official (which already has WGS84 coords).

## Critical Unknown
How to handle geographic (degree-based) topology operations: node snapping at 0.05m makes no sense with EPSG:4326 lat/lon coordinates. Temporary projection to local UTM zone for topology repair, then unproject back?

## Recommended Discriminating Probe
Verify /official DWG coordinates are truly WGS84 by spot-checking a known location against OpenStreetMap in QGIS.
