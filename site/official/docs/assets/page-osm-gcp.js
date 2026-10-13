/* ═══ 子页 1 · 路网匹配与人工校验 ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;
  const RM = D.RM;
  const PAL = { ink: "#051c2c", inkMd: "#42566a", inkLo: "#8595a6", line: "#dbe2ea", lineLo: "#eef1f6", blue: "#2251ff", blueHi: "#1233b8", neg: "#c22f4e", copper: "#b07a10", hi: "#f7f9fc" };

  /* ═══════════ §1 五幕匹配剧场 ═══════════ */
  const matchHost = document.getElementById("match-fig");
  if (matchHost) {
    const body = D.frame(matchHost, {
      title: "OSM 标准匹配 · 五幕动态过程（演示动画，参数真实）",
      sub: "HOW TO READ · 电蓝折线 = CAD 缆线 · 墨色细线 = OSM 路网 · 五幕依次：弧长采样 / 可观测性门（eigen ≥0.04）/ 平移投票（64 bin）/ corridor 评分（25 m）/ Huber 精化（≤100 迭代）· 可播放 / 单步 / 重播",
      src: "docs/OSM_STANDARD_MATCHING.md（K25）· 动画为过程演示，几何为合成示意，参数为真实默认值",
    });

    const ctl = D.el("div", "stage-ctl");
    const btnPlay = D.el("button", "", "▶ 播放");
    const btnStep = D.el("button", "", "⏭ 单步");
    const btnReplay = D.el("button", "", "↺ 重播");
    const actChips = D.el("span");
    actChips.style.cssText = "display:inline-flex;gap:5px;margin-left:6px";
    ctl.append(btnPlay, btnStep, btnReplay, actChips);
    body.appendChild(ctl);

    const canvas = D.el("canvas");
    canvas.id = "match-stage";
    body.appendChild(canvas);
    const hud = D.el("div", "match-hud");
    body.appendChild(hud);
    const caption = D.el("p", "match-caption");
    body.appendChild(caption);
    const rail = D.el("div", "param-rail",
      ["corridor <b>25 m</b>", "coverage <b>0.75</b>", "direction <b>0.7</b>", "merge <b>0.25 m</b>",
       "eigen <b>≥0.04</b>", "anchors <b>≤32</b>", "细种子 <b>256</b>", "bin <b>5 m</b>", "分辨率 <b>0.05 m</b>", "容差 <b>0.01 m</b>"]
        .map(s => `<span>${s}</span>`).join(""));
    body.appendChild(rail);

    // ── 场景几何（880×480 合成示意）──
    const W = 880, H = 480;
    const roadA = [[40, 320], [180, 296], [330, 312], [470, 268], [620, 284], [760, 250], [846, 258]];
    const roadB = [[40, 190], [220, 172], [400, 188], [600, 160], [846, 170]];
    const roadC = [[290, 60], [310, 200], [330, 430]];
    const roadD = [[640, 70], [600, 210], [570, 440]];
    const roadE = [[40, 400], [300, 396], [560, 402], [846, 390]];
    // 缆线初始位置：相对 roadA 偏移 (+26, +64)，带轻微折角
    const OFF0 = { x: 26, y: 64 };
    const OFF1 = { x: 0, y: 18 }; // 收敛到道路旁
    const cableBase = [[70, 318], [200, 296], [330, 310], [470, 266], [610, 282], [800, 254]]
      .map(p => [p[0] + OFF0.x, p[1] + OFF0.y]);
    // 弧长均匀采样点
    function arcSamples(pts, n) {
      const segs = []; let total = 0;
      for (let i = 1; i < pts.length; i++) {
        const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
        const L = Math.hypot(dx, dy); segs.push({ i: i - 1, L, acc: total }); total += L;
      }
      const out = [];
      for (let k = 0; k < n; k++) {
        const d = (k + 0.5) / n * total;
        const s = segs.find(s => d >= s.acc && d <= s.acc + s.L) || segs[segs.length - 1];
        const t = (d - s.acc) / s.L;
        const a = pts[s.i], b = pts[s.i + 1];
        const x = a[0] + (b[0] - a[0]) * t, y = a[1] + (b[1] - a[1]) * t;
        const dx = (b[0] - a[0]) / s.L, dy = (b[1] - a[1]) / s.L;
        out.push({ x, y, nx: -dy, ny: dx });
      }
      return out;
    }
    const samples = arcSamples(cableBase, 14);

    const ACTS = [
      { name: "幕 1 · 弧长采样", dur: 2600, cap: "采样点沿缆线弧长<b>均匀点亮</b>——按代表长度加权，消除顶点密度偏差。" },
      { name: "幕 2 · 可观测性门", dur: 2800, cap: "采样点法向向量扇形展开，信息矩阵特征值 0.00 → <b>0.047</b>：≥0.04 变蓝通过——平行线不再误判为充分约束。" },
      { name: "幕 3 · 平移投票", dur: 2800, cap: "64 个投票 bin 网格热力点亮，胜出 bin 弹出平移箭头（≤32 anchors / 256 细种子 / 5 m bin / 0.05 m 分辨率）。" },
      { name: "幕 4 · corridor 评分", dur: 2800, cap: "25 m 缓冲带沿候选道路显现：方向兼容线段亮蓝、不兼容灰掉（0.7 方向阈值）。" },
      { name: "幕 5 · Huber 精化", dur: 3400, cap: "整条缆线平移收敛到道路旁，迭代计数在 100 上限前收敛（容差 0.01 m），残差定格——角落盖章 <b>authority: relative_osm</b>。" },
    ];

    let act = 0, t0 = null, playing = false, raf = 0, finished = false;
    const ctx = canvas.getContext("2d");

    function fit() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round((r.width * H / W) * dpr);
      canvas.style.height = (r.width * H / W) + "px";
      ctx.setTransform(canvas.width / W, 0, 0, canvas.width / W, 0, 0);
    }

    function drawRoad(pts, w, color) {
      ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.beginPath();
      pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      ctx.stroke();
    }
    function cablePos(off) { return cableBase.map(p => [p[0] + off.x - OFF0.x, p[1] + off.y - OFF0.y]); }
    function drawCable(off, color, w) {
      drawRoad(cablePos(off), w || 3, color || PAL.blue);
    }

    function drawScene(aIdx, t) {
      ctx.clearRect(0, 0, W, H);
      // 背景路网
      [roadA, roadB, roadC, roadD, roadE].forEach(r => drawRoad(r, 1.4, PAL.line));
      // 当前缆线偏移（幕 5 插值）
      let off = { ...OFF0 };
      if (aIdx >= 4) {
        const tt = aIdx > 4 ? 1 : (1 - Math.pow(1 - t, 3));
        off = { x: OFF0.x + (OFF1.x - OFF0.x) * tt, y: OFF0.y + (OFF1.y - OFF0.y) * tt };
      }

      // ── 幕 3（投票网格，画在底层）──
      if (aIdx >= 2) {
        const gx = 240, gy = 120, gw = 420, gh = 260, n = 8;
        const tt = aIdx > 2 ? 1 : t;
        const win = { i: 4, j: 5 };
        for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
          const order = (j * n + i) / (n * n);
          if (tt < order * 0.85) continue;
          const isWin = i === win.i && j === win.j;
          const heat = isWin ? (tt > 0.9 ? 0.95 : 0.4) : 0.08 + 0.3 * Math.abs(Math.sin(i * 3.1 + j * 1.7));
          ctx.fillStyle = `rgba(34,81,255,${(heat * 0.55).toFixed(3)})`;
          ctx.fillRect(gx + i * gw / n + 1, gy + j * gh / n + 1, gw / n - 2, gh / n - 2);
        }
        ctx.strokeStyle = "rgba(5,28,44,.18)"; ctx.lineWidth = 0.7;
        for (let i = 0; i <= n; i++) {
          ctx.beginPath(); ctx.moveTo(gx + i * gw / n, gy); ctx.lineTo(gx + i * gw / n, gy + gh); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(gx, gy + i * gh / n); ctx.lineTo(gx + gw, gy + i * gh / n); ctx.stroke();
        }
        if (tt > 0.88) { // 胜出 bin 平移箭头
          const cx = gx + (win.i + .5) * gw / n, cy = gy + (win.j + .5) * gh / n;
          ctx.strokeStyle = PAL.blue; ctx.lineWidth = 2.4;
          ctx.beginPath(); ctx.moveTo(cx, cy - 60); ctx.lineTo(cx, cy - 8); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(cx - 7, cy - 18); ctx.lineTo(cx, cy - 6); ctx.lineTo(cx + 7, cy - 18); ctx.stroke();
          ctx.fillStyle = PAL.blue; ctx.font = "700 10px Menlo, monospace";
          ctx.fillText("WIN BIN → Δ", cx + 10, cy - 30);
        }
      }

      // ── 幕 4（corridor 缓冲带）──
      if (aIdx >= 3) {
        const tt = aIdx > 3 ? 1 : t;
        ctx.save();
        ctx.strokeStyle = "rgba(34,81,255,.14)"; ctx.lineWidth = 50; ctx.lineJoin = "round"; ctx.lineCap = "round";
        ctx.beginPath();
        const upto = Math.max(2, Math.ceil(roadA.length * tt));
        roadA.slice(0, upto).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
        ctx.stroke();
        ctx.restore();
        drawRoad(roadA, 2.2, "rgba(34,81,255,.55)");
      }

      // 缆线本体（幕 4 时按方向兼容性分段着色）
      if (aIdx === 3) {
        const pts = cablePos(off);
        for (let i = 1; i < pts.length; i++) {
          const compat = i !== 2 && i !== 5; // 两段方向不兼容 → 灰
          const appear = D.clamp(t * pts.length - i + 1, 0, 1);
          ctx.globalAlpha = 0.25 + 0.75 * appear;
          drawRoad([pts[i - 1], pts[i]], compat ? 3.4 : 2.2, compat ? PAL.blue : PAL.inkLo);
          ctx.globalAlpha = 1;
        }
      } else {
        drawCable(off);
      }

      // ── 幕 1（采样点点亮）──
      if (aIdx >= 0) {
        const tt = aIdx > 0 ? 1 : t;
        samples.forEach((s, k) => {
          const appear = D.clamp(tt * samples.length - k, 0, 1);
          if (appear <= 0) return;
          const sx = s.x + off.x - OFF0.x, sy = s.y + off.y - OFF0.y;
          ctx.globalAlpha = appear;
          ctx.fillStyle = PAL.blue;
          ctx.beginPath(); ctx.arc(sx, sy, 3.4, 0, 7); ctx.fill();
          ctx.globalAlpha = 1;
        });
      }

      // ── 幕 2（法向扇形 + eigen）──
      if (aIdx >= 1) {
        const tt = aIdx > 1 ? 1 : t;
        samples.forEach((s, k) => {
          const sx = s.x + off.x - OFF0.x, sy = s.y + off.y - OFF0.y;
          const base = Math.atan2(s.ny, s.nx);
          for (let f = -2; f <= 2; f++) {
            const ang = base + f * 0.42;
            const len = (14 + 8 * Math.abs(Math.sin(k * 1.3 + f))) * D.clamp(tt * 2 - Math.abs(f) * 0.25, 0, 1);
            if (len <= 0) continue;
            ctx.strokeStyle = `rgba(34,81,255,${0.5 - Math.abs(f) * 0.12})`;
            ctx.lineWidth = 1.2;
            ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + Math.cos(ang) * len, sy + Math.sin(ang) * len); ctx.stroke();
          }
        });
      }

      // ── 幕 5（残差尾迹 + 章）──
      if (aIdx >= 4) {
        drawCable(OFF0, "rgba(5,28,44,.16)", 1.6); // 原始位置残影
        const tt = aIdx > 4 ? 1 : t;
        if (tt > 0.86) { // 盖章
          ctx.save();
          ctx.translate(W - 158, 44); ctx.rotate(-0.06);
          ctx.strokeStyle = PAL.blue; ctx.lineWidth = 2;
          ctx.strokeRect(0, 0, 146, 30);
          ctx.fillStyle = PAL.blue; ctx.font = "700 11.5px Menlo, monospace";
          ctx.fillText("authority: relative_osm", 8, 20);
          ctx.restore();
        }
      }

      // HUD
      const eigen = aIdx >= 1 ? (aIdx > 1 ? 0.047 : 0.047 * t) : 0;
      const iter = aIdx >= 4 ? (aIdx > 4 ? 87 : Math.round(87 * (1 - Math.pow(1 - t, 2)))) : 0;
      const resid = aIdx >= 4 ? (aIdx > 4 ? 0.31 : 12.4 - 12.09 * (1 - Math.pow(1 - t, 2))) : 12.4;
      hud.innerHTML =
        `<span>ACT <b>${aIdx + 1}/5</b></span>` +
        `<span>eigen <b style="color:${eigen >= 0.04 ? PAL.blue : PAL.ink}">${eigen.toFixed(3)}</b> ${eigen >= 0.04 ? "✓ ≥0.04" : ""}</span>` +
        `<span>iteration <b>${iter}</b>/100</span>` +
        `<span>残差 <b>${resid.toFixed(2)} m</b></span>`;
      caption.innerHTML = `<b>${ACTS[aIdx].name}</b>　${ACTS[aIdx].cap}`;
      Array.from(actChips.children).forEach((c, i) => c.classList.toggle("on", i === aIdx));
    }

    function renderAt(aIdx, t) { fit(); drawScene(aIdx, t); }
    function loop(ts) {
      if (!playing) return;
      if (t0 == null) t0 = ts;
      const t = (ts - t0) / ACTS[act].dur;
      if (t >= 1) {
        if (act >= ACTS.length - 1) { playing = false; finished = true; btnPlay.textContent = "▶ 播放"; renderAt(act, 1); return; }
        act++; t0 = ts; renderAt(act, 0);
      } else renderAt(act, t);
      raf = requestAnimationFrame(loop);
    }
    function play() {
      if (playing) return;
      if (finished) { act = 0; finished = false; }
      playing = true; t0 = null; btnPlay.textContent = "⏸ 暂停";
      raf = requestAnimationFrame(loop);
    }
    function pause() { playing = false; btnPlay.textContent = "▶ 播放"; cancelAnimationFrame(raf); }

    ACTS.forEach((a, i) => {
      const c = D.el("button", "chip-btn", String(i + 1));
      c.title = a.name;
      c.onclick = () => { pause(); act = i; finished = false; renderAt(act, RM ? 1 : 0.001); if (RM) renderAt(act, 1); };
      actChips.appendChild(c);
    });
    btnPlay.onclick = () => playing ? pause() : play();
    btnStep.onclick = () => {
      pause();
      act = Math.min(act + 1, ACTS.length - 1);
      if (act === ACTS.length - 1 && finished) finished = false;
      renderAt(act, RM ? 1 : 0.001);
    };
    btnReplay.onclick = () => { pause(); act = 0; finished = false; renderAt(0, 0.001); };

    if (RM) { act = 4; renderAt(4, 1); caption.innerHTML = `<b>${ACTS[4].name}</b>　${ACTS[4].cap}（reduced-motion：直接呈现完成帧）`; }
    else renderAt(0, 0.001);
    let rto; window.addEventListener("resize", () => { clearTimeout(rto); rto = setTimeout(() => renderAt(act, finished || RM ? 1 : 0.4), 120); });
  }
})();

/* ═══ 子页 1（续）· §2 before/after 滑块 + §3 GCP 微调 + §5 画廊 + init ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;

  /* ── §2 真实配准对照 ── */
  const baHost = document.getElementById("ba-fig");
  if (baHost) {
    const body = D.frame(baHost, {
      title: "历史对齐报告 · 同一项目的 before / after",
      sub: "HOW TO READ · 拖动分割线或使用方向键查看历史图像 · 缩略图切换三个项目 · 当前基线的交互地图单独打开；历史报告不等于当前基线截图",
      src: "assets/img/alignment/（assets_src/baselines 各项目 delivery/alignment-*.png 压缩副本）",
    });
    const projects = [
      { id: "lamteh_main", label: "LAMTEH MAIN" },
      { id: "tinggede", label: "TINGGEDE" },
      { id: "lamteh_sf", label: "LAMTEH SF" },
    ];
    const wrap = D.el("div", "ba-wrap");
    const imgBefore = D.lazyImg(D.el("img"));
    const afterBox = D.el("div", "ba-after");
    const imgAfter = D.lazyImg(D.el("img"));
    afterBox.appendChild(imgAfter);
    const tagL = D.el("span", "ba-tag l", "BEFORE");
    const tagR = D.el("span", "ba-tag r", "AFTER");
    const handle = D.el("div", "ba-handle");
    handle.tabIndex = 0;
    handle.setAttribute("role", "slider");
    handle.setAttribute("aria-label", "历史配准图像分割位置");
    handle.setAttribute("aria-valuemin", "2");
    handle.setAttribute("aria-valuemax", "98");
    wrap.append(imgBefore, afterBox, tagL, tagR, handle);
    body.appendChild(wrap);
    const thumbs = D.el("div", "ba-thumbs");
    body.appendChild(thumbs);
    const mapBtn = D.el("a", "ba-map-btn", "打开该项目的真实交互地图 →");
    mapBtn.href = "map.html?p=lamteh_main";
    body.appendChild(mapBtn);

    let pos = 0.5;
    function apply() {
      afterBox.style.clipPath = `inset(0 0 0 ${pos * 100}%)`;
      handle.style.left = `calc(${pos * 100}% - 1px)`;
      handle.setAttribute("aria-valuenow", String(Math.round(pos * 100)));
    }
    function setProject(p) {
      imgBefore.src = `assets/img/alignment/${p.id}-before.jpg`;
      imgAfter.src = `assets/img/alignment/${p.id}-after.jpg`;
      imgBefore.alt = `${p.label} 历史配准报告 before`;
      imgAfter.alt = `${p.label} 历史配准报告 after`;
      mapBtn.href = `map.html?p=${p.id}`;
      Array.from(thumbs.children).forEach(b => b.classList.toggle("on", b.dataset.pid === p.id));
    }
    projects.forEach(p => {
      const b = D.el("button");
      b.dataset.pid = p.id;
      const im = D.lazyImg(D.el("img"));
      im.src = `assets/img/alignment/${p.id}-overview.jpg`;
      im.alt = p.label;
      b.appendChild(im);
      b.appendChild(D.el("span", "", p.label));
      b.onclick = () => setProject(p);
      thumbs.appendChild(b);
    });
    function onMove(e) {
      const r = wrap.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      pos = D.clamp(x / r.width, 0.02, 0.98);
      apply();
    }
    let dragging = false;
    handle.addEventListener("pointerdown", e => { dragging = true; handle.setPointerCapture(e.pointerId); });
    window.addEventListener("pointermove", e => dragging && onMove(e));
    window.addEventListener("pointerup", () => dragging = false);
    wrap.addEventListener("pointerdown", onMove);
    handle.addEventListener("keydown", e => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      pos = e.key === "Home" ? 0.02 : e.key === "End" ? 0.98 : D.clamp(pos + (e.key === "ArrowRight" ? 0.05 : -0.05), 0.02, 0.98);
      apply();
    });
    setProject(projects[0]);
    apply();
  }

  /* ── §3 人工 GCP 校验 ── */
  const gcpHost = document.getElementById("gcp-fig");
  if (gcpHost) {
    const body = D.frame(gcpHost, {
      title: "人工控制点微调 · 交互演示",
      sub: "HOW TO READ · 左 = CAD 视图选中点 · 右 = 地图视图对应点 · 四向按钮按步长移动预览点 · train/check 角色切换：check 只评估不拟合 · 演示数据为合成",
      src: "README「Web 配准、坐标传送与审查」/ docs/UPGRADE_0_6_0「第一次使用」（K26/K28）",
    });
    const grid = D.el("div", "gcp-grid");
    body.appendChild(grid);

    // 左：双窗格 SVG
    const panes = D.el("div");
    panes.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:10px";
    grid.appendChild(panes);
    const NS = "http://www.w3.org/2000/svg";
    function mkPane(label, drawFn) {
      const box = D.el("div", "gcp-pane");
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 300 240");
      box.appendChild(svg);
      const lab = D.el("span", "af-pane-lab", label);
      box.appendChild(lab);
      drawFn(svg);
      panes.appendChild(box);
      return svg;
    }
    const mk = (t, a, p) => { const n = document.createElementNS(NS, t); Object.entries(a).forEach(([k, v]) => n.setAttribute(k, v)); p.appendChild(n); return n; };
    // CAD 视图：图纸网格 + 缆线 + 控制点
    let cadPt, mapPt, mapArrow;
    mkPane("CAD 视图", svg => {
      for (let x = 0; x <= 300; x += 30) mk("line", { x1: x, y1: 0, x2: x, y2: 240, stroke: "#eef1f6", "stroke-width": 1 }, svg);
      for (let y = 0; y <= 240; y += 30) mk("line", { x1: 0, y1: y, x2: 300, y2: y, stroke: "#eef1f6", "stroke-width": 1 }, svg);
      mk("polyline", { points: "20,170 90,150 160,158 230,132 285,138", fill: "none", stroke: "#2251ff", "stroke-width": 2.4, "stroke-linejoin": "round" }, svg);
      mk("polyline", { points: "30,80 120,74 210,86 280,70", fill: "none", stroke: "#42566a", "stroke-width": 1.2 }, svg);
      cadPt = mk("g", {}, svg);
      mk("circle", { cx: 0, cy: 0, r: 7, fill: "none", stroke: "#2251ff", "stroke-width": 2 }, cadPt);
      mk("line", { x1: -11, y1: 0, x2: 11, y2: 0, stroke: "#2251ff", "stroke-width": 1.6 }, cadPt);
      mk("line", { x1: 0, y1: -11, x2: 0, y2: 11, stroke: "#2251ff", "stroke-width": 1.6 }, cadPt);
    });
    // 地图视图：路网 + 对应点
    mkPane("地图视图", svg => {
      mk("rect", { x: 0, y: 0, width: 300, height: 240, fill: "#f7f9fc" }, svg);
      mk("polyline", { points: "10,168 90,148 165,156 235,130 295,136", fill: "none", stroke: "#8595a6", "stroke-width": 5, "stroke-linejoin": "round", opacity: .5 }, svg);
      mk("polyline", { points: "0,110 100,104 200,116 300,100", fill: "none", stroke: "#8595a6", "stroke-width": 3, opacity: .4 }, svg);
      mk("polyline", { points: "150,0 156,90 146,240", fill: "none", stroke: "#8595a6", "stroke-width": 3, opacity: .4 }, svg);
      mapArrow = mk("line", { x1: 0, y1: 0, x2: 0, y2: 0, stroke: "#c22f4e", "stroke-width": 1.4, "stroke-dasharray": "3 3" }, svg);
      mapPt = mk("g", {}, svg);
      mk("circle", { cx: 0, cy: 0, r: 6, fill: "rgba(34,81,255,.18)", stroke: "#2251ff", "stroke-width": 2 }, mapPt);
      mk("circle", { cx: 0, cy: 0, r: 1.8, fill: "#2251ff" }, mapPt);
    });

    // 右：控制
    const panel = D.el("div");
    grid.appendChild(panel);
    const readout = D.el("div", "gcp-readout");
    panel.appendChild(readout);
    const stepsBar = D.el("div", "gcp-steps");
    panel.appendChild(stepsBar);
    const dpad = D.el("div", "gcp-dpad");
    panel.appendChild(dpad);
    const undoBtn = D.el("button", "chip-btn", "↩ 撤销");
    const roleBar = D.el("div", "gcp-role");
    panel.appendChild(roleBar);
    const undoWrap = D.el("div", "", "");
    undoWrap.style.cssText = "text-align:center;margin-top:10px";
    undoWrap.appendChild(undoBtn);
    panel.appendChild(undoWrap);
    panel.appendChild(D.el("div", "gcp-big", "0.01 m 是操作步长，<br>不是厘米级精度承诺"));

    const state = { x: 0, y: 0, step: 0.1, role: "train", stack: [], base: { cad: [160, 158], map: [168, 152] } };
    /* OC molamaker 状态角标：点按钮 press / 计算 working / 保存 success / 非法操作 error */
    const ocRow = D.el("div", "gcp-oc");
    const ocB = D.el("span", "oc-badge s-idle");
    const ocT = D.el("span", "gcp-oc-text", "待机 · 选择步长后按方向键微调");
    ocRow.append(ocB, ocT);
    panel.insertBefore(ocRow, readout);
    panel.appendChild(D.el("p", "oc-credit", "OC 形象 · molamaker"));
    let ocTimer = null;
    function ocSet(s, text, hold) {
      ocB.className = "oc-badge s-" + s;
      ocT.textContent = text;
      clearTimeout(ocTimer);
      if (hold) ocTimer = setTimeout(() => {
        ocB.className = "oc-badge s-idle"; ocT.textContent = "待机 · 选择步长后按方向键微调";
      }, hold);
    }
    const STEPS = [0.01, 0.1, 1];
    const SCALE = 40; // px per m（演示比例）
    function fmt(v) { return (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2); }
    function render() {
      const dx = state.x * SCALE, dy = -state.y * SCALE;
      cadPt.setAttribute("transform", `translate(${state.base.cad[0] + dx},${state.base.cad[1] + dy})`);
      mapPt.setAttribute("transform", `translate(${state.base.map[0] + dx},${state.base.map[1] + dy})`);
      mapArrow.setAttribute("x1", state.base.map[0]); mapArrow.setAttribute("y1", state.base.map[1]);
      mapArrow.setAttribute("x2", state.base.map[0] + dx); mapArrow.setAttribute("y2", state.base.map[1] + dy);
      readout.innerHTML =
        `东 <b>${fmt(state.x)} m</b> · 北 <b>${fmt(state.y)} m</b><br>` +
        `撤销栈 <b>${state.stack.length}</b>/128 · 角色 <b>${state.role}</b>` +
        (state.role === "check" ? `<br><span style="color:#c22f4e">check 点只评估残差，不参与拟合</span>` : "");
      Array.from(stepsBar.children).forEach(b => b.classList.toggle("on", parseFloat(b.dataset.s) === state.step));
      Array.from(roleBar.children).forEach(b => b.classList.toggle("on", b.dataset.r === state.role));
    }
    STEPS.forEach(s => {
      const b = D.el("button", "chip-btn", s + " m");
      b.dataset.s = s;
      b.onclick = () => { state.step = s; render(); ocSet("working", `重算步长 ${s} m …`, 0); ocSet("success", `步长已保存 · ${s} m`, 1400); };
      stepsBar.appendChild(b);
    });
    ["train", "check"].forEach(r => {
      const b = D.el("button", "chip-btn", r);
      b.dataset.r = r;
      b.onclick = () => { state.role = r; render(); ocSet("success", `角色已保存 · ${r}`, 1400); };
      roleBar.appendChild(b);
    });
    const moves = [["↑", 0, 1], ["←", -1, 0], ["→", 1, 0], ["↓", 0, -1]];
    const layout = [["", null], ["↑", moves[0]], ["", null], ["←", moves[1]], ["", null], ["→", moves[2]], ["", null], ["↓", moves[3]], ["", null]];
    layout.forEach(([lab, mv]) => {
      if (!mv) { dpad.appendChild(D.el("span", "void")); return; }
      const b = D.el("button", "", lab);
      b.onclick = () => {
        if (state.stack.length >= 128) { ocSet("error", "非法操作 · 撤销栈已满 128/128", 2000); return; }
        state.stack.push({ x: state.x, y: state.y });
        state.x += mv[1] * state.step; state.y += mv[2] * state.step;
        render();
        ocSet("press", `微调 ${lab} ${state.step} m`, 900);
      };
      dpad.appendChild(b);
    });
    undoBtn.onclick = () => {
      const prev = state.stack.pop();
      if (prev) {
        state.x = prev.x; state.y = prev.y;
        render(); ocSet("working", "撤销重算 …", 0); ocSet("success", "已撤销一步", 1400);
      } else {
        ocSet("error", "非法操作 · 撤销栈为空", 2000);
      }
      render();
    };
    render();
  }

  /* 原尺寸图像查看器：原生 dialog 管理焦点，图片链接同时保留直接打开能力。 */
  let imageDialog, imageReturnFocus;
  function openImage(src, caption, trigger) {
    if (!imageDialog) {
      imageDialog = D.el("dialog", "review-lightbox");
      imageDialog.setAttribute("aria-labelledby", "review-image-title");
      imageDialog.innerHTML = `<div class="review-lightbox-head"><h2 id="review-image-title"></h2><button type="button" class="review-lightbox-close" aria-label="关闭图片">关闭 <span aria-hidden="true">×</span></button></div><div class="review-lightbox-stage"><img alt=""></div><div class="review-lightbox-foot"><span>QGIS 参考图 · 静态图像</span><a target="_blank" rel="noopener">打开原尺寸图片 ↗</a></div>`;
      document.body.appendChild(imageDialog);
      imageDialog.querySelector("button").addEventListener("click", () => imageDialog.close());
      let startedOnBackdrop = false;
      imageDialog.addEventListener("pointerdown", e => { startedOnBackdrop = e.target === imageDialog; });
      imageDialog.addEventListener("click", e => {
        if (startedOnBackdrop && e.target === imageDialog) {
          const r = imageDialog.getBoundingClientRect();
          if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) imageDialog.close();
        }
        startedOnBackdrop = false;
      });
      imageDialog.addEventListener("close", () => {
        document.body.classList.remove("review-image-open");
        if (imageReturnFocus && imageReturnFocus.isConnected) imageReturnFocus.focus({ preventScroll: true });
      });
    }
    const img = imageDialog.querySelector("img");
    imageDialog.querySelector("h2").textContent = caption;
    img.src = src; img.alt = caption;
    imageDialog.querySelector(".review-lightbox-foot a").href = src;
    imageReturnFocus = trigger || document.activeElement;
    imageDialog.showModal();
    document.body.classList.add("review-image-open");
    imageDialog.querySelector("button").focus({ preventScroll: true });
  }
  function imageLink(src, caption, className) {
    const a = D.el("a", className);
    a.href = src;
    a.setAttribute("aria-label", `放大查看 ${caption}`);
    a.addEventListener("click", e => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault(); openImage(src, caption, a);
    });
    return a;
  }

  /* ── §0 项目入口由生成的基线索引提供，与地图项目选择器同源。 ── */
  const mapList = document.getElementById("baseline-map-list");
  const mapIndex = window.__MAPDATA && window.__MAPDATA.index;
  const baselineProjects = mapIndex && Array.isArray(mapIndex.projects) ? mapIndex.projects : [];
  const assetUrl = value => typeof value === "string" ? value : value && (value.url || value.href || value.path);
  if (mapList) {
    if (!baselineProjects.length) {
      const notice = D.el("p", "me-note");
      notice.textContent = "基线索引未加载，请刷新页面后重试。";
      mapList.appendChild(notice);
    }
    baselineProjects.forEach((p, i) => {
      const row = D.el("article", "baseline-project");
      const preview = assetUrl(p.preview);
      const main = D.el("div", "baseline-project-main");
      const order = D.el("span", "baseline-order");
      order.textContent = `${String(i + 1).padStart(2, "0")} / BASELINE`;
      const title = D.el("h3"); title.textContent = p.title || p.id;
      const details = D.el("p", "baseline-details");
      const count = Number.isFinite(p.feature_total) ? `${p.feature_total.toLocaleString("zh-CN")} 个真实矢量要素` : "QGZ 矢量图层";
      details.textContent = count + (p.has_before ? " · 含历史配准对照" : "");
      const actions = D.el("div", "baseline-actions");
      const map = D.el("a", "baseline-open", "打开真实交互地图 ↗");
      map.href = `map.html?p=${encodeURIComponent(p.id)}`;
      actions.appendChild(map);
      if (preview) {
        const reference = imageLink(preview, `${p.title || p.id} · QGIS 参考图`, "baseline-reference");
        reference.textContent = "QGIS 参考图 ↗";
        actions.appendChild(reference);
      }
      const qgz = assetUrl(p.qgz || p.download);
      if (qgz) {
        const link = D.el("a", "", "QGZ 工程 ↓"); link.href = qgz;
        if (!/^https?:/i.test(qgz)) link.setAttribute("download", "");
        actions.appendChild(link);
      }
      const source = assetUrl(p.source_url);
      if (source) {
        const link = D.el("a", "baseline-source", "查看来源 ↗");
        link.href = source; link.target = "_blank"; link.rel = "noopener";
        actions.appendChild(link);
      }
      main.append(order, title, details, actions);
      row.appendChild(main); mapList.appendChild(row);
    });
  }

  /* ── §5 真实成果画廊 ── */
  const galHost = document.getElementById("qgis-fig");
  if (galHost) {
    const body = D.frame(galHost, {
      title: "CAD2GIS OSM review — nominal CAD registration · QGIS",
      sub: "HOW TO READ · 七张 QGIS 静态参考图均可放大，Esc 或点击背景关闭 · 每张图片下方的「打开真实交互地图」进入可操作的矢量图层",
      src: "assets_src/baselines 各项目「屏幕截图 *.png」· 缩略图为 JPEG 副本，放大图为原尺寸 PNG · 图片记录拍摄时的 QGIS 视图",
    });
    const gal = D.el("div", "gal");
    const shots = [
      { n: "darat-1", map: "darat_sekip_sf" }, { n: "darat-2", map: "darat_sekip_sf" },
      { n: "hutabohu-1", map: "hutabohu" }, { n: "hutabohu-2", map: "hutabohu" }, { n: "hutabohu-3", map: "hutabohu" },
      { n: "kletek-1", map: "kletek" }, { n: "kletek-2", map: "kletek" },
    ];
    shots.forEach(s => {
      const fig = D.el("figure");
      const caption = `${s.n} · QGIS 参考图 · nominal CAD registration`;
      const a = imageLink(`assets/img/qgis/full/${s.n}.png`, caption, "gal-image-link");
      const img = D.lazyImg(D.el("img"));
      img.src = `assets/img/qgis/${s.n}.jpg`;
      img.alt = `${s.n} 的 QGIS 原始截图`;
      a.append(img, D.el("span", "gal-enlarge", "放大参考图 ↗"));
      fig.appendChild(a);
      const cap = D.el("figcaption");
      cap.appendChild(D.el("span", "gal-caption", caption));
      const map = D.el("a", "gal-map-link", "打开真实交互地图 →");
      map.href = `map.html?p=${encodeURIComponent(s.map)}`;
      map.setAttribute("aria-label", `打开 ${s.n} 对应的 ${s.map} 真实交互地图`);
      cap.appendChild(map); fig.appendChild(cap); gal.appendChild(fig);
    });
    body.appendChild(gal);

    const me = D.el("div", "map-entry");
    me.innerHTML = `<div class="me-title">全部基线项目 · 真实矢量交互地图</div><div class="me-grid"></div>` +
      `<p class="me-note">截图与当前基线可能来自不同时间；地图内展示各自的来源记录。HISTORY 表示可查看历史配准数据，不表示已验证绝对精度。<a href="#baseline-maps">返回基线成果目录 ↑</a></p>`;
    const grid2 = me.querySelector(".me-grid");
    baselineProjects.forEach(p => {
      const a = D.el("a", "me-chip");
      a.href = `map.html?p=${encodeURIComponent(p.id)}`;
      const t = D.el("span", "me-t"); t.textContent = p.title || p.id;
      a.appendChild(t);
      if (p.has_before) a.appendChild(D.el("span", "me-badge", "HISTORY"));
      grid2.appendChild(a);
    });
    body.appendChild(me);
  }

  D.init({
    page: "osm-gcp",
    footerSrc: "来源 · docs/OSM_STANDARD_MATCHING.md / docs/UPGRADE_OSM / docs/UPGRADE_GCP（K25/K26/K28 沿用主报告编号）· 配准图与截图为真实交付证据",
  });
})();
