# Android 连接 CAD2GIS 服务器

Android 安装包是服务器客户端。手机负责登录、浏览图纸和查看审查地图；DWG 读取、
OSM 匹配、SVG 提取以及二次匹配由 Windows 或 Linux 服务端执行。

## 1. 准备服务端

在服务器上安装对应桌面包。管理员准备一个手机能够访问的域名、与域名匹配且被
Android 系统信任的 HTTPS 证书，以及一个至少 12 字符的服务密码。手机和服务器
可以位于同一局域网，不要求部署公共云。

Linux 示例，替换下列示例路径、域名和密码：

```sh
export CAD2GIS_APP_PASSWORD='替换为至少12字符的服务密码'
cad2gis-desktop --no-browser --host 0.0.0.0 --port 8765 \
  --public-host cad.example.com \
  --project-root '/srv/cad-projects' \
  --ssl-certfile '/etc/cad2gis/fullchain.pem' \
  --ssl-keyfile '/etc/cad2gis/privkey.pem'
```

Windows 示例：

```powershell
$env:CAD2GIS_APP_PASSWORD = '替换为至少12字符的服务密码'
cad2gis-desktop --no-browser --host 0.0.0.0 --port 8765 `
  --public-host cad.example.com `
  --project-root 'D:\CAD Projects' `
  --ssl-certfile 'D:\Certificates\fullchain.pem' `
  --ssl-keyfile 'D:\Certificates\privkey.pem'
```

服务只允许访问 `--project-root` 指定的目录；需要多个目录时重复此参数。
`--public-host` 必须与手机地址中的主机名一致。网络和防火墙由服务器管理员配置，
安装程序不会自动开放端口。服务端从启动环境读取密码，Android 客户端不另存密码。

## 2. 安装并连接手机

1. 安装 `CAD2GIS-0.4.0-android-dev.apk`，按 Android 提示允许所用文件管理器安装应用。
2. 打开 CAD2GIS，点“服务器”，填写 `https://cad.example.com:8765`；替换为实际地址。
3. 使用服务密码登录。登录后可浏览服务器项目、打开转换结果和审查地图。

客户端要求 Android 8.0 或更新版本，以及 Android System WebView 83 或更新版本。
版本过旧时按应用提示更新 WebView。开发 APK 使用测试签名，后续更换正式签名时
不能直接覆盖安装旧签名版本。

## 3. 使用匹配结果

服务端沿用现有 DWG 输入引擎。DXF 请先在 CAD 软件中另存为 DWG。标准转换生成
OSM 相对匹配结果和 SVG 候选；审查页中的人工控制点先用于预览，确认二次匹配后
才生成新的 GIS 结果，原结果继续保留。可直接查看 SVG 对应复核报告。

未建立审核配置的新项目，可以由已配置的 AI 提供者或现有智能体插件协助建项；
缺少审核证据时，程序会保留草稿并说明未完成的原因。

## 连接异常

- **地址不被接受**：使用 HTTPS 服务根地址；地址中不要带账号、密码或其他网站的路径。
- **证书被拒绝**：确认域名、有效期和证书链。客户端不跳过证书错误，只信任系统证书。
- **已登录但没有图纸**：把 DWG 和项目目录放入管理员授权的服务器目录。手机文件导入尚未完成实际上传到服务器的端到端验收，优先使用服务器已有文件。
- **看不到在线底图**：地图渲染代码随服务安装；OSM 瓦片和道路查询仍需要服务端/手机相应网络可达。
- **切换服务器后要求重新登录**：这是正常行为；客户端清除旧服务器的登录状态与页面。

服务端和手机的实际验收范围见 [安装验证记录](INSTALLATION_VERIFICATION.md)。
