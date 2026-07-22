# Deep Dive Trace: geoformer-prompt-transplant-plan

## Observed Result
The GeoFormer agent prompt document (`GeoFormer_FiberHome_P2_AgentPrompts_revised.md`) contains 11 Morocco-specific hardcoded values across 6 of 9 agents. Sonnet created a Hutabohu adaptation by find-and-replace of all 11 anchors, producing a structurally identical document. The validation codebase (converter.py, schema_config.py, domain_vocab.py, evaluator.py) already supports multi-domain operation through CLI flags and config files, but the prompt document has no equivalent parameterization mechanism.

## Ranked Hypotheses
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | No domain config bridge to prompts | High | Strong | The code already has `--source-crs`, `--region-bounds`, CSV domain vocab loading, and configurable schemas. Agent 9's job manifest uses `{CONFIG_DIR}` template variables. The infrastructure exists but stops one layer short — there's no `deployment_config.json` and no template variable syntax in agent prompts. This is the highest-leverage fix: create one config file + add `{{var}}` syntax to prompts. |
| 2 | Domain parameters buried in prose | Medium | Strong | The 11 anchors are scattered as inline literals with no centralized config section. However, the document already HAS a Scope Declaration preamble and Agent 9 job manifest where parameters could be consolidated. The dual-purpose nature (spec + operational prompts) explains why parameters were written inline, but the fix is straightforward: extract to a preamble config block. |
| 3 | Prompt-code divergence | Medium | Moderate | Code defaults to EPSG:32629 (Morocco) but prompts claim "native WGS84." Code has English telecom keywords but prompts have French. Code warnings still say "outside Morocco bounds." These are real but surface-level discrepancies — the architectural alignment (8 FCs, EPSG:4326 output, quality gates) is intact. |

## Evidence Summary by Hypothesis

- **Hypothesis 1 (No config bridge)**: `converter.py:1156` has `--source-crs` with default EPSG:32629. `converter.py:106` has `DEFAULT_REGION_BOUNDS = None` with `--region-bounds` CLI flag documented. `domain_vocab.py:17-33` maps 14 CSV files to domain keys. `schema_config.py:2580-2581` declares `CRS_DATA` and `CRS_QGIS_DISPLAY`. Agent 9 job manifest (line 1317-1339) uses `{CONFIG_DIR}`, `{DWG_DIR}`, `{TILE_DIR}` template variables. But NO agent prompt uses `{{deployment_bounds}}` or `{{domain_name}}` syntax. No `deployment_config.json` schema exists. The bridge is half-built: config sources exist, template mechanism exists in A9 manifest, but they don't connect to agent prompts.

- **Hypothesis 2 (Buried in prose)**: Scope Declaration (line 10) hardcodes "Francophone FTTH telecom deployment." Agent 1 system prompt (line 112) repeats it. Agent 2 system prompt (line 298-299) hardcodes `lat ∈ [21, 36], lon ∈ [-17, -2]`. Agent 2 task prompt (line 339) repeats bounds as `morocco_bounds` JSON. Agent 3 (line 425) references "Morocco bounds." Agent 4 (line 507) says "~11m at Morocco latitude." Agent 8 (line 1126 in Morocco, 1125 in Hutabohu) says "~1.1m at Morocco/Gorontalo latitude." These 11 anchors span Agents 1,2,3,4,8 — all could be replaced with `{{deployment_bounds}}`, `{{domain_name}}`, `{{reference_latitude_label}}`.

- **Hypothesis 3 (Prompt-code divergence)**: `converter.py:1156` defaults to `EPSG:32629` but prompt Scope Declaration claims "native WGS84 coordinates (EPSG:4326)." `converter.py:147-153` uses English telecom patterns (`(?i)(AERIAL|UNDERGROUND|BURIED|...)`) while Agent 5 prompts use French patterns (`SOUTERRAIN`, `AERIEN`, `FACADE`). `converter.py:1207` warning message still says "outside Morocco bounds" (not domain-agnostic). However, `schema_config.py`'s 8 FC schemas, `evaluator.py`'s verification rules, and the quality gates are fully aligned between code and prompts. The divergence is in defaults and language, not architecture.

## Evidence Against / Missing Evidence

- **Hypothesis 1**: Agent 9 job manifest already templates `{CONFIG_DIR}/domain_vocab.json` and `{CONFIG_DIR}/telecom_schema_mapping.json` — showing the template pattern IS used where config files already exist. The gap is specifically that no deployment-level config file exists, not that the template mechanism is absent.
- **Hypothesis 2**: Well-structured documents with section headers per agent. The Scope Declaration could serve as a natural config preamble. The inline values are few (11) and localized — extraction would not require restructuring the document architecture.
- **Hypothesis 3**: The code's Morocco defaults could be intentional (Morocco was the reference implementation). The French keywords in prompts may be correct for Francophone domains (Morocco, but not Indonesia). The divergence may reflect different maturity levels rather than bugs.

## Per-Lane Critical Unknowns

- **Lane 1 (Buried parameters)**: Which of the 11 anchors are semantically load-bearing (must change per domain) vs. illustrative examples that could remain as-is? Specifically, the file bbox example and the precision benchmark annotations (~1.1m at X latitude) may be illustrative rather than functional.

- **Lane 2 (Config bridge)**: What is the minimal schema for `deployment_config.json`? The 11 anchors suggest ~6-8 config keys: domain_name, bounds_lat, bounds_lon, reference_latitude_label, national_crs, national_crs_epsg, projected_crs, projected_crs_epsg, file_bbox_example. But are all needed?

- **Lane 3 (Divergence)**: Should the code defaults change from Morocco (EPSG:32629) to worldwide (EPSG:4326 identity), or should prompts document the code's current default behavior? Which is the reference and which is the derivative?

## Rebuttal Round
- **Best rebuttal to leader (H1 → H2)**: The config bridge is unnecessary if the 11 anchors are simply extracted to a preamble section within the same document. A document-internal "Deployment Parameters" block could achieve transplantability without any external config file — just edit one section instead of 11 scattered locations.
- **Why H1 held**: An internal preamble still requires manual find-and-replace for each new domain. A `deployment_config.json` approach enables programmatic prompt generation (e.g., `python render_prompts.py --config hutabohu.json`), which is the difference between "easier to edit" and "automated transplant." The code already uses external config files — consistency demands the prompts do too.

## Convergence / Separation Notes
- H1 and H2 converge on the same solution (externalize domain parameters) but differ on mechanism: H1 says external config file with template variables, H2 says consolidate within the document. Both agree the 11 anchors must be parameterized.
- H3 is partially downstream of H1/H2 — the code-prompt divergence in defaults and language will naturally resolve when prompts are generated from config rather than hand-edited.

## Most Likely Explanation
The prompt document lacks a domain configuration layer because it was authored as a static design specification during the Morocco reference implementation phase. The validation code, being the execution artifact, naturally evolved configurable parameters (CLI flags, CSV loading) as it was tested against real DWG files from different sources. The prompts were never updated because there was no mechanism to do so — they're a markdown file, not a template system. The fix is to create a `deployment_config.json` schema covering the 11 domain anchors, add `{{variable}}` syntax to the prompt document, and provide a render script that produces domain-specific prompt files. This mirrors the code's existing pattern of `--source-crs` + `--region-bounds` + CSV domain vocabularies.

## Critical Unknown
What is the complete and minimal set of domain parameters that must be externalized? The 11 Morocco→Hutabohu replacements suggest 6-8 config keys, but some anchors (file bbox example, precision benchmark annotations) may be illustrative prose rather than functional parameters. A systematic audit of all 9 agents is needed to distinguish "must change" from "safe as-is."

## Recommended Discriminating Probe
Catalog every domain-specific token in the prompt document by agent, classify each as **FUNCTIONAL** (affects agent behavior if wrong), **GEODETIC** (affects coordinate validation), or **ILLUSTRATIVE** (example only, safe to keep). The count in each category determines the minimal config schema and whether a simple find-and-replace script suffices vs. a full template system.
