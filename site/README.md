# Official website source

`official/` contains only the production subtree from the owner-supplied
`07_Pages网页.zip`: `tencent-deploy-20260912/publish/`. The archived index matched
https://cad2gis.molamaker.cn/ byte for byte on 2026-10-13. Backups, deployment
scripts and other archive folders are not imported.

`source-manifest.json` binds each original file by SHA-256. `.gitattributes`
retains exact bytes on all platforms. Fonts/CSS and the interactive report are
preserved. Historical experiments, screenshots, derived maps and QGIS project
examples retain their stated provenance; they do not gain new accuracy claims.
No source DWG/DXF or additional nine-drawing report ZIP is included.

Build both hosts with `python scripts/build_pages.py --output _site`. The builder
adds current bilingual Wiki pages from `docs/wiki`, release navigation, font
notices and search metadata. Canonical URLs always follow the official domain;
relative navigation also works under the GitHub `/CAD2GIS/` path. The selected
preview at http://127.0.0.1:18868/ is this build.

The separate CLI/local review demo is built by `scripts/build_local_demo.py`.
It is not the source of the public Pages website.
