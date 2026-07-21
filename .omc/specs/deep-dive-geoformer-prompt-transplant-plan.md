# GeoFormer Prompt Transplant Plan — Crystallized Spec

## Goal
**Disentangle and generalize** the GeoFormer-FiberHome P2 agent prompts. Separate domain-specific configuration (bounds, CRS, labels, language) from domain-agnostic pipeline logic (9-agent architecture, FTTH schemas, topology rules, quality gates). The Morocco reference is deprecated — the universal template is the new canonical form. Domain-specific deployments are produced by pairing the template with a `deployment_config.json` + snippet files.

## Constraints
- **Scope: prompts only.** Code changes (converter.py default CRS, warning messages, keyword language) are documented as follow-up tasks but NOT implemented in this transplant.
- **deployment_config.json schema** must cover all 11 domain anchors identified in the Morocco→Hutabohu differentiation.
- **{{variable}} syntax** must be unambiguous and not conflict with existing markdown or the Agent 9 job manifest `{CONFIG_DIR}` pattern (use `{{deployment.key}}` double-brace syntax to distinguish from job manifest single-brace `{PLACEHOLDER}`).
- **Render script** must be a single Python file with no dependencies beyond stdlib (`json`, `pathlib`, `re`).
- **Existing files preserved as historical artifacts.** The Morocco original and Hutabohu adaptation remain in `experiment/guide/` unchanged. They are NOT verification targets — they are historical snapshots.
- **Morocco is fully removed from the template lineage.** The universal template is built as a clean generalization. No Morocco config is created. No Morocco-specific references exist in the template. The existing Morocco `.md` files are historical artifacts only.

## Non-Goals
- Fixing `converter.py` default `--source-crs EPSG:32629` → `EPSG:4326`
- Fixing `converter.py:1207` "outside Morocco bounds" warning message
- Converting Agent 5 French keyword patterns to bilingual/English
- Changing `schema_config.py` or `domain_vocab.py` in any way
- CI/CD integration or pre-commit hooks
- Modifying Agent 9 job manifest (already uses `{CONFIG_DIR}` — out of scope)

## Acceptance Criteria
1. `deployment_config.json` schema covers all domain-variable parameters with typed fields.
2. `deployment_config.hutabohu.json` + snippet files exist as the active reference deployment.
3. Universal template `GeoFormer_FiberHome_P2_AgentPrompts.template.md` contains ZERO domain-specific hardcoded values. No Morocco, no Francophone, no region-specific references. All domain-variable content uses `{{deployment.key}}` or `{{snippet:key}}` placeholders.
4. `render_prompts.py` reads a config JSON + snippet files and produces a complete domain-specific `.md` prompt file.
5. **Active deployment verification:** `python render_prompts.py --config deployment_config.hutabohu.json` produces output matching `GeoFormer_FiberHome_Hutabohu_AgentPrompts.md` (except footer version line).
6. **Generalization proof:** Creating a 2nd deployment config for a different region (e.g., Fortaleza, Brazil) requires NO changes to the template or render script — only a new config JSON + 2 snippet files. Render succeeds and produces a valid, complete prompt document.
7. **No Morocco config exists.** The Morocco `.md` files in `experiment/guide/` are historical artifacts only, not verification targets.

## Assumptions Exposed
- **A1:** The 11 anchors are the complete set of domain-specific tokens. If additional anchors are discovered, the config schema must be extended.
- **A2:** Double-brace `{{deployment.key}}` does not conflict with any existing content in the prompt document (verified: no `{{` sequences exist in the current .md files).
- **A3:** The prompt document structure (9 agents, appendices) is stable and won't change independently of the template. If the document is edited, the template must be updated in sync.
- **A4:** French telecom keywords and Latin-1 encoding are treated as the "reference language pack" for now. A future `deployment_config.language` key could switch keyword sets, but this is out of scope.
- **A5:** Byte-identical round-trip is achievable because the 11 replacements are exact string substitutions with no whitespace or formatting changes.

## Technical Context

### Files involved
| File | Location | Role |
|------|----------|------|
| `GeoFormer_FiberHome_P2_AgentPrompts_revised.md` | `experiment/guide/` | Historical artifact (Morocco, deprecated) |
| `GeoFormer_FiberHome_Hutabohu_AgentPrompts.md` | `experiment/guide/` | Hutabohu reference (verification target) |
| `deployment_config.schema.json` | `experiment/config/` | JSON Schema for deployment config |
| `deployment_config.hutabohu.json` | `experiment/config/` | Hutabohu config (active reference) |
| `snippets/hutabohu_crs_preamble.md` | `experiment/config/snippets/` | Hutabohu CRS preamble |
| `snippets/hutabohu_qgis_note.md` | `experiment/config/snippets/` | Hutabohu QGIS note |
| `GeoFormer_FiberHome_P2_AgentPrompts.template.md` | `experiment/guide/` | Universal template (canonical, built clean) |
| `render_prompts.py` | `experiment/` | Render script |

### The 11 domain anchors mapped to config keys
| # | Anchor | Config Key | Type | Agents Affected |
|---|--------|-----------|------|----------------|
| 1 | Domain name ("Francophone FTTH...") | `deployment.domain_name` | string | Scope, A1 |
| 2 | Deployment bounds (lat) | `deployment.bounds.lat_min`, `deployment.bounds.lat_max` | float | A2, A3 |
| 3 | Deployment bounds (lon) | `deployment.bounds.lon_min`, `deployment.bounds.lon_max` | float | A2, A3 |
| 4 | Bounds label ("Morocco bounds") | `deployment.bounds_label` | string | A2, A3 |
| 5 | Latitude label ("Morocco latitude") | `deployment.latitude_label` | string | A4, A8 |
| 6 | File bbox example | `deployment.file_bbox_example` | string | A2 |
| 7 | National CRS name | `deployment.national_crs.name` | string | Scope |
| 8 | National CRS EPSG | `deployment.national_crs.epsg` | integer | Scope |
| 9 | Projected CRS name | `deployment.projected_crs.name` | string | Scope, App.B |
| 10 | Projected CRS EPSG | `deployment.projected_crs.epsg` | integer | Scope, App.B |
| 11 | CRS preamble text | `deployment.snippet_crs_preamble` | string (file path) | Scope |
| 12 | QGIS setup note | `deployment.snippet_qgis_note` | string (file path) | App.B |
| 13 | Footer changelog | `deployment.footer.changelog` | string | Footer |
| 14 | Footer version | `deployment.footer.version` | string | Footer |

Note: 11 conceptual anchors expanded to 14 config keys because some anchors (bounds, CRS) decompose into multiple typed fields.

### deployment_config.json schema
```json
{
  "domain_name": "<deployment name>",
  "bounds": {
    "lat_min": "<float>",
    "lat_max": "<float>",
    "lon_min": "<float>",
    "lon_max": "<float>"
  },
  "bounds_label": "<human-readable bounds reference>",
  "latitude_label": "<latitude label for precision benchmark comments>",
  "file_bbox_example": "<example bbox for documentation>",
  "national_crs": {
    "name": "<national CRS name or null>",
    "epsg": "<EPSG code or null>"
  },
  "projected_crs": {
    "name": "<projected CRS name or null>",
    "epsg": "<EPSG code or null>"
  },
  "snippet_crs_preamble": "snippets/<domain>_crs_preamble.md",
  "snippet_qgis_note": "snippets/<domain>_qgis_note.md",
  "footer": {
    "version": "<semver>",
    "changelog": "<domain-specific changelog entry>"
  }
}
```

When `national_crs.name` is null, the template omits the national CRS sentence from the Scope Declaration. Snippet paths are resolved relative to the config file's directory.

## Ontology
| Term | Definition |
|------|-----------|
| **Domain anchor** | A hardcoded string or numeric value in the prompt document that varies per deployment geography |
| **Deployment config** | A JSON file containing all domain-specific values for one deployment (e.g., Hutabohu) |
| **Universal template** | The prompt .md file with all domain anchors replaced by `{{deployment.key}}` variables — the canonical source |
| **Render script** | `render_prompts.py` — reads a deployment config JSON + snippet files + the template, writes a domain-specific .md |
| **Generalization proof** | Render with a 2nd, previously unseen config → produces a valid complete prompt document with zero template changes |
| **Job manifest** | Agent 9's `{CONFIG_DIR}` template — distinct from `{{deployment.key}}` (single-brace vs double-brace) |
| **Deprecated** | The Morocco `.md` files are historical artifacts. They are not verification targets and have no corresponding config.

## Ontology Convergence
All terms stable across all 5 interview rounds. No redefinitions needed.

## Interview Transcript

**Q1 (Trace Lane 1):** Which of the 11 domain anchors should be parameterized?
→ **A:** Parameterize all 11. Maximal transplantability.

**Q2 (Trace Lane 2):** What form should the transplant mechanism take?
→ **A:** `deployment_config.json` + `{{var}}` syntax with a render script. Mirrors code's existing config pattern.

**Q3 (Trace Lane 3):** How should code-prompt divergences be resolved?
→ **A:** Code defaults → worldwide (EPSG:4326), prompts become language-agnostic. But implemented as follow-up — scope is prompts only.

**Q4 (Criteria):** What verification strategy proves correctness?
→ **A:** Round-trip: render with Morocco config → byte-identical diff against original. Render with Hutabohu config → match Sonnet's adaptation.

**Q5 (Constraints):** What is the scope boundary?
→ **A:** Prompts only (deployment_config.json + template + render script). Code fixes documented as follow-up.

**Q6 (Multi-line handling):** How should multi-line text blocks (crs_preamble, qgis_note) be handled?
→ **A:** External `.md` snippet files. `deployment_config.json` keeps only scalars. Long text blocks live in `experiment/config/snippets/{domain}_crs_preamble.md` and `{domain}_qgis_note.md`. Render script reads and injects them.

## Trace Findings
*From the deep-dive trace (Phase 3)*

**Most likely explanation:** The prompt document lacks a domain configuration layer because it was authored as a static design specification during Morocco reference implementation. The validation code naturally evolved configurable parameters (CLI flags, CSV loading), but the prompts — being a markdown file, not a template system — were never updated.

**Ranked hypotheses:**
1. **No domain config bridge (HIGH confidence):** Code has `--source-crs`, `--region-bounds`, CSV vocab loading, and Agent 9 job manifest uses `{CONFIG_DIR}` template variables — but no `deployment_config.json` and no `{{var}}` syntax in agent prompts.
2. **Domain parameters buried in prose (MEDIUM):** 11 anchors scattered as inline literals across 6 agents with no centralized config section.
3. **Prompt-code divergence (MEDIUM):** Code defaults EPSG:32629 vs prompts claiming EPSG:4326. English vs French keyword patterns. Surface-level discrepancies.

**Per-lane critical unknowns resolved:**
- Lane 1: All 11 anchors are FUNCTIONAL (decision: parameterize all)
- Lane 2: Minimal config schema = 14 JSON keys covering all anchors (decision: deployment_config.json)
- Lane 3: Code defaults → worldwide, language-agnostic (decision: yes, but as follow-up)

## Implementation Plan

### Step 1: Create `experiment/config/deployment_config.schema.json`
JSON Schema with all scalar keys (bounds, labels, CRS codes, snippet file paths, footer). Multi-line text blocks (`crs_preamble`, `qgis_note`) are stored as separate `.md` snippet files referenced by path in the config.

### Step 2: Create Hutabohu config + snippets (active reference deployment)
- `experiment/config/deployment_config.hutabohu.json` — bounds `[0.5, 1.0, 122.7, 123.2]`, "Gorontalo latitude", "Hutabohu - Limboto Barat FTTH telecom deployment", SRGI2013/EPSG:9470, DGN95 UTM 51N/EPSG:23871.
- `experiment/config/snippets/hutabohu_crs_preamble.md` — CRS preamble with Indonesia national CRS context.
- `experiment/config/snippets/hutabohu_qgis_note.md` — QGIS note with Gorontalo coordinates and EPSG:23871 reference.

### Step 3: Create `experiment/guide/GeoFormer_FiberHome_P2_AgentPrompts.template.md`
Build the **canonical universal template** as a clean generalization — NOT derived from any existing domain-specific .md file. The template preserves all domain-agnostic pipeline content: 9-agent architecture, paradigm gap analysis, bottleneck registry, FTTH schemas, topology rules, repair operations, quality metrics, verification rules, and the Agent 9 job manifest structure.

All 14 domain-variable anchors are represented as `{{deployment.key}}` (scalars) or `{{snippet:key}}` (multi-line blocks). The template contains ZERO hardcoded domain references — no Morocco, no Francophone, no region-specific values.

The double-brace `{{deployment.key}}` syntax avoids collision with Agent 9's single-brace `{CONFIG_DIR}` job manifest placeholders.

Key placeholders in the template:
- `{{deployment.domain_name}}` — deployment name (Scope Declaration, Agent 1)
- `{{deployment.bounds.lat_min}}`, `{{deployment.bounds.lat_max}}`, `{{deployment.bounds.lon_min}}`, `{{deployment.bounds.lon_max}}` — deployment bounds (Agent 2, 3)
- `{{deployment.bounds_label}}` — human-readable bounds reference (Agent 2, 3)
- `{{deployment.latitude_label}}` — latitude for precision benchmark comments (Agent 4, 8)
- `{{deployment.file_bbox_example}}` — example bbox for documentation (Agent 2)
- `{{deployment.national_crs.name}}`, `{{deployment.national_crs.epsg}}` — national CRS (Scope Declaration, Appendix B)
- `{{deployment.projected_crs.name}}`, `{{deployment.projected_crs.epsg}}` — projected CRS (Scope Declaration, Appendix B)
- `{{snippet:crs_preamble}}` — CRS preamble block, injected from snippet file
- `{{snippet:qgis_note}}` — QGIS setup note, injected from snippet file
- `{{deployment.footer.version}}`, `{{deployment.footer.changelog}}` — document footer

Conditional handling: when `deployment.national_crs.name` is null/empty, the template omits the national CRS sentence from the Scope Declaration. This is the only conditional logic in the template — everything else is direct substitution.

### Step 4: Create `experiment/render_prompts.py`
Single-file Python script using only stdlib. Usage:
```
python render_prompts.py --config experiment/config/deployment_config.hutabohu.json [--output path.md]
```
Reads the template, loads the config JSON, performs `{{deployment.key}}` → value substitution for scalars, handles the null-conditional for national CRS, and injects snippet file contents for `{{snippet:key}}` placeholders. Snippet paths are resolved relative to the config file's directory.

### Step 5: Verification
**Active deployment:**
```bash
python render_prompts.py --config experiment/config/deployment_config.hutabohu.json --output /tmp/rendered_hutabohu.md
diff experiment/guide/GeoFormer_FiberHome_Hutabohu_AgentPrompts.md /tmp/rendered_hutabohu.md
# Expected: only footer version line differs
```

**Generalization proof:**
Create a minimal 2nd config (e.g., Fortaleza, Brazil: bounds lat [-4,-3], lon [-39,-38]) with basic snippet files. Render succeeds with no template errors — proves the template is truly domain-agnostic and requires zero changes for new deployments.

**No Morocco verification.** The existing Morocco `.md` files in `experiment/guide/` are historical artifacts. No Morocco config is created. No Morocco round-trip verification is performed.

### Follow-up (documented, not implemented)
- Change `converter.py` default `--source-crs` from `EPSG:32629` to `EPSG:4326`
- Change `converter.py:1207` warning from "outside Morocco bounds" to "outside deployment region bounds"
- Add `deployment_config.language` key for keyword pattern switching (French/English/Bilingual)
- CI check: `render_prompts.py` runs on config changes, fails if output diverges from committed reference
