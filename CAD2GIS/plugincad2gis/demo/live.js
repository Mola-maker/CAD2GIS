/* live demo — interactive map + SSE live converter */
(function () {
  var reviewState = { diagnostics: { issues: [] }, proposals: { proposals: [] }, corrections: { records: [] }, verification: {} };

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function issueRows(items) {
    if (!items.length) return '<p class="muted">No issues loaded.</p>';
    return '<table class="review-table"><thead><tr><th>Severity</th><th>Type</th><th>Class</th><th>Handle</th><th>Status</th></tr></thead><tbody>' +
      items.map(function (i) {
        return '<tr><td class="sev-' + esc(i.severity) + '">' + esc(i.severity) + '</td><td>' + esc(i.issue_type) +
          '</td><td>' + esc(i.feature_class) + '</td><td>' + esc(i.source_handle) + '</td><td>' + esc(i.status) + '</td></tr>';
      }).join("") + '</tbody></table>';
  }

  function correctionRows(items) {
    if (!items.length) return '<p class="muted">No correction ledger records loaded.</p>';
    return '<table class="review-table"><thead><tr><th>Status</th><th>Patch</th><th>Type</th><th>Handle</th><th>Reason</th></tr></thead><tbody>' +
      items.map(function (r) {
        return '<tr><td>' + esc(r.status) + '</td><td>' + esc(r.patch_id) + '</td><td>' + esc(r.patch_type) +
          '</td><td>' + esc(r.source_handle) + '</td><td>' + esc(r.reason) + '</td></tr>';
      }).join("") + '</tbody></table>';
  }

  function renderReview(tab) {
    var panel = document.getElementById("review-panel");
    if (!panel) return;
    var issues = reviewState.diagnostics.issues || [];
    var proposals = reviewState.proposals.proposals || [];
    var records = reviewState.corrections.records || [];
    if (tab === "issues") panel.innerHTML = issueRows(issues);
    else if (tab === "evidence") panel.innerHTML = issueRows(issues.filter(function (i) { return i.evidence; }));
    else if (tab === "corrections") panel.innerHTML = correctionRows(records);
    else if (tab === "report") panel.innerHTML =
      '<table class="review-table"><tbody><tr><th>Verification</th><td>' + esc(reviewState.verification.status || "loaded") +
      '</td></tr><tr><th>Issues</th><td>' + issues.length + '</td></tr><tr><th>Proposals</th><td>' + proposals.length +
      '</td></tr><tr><th>Corrections</th><td>' + records.length + '</td></tr></tbody></table>';
    else panel.innerHTML = '<div class="review-grid">' +
      '<div class="review-metric"><strong>' + issues.length + '</strong><span>diagnostic issues</span></div>' +
      '<div class="review-metric"><strong>' + proposals.length + '</strong><span>doctor proposals</span></div>' +
      '<div class="review-metric"><strong>' + records.length + '</strong><span>ledger records</span></div>' +
      '<div class="review-metric"><strong>' + esc(reviewState.verification.status || "not_run") + '</strong><span>verification</span></div>' +
      '</div>';
  }

  Promise.all([
    fetch("/api/diagnostics").then(function (r) { return r.json(); }).catch(function () { return { issues: [] }; }),
    fetch("/api/proposals").then(function (r) { return r.json(); }).catch(function () { return { proposals: [] }; }),
    fetch("/api/corrections").then(function (r) { return r.json(); }).catch(function () { return { records: [] }; }),
    fetch("/api/verification").then(function (r) { return r.json(); }).catch(function () { return {}; })
  ]).then(function (all) {
    reviewState.diagnostics = all[0]; reviewState.proposals = all[1]; reviewState.corrections = all[2]; reviewState.verification = all[3];
    renderReview("overview");
  });

  Array.prototype.forEach.call(document.querySelectorAll(".review-tab"), function (btn) {
    btn.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".review-tab"), function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      renderReview(btn.getAttribute("data-review-tab"));
    });
  });
  var $ = function (id) { return document.getElementById(id); };
  var pct = function (x) { return x == null ? "—" : (x * 100).toFixed(1) + "%"; };

  // ---- map ----
  var map = L.map("map-canvas").setView([0, 0], 2);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap", maxZoom: 19
  }).addTo(map);

  var STYLES = {
    manhole: { color: "#C9663F", radius: 3, fill: true, fillColor: "#C9663F", fillOpacity: 0.9 },
    cable: { color: "#FF8C00", weight: 0.8, opacity: 0.85 },
    duct: { color: "#1E90FF", weight: 1.0, opacity: 0.8, dashArray: "4,3" },
    annotation: { color: "#787878", radius: 1.4, fill: true, fillOpacity: 0.5 }
  };

  fetch("/api/layers").then(function (r) { return r.json(); }).then(function (layers) {
    if (layers.error) { $("popup").textContent = layers.error; $("popup").classList.add("show"); return; }
    var allx = [], ally = [];
    Object.keys(layers).forEach(function (cls) {
      var gj = layers[cls], st = STYLES[cls] || {};
      var layer = L.geoJSON(gj, {
        pointToLayer: function (f, ll) { return L.circleMarker(ll, st); },
        style: function () { return st; },
        onEachFeature: function (f, l) {
          var p = f.properties || {};
          var html = "<strong>" + cls + "</strong><br>" +
            (p.src_layer ? "layer: " + p.src_layer + "<br>" : "") +
            (p.src_block ? "block: " + p.src_block + "<br>" : "") +
            (p.src_handle ? "handle: " + p.src_handle + "<br>" : "") +
            (p.facility ? "facility: " + p.facility : "");
          l.bindPopup(html);
          l.on("click", function () {
            var pp = $("popup"); pp.classList.add("show");
            pp.innerHTML = '<div class="ph">' + cls + ' provenance</div><div class="pv">' +
              (p.src_layer || "—") + " · " + (p.src_block || "—") + " · " + (p.src_handle || "—") + "</div>";
          });
        }
      }).addTo(map);
      // collect coords to fit bounds
      try {
        var b = layer.getBounds();
        if (b.isValid()) { allx.push(b.getWest(), b.getEast()); ally.push(b.getSouth(), b.getNorth()); }
      } catch (e) {}
    });
    if (allx.length) {
      map.fitBounds([[Math.min.apply(null, ally), Math.min.apply(null, allx)],
                     [Math.max.apply(null, ally), Math.max.apply(null, allx)]], { padding: [30, 30] });
    }
  }).catch(function (e) { $("popup").textContent = "map load failed: " + e; $("popup").classList.add("show"); });

  // ---- accuracy (latest run) ----
  fetch("/api/report").then(function (r) { return r.json(); }).then(function (rep) {
    var acc = rep.accuracy || rep;
    if (!acc.dimensions) return;
    var banner = $("acc-banner");
    banner.innerHTML = '<div class="overall-num">' + pct(acc.overall) + '</div>' +
      '<div class="overall-meta"><strong>Overall conversion accuracy</strong>' +
      '<span class="muted">target ' + pct(acc.threshold) + '</span></div>';
    var dims = $("acc-dims");
    acc.dimensions.forEach(function (d) {
      var lvl = d.score >= 0.9 ? "" : (d.score >= 0.7 ? "warn" : "low");
      dims.insertAdjacentHTML("beforeend",
        '<div class="dim' + (d.evaluated ? "" : " skip") + '">' +
        '<div class="dim-top"><span class="dim-name">' + d.name + '</span>' +
        '<span class="dim-score">' + (d.evaluated ? (d.score * 100).toFixed(0) : "—") + '</span></div>' +
        '<div class="dim-bar"><div class="dim-fill ' + lvl + '" style="width:' + (d.evaluated ? d.score * 100 : 0) + '%"></div></div>' +
        '<div class="dim-detail">' + (d.details || "") + '</div></div>');
    });
  }).catch(function () { /* accuracy optional */ });

  // ---- live converter (SSE) ----
  var STAGES = ["parse", "topology", "network", "georeference", "accuracy", "warehouse"];
  var pipeEl = $("live-pipe");
  STAGES.forEach(function (s) {
    pipeEl.insertAdjacentHTML("beforeend",
      '<li data-stage="' + s + '"><span class="p-name">' + s + '</span>' +
      '<span class="p-desc"></span><span class="p-gate">waiting</span></li>');
  });

  function setStage(name, state, msg) {
    var li = pipeEl.querySelector('li[data-stage="' + name + '"]');
    if (!li) return;
    li.classList.remove("active", "done", "err");
    li.classList.add(state);
    li.querySelector(".p-gate").textContent = state === "done" ? "✓ done" : (state === "err" ? "✗ error" : "running…");
    if (msg) li.querySelector(".p-desc").textContent = msg;
  }

  $("go").addEventListener("click", async function () {
    var file = $("file").files[0];
    if (!file) { $("status").textContent = "choose a DXF/DWG first"; return; }
    $("go").disabled = true; $("status").textContent = "converting…";
    STAGES.forEach(function (s) { setStage(s, "", ""); });
    $("result").innerHTML = "";

    var fd = new FormData(); fd.append("file", file);
    var resp = await fetch("/api/convert", { method: "POST", body: fd });
    var reader = resp.body.getReader(); var decoder = new TextDecoder();
    var buf = "";
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      var parts = buf.split("\n\n"); buf = parts.pop();
      parts.forEach(function (frame) {
        var m = frame.match(/event: (\w+)\ndata: (.+)/s);
        if (!m) return;
        var data = JSON.parse(m[2]);
        if (m[1] === "stage") setStage(data.stage, data.status === "done" ? "done" : "active",
          data.connectivity != null ? "connectivity " + (data.connectivity * 100).toFixed(1) + "%" :
          (data.entities != null ? data.entities + " entities" : ""));
        else if (m[1] === "done") {
          $("status").textContent = "done";
          $("result").innerHTML = '<div class="rrow"><div class="rnum">' + pct(data.overall) + '</div>' +
            '<div class="rcaps"><strong>Conversion complete</strong><span>overall accuracy for ' + file.name + '</span></div></div>';
        } else if (m[1] === "error") {
          setStage("parse", "err", data.message); $("status").textContent = "error";
        }
      });
    }
    $("go").disabled = false;
  });
})();
