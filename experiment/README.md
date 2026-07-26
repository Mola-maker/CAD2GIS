# APD Hutabohu compatibility project pack

此目录只保存 APD Hutabohu 的真实 DWG 和与该源 SHA-256 绑定的 reviewed
配置，用于兼容性转换：

- `APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`
- `config/apd_source_profile.json`
- `config/apd_mapping_registry.json`
- `config/apd_gcp_profile.json`

源 DWG SHA-256：

```text
557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557
```

这里不再保存 Python 实现副本、测试、缓存或历史 run。生产实现只位于
`src/cad2gis/`，所有自动化测试只位于 `tests/`，运行输出应写到仓库外或新的
显式 run directory。

```powershell
cad2gis validate --project experiment --json
cad2gis convert `
  "experiment\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" `
  --run-dir "<NEW_RUN_DIR>" `
  --project experiment `
  --json
```

这些配置只能用于上述 source hash，不能作为其他 CAD 的模板。当前 GCP profile
没有 surveyed controls，因此名义 CRS 转换可复现，但绝对地面精度仍是
`not_verified`。
