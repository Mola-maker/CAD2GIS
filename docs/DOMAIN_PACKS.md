# Domain packs

A domain pack is the delivery vocabulary a conversion targets: which delivery
layers exist and in what order, which class carries network routes, which
classes are supports, boxes and sites, and which generic semantic roles a
committed semantic revision may confirm.

`src/cad2gis/cad2gis_v3/domain_pack.py` defines `DomainPack` and the only pack
today, `FTTH_APD` (FiberHome FTTH As-Plan-Drawing, eight-layer GeoPackage
contract). The `--domain` modes `auto`, `generic` and `ftth_apd` all resolve to
it; the requested mode is still recorded separately in the run manifest, so a
run never claims a generic delivery.

## What reads the pack

| Module | Uses |
| --- | --- |
| `warehouse.py` | delivery layer order, optional layers and their insertion point |
| `topology.py` | support / box / site / route classes for asset collocation and route endpoints |
| `pipeline.py` | route class, route-endpoint support classes, passive support class, GCP coverage classes |
| `semantic_delivery.py` | role → class compatibility for committed semantic revisions |

`tests/test_domain_pack.py` pins every value to the reviewed FTTH contract, so
the pack cannot drift without a failing test.

## Still FTTH-specific (not behind the pack yet)

- `semantics.py`: classification rules (frame-derived BOITE admission, red ZNRO
  boundaries, sling-wire and leader-line filters, …).
- `schema_config.py`: field schemas and domain values of the eight layers.
- `styles.py`: symbology per layer.
- `onboarding.py`: the point classes offered to AI onboarding.

These are the next candidates. Moving them is only worth doing together with a
second real domain (for example power distribution or water networks) and a
reviewed truth set for it; without one, a generic abstraction cannot be
validated.

## Adding a pack

1. Create a `DomainPack` with its own layers, route/support/box/site classes,
   coverage classes and role mapping, and register it in `_PACKS`.
2. Provide the matching field schema and styles (today these are FTTH modules).
3. Thread the selected pack from `ConversionRequest.domain` into the phase
   functions of `cad2gis_v3.pipeline.convert` instead of the `FTTH_APD` default.
4. Prove the FTTH baselines unchanged with `tools/verify_delivery_equivalence.py`.
