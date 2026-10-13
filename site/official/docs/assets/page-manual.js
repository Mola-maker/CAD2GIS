/* ═══ 文档中心入口 · 使用手册（十一章分页 + hash 定位 + ToC） ═══ */
(() => {
  const D = window.DOCS;
  if (!D) return;

  const pages = Array.from(document.querySelectorAll(".man-page"));
  const cover = document.getElementById("man-cover");
  const CH = pages.map(p => ({ id: p.id, title: p.dataset.title || p.id, el: p }));
  const DESCS = {
    "ch-01": "安装器 GUI 步骤 + doctor 验证命令 + 日志路径 + uv 免安装路径",
    "ch-02": "DEB + 便携包 + verify_install.py + 可选 Redis 缓存",
    "ch-03": "inspect→bootstrap→validate→convert 逐字流程 + DeepSeek auto-convert",
    "ch-04": "参数表 + 四态 + 模型约束「不旋转不拉伸不吸附」+ 互斥规则",
    "ch-05": "candidate 模式三产物 + 独立运行 + 人工绑定到 QGZ",
    "ch-06": "六步操作流 + 0.01/0.1/1 m 步长 + RELATIVE_OSM_REFERENCE_ONLY",
    "ch-07": "冻结本轮 → 正式转换 → 新目录不覆盖",
    "ch-08": "publish-match + 六文件固定清单 + QML 恢复样式",
    "ch-09": "stdio/HTTP + 四客户端安装 + get_capabilities→audit_run + 目录授权",
    "ch-10": "服务端 HTTPS 准备 → APK 登录 → 证书不跳过",
    "ch-11": "APD/SF/FTTH/BOITE/SITE/PTECH + CONDITIONAL 语义",
  };

  /* ── 封面十一章目录 ── */
  const grid = document.getElementById("man-toc-grid");
  if (grid) {
    CH.forEach(c => {
      const a = D.el("a", "man-chapter-link");
      a.href = "#" + c.id;
      const [no, ...rest] = c.title.split(" ");
      a.innerHTML = `<div class="mc-no">CHAPTER ${no}</div><div class="mc-t">${rest.join(" ")}</div><div class="mc-d">${DESCS[c.id] || ""}</div>`;
      grid.appendChild(a);
    });
  }

  /* ── 侧栏 ToC（复用 docs.css 式样） ── */
  const side = D.el("nav");
  side.id = "docs-toc";
  side.innerHTML = `<div class="toc-lab">MANUAL · 11 CHAPTERS</div>`;
  const chips = D.el("nav");
  chips.id = "docs-toc-chips";
  const tocLinks = [];
  const mkLink = (host, id, label) => {
    const a = D.el("a", "", label);
    a.href = id ? "#" + id : "#";
    host.appendChild(a);
    return a;
  };
  tocLinks.push({ id: null, a: mkLink(side, null, "封面 · Cover"), c: mkLink(chips, null, "封面") });
  CH.forEach(c => tocLinks.push({ id: c.id, a: mkLink(side, c.id, c.title), c: mkLink(chips, c.id, c.title) }));
  document.body.appendChild(side);
  const main = document.querySelector(".doc-main");
  if (main) main.prepend(chips);

  /* ── 每章末尾 prev / next ── */
  CH.forEach((c, i) => {
    const pager = D.el("div", "man-pager");
    const prev = D.el("button", "", i === 0 ? "← 封面" : "← " + CH[i - 1].title);
    const next = D.el("button", "", i === CH.length - 1 ? "回到封面 →" : CH[i + 1].title + " →");
    prev.onclick = () => { location.hash = i === 0 ? "" : CH[i - 1].id; };
    next.onclick = () => { location.hash = i === CH.length - 1 ? "" : CH[i + 1].id; };
    pager.append(prev, next);
    c.el.querySelector(".doc-wrap").appendChild(pager);
  });

  /* ── hash 路由 ── */
  function route() {
    const h = (location.hash || "").replace(/^#/, "");
    const cur = CH.find(c => c.id === h) || null;
    if (cover) cover.style.display = cur ? "none" : "";
    CH.forEach(c => c.el.classList.toggle("on", cur && c.id === cur.id));
    tocLinks.forEach(L => {
      const on = cur ? L.id === cur.id : L.id === null;
      L.a.classList.toggle("on", on);
      L.c.classList.toggle("on", on);
    });
    window.scrollTo(0, 0);
    document.title = (cur ? cur.title + " · " : "") + "使用手册 · CAD2GIS 文档中心";
  }
  window.addEventListener("hashchange", route);
  route();

  D.init({
    page: "index",
    footerSrc: "来源 · usage-docs-material 全文（README / INSTALL_WINDOWS / INSTALL_LINUX / OSM_STANDARD_MATCHING / OPTIONAL_SYMBOL_ASSETS / CONNECT_ANDROID / GLOSSARY，命令逐字）",
  });
})();
