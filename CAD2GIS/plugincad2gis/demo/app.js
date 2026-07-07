/* CAD2GIS demo renderer — hydrates the page from window.REPORT (data/report.js). */
(function () {
  var R = window.REPORT || {};
  var $ = function (id) { return document.getElementById(id); };
  var fmt = function (n) { return (n == null ? "—" : Number(n).toLocaleString("en-US")); };
  var pct = function (x) { return (x == null ? "—" : (x * 100).toFixed(1) + "%"); };

  // ---- hero ----
  var acc = R.accuracy || {};
  if ($("h-overall")) $("h-overall").textContent = acc.overall != null ? pct(acc.overall) : "—";
  if ($("h-features")) $("h-features").textContent = fmt(R.total_features);
  if ($("h-rmse")) $("h-rmse").textContent = (R.georef && R.georef.rmse != null) ? R.georef.rmse.toFixed(2) + " m" : "—";
  if ($("h-entities")) $("h-entities").textContent = fmt(R.source_entities);
  if ($("foot-src")) $("foot-src").textContent = R.source || "";
  if ($("badge-track") && R.track) $("badge-track").textContent = R.track;

  // ---- accuracy: overall banner + dimension cards ----
  var WEIGHTS = { semantic: .30, geometric: .25, count: .20, attribute: .15, network: .05, positional: .05 };
  var dimsEl = $("dims");
  if (dimsEl && acc.dimensions) {
    // overall banner (prepended before the grid, inside section)
    var banner = document.createElement("div");
    banner.className = "overall-row";
    banner.innerHTML =
      '<div class="overall-num">' + pct(acc.overall) + '</div>' +
      '<div class="overall-meta"><strong>Overall conversion accuracy</strong>' +
      '<span class="muted">weighted across ' + acc.dimensions.filter(function (d) { return d.evaluated; }).length +
      ' scored dimensions · target ' + pct(acc.threshold) + '</span></div>' +
      '<span class="overall-pass ' + (acc.passed ? "yes" : "no") + '">' +
      (acc.passed ? "meets target" : "below target") + '</span>';
    dimsEl.parentNode.insertBefore(banner, dimsEl);

    acc.dimensions.forEach(function (d) {
      var card = document.createElement("div");
      card.className = "dim" + (d.evaluated ? "" : " skip");
      var lvl = d.score >= 0.9 ? "" : (d.score >= 0.7 ? "warn" : "low");
      var w = WEIGHTS[d.name] != null ? "weight " + Math.round(WEIGHTS[d.name] * 100) + "%" : "";
      card.innerHTML =
        (d.evaluated ? "" : '<span class="pill-skip">not scored</span>') +
        '<div class="dim-top"><span class="dim-name">' + d.name + '</span>' +
        '<span class="dim-score">' + (d.evaluated ? (d.score * 100).toFixed(0) : "—") + '</span></div>' +
        '<div class="dim-weight">' + w + '</div>' +
        '<div class="dim-bar"><div class="dim-fill ' + lvl + '" style="width:' + (d.evaluated ? d.score * 100 : 0) + '%"></div></div>' +
        '<div class="dim-detail">' + (d.details || "") + '</div>';
      dimsEl.appendChild(card);
    });
  }
  if ($("bench-note") && R.benchmark_note) $("bench-note").textContent = R.benchmark_note;

  // ---- per-feature verification ----
  var pf = R.per_feature || {};
  var pfBanner = $("pf-banner");
  if (pfBanner && pf.per_feature_correctness != null) {
    pfBanner.innerHTML =
      '<div class="overall-num">' + pct(pf.per_feature_correctness) + '</div>' +
      '<div class="overall-meta"><strong>Per-feature correctness (independently verified)</strong>' +
      '<span class="muted">' + fmt(pf.overall_verified) + ' of ' + fmt(pf.overall_verifiable) +
      ' verifiable features confirmed by a non-classifier signal</span></div>';
  }
  var pfGrid = $("pf-grid");
  var PF_SIG = {
    manhole: "cross-source: surveyed coordinate label",
    cable: "topology: endpoint anchored to a node/route",
    duct: "geometry: block fingerprint = cross-section",
    annotation: "label: non-empty text present"
  };
  if (pfGrid && pf.by_class) {
    Object.keys(pf.by_class).forEach(function (cls) {
      var v = pf.by_class[cls];
      var lvl = v.rate >= 0.9 ? "" : (v.rate >= 0.7 ? "warn" : "low");
      var sig = PF_SIG[cls] || (v.not_verifiable ? "no independent signal available" : "");
      pfGrid.insertAdjacentHTML("beforeend",
        '<div class="pf"><div class="pf-class">' + cls + '</div>' +
        '<div class="pf-rate">' + (v.total ? (v.rate * 100).toFixed(0) + "%" : "—") + '</div>' +
        '<div class="pf-count">' + fmt(v.verified) + " / " + fmt(v.total) + " verified</div>" +
        '<div class="pf-bar"><div class="pf-fill ' + lvl + '" style="width:' + (v.rate * 100) + '%"></div></div>' +
        '<div class="pf-sig">' + sig + '</div></div>');
    });
  }

  // ---- before / after ----
  if ($("ba-entities")) $("ba-entities").textContent = fmt(R.source_entities);
  if ($("ba-features")) $("ba-features").textContent = fmt(R.total_features);
  var rawList = $("ba-raw-list");
  if (rawList && R.raw_breakdown) {
    R.raw_breakdown.forEach(function (r) {
      rawList.insertAdjacentHTML("beforeend", "<li><span>" + r[0] + "</span><span>" + fmt(r[1]) + "</span></li>");
    });
  }
  var cleanList = $("ba-clean-list");
  if (cleanList && R.counts) {
    var order = ["cable", "manhole", "duct", "annotation", "control_point"];
    order.forEach(function (k) {
      if (R.counts[k] != null) cleanList.insertAdjacentHTML("beforeend",
        "<li><span>" + k + "</span><span>" + fmt(R.counts[k]) + "</span></li>");
    });
  }

  // ---- pipeline ----
  var pipeEl = $("pipe");
  var STAGES = R.pipeline || [
    ["Ingest / Normalize", "DWG→DXF via LibreDWG; encoding-safe", "Ingest gate"],
    ["Profile", "inventory layers, blocks, extent", "Profile gate"],
    ["Parse", "ezdxf → typed features + provenance", "Parse gate"],
    ["Classify", "rules + reviewed block-codes, evidence-gated", "Classify gate"],
    ["Topology + Refine", "clean, drop noise, snap routes", "Topology gate"],
    ["Network", "node-edge graph + junction synthesis", "Network gate"],
    ["Georeference", "GCP fit from surveyed labels", "Georef gate"],
    ["Warehouse", "GeoPackage + schema + styles", "Warehouse gate"],
    ["Accuracy", "6-dimension scoring vs ground truth", "Accuracy gate"]
  ];
  if (pipeEl) STAGES.forEach(function (s) {
    pipeEl.insertAdjacentHTML("beforeend",
      '<li><span class="p-name">' + s[0] + '</span>' +
      '<span class="p-desc">' + s[1] + '</span>' +
      '<span class="p-gate">✓ ' + s[2] + '</span></li>');
  });

  // ---- evidence ----
  var evEl = $("ev-grid");
  if (evEl && R.evidence) {
    R.evidence.slice(0, 8).forEach(function (e) {
      var labels = (e.nearest_text_top || []).slice(0, 4)
        .map(function (t) { return '<span>' + t[0] + " ·" + t[1] + '</span>'; }).join("");
      evEl.insertAdjacentHTML("beforeend",
        '<div class="ev"><div class="ev-block">' + (e.block || "—") + '</div>' +
        '<div class="ev-count">' + fmt(e.count) + '</div>' +
        '<div class="ev-reason">' + (e.reason || "") + '</div>' +
        '<div class="ev-labels">' + labels + '</div></div>');
    });
  }
})();
