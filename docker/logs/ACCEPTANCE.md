# cad2gis-ftth:0.1.0 全量验收（10 站）

转换命令：`docker run --rm --user 1000:1000 -e HOME=/tmp -v /tmp/cad2gis_out:/out cad2gis-ftth:0.1.0`

| site | BOITE | CABLE* | EMR | IMB | INFRA | PTECH | SITE | ZNRO | ZPM | non-CABLE census OK |
|---|---|---|---|---|---|---|---|---|---|---|
| hutabohu | 43 | 179 | 0 | 682 | 31 | 167 | 2 | 3 | 43 | True |
| lamteh_main | 38 | 237 | 0 | 423 | 53 | 208 | 2 | 1 | 38 | True |
| lamteh_sf | 2 | 25 | 0 | 0 | 1 | 26 | 2 | 0 | 0 | True |
| kletek | 9 | 33 | 0 | 167 | 9 | 33 | 1 | 2 | 9 | True |
| semarang_sf | 0 | 18 | 0 | 0 | 1 | 19 | 1 | 0 | 0 | True |
| darat_sekip_sf | 1 | 12 | 0 | 0 | 1 | 13 | 1 | 0 | 0 | True |
| manado-tomohon_uplink | 16 | 62 | 2 | 243 | 16 | 61 | 1 | 1 | 15 | True |
| taipa | 26 | 118 | 0 | 369 | 17 | 112 | 1 | 4 | 26 | True |
| tinggar | 31 | 119 | 0 | 435 | 17 | 109 | 2 | 2 | 0 | True |
| tinggede | 22 | 65 | 0 | 337 | 16 | 51 | 1 | 1 | 22 | True |

* CABLE 口径：`source_profile.json` 的 `CABLE` 是语义正线缆路由数（与 `INFRASTRUCTURE` 层计数相同）；`delivery.gpkg` 的 `CABLE` 层是物化后的逐段线缆几何，因此计数更大。
* 除 CABLE 口径差异外，10 站 combined `delivery.gpkg` 所有正数 feature class 计数与 `source_profile.json` 全部一致；manado 主包 + EMR28560/EMR29619 分区合并后 `EMR=2`。
* 10 站输出与工作区 `baselines/<site>/run/delivery.gpkg`（8 月 24 日 robustness 基线）逐图层逐计数完全一致。
