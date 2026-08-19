# Glossary

Project-specific drawing-type and network terminology.  These definitions are
authoritative for docs, code comments, AI context injection and onboarding
prompts.

## Drawing-type acronyms

- **APD — As Plan Drawing**
  - A construction/civil-engineering drawing type that records the
    **as-planned** design, not the as-built survey of what was installed.
  - In this repository, files prefixed `APD -` are plan drawings for FTTH
    access-network designs.  APD does **not** mean “access point device”,
    “as-built drawing”, or any equipment class.
  - Operational consequence: APD geometry is the authoritative design plan.
    Conversion must not re-interpret plan geometry as surveyed construction
    state, and accuracy statements must keep “plan drawing” and
    “ground-verified” separate.

- **SF — Subfeeder**
  - In cable/optical access networks, a subfeeder is a secondary feed or
    distribution leg (副馈线 / 分支配电线).
  - A filename suffix such as `- SF` means that drawing shows the subfeeder
    portion of the network (e.g. `... LAMTEH DAYAH ACEH - SF.dwg`), not a
    “site facility”, “small form-factor”, or a separate project category.
  - `lamteh_main` and `lamteh_sf` are two drawings of the same plan family:
    the main drawing and its subfeeder drawing.

## Corpus split

- **Development / baseline set (4 DWGs, used to build and review the pipeline):**
  - `raw/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`
  - `raw/APD - KLETEK RW 05 SIDOARJO.dwg`
  - `raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg`
  - `raw/APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg`

- **Validation set (6 DWGs, held out):**
  - `raw/APD - BULU LOR RW 05 SEMARANG - SF.dwg`
  - `raw/APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg`
  - `raw/APD - MANADO- UPLINK_FWA_OLT_TOMOHON_TO_EMR- 46478_FO_24C.dwg`
  - `raw/APD - PERUMAHAN TINGGEDE VIEW PALU.dwg`
  - `raw/APD - TAIPA RW 05 PALU.dwg`
  - `raw/APD - TINGGAR RW 04 SERANG.dwg`

  Validation drawings must be processed as new source-bound projects: their
  own inventory, profile, mapping registry and review evidence.  They are
  never training material and must not receive baseline rules, counts or
  thresholds borrowed from the four development drawings.

## Related domain terms

- **FTTH** — Fibre To The Home.  The `ftth_apd` conversion domain is the
  FTTH As-Plan-Drawing corpus.
- **BOITE / SITE / PTECH** — delivery feature classes: distribution box,
  site enclosure, and pole/support tech.
