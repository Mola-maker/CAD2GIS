/* ═══ CAD2GIS 文档中心 · docs.js 共享骨架 ═══
   顶部导航 / ToC / prev-next 分页 / 页脚来源行 / 图框三件套 / drill 卡 */
window.DOCS = (() => {
  if (!document.querySelector('link[rel~="icon"]')) {
    const icon = document.createElement("link");
    icon.rel = "icon";
    icon.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%232251ff'/%3E%3Cpath d='M7 23L15 9L25 18' fill='none' stroke='white' stroke-width='3'/%3E%3Ccircle cx='15' cy='9' r='3' fill='white'/%3E%3C/svg%3E";
    document.head.appendChild(icon);
  }
  const PAGES = [
    { id: "index",    href: "index.html",    label: "使用手册" },
    { id: "osm-gcp",  href: "osm-gcp.html",  label: "路网匹配与人工校验" },
    { id: "svg",      href: "svg.html",      label: "SVG 提取与渲染" },
    { id: "cli",      href: "cli.html",      label: "终端与 CLI" },
    { id: "app",      href: "app.html",      label: "软件界面" },
  ];
  const RM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  /* ── 顶部导航 ── */
  function buildTopbar(active) {
    const bar = el("header", "", "");
    bar.id = "docs-topbar";
    const back = el("a", "tb-back", "← 返回报告");
    back.href = location.protocol === "file:" ? "../cad2gis-report.html" : "../";
    bar.appendChild(back);
    const identity = el("a", "tb-identity");
    identity.href = "index.html";
    identity.setAttribute("aria-label", "CAD2GIS 文档中心 · OC molamaker");
    const portrait = el("img", "tb-portrait");
    portrait.src = "assets/img/oc/molamaker-layers.jpg";
    portrait.alt = "";
    portrait.width = 34; portrait.height = 34;
    identity.append(portrait, el("span", "tb-brand", "CAD2GIS <small>molamaker · DOCS</small>"));
    bar.appendChild(identity);
    const nav = el("nav");
    nav.setAttribute("aria-label", "文档导航");
    PAGES.forEach(p => {
      const a = el("a", p.id === active ? "on" : "", p.label);
      a.href = p.href;
      if (p.id === active) a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    });
    bar.appendChild(nav);
    document.body.prepend(bar);
  }

  /* ── ToC（桌面左侧 + 窄屏 chips）── */
  function buildToc() {
    const secs = Array.from(document.querySelectorAll("[data-toc]"));
    if (!secs.length) return;
    const side = el("nav", "", `<div class="toc-lab">CONTENTS</div>`);
    side.id = "docs-toc";
    const chips = el("nav"); chips.id = "docs-toc-chips";
    const links = [];
    secs.forEach((s, i) => {
      if (!s.id) s.id = "doc-sec-" + i;
      const label = s.getAttribute("data-toc");
      const a = el("a", "", label); a.href = "#" + s.id;
      side.appendChild(a);
      const c = el("a", "", label); c.href = "#" + s.id;
      chips.appendChild(c);
      links.push({ a, c, s });
    });
    document.body.appendChild(side);
    const main = document.querySelector(".doc-main");
    if (main) main.prepend(chips);
    const onScroll = () => {
      let cur = 0;
      links.forEach((L, i) => { if (L.s.getBoundingClientRect().top < 140) cur = i; });
      links.forEach((L, i) => {
        L.a.classList.toggle("on", i === cur);
        L.c.classList.toggle("on", i === cur);
      });
    };
    document.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ── prev / next ── */
  function buildPager(active) {
    const i = PAGES.findIndex(p => p.id === active);
    if (i < 0) return;
    const host = document.getElementById("docs-pager");
    if (!host) return;
    const mk = (p, dir, cls) => {
      if (!p) { const s = el("a", "none"); return s; }
      const a = el("a", cls, `<span class="pg-dir">${dir}</span><span class="pg-title">${p.label}</span>`);
      a.href = p.href; return a;
    };
    host.appendChild(mk(PAGES[i - 1], "← PREV", "prev"));
    host.appendChild(mk(PAGES[i + 1], "NEXT →", "next"));
  }

  /* ── 页脚来源行 ── */
  function buildFooter(srcText) {
    const f = el("footer"); f.id = "docs-footer";
    f.innerHTML =
      `<div class="doc-wrap">` +
      `<div>${srcText}</div>` +
      `<div>CAD2GIS 文档中心 · 仓库快照 commit ed93cd2 · v0.7.0 · 命令逐字转录自仓库文档，尖括号 &lt;...&gt; 为需替换的本地路径/值</div>` +
      `<div>官网 · <a href="https://cad2gis.molamaker.cn/">cad2gis.molamaker.cn</a> · Original character · molamaker</div>` +
      `</div>`;
    document.body.appendChild(f);
  }

  /* ── 图框三件套：title / sub / body / src ── */
  function frame(host, { title, sub, src }) {
    host.classList.add("fig");
    const head = el("div", "fig-head");
    if (title) head.innerHTML = `<p class="fig-title">${title}</p>${sub ? `<p class="fig-sub">${sub}</p>` : ""}`;
    host.appendChild(head);
    const body = el("div", "fig-body");
    host.appendChild(body);
    if (src) host.appendChild(el("p", "fig-src", "Source · " + src));
    return body;
  }

  /* ── drill 卡 ── */
  let drill, drillOpen = false;
  function ensureDrill() {
    if (drill) return;
    drill = el("div"); drill.id = "drill-card"; drill.hidden = true;
    document.body.appendChild(drill);
    document.addEventListener("click", e => {
      if (drillOpen && !drill.contains(e.target) && !e.target.closest("[data-drill-keep]")) hideDrill();
    }, true);
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function showDrill({ title, value, sub, source, x, y }) {
    ensureDrill();
    drill.innerHTML = `<button class="d-close">✕</button>
      <div class="d-title">${title}</div>
      ${value ? `<div class="d-val">${value}</div>` : ""}
      ${sub ? `<div class="d-sub">${sub}</div>` : ""}
      ${source ? `<div class="d-src">Source · ${source}</div>` : ""}`;
    drill.hidden = false; drillOpen = true;
    const r = drill.getBoundingClientRect();
    let left = clamp(x + 14, 8, window.innerWidth - r.width - 8);
    let top = clamp(y - r.height - 14, 8, window.innerHeight - r.height - 8);
    if (y - r.height - 14 < 8) top = clamp(y + 18, 8, window.innerHeight - r.height - 8);
    drill.style.left = left + "px"; drill.style.top = top + "px";
    drill.querySelector(".d-close").onclick = hideDrill;
  }
  function hideDrill() { if (drill) { drill.hidden = true; drillOpen = false; } }

  /* ── 图片懒加载（file:// 下 IO 可用；退化则直接加载）── */
  function lazyImg(img) {
    img.loading = "lazy";
    return img;
  }

  function init({ page, footerSrc }) {
    buildTopbar(page);
    buildToc();
    buildPager(page);
    buildFooter(footerSrc || "来源 · CAD2GIS 仓库使用文档（usage-docs-material）");
  }

  return { PAGES, RM, el, frame, showDrill, hideDrill, lazyImg, init, clamp };
})();
