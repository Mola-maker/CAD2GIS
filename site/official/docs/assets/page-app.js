/* ═══ 子页 4 · 软件界面 · 四屏 shell 重绘 + 证据画廊 ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;
  const NS = "http://www.w3.org/2000/svg";
  const mk = (t, a, p) => { const n = document.createElementNS(NS, t); Object.entries(a).forEach(([k, v]) => n.setAttribute(k, v)); p.appendChild(n); return n; };

  /* ── §1 四屏重绘 ── */
  const host = document.getElementById("app-fig");
  if (host) {
    const body = D.frame(host, {
      title: "/_app/ shell 四屏重绘（HTML/CSS，非截图）",
      sub: "HOW TO READ · 上方切屏 · 每屏下方 ◆ 为该屏的设计原则 · 界面元素按 Framework7 风格工具栏重绘，配色沿用报告设计系统",
      src: "APPLICATION_ARCHITECTURE / UI_SEMANTIC_UPGRADE / MOTION_UPGRADE（仓库 /_app/ shell 重绘，非像素级复刻）",
    });

    const screens = ["登录", "项目面板", "语义提取任务", "审查工作台"];
    const chips = D.el("div", "stage-ctl");
    body.appendChild(chips);
    const frame = D.el("div", "appframe");
    const bar = D.el("div", "af-bar", "<i></i><i></i><i></i><span>CAD2GIS — /_app/ · 桌面与 Android WebView 同一 shell</span>");
    const afBody = D.el("div", "af-body");
    frame.append(bar, afBody);
    body.appendChild(frame);
    const principle = D.el("p", "af-principle");
    body.appendChild(principle);

    function toolbar(title, sub) {
      return `<div class="af-toolbar"><span class="af-t">${title}</span><span class="af-sub">${sub}</span></div>`;
    }

    /* 屏 1 · 登录 */
    function screenLogin() {
      afBody.innerHTML =
        toolbar("CAD2GIS", "本地服务 · local service") +
        `<div class="af-card" style="max-width:420px;margin:0 auto">
          <p style="font-weight:700;font-size:15px;margin:0 0 4px">登录到本地服务</p>
          <p style="font-size:12px;color:var(--ink-md);margin:0 0 12px">启动本地服务后，系统浏览器会打开带随机启动凭证的专用登录链接。</p>
          <div class="af-field">服务器地址 · http://127.0.0.1:8765（仅监听 loopback）</div>
          <div class="af-field">服务密码 · ≥12 字符（远程访问需 TLS + 服务密码）</div>
          <button class="af-btn" style="width:100%">登 录</button>
        </div>
        <p style="font-family:var(--mono);font-size:10px;color:var(--ink-lo);text-align:center;margin-top:12px">仅监听 127.0.0.1 · 随机启动凭证 · TLS + ≥12 字符服务密码</p>`;
      principle.textContent = "loopback 默认：不指明主机就不出网卡；远程访问必须 TLS + 服务密码，证书错误不跳过。";
    }

    /* 屏 2 · 项目面板 */
    function screenProjects() {
      afBody.innerHTML =
        toolbar("项目", "projects · project-root 授权目录内") +
        `<div class="af-card">
          <div class="af-list-item"><span>APD · MANADO–TOMOHON UPLINK</span><span class="mono">EPSG:3857 · 3 分区</span></div>
          <div class="af-list-item"><span>APD · LAMTEH DAYAH ACEH</span><span class="mono">EPSG:23846</span></div>
          <div class="af-list-item" style="border-bottom:0"><span>APD · TAIPA RW 05 PALU</span><span class="mono">673 要素</span></div>
        </div>
        <div class="af-card">
          <p style="font-weight:700;font-size:14px;margin:0 0 10px">提取语义</p>
          <div class="af-field">选择 DWG 文件 · 拖入或点击选择</div>
          <div class="af-field">新输出目录 · 不复用已有 run 目录</div>
          <button class="af-btn">开始提取</button>
          <button class="af-btn ghost" style="margin-left:8px">打开项目</button>
          <p style="font-size:12px;color:var(--ink-md);margin:10px 0 0">只需一个 DWG + 一个新输出目录；<b>不要求 AI provider</b>。</p>
        </div>`;
      principle.textContent = "project-root 授权：服务只访问 --project-root 指定目录；提取语义不依赖任何云端模型。";
    }

    /* 屏 3 · 语义提取任务 */
    function screenTask() {
      const stage = (name, cur, tot, done) =>
        `<div style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;font-size:13px">
            <span style="font-family:var(--mono);font-size:11.5px">${name}</span>
            <span style="font-family:var(--mono);font-size:11.5px;color:${done ? "var(--red)" : "var(--ink-md)"}">${cur}/${tot}${done ? " ✓" : ""}</span>
          </div>
          <div class="af-prog"><i style="width:${Math.round(cur / tot * 100)}%"></i></div>
        </div>`;
      afBody.innerHTML =
        toolbar("语义提取任务", "单一监督任务 · supervised run") +
        `<div class="af-card">
          ${stage("stage 1 · export-source", 82, 82, true)}
          ${stage("stage 2 · semantic prepare", 82, 82, true)}
          ${stage("stage 3 · 绑定校验", 82, 82, true)}
          <p style="font-size:12px;color:var(--ink-md);margin:6px 0 0">按阶段计数，<b>不虚构百分比</b>：每个阶段只报告已完成/总数。</p>
        </div>
        <div class="af-card" style="display:flex;align-items:center;gap:14px">
          <button class="af-btn">查看结果 · 82/82</button>
          <p style="font-size:12px;color:var(--ink-md);margin:0">按钮动效按 82/82 完成态播完；molamaker 六状态精灵对应 idle / export / prepare / validate / done / error。</p>
        </div>`;
      principle.textContent = "单一监督任务：一次只跑一个重任务，三阶段计数全程可见，失败停在本阶段不假装推进。";
    }

    /* 屏 4 · 审查工作台 */
    let partition = "MAIN";
    function miniCAD(svg, seed) {
      mk("rect", { x: 0, y: 0, width: 300, height: 200, fill: "#ffffff" }, svg);
      for (let x = 0; x <= 300; x += 30) mk("line", { x1: x, y1: 0, x2: x, y2: 200, stroke: "#eef1f6", "stroke-width": 1 }, svg);
      for (let y = 0; y <= 200; y += 30) mk("line", { x1: 0, y1: y, x2: 300, y2: y, stroke: "#eef1f6", "stroke-width": 1 }, svg);
      const off = seed * 7;
      mk("polyline", { points: `18,${140 + off % 12} 90,${120 - off % 9} 165,${128 + off % 7} 235,${104 - off % 8} 288,${112 + off % 6}`, fill: "none", stroke: "#2251ff", "stroke-width": 2.2, "stroke-linejoin": "round" }, svg);
      mk("polyline", { points: `26,${66 + off % 10} 120,${58 - off % 6} 212,${70 + off % 8} 284,${54 - off % 5}`, fill: "none", stroke: "#42566a", "stroke-width": 1.1 }, svg);
      [[60, 132], [170, 126], [250, 106]].forEach(p => mk("circle", { cx: p[0], cy: p[1] + off % 5, r: 4.5, fill: "none", stroke: "#2251ff", "stroke-width": 1.6 }, svg));
    }
    function miniMap(svg, seed) {
      mk("rect", { x: 0, y: 0, width: 300, height: 200, fill: "#f7f9fc" }, svg);
      const off = seed * 5;
      mk("polyline", { points: `8,${138 + off % 10} 92,${118 - off % 8} 168,${126 + off % 6} 238,${102 - off % 7} 296,${110 + off % 5}`, fill: "none", stroke: "#8595a6", "stroke-width": 5, "stroke-linejoin": "round", opacity: .5 }, svg);
      mk("polyline", { points: `0,${80 + off % 9} 100,${74 - off % 5} 200,${86 + off % 7} 300,${70 - off % 4}`, fill: "none", stroke: "#8595a6", "stroke-width": 3, opacity: .4 }, svg);
      mk("polyline", { points: `${150 + off},0 ${156 - off % 6},70 ${146 + off % 8},200`, fill: "none", stroke: "#8595a6", "stroke-width": 3, opacity: .4 }, svg);
      mk("circle", { cx: 168, cy: 126, r: 6, fill: "rgba(34,81,255,.18)", stroke: "#2251ff", "stroke-width": 2 }, svg);
    }
    function screenReview() {
      const seeds = { MAIN: 0, EMR28560: 1, EMR29619: 2 };
      const parts = ["MAIN", "EMR28560", "EMR29619"];
      afBody.innerHTML =
        toolbar("审查工作台", "review · Manado 分区切换") +
        `<div class="stage-ctl" style="margin-bottom:12px">` +
        parts.map(p => `<button class="chip-btn${p === partition ? " on" : ""}" data-part="${p}">${p}</button>`).join("") +
        `<span class="stage-cap" style="margin-left:6px">分区控制点平移不应用到其他图框</span></div>
        <div class="af-split">
          <div class="af-pane"><span class="af-pane-lab">CAD 视图 · ${partition}</span></div>
          <div class="af-pane"><span class="af-pane-lab">地图视图 · OSM 底图对照</span></div>
        </div>
        <div style="display:flex;gap:10px;margin-top:14px;align-items:center;flex-wrap:wrap">
          <button class="af-btn">二次匹配并生成新结果</button>
          <span style="font-family:var(--mono);font-size:10px;color:var(--ink-lo)">冻结本轮人工点与匹配记录 → 正式转换 → 新目录不覆盖</span>
        </div>`;
      const panes = afBody.querySelectorAll(".af-pane");
      [miniCAD, miniMap].forEach((fn, i) => {
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("viewBox", "0 0 300 200");
        svg.style.cssText = "display:block;width:100%;height:auto";
        panes[i].appendChild(svg);
        fn(svg, seeds[partition]);
      });
      afBody.querySelectorAll("[data-part]").forEach(b => {
        b.onclick = () => { partition = b.dataset.part; screenReview(); };
      });
      principle.textContent = "≥44px 触摸目标；桌面与 Android WebView 同一 shell——手机与桌面不会维护两套配准算法。";
    }

    const SCREENS = [screenLogin, screenProjects, screenTask, screenReview];
    function select(i) {
      Array.from(chips.children).forEach((b, j) => b.classList.toggle("on", j === i));
      SCREENS[i]();
    }
    screens.forEach((s, i) => {
      const b = D.el("button", "chip-btn", `屏${i + 1} · ${s}`);
      b.onclick = () => select(i);
      chips.appendChild(b);
    });
    select(0);
  }

  /* ── §3 真实运行证据画廊 ── */
  const evHost = document.getElementById("ev-fig");
  if (evHost) {
    const body = D.frame(evHost, {
      title: "真实运行证据 · 首开验证 + 用户截图",
      sub: "HOW TO READ · 上排四张 = 独立 QGIS 进程首开验证（first-open）· 下排两张 = 用户回传 QGIS 截图 · 点击放大",
      src: "assets/img/first-open/ 与 assets/img/qgis/（与子页 1、2 复用文件，不重复拷贝）",
    });
    const gal = D.el("div", "gal");
    const items = [
      { src: "assets/img/first-open/drawing-03.jpg", cap: "drawing-03 · MANADO–TOMOHON UPLINK 首开", sub: "独立 QGIS 进程首开验证 · svg-review drawing-03/delivery/verification/first-open.png" },
      { src: "assets/img/first-open/drawing-03-emr29619.jpg", cap: "drawing-03-emr29619 · 分区首开", sub: "Manado EMR29619 分区独立首开验证" },
      { src: "assets/img/first-open/drawing-01.jpg", cap: "drawing-01 · LAMTEH - SF 首开", sub: "独立 QGIS 进程首开验证 · drawing-01" },
      { src: "assets/img/first-open/drawing-02.jpg", cap: "drawing-02 · LAMTEH 首开", sub: "独立 QGIS 进程首开验证 · drawing-02" },
      { src: "assets/img/qgis/hutabohu-1.jpg", cap: "hutabohu-1 · 用户截图", sub: "CAD2GIS OSM review — nominal CAD registration · 用户回传" },
      { src: "assets/img/qgis/kletek-1.jpg", cap: "kletek-1 · 用户截图", sub: "CAD2GIS OSM review — nominal CAD registration · 用户回传" },
    ];
    items.forEach(it => {
      const fig = D.el("figure");
      const img = D.lazyImg(D.el("img"));
      img.src = it.src; img.alt = it.cap;
      fig.appendChild(img);
      fig.appendChild(D.el("figcaption", "", it.cap));
      fig.onclick = e => D.showDrill({
        title: it.cap, sub: it.sub,
        source: "真实交付证据（压缩副本）", x: e.clientX, y: e.clientY,
      });
      fig.setAttribute("data-drill-keep", "");
      gal.appendChild(fig);
    });
    body.appendChild(gal);
  }

  D.init({
    page: "app",
    footerSrc: "来源 · APPLICATION_ARCHITECTURE / UI_SEMANTIC_UPGRADE / MOTION_UPGRADE / PLATFORM_DELIVERY_ACCEPTANCE（K23 字节数与签名声明）· 界面为 HTML/CSS 重绘，非截图",
  });
})();
