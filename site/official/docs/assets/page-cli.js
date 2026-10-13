/* ═══ 子页 3 · 终端与 CLI · 打字机四章剧场 ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;
  const RM = D.RM;

  /* ── 四章剧本：命令逐字取自 usage-docs-material；输出为真实流程的精简重放 ── */
  const CH = [
    {
      id: "install", name: "CH1 · 安装",
      lines: [
        { k: "cmd", t: 'uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"' },
        { k: "out", c: "dim", t: "Installed executables: cad2gis, cad2gis-desktop, cad2gis-agent-mcp, cad2gis-symbols, …" },
        { k: "cmd", t: "cad2gis runtime install" },
        { k: "out", t: "runtime 0.7.0 · python 3.12 · LibreDWG · GIS libs → 用户缓存" },
        { k: "out", c: "ok", t: "runtime install  ✓" },
        { k: "cmd", t: "cad2gis doctor --deep --strict --profile full --json" },
        { k: "out", c: "ok", t: "✓ python 3.12 runtime" },
        { k: "out", c: "ok", t: "✓ LibreDWG read path" },
        { k: "out", c: "ok", t: "✓ GeoPackage / PROJ / GDAL stack" },
        { k: "out", c: "ok", t: "✓ symbol-assets pipeline" },
        { k: "out", c: "ok", t: "✓ agent-mcp entrypoint" },
        { k: "out", c: "ok", t: "selected_profile_ready: true" },
      ],
    },
    {
      id: "first-run", name: "CH2 · 首个转换",
      lines: [
        { k: "cmd", t: 'cad2gis inspect "APD-MANADO-TOMOHON-UPLINK.dwg" --json' },
        { k: "out", c: "dim", t: "entities 375 · reader records 5,087 · crs EPSG:3857 · partitions MAIN / EMR28560 / EMR29619" },
        { k: "cmd", t: 'cad2gis bootstrap "APD-MANADO-TOMOHON-UPLINK.dwg" --project "D:\\cad\\manado" --json' },
        { k: "out", t: "source-bound profile + mapping registry written → D:\\cad\\manado" },
        { k: "cmd", t: 'cad2gis validate --project "D:\\cad\\manado" --json' },
        { k: "out", c: "ok", t: "validation passed · 0 blocking issues" },
        { k: "cmd", t: 'cad2gis convert "APD-MANADO-TOMOHON-UPLINK.dwg" --project "D:\\cad\\manado" --run-dir "D:\\cad\\manado\\runs\\r1" --json' },
        { k: "out", t: "[1/9] ingest ............................. receipt ✓" },
        { k: "out", t: "[2/9] plan_domain ........................ receipt ✓" },
        { k: "out", t: "[3/9] semantic_classification ............ receipt ✓" },
        { k: "out", t: "[4/9] topology ........................... receipt ✓" },
        { k: "out", t: "[5/9] semantic_revision_projection ....... receipt ✓" },
        { k: "out", t: "[6/9] write_evidence_graph ............... receipt ✓" },
        { k: "out", t: "[7/9] write_source_gpkg .................. receipt ✓" },
        { k: "out", t: "[8/9] write_evidence_gpkg ................ receipt ✓" },
        { k: "out", t: "[9/9] write_delivery_gpkg ................ receipt ✓" },
        { k: "out", c: "dim", t: "census: 375 entities · 5,087 records · 0 silently skipped" },
        { k: "out", c: "warn", t: "run_status: CONDITIONAL" },
      ],
    },
    {
      id: "review", name: "CH3 · 审查发布",
      lines: [
        { k: "cmd", t: 'cad2gis review "D:\\cad\\manado\\runs\\r1" --workspace "D:\\cad\\manado\\review" --port 8765' },
        { k: "out", t: "review workspace ready · matching revision r1" },
        { k: "out", c: "ok", t: "serving on http://127.0.0.1:8765/  (loopback only)" },
        { k: "out", c: "dim", t: "… 浏览器内：保存 ≥2 训练点 → 人工平移预览 → 二次匹配并生成新结果 …" },
        { k: "out", c: "dim", t: "… 工作台自动加载新 GIS · 显示新匹配版本 · 保留人工点 …" },
        { k: "cmd", t: 'cad2gis publish-match "D:\\cad\\manado\\run.review\\matching\\r2-3f9a1c\\run"' },
        { k: "out", t: "osm_matching.revision = r2 → delivery\\r2 已创建（已存在版本目录被拒绝，不覆盖旧版本）" },
        { k: "out", c: "ok", t: "  delivery.gpkg                     （与对应 run 字节完全一致）" },
        { k: "out", c: "ok", t: "  delivery.qgz" },
        { k: "out", c: "ok", t: "  delivery.review.json" },
        { k: "out", c: "ok", t: "  osm-basemap.gpkg" },
        { k: "out", c: "ok", t: "  view-with-source-SVG.qgz" },
        { k: "out", c: "ok", t: "  view-with-source-SVG.review.json" },
        { k: "out", c: "warn", t: "authority: relative_osm · absolute_accuracy_verified: false" },
      ],
    },
    {
      id: "agent", name: "CH4 · MCP 智能体",
      lines: [
        { k: "cmd", t: "cad2gis-agent-mcp --transport stdio" },
        { k: "out", t: "cad2gis-agent-mcp 0.7.0 · stdio transport · roots: 客户端工作区 + 显式授权目录" },
        { k: "out", c: "dim", t: "← tools/call get_capabilities" },
        { k: "out", t: "→ 50 tools · inspection / onboarding / conversion / evidence / repair / review" },
        { k: "out", t: "  transport: stdio · 转换边界与精度声明随能力一并返回" },
        { k: "out", c: "dim", t: '← tools/call audit_run {"run_dir": "D:\\cad\\manado\\runs\\r1"}' },
        { k: "out", c: "ok", t: "→ 每个产物 SHA-256 校验通过 ✓" },
        { k: "out", c: "ok", t: "→ GeoPackage 图层计数匹配 ✓ · manifest census 一致 ✓" },
      ],
    },
  ];

  const host = document.getElementById("term-fig");
  if (!host) return;
  const body = D.frame(host, {
    title: "终端剧场 · 四章打字机（命令逐字，输出忠实精简）",
    sub: "HOW TO READ · 电蓝 $ = 提示符 · 绿 = 通过 · 黄 = 精度声明 · 灰 = 注释 · 可播放 / 暂停 / 0.5–4× 倍速 / 章节跳转 · CH2 收尾必现 run_status: CONDITIONAL",
    src: "usage-docs-material §1–§9（README / INSTALL / OSM_STANDARD_MATCHING）· 输出为真实流程精简重放，未虚构能力",
  });

  /* 控件条 */
  const ctl = D.el("div", "stage-ctl");
  const btnPlay = D.el("button", "", "▶ 播放");
  ctl.appendChild(btnPlay);
  /* OC molamaker 状态角标：播放 working / 暂停 idle / 章节完成 success / 切章 press */
  const ocB = D.el("span", "oc-badge sm s-idle");
  ocB.title = "OC 形象 · molamaker";
  ctl.appendChild(ocB);
  const speedBox = D.el("span");
  speedBox.style.cssText = "display:inline-flex;gap:5px;margin-left:4px";
  const SPEEDS = [0.5, 1, 2, 4];
  let speed = 1;
  SPEEDS.forEach(s => {
    const b = D.el("button", "chip-btn" + (s === 1 ? " on" : ""), s + "×");
    b.onclick = () => {
      speed = s;
      Array.from(speedBox.children).forEach(x => x.classList.toggle("on", x === b));
    };
    speedBox.appendChild(b);
  });
  ctl.appendChild(speedBox);
  const chBox = D.el("span");
  chBox.style.cssText = "display:inline-flex;gap:5px;margin-left:8px;flex-wrap:wrap";
  ctl.appendChild(chBox);
  body.appendChild(ctl);

  /* 终端窗 */
  const term = D.el("div", "term");
  const bar = D.el("div", "term-bar", "<i></i><i></i><i></i><span>cad2gis — pwsh · 127.0.0.1 · 重放演示</span>");
  const tBody = D.el("div", "term-body");
  term.append(bar, tBody);
  body.appendChild(term);

  /* 打字机引擎：cmd 逐字，out 整行 */
  let chIdx = 0, li = 0, ci = 0, playing = false, timer = null, curSpan = null;
  const CHAP_DELAY = 520, OUT_DELAY = 150, CHAR_DELAY = 26;

  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  function lineHTML(l, partial) {
    const t = partial != null ? l.t.slice(0, partial) : l.t;
    if (l.k === "cmd") return `<span class="pr">$ </span>${esc(t)}`;
    return `<span class="${l.c || ""}">${esc(t)}</span>`;
  }
  function scrollDown() { tBody.scrollTop = tBody.scrollHeight; }
  function setCursor() {
    if (!curSpan) { curSpan = D.el("span", "cur"); }
    tBody.appendChild(curSpan);
  }
  function removeCursor() { if (curSpan && curSpan.parentNode) curSpan.parentNode.removeChild(curSpan); }

  function renderInstant() {
    removeCursor();
    tBody.innerHTML = CH[chIdx].lines.map(l => `<div>${lineHTML(l)}</div>`).join("");
    scrollDown();
  }

  function step() {
    const lines = CH[chIdx].lines;
    if (li >= lines.length) { finishChapter(); return; }
    const l = lines[li];
    if (l.k === "cmd") {
      if (ci === 0) {
        const div = D.el("div");
        div.innerHTML = lineHTML(l, 0);
        removeCursor(); tBody.appendChild(div); setCursor(); scrollDown();
      }
      ci++;
      // 更新当前行（游标在行末）
      removeCursor();
      const divs = tBody.querySelectorAll("div");
      const cur = divs[divs.length - 1];
      cur.innerHTML = lineHTML(l, ci);
      setCursor(); scrollDown();
      if (ci >= l.t.length) { li++; ci = 0; timer = setTimeout(step, OUT_DELAY / speed); }
      else timer = setTimeout(step, CHAR_DELAY / speed);
    } else {
      removeCursor();
      const div = D.el("div");
      div.innerHTML = lineHTML(l);
      tBody.appendChild(div); setCursor(); scrollDown();
      li++; ci = 0;
      timer = setTimeout(step, OUT_DELAY / speed);
    }
  }

  function finishChapter() {
    playing = false;
    removeCursor();
    btnPlay.textContent = "↺ 重播";
    ocB.className = "oc-badge sm s-success";
    if (chIdx < CH.length - 1) {
      timer = setTimeout(() => { if (!playing) selectChapter(chIdx + 1, true); }, CHAP_DELAY * 2 / speed);
    }
  }

  function play() {
    if (playing) return;
    if (li >= CH[chIdx].lines.length) { li = 0; ci = 0; tBody.innerHTML = ""; }
    playing = true; btnPlay.textContent = "⏸ 暂停";
    ocB.className = "oc-badge sm s-working";
    step();
  }
  function pause() {
    playing = false; btnPlay.textContent = "▶ 播放";
    ocB.className = "oc-badge sm s-idle";
    clearTimeout(timer);
  }

  function selectChapter(i, autoplay) {
    clearTimeout(timer);
    playing = false;
    chIdx = i; li = 0; ci = 0;
    tBody.innerHTML = "";
    Array.from(chBox.children).forEach((b, j) => b.classList.toggle("on", j === i));
    if (RM) { renderInstant(); btnPlay.textContent = "▶ 播放"; }
    else if (autoplay !== false) play();
    else btnPlay.textContent = "▶ 播放";
  }

  CH.forEach((c, i) => {
    const b = D.el("button", "chip-btn", c.name);
    b.onclick = () => { ocB.className = "oc-badge sm s-press"; selectChapter(i, !RM); };
    chBox.appendChild(b);
  });
  btnPlay.onclick = () => {
    if (RM) return;
    playing ? pause() : play();
  };
  body.insertAdjacentHTML("beforeend", `<p class="oc-credit">OC 形象 · molamaker</p>`);

  if (RM) {
    btnPlay.disabled = true;
    btnPlay.style.opacity = ".4";
    speedBox.style.display = "none";
    selectChapter(0, false);
    tBody.insertAdjacentHTML("beforeend", `<div><span class="dim"># reduced-motion：静态完成帧（用章节按钮切换四章）</span></div>`);
  } else {
    selectChapter(0, true);
  }

  D.init({
    page: "cli",
    footerSrc: "来源 · usage-docs-material §1–§9（README / INSTALL_WINDOWS / INSTALL_LINUX / OSM_STANDARD_MATCHING / OPTIONAL_SYMBOL_ASSETS，命令逐字）· 终端输出为真实流程精简重放",
  });
})();
