# CAD2GIS Linux — Portable FTTH DWG-to-GeoPackage Converter

Decoupled CAD-to-GIS converter for FTTH network drawings. Extracts 8 feature classes
(BOITE, CABLE, PTECH, INFRASTRUCTURE, SITE, ZNRO, ZPM, IMB) from DWG files into
a single GeoPackage with QGIS styling.

## Install

```bash
# GDAL
pip install gdal

# LibreDWG (two options)

# A. System install
git clone https://git.savannah.gnu.org/git/libredwg.git
cd libredwg && ./autogen.sh && ./configure && make && sudo make install

# B. Local build (no root)
./configure --prefix=$HOME/.local && make && make install
export LIBREDWG_SO=$HOME/.local/lib/libredwg.so
export LIBREDWG_PYTHON=$(python3 -c "import site; print(site.getusersitepackages())")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIBREDWG_SO` | auto-search | Path to libredwg.so |
| `LIBREDWG_PYTHON` | auto-search | Parent directory of LibreDWG SWIG package |
| `DWGREAD_BIN` | `dwgread` | dwgread CLI for layout_miner JSON export (optional) |

`LIBREDWG_SO` search order: `$LIBREDWG_SO` → `/usr/local/lib/libredwg.so` → `/usr/lib/libredwg.so` → `./libredwg.so`

## Quick Start

```bash
python3 -m python.ftth_converter \
  --input <file.dwg> \
  --output <output.gpkg> \
  --config config/<project>.json \
  --source-crs EPSG:3857 --target-crs EPSG:3857
```

Or use the project pipeline:

```bash
python3 -m python.project_pipeline \
  --project aceh_main \
  --input "APD - KELURAHAN LAMTEH DAYAH ACEH.dwg"
```

## Project Configs

| Config | Project |
|--------|---------|
| `config/hutabohu.json` | APD DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO |
| `config/aceh_main.json` | APD KELURAHAN LAMTEH DAYAH ACEH (main) |
| `config/aceh_sf.json` | APD KELURAHAN LAMTEH DAYAH ACEH (SF, independent) |

## Architecture

```
python/
  cad_common.py        L1-L2: DWG read + geometry + CRS (portable)
  ftth_converter.py    L3-L5: FTTH classification + label binding + GPKG write
  topology_repair.py   CABLE chaining + endpoint snapping + FDT domain tagging
  style_exporter.py    QML sidecar + layer_styles + .qgz project
  schema_config.py     FTTH layer patterns (defaults)
  domain_vocab.py      Domain vocabulary (hardcoded, no external files)
  layout_miner.py      Paper-space layout mining
  legend_detector.py   Model-space non-subject cluster detection
  georeference.py      Helmert transform for local engineering grids
  project_pipeline.py  Per-project orchestrator
  convert_all.py       Legacy 4-stage orchestrator
  evaluator.py         Quality verification engine
  evidence_ledger.py   Conservation ledger (component D)
```

## Evaluation Standards

`evaluation_standards/APD/` — As Plan Drawing type (Indonesian FTTH spec, currently using Moroccan standards):

- BOITE.csv, CABLE.csv, INFRASTRUCTURE.csv, PTECH.csv, SITE.csv, ZNRO.csv, ZPM.csv
- VERIFICATION_RULE.csv

## Known Issues

- TEXT entity text extraction via LibreDWG dynapi returns empty for some DWG versions
  (R2018 TEXT_r11 entities). This blocks annotation-based label binding.
- Georeference module (Helmert transform) requires manual GCP input from paper layouts.
- CABLE topology correction is deferred.
