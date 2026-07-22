# Deep Dive Spec: GeoFormer Telecom FTTH Agent Prompts

## Goal
Generate an integrated, first-hand agent prompt corpus for CAD-to-GIS conversion of FTTH telecom deployments, synthesizing the GeoFormer 9-agent architecture with /official JAD-MARJANE domain knowledge and /plugincad2gis conversion methodologies.

## Trace Findings
- 4 parallel trace lanes completed (High confidence all lanes)
- **Lane 1**: All 9 GeoFormer stages map to telecom domain — Agent 2 (CRS) OMIT entirely, Agent 3 (Normalizer) simplified to identity transform, Agents 4-7 ADAPT with telecom rules
- **Lane 2**: 11 plugincad2gis methodologies extracted and embedded: SourceRef provenance, two-tier classification, negative evidence gates, guarded geometry repair, multi-pass refinement, scale-aware tolerances, GCP auto-discovery, correction audit ledger
- **Lane 3**: 14 CSV domain dictionaries mapped to 8 FTTH feature class attributes with explicit cross-cutting relationships (STATUT across all 8 FCs)
- **Lane 4**: 14 OMIT items documented (hardcoded offsets, UTM48N chain, Chinese schema/vocab/topology, EPSG:3857, 0.0012m benchmark), 6 ADD items (multi-DWG merge, EPSG:4326 topology, domain vocab compliance, network connectivity graph, CRS passthrough, shapefile output), root cause of 65km residual identified

## Deliverable
Integrated prompts file at `/home/cat/projects/CAD2GIS/temp/claude_desktop/GeoFormer_Telecom_FTTH_AgentPrompts.md` (924 lines, 48KB):
- 9 agent system prompts + 9 task prompts
- Appendix A: plugincad2gis methodology integration table (11 modules)
- Appendix B: OMIT register (14 excluded GeoFormer components with explanations)

## Acceptance Criteria
1. All 9 agents have [REUSE]/[ADAPT]/[OMIT]/[ADD] classification
2. Dongxi-specific components explicitly excluded with rationale
3. /official 14 CSV domain dictionaries embedded as controlled vocabularies
4. /plugincad2gis methodologies referenced in relevant agent prompts
5. EPSG:4326 target CRS throughout (not 3857)
6. Multi-DWG merge logic present in Agent 1 and Agent 7
7. French/English telecom keyword patterns replace Chinese infrastructure patterns
