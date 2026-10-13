"""Current-code copy layered onto the owner's retained official website design."""

from __future__ import annotations
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _constant(filename: str, name: str):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset"
            ):
                value = value.args[0]
            return ast.literal_eval(value)
    raise ValueError(f"Missing code contract: {name}")


def current_homepage(page: str) -> str:
    version = _constant("src/cad2gis/contracts.py", "PLUGIN_CONTRACT_VERSION")
    core = _constant("src/cad2gis/contracts.py", "MCP_TOOL_NAMES")
    cad = _constant("src/cad2gis/cad_bundle_policy.py", "ALLOWED_TOOLS")
    count, optional = len(core), len(cad)
    title = f"从图纸到证据：CAD2GIS {version} · MCP 与 GIS 交付"
    page = re.sub(r"<title>.*?</title>", "<title>" + title + "</title>", page, count=1)
    page = re.sub(
        r'(<meta name="description" content=")[^"]*(">)',
        r"\g<1>"
        + f"CAD2GIS {version}：DWG/DXF 转 GIS 与 QGIS 交付，{count} 个本地 MCP 接口、{optional} 个可选 CAD 工具、Apache-2.0 与来源证据。"
        + r"\g<2>",
        page,
        count=1,
    )
    page = re.sub(
        r'(<meta property="og:title" content=")[^"]*(">)',
        r"\g<1>" + title + r"\g<2>",
        page,
        count=1,
    )
    page = re.sub(
        r'(<meta property="og:description" content=")[^"]*(">)',
        r"\g<1>"
        + f"CAD2GIS {version}: {count} local MCP interfaces, {optional} optional CAD tools, GIS delivery with validation evidence."
        + r"\g<2>",
        page,
        count=1,
    )
    updates = {
        "cover-kicker": f"CAD2GIS / {version} · CURRENT CODE · 2026-10-13 · APACHE-2.0",
        "cover-title": '从图纸到证据：<br><span class="cover-num">CAD2GIS</span> 的 MCP 与 GIS 交付',
        "cover-sub": "DWG / DXF → GIS · Source-preserving CAD tools for AI agents",
        "cover-lede": f"把图纸整理成可以审查和交接的 GIS 成果。{count} 个本地 MCP 接口连接图纸理解、转换、检查与 QGIS 交付；独立 CAD 旁路提供 {optional} 个已验证二维工具，修改副本后回到统一验收链路。",
        "cover-anchor": f"当前版本 {version} · 工具数量来自 contracts.py 与 cad_bundle_policy.py · 合成 DXF 实调验证 · 九图展示仍为 CONDITIONAL，绝对精度未独立验收。",
    }
    for css, text in updates.items():
        page = re.sub(
            r'(<(?:p|h1) class="' + css + r'">).*?(</(?:p|h1)>)',
            lambda m: m[1] + text + m[2],
            page,
            count=1,
            flags=re.S,
        )
    chips = [
        ("current-value", "项目价值"),
        ("current-tools", "MCP 与 CAD"),
        ("current-flow", "工作流程"),
        ("current-evidence", "验证范围"),
        ("current-install", "安装与许可"),
        ("current-gallery", "九图效果"),
        ("current-history", "历史证据"),
    ]
    page = re.sub(
        r'(<div class="cover-chips">).*?(</div>)',
        lambda m: m[1]
        + "".join(
            f'<button class="chip" data-goto="#{target}">{label}</button>'
            for target, label in chips
        )
        + m[2],
        page,
        count=1,
        flags=re.S,
    )
    tools = " · ".join("<code>" + name + "</code>" for name in sorted(cad))
    content = f"""
<section id="current-value" class="band"><div class="prose"><p class="sec-no">01 · CURRENT VALUE</p><h2>把图纸、修订与交付放进一条可以复查的流程</h2><p class="dek">保留原始文件，让智能体调用实际工具，并给审查者留下来源与检查结果。</p><p>CAD2GIS 支持 DWG/DXF 图纸理解、GIS 图层转换、工程检查、审查迭代与 QGIS 交付。输入 SHA-256、运行状态和来源绑定帮助团队判断一份成果来自哪里、经历了什么处理、还需要解决哪些问题。</p><p>MCP 提供本地工具接口；Codex/Claude Code 插件提供配置、技能和任务流程，Cursor/VS Code 也可使用标准 MCP 配置。用户选择授权项目根目录，智能体在该范围内工作。首次安装与某些 OSM 流程需要联网。</p></div></section>
<section id="current-tools" class="band"><div class="prose"><p class="sec-no">02 · MCP & CAD TOOLS</p><h2>{count} 个 MCP 接口，{optional} 个已验证可选 CAD 工具</h2><p>当前代码注册 {count} 个 MCP 接口，覆盖能力发现、图纸读取、语义工作流、转换、检查、审查与交付。这个数量包含四个 CAD 包装接口；{optional} 个旁路工具是包装器中的可选能力，不能把两个数字相加为独立 MCP 接口数量。</p><p><code>get_cad_bundle_status</code> 检查独立运行时；<code>prepare_cad_bundle_workspace</code> 准备原图保护的 DXF 副本；<code>list_cad_bundle_tools</code> 获取实际工具与参数 schema；<code>call_cad_bundle_tool</code> 查询或编辑副本。返回的 <code>edited_source</code> 再进入 CAD2GIS 转换流程。</p><p>旁路使用 MIT 许可的 U-C4N/Autocad-MCP v1.6.0 固定源码修订，采用独立 Python 环境与 ezdxf 后端。当前开放以下工具：</p><p>{tools}</p><p>能力包括结构理解、拓扑/差异检查、图层和实体查询、二维创建与删除。AutoCAD COM、3D 编辑和上游全部工具未纳入当前已验证适配范围。</p><p><a href="wiki/index.html">中文能力说明 →</a> · <a href="wiki/en.html">English guide →</a></p></div></section>
<section id="current-flow" class="band"><div class="prose"><p class="sec-no">03 · WORKFLOW</p><h2>理解 → 修订副本 → 转换 → 检查 → 交付</h2><ol><li>指定项目根目录，检查运行环境并理解图层与实体。</li><li>保留原图；在独立工作区内选择允许的 CAD 工具，执行二维修订并记录版本。</li><li>读取返回的 edited_source，inspect 并 bootstrap 新项目，核对单位、语义和坐标声明。</li><li>执行 CAD2GIS 转换与检查，审查图层、连接关系、警告和运行状态。</li><li>生成 GIS/QGIS 交付与来源证据；需要绝对精度保证时补充权威控制资料和独立检查点验收。</li></ol><p>示例任务：“保留原图，检查光纤线与端点连接，把修订 DXF 转成 GeoPackage，并列出来源哈希、检查状态和人工待办。”</p></div></section>
<section id="current-evidence" class="band"><div class="prose"><p class="sec-no">04 · VALIDATION SCOPE</p><h2>工具实调和回归证据，配合明确的精度状态</h2><p>0.8.0 核心与 Agent 修复后的 Linux / Python 3.12 全套基线：<b>1661 passed / 19 skipped</b>。对应主分支 CI 已在 Linux、Windows、macOS 与离线精度门禁通过；网页更新另行运行构建、路径、文案与浏览器检查。</p><p>{optional} 个旁路工具均在合成 DXF 上实际调用成功，覆盖原图不变、查询/编辑、修订差异和返回 GIS 转换。它们未因此获得对所有客户 DWG、AutoCAD COM 或 3D 对象的保证。</p><p>九图历史效果仍为 <code>CONDITIONAL</code>，<code>absolute_accuracy_verified=false</code>。OSM 对齐、长度闭合或视觉效果不能替代权威控制点与独立绝对精度验收。</p></div></section>
<section id="current-install" class="band"><div class="prose"><p class="sec-no">05 · INSTALL & LICENSE</p><h2>Apache-2.0 原创代码，可在多个智能体客户端使用</h2><p>Python 3.12 与 uv 安装当前 Agent：</p><pre><code>uv tool install --python 3.12 "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/releases/download/v{version}/cad2gis-{version}-py3-none-any.whl"
cad2gis-agent-mcp --help
cad2gis cad-bundle status --json</code></pre><p>可选 CAD 旁路单独安装：<code>cad2gis cad-bundle install --prefix &lt;NEW_RUNTIME_PREFIX&gt; --json</code>。将安装输出的可执行文件路径配置到 <code>CAD2GIS_CAD_BUNDLE_COMMAND</code>，再启动 MCP 客户端。DWG 仍需要 LibreDWG；GIS 转换需要 GIS 运行时。</p><p>Apache-2.0 允许原创代码的商业使用、修改和再分发；LICENSE、NOTICE 与第三方组件、字体和数据的原许可仍需保留。历史桌面安装包保留原版本，未改名为 {version}。</p><p><a href="https://github.com/Mola-maker/CAD2GIS/releases/tag/v{version}">下载 {version} Agent →</a> · <a href="https://github.com/Mola-maker/CAD2GIS/wiki">GitHub 双语 Wiki →</a> · <a href="https://smithery.ai/servers/chenlihuasb123/cad2gis-agent">Smithery →</a></p></div></section>
<section id="current-gallery" class="band"><div class="prose"><p class="sec-no">06 · NINE-DRAWING EFFECTS</p><h2>复用九图网页效果，直接查看转换与审查过程</h2><p>这套官网发布包已经包含九张主图及分区效果、QGIS 首开截图、SVG 提取画廊与地图预览。这里直接复用这些资源，不额外发布九图报告压缩包。</p><p><a href="docs/osm-gcp.html">图纸与配准效果 →</a> · <a href="docs/svg.html">SVG 提取画廊 →</a> · <a href="docs/map.html">地图预览 →</a></p><figure><img src="docs/assets/img/first-open/drawing-01.jpg" alt="九图历史展示中的 QGIS 首开效果，CONDITIONAL" loading="lazy" style="width:100%;height:auto"><figcaption>网页包已有的 QGIS 首开效果 · 历史演示 · CONDITIONAL</figcaption></figure></div></section>
<section id="current-history" class="band"><div class="prose"><p class="sec-no">07 · HISTORICAL EVIDENCE</p><h2>历史架构报告与实验来源</h2><p>下方保留原始报告的交互图表和来源链。它对应 2026-09 的 0.4.x–0.7.x 快照，旧测试数、代码体量、性能数据和平台安装包属于当时记录，不作为 {version} 的重新验证。当前能力以本页上方、双语 Wiki 与代码契约为准。</p></div></section>
"""
    page = page.replace(
        "<main>",
        "<main>"
        + content
        + '<details id="historical-report"><summary class="prose" style="cursor:pointer;padding:24px">展开历史架构与九图证据 · 2026-09 · 旧版实验来源</summary>',
        1,
    )
    page = page.replace("</main>", "</details></main>", 1)
    style = "<style>#current-install pre{overflow-x:auto;border:1px solid var(--line);padding:20px;line-height:1.7;white-space:pre}#current-install pre code{font-size:13px}#current-tools code{overflow-wrap:anywhere}#historical-report{border-top:1px solid var(--line)}#historical-report>summary{font-family:var(--mono);color:var(--red)}.prose ol{line-height:1.9}</style>"
    schema = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "CAD2GIS",
        "softwareVersion": version,
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Windows, macOS, Linux",
        "url": "https://cad2gis.molamaker.cn/",
        "license": "https://www.apache.org/licenses/LICENSE-2.0",
        "downloadUrl": f"https://github.com/Mola-maker/CAD2GIS/releases/tag/v{version}",
        "description": f"DWG/DXF to GIS, {count} local MCP interfaces and {optional} optional source-preserving CAD tools.",
    }
    metadata = '<script type="application/ld+json">' + json.dumps(schema) + "</script>"
    metadata += '<meta property="og:image" content="https://cad2gis.molamaker.cn/docs/assets/img/first-open/drawing-01.jpg">'
    return page.replace("</head>", style + metadata + "</head>", 1)
