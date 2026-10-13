/* ═══ 子页 2 · SVG 提取与渲染 ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;
  const RM = D.RM;
  const DATA = window.DOCS_DATA || null;

  /* ── §1 流程步进 ── */
  const flowHost = document.getElementById("flow-fig");
  if (flowHost) {
    const body = D.frame(flowHost, {
      title: "SVG 候选提取流水线 · 七步",
      sub: "HOW TO READ · 点击步进按钮或步骤芯片 · 每步配真实产物名 · 候选与 GIS 完整生成后原子发布，提取失败不发布半成品 run",
      src: "docs/OPTIONAL_SYMBOL_ASSETS.md / docs/SVG_UPGRADE_EVIDENCE（K27）",
    });
    const STEPS = [
      { t: "DWG 实体读入", d: "INSERT 块引用 + 几何七类 LWPOLYLINE / POLYLINE / LINE / CIRCLE / ARC / ELLIPSE / SPLINE 全部展开。", a: "drawing.dwg" },
      { t: "源身份解析", d: "选择配置绑定实际源文件 SHA-256 与实际 handle；错误源 SHA、找不到的 handle、重复 ID 一律拒绝。", a: "selection.json" },
      { t: "提取", d: "按 selection 提取原图 SVG；缺字体环境可重复传 --svg-font-dir；无字体时拒绝提取含文字的符号。", a: "symbols/*.svg" },
      { t: "候选数据库", d: "SVG + SHA-256 + 原图 SHA + 实体 handle + 定义依赖与诊断入库，symbol_id 主键。", a: "symbols.sqlite3" },
      { t: "对应报告", d: "JSON 报告 + 并排复核页；dispositions 三态（LEGEND_MATCH_CANDIDATE / SOURCE_INSTANCE_ONLY / UNRESOLVED_NON_BLOCK）。", a: "correspondence.json / .html" },
      { t: "QML / QGZ 内嵌", d: "candidate 不自动应用到交付样式；人工绑定后 SVG 以 base64 内嵌进 QGZ，随包独立复验。", a: "*.qml / view-with-source-SVG.qgz" },
      { t: "QGIS 渲染", d: "独立 QGIS 进程首开验证：11 份 QGZ 全部在实际 QGIS 中打开并渲染（见 §4 画廊）。", a: "verification/first-open.png" },
    ];
    const chips = D.el("div", "flow-steps");
    body.appendChild(chips);
    const stage = D.el("div", "flow-stage");
    body.appendChild(stage);
    const ctl = D.el("div", "stage-ctl");
    const btnPrev = D.el("button", "", "← 上一步");
    const btnNext = D.el("button", "", "下一步 →");
    const btnAll = D.el("button", "", "▶ 自动播放");
    ctl.append(btnPrev, btnNext, btnAll);
    body.appendChild(ctl);

    let cur = 0, timer = 0;
    function render() {
      Array.from(chips.children).forEach((c, i) => c.classList.toggle("on", i === cur));
      const s = STEPS[cur];
      stage.innerHTML = `<div class="fs-no">STEP ${String(cur + 1).padStart(2, "0")} / 07</div>
        <div class="fs-title">${s.t}</div>
        <div class="fs-body">${s.d}</div>
        <span class="fs-art">${s.a}</span>`;
    }
    STEPS.forEach((s, i) => {
      const c = D.el("button", "chip-btn", `${i + 1} ${s.t}`);
      c.onclick = () => { stop(); cur = i; render(); };
      chips.appendChild(c);
    });
    function stop() { clearInterval(timer); timer = 0; btnAll.textContent = "▶ 自动播放"; }
    btnPrev.onclick = () => { stop(); cur = Math.max(0, cur - 1); render(); };
    btnNext.onclick = () => { stop(); cur = Math.min(STEPS.length - 1, cur + 1); render(); };
    btnAll.onclick = () => {
      if (timer) { stop(); return; }
      if (RM) { render(); return; }
      btnAll.textContent = "⏸ 停止";
      timer = setInterval(() => { cur = (cur + 1) % STEPS.length; render(); }, 1900);
    };
    render();
  }

  /* ── §2 759 SVG 画廊 ── */
  const svgHost = document.getElementById("svg-fig");
  let catalog = DATA ? DATA.catalog : null;
  if (svgHost && catalog) {
    const body = D.frame(svgHost, {
      title: "759 个真实提取 SVG · 全量画廊",
      sub: "HOW TO READ · 按图纸筛选 · 徽记 = 符号数 · 懒加载 · 点击开深色详情卡 · SVG 为透明底细线稿，格子纸底衬托",
      src: "svg-review/drawing-01..09/source-symbols/symbols/*.svg（真实 DWG 交付包，未修改）",
    });
    const filter = D.el("div", "svg-filter");
    body.appendChild(filter);
    const gal = D.el("div", "svg-gal");
    body.appendChild(gal);
    const ids = Object.keys(catalog).sort();
    const mkBtn = (id, label, count) => {
      const b = D.el("button", "chip-btn", `${label} <span class="svg-count">${count}</span>`);
      b.onclick = () => {
        Array.from(filter.children).forEach(x => x.classList.toggle("on", x === b));
        show(id);
      };
      filter.appendChild(b);
      return b;
    };
    const total = ids.reduce((s, id) => s + catalog[id].files.length, 0);
    const allBtn = mkBtn("all", "全部", total);
    ids.forEach(id => mkBtn(id, id, catalog[id].files.length));

    const PAGE = 120;
    let shown = 0, curFiles = [];
    function show(id) {
      gal.innerHTML = ""; shown = 0;
      curFiles = [];
      (id === "all" ? ids : [id]).forEach(d => catalog[d].files.forEach(f => curFiles.push([d, f])));
      more();
    }
    function more() {
      const slice = curFiles.slice(shown, shown + PAGE);
      slice.forEach(([d, f]) => {
        const cell = D.el("div", "svg-cell");
        const img = D.lazyImg(D.el("img"));
        img.src = `assets/svg-symbols/${d}/${f}`;
        img.alt = f;
        cell.appendChild(img);
        cell.setAttribute("data-drill-keep", "");
        cell.onclick = () => openModal(d, f);
        gal.appendChild(cell);
      });
      shown += slice.length;
    }
    gal.addEventListener("scroll", () => {}, { passive: true });
    // 滚动懒加载更多
    const io = new IntersectionObserver(es => {
      es.forEach(e => { if (e.isIntersecting && shown < curFiles.length) more(); });
    }, { rootMargin: "600px" });
    const sentinel = D.el("div");
    body.appendChild(sentinel);
    io.observe(sentinel);

    // 深色详情卡
    const modal = D.el("div"); modal.id = "svg-modal"; modal.hidden = true;
    document.body.appendChild(modal);
    function openModal(d, f) {
      const meta = catalog[d];
      modal.innerHTML = `<div class="sm-card">
        <button class="sm-close">✕</button>
        <div class="sm-art"><img src="assets/svg-symbols/${d}/${f}" alt="${f}"></div>
        <div class="sm-name">${f}</div>
        <div class="sm-meta">
          handle 文件名 · ${f.replace(/\.svg$/, "")}<br>
          所属图 · ${d} — ${meta.name}<br>
          ${meta.features} 要素 / ${meta.records.toLocaleString("en-US")} records / ${meta.crs}<br>
          该 SVG 来自真实 DWG 交付包 svg-review/${d}/source-symbols
        </div>
      </div>`;
      modal.hidden = false;
      modal.querySelector(".sm-close").onclick = () => modal.hidden = true;
      modal.onclick = e => { if (e.target === modal) modal.hidden = true; };
    }
    allBtn.classList.add("on");
    show("all");
  }

  /* ── §3 覆盖率对比 ── */
  const covHost = document.getElementById("cov-fig");
  if (covHost && DATA && DATA.coverage) {
    const body = D.frame(covHost, {
      title: "九图 correspondence dispositions 对比",
      sub: "HOW TO READ · 每行 = 一张图 · 电蓝 = LEGEND_MATCH_CANDIDATE · 灰 = SOURCE_INSTANCE_ONLY / UNRESOLVED_NON_BLOCK · 点击行下钻",
      src: "assets/svg-symbols/<drawing>/correspondence.json（dispositions 字段，真实文件）",
    });
    const tbl = D.el("table", "doc-table");
    tbl.innerHTML = `<thead><tr><th>图纸</th><th>图例候选</th><th>源实例</th><th>未解析</th><th style="width:38%">比例</th></tr></thead>`;
    const tb = D.el("tbody");
    tbl.appendChild(tb);
    Object.keys(DATA.coverage).sort().forEach(d => {
      const c = DATA.coverage[d];
      const inst = c.dispositions.SOURCE_INSTANCE_ONLY || 0;
      const un = c.dispositions.UNRESOLVED_NON_BLOCK || 0;
      const pct = v => (c.total ? (v / c.total * 100) : 0);
      const tr = D.el("tr");
      tr.setAttribute("data-drill-keep", "");
      tr.style.cursor = "pointer";
      tr.innerHTML = `<td><code>${d}</code></td><td><b>${c.legend_match}</b></td><td>${inst}</td><td>${un}</td>
        <td><div style="display:flex;height:9px;border-radius:99px;overflow:hidden;background:#eef1f6;margin-top:5px">
          <i style="display:block;width:${pct(c.legend_match)}%;background:#2251ff"></i>
          <i style="display:block;width:${pct(inst)}%;background:#8595a6"></i>
          <i style="display:block;width:${pct(un)}%;background:#dbe2ea"></i>
        </div></td>`;
      tr.onclick = e => D.showDrill({
        title: `${d} · dispositions`,
        value: `${c.legend_match}/${c.total}`,
        sub: `LEGEND_MATCH_CANDIDATE ${c.legend_match} · SOURCE_INSTANCE_ONLY ${inst} · UNRESOLVED_NON_BLOCK ${un}。${DATA.catalog[d].name}（${DATA.catalog[d].features} 要素 / ${DATA.catalog[d].records.toLocaleString("en-US")} records）。`,
        source: "correspondence.json · svg-review 真实交付",
        x: e.clientX, y: e.clientY,
      });
      tb.appendChild(tr);
    });
    body.appendChild(tbl);
  }

  /* ── §4 QGZ 内嵌证据表 + 首开画廊 ── */
  const qgzHost = document.getElementById("qgz-fig");
  if (qgzHost && DATA && DATA.qgz_base64) {
    const body = D.frame(qgzHost, {
      title: "view-with-source-SVG.qgz · base64 SVG 载荷计数",
      sub: "HOW TO READ · 对每个 QGZ 解包 .qgs，数 base64: 载荷 · taipa = 139 与 SVG 提取数一致，交叉验证通过",
      src: "assets_src/baselines 各项目 delivery/view-with-source-SVG.qgz（tools_docs_assets.py 实测）",
    });
    const tbl = D.el("table", "doc-table");
    tbl.innerHTML = `<thead><tr><th>项目</th><th>base64 SVG 数</th><th style="width:42%">占比（相对最多）</th></tr></thead>`;
    const tb = D.el("tbody");
    tbl.appendChild(tb);
    const max = Math.max(...Object.values(DATA.qgz_base64));
    Object.keys(DATA.qgz_base64).sort((a, b) => DATA.qgz_base64[b] - DATA.qgz_base64[a]).forEach(p => {
      const n = DATA.qgz_base64[p];
      const tr = D.el("tr");
      tr.innerHTML = `<td><code>${p}</code></td><td><b>${n}</b></td>
        <td><div style="height:9px;border-radius:99px;background:#eef1f6;margin-top:5px"><i style="display:block;height:100%;width:${(n / max * 100).toFixed(1)}%;background:#2251ff;border-radius:99px"></i></div></td>`;
      tb.appendChild(tr);
    });
    body.appendChild(tbl);
  }
  const foHost = document.getElementById("fo-fig");
  if (foHost && DATA && DATA.first_open) {
    const body = D.frame(foHost, {
      title: "独立 QGIS 进程首开验证 · 11 份 QGZ",
      sub: "HOW TO READ · 九张主图 + drawing-03 的 EMR28560 / EMR29619 两分区 · 每份 QGZ 都在真实 QGIS 中打开并渲染 · 点击下钻",
      src: "svg-review/<drawing>/delivery/verification/first-open.png（JPEG q80 压缩副本）",
    });
    const gal = D.el("div", "gal");
    DATA.first_open.forEach(f => {
      const fig = D.el("figure");
      const img = D.lazyImg(D.el("img"));
      img.src = `assets/img/first-open/${f}`;
      img.alt = f;
      fig.appendChild(img);
      fig.appendChild(D.el("figcaption", "", f.replace(".jpg", "") + " · first-open"));
      fig.setAttribute("data-drill-keep", "");
      fig.onclick = e => D.showDrill({
        title: "首开验证 · " + f.replace(".jpg", ""),
        sub: "独立 QGIS 进程打开 delivery/view-with-source-SVG.qgz 的首帧截图。SVG 符号随包内嵌（base64），样式恢复。",
        source: "svg-review verification/first-open.png",
        x: e.clientX, y: e.clientY,
      });
      gal.appendChild(fig);
    });
    body.appendChild(gal);
  }

  D.init({
    page: "svg",
    footerSrc: "来源 · docs/OPTIONAL_SYMBOL_ASSETS.md / docs/SVG_UPGRADE_EVIDENCE · 画廊 SVG 与首开图为真实交付包文件，未修改",
  });
})();
