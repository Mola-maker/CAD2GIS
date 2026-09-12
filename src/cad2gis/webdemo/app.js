const $ = (selector) => document.querySelector(selector);
const pageConfig = JSON.parse(document.getElementById("cad2gis-page-config")?.textContent || "{}");
// The installed application selects a run per login; keep every page bound to
// the selection that opened it even when another browser tab opens another run.
// Standalone review and the public demo have no application page identity.
const applicationReviewId = new URLSearchParams(location.search).get("review");
if (applicationReviewId && !document.getElementById("cad2gis-review-theme")) {
  const atelier = document.createElement("link");
  atelier.id = "cad2gis-review-theme";
  atelier.rel = "stylesheet";
  atelier.href = "/_app/assets/review-theme.css";
  document.head.append(atelier);
  // Installed clients open a selected result directly, including on mobile
  // WebViews whose initial hash scroll can move while local fonts load.
  const landing = document.getElementById("hero-page");
  if (landing) landing.style.display = "none";
}
let reviewPageExpired = false;

const terminalEvent = (level, message) => {
  const root = $("#terminal-log");
  if (!root) return;
  root.querySelector(".terminal-empty")?.remove();
  const row = document.createElement("div");
  row.className = `terminal-line${level === "error" ? " is-error" : level === "warn" ? " is-warn" : ""}`;
  const timestamp = document.createElement("time");
  timestamp.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const tag = document.createElement("b");
  tag.textContent = level.toUpperCase();
  const text = document.createElement("span");
  text.textContent = message;
  row.append(timestamp, tag, text);
  root.append(row);
  while (root.children.length > 80) root.firstElementChild?.remove();
  root.scrollTop = root.scrollHeight;
};

const copyText = async (value, successMessage) => {
  const text = String(value || "").trim();
  if (!text) throw new Error("没有可复制的内容");
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
  } else {
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    if (!document.execCommand("copy")) throw new Error("浏览器拒绝剪贴板访问");
    input.remove();
  }
  toast(successMessage);
  terminalEvent("ok", successMessage);
};

const activateTab = (name) => {
  document.querySelectorAll(".tab-button").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const active = panel.id === `tab-${name}`;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
};

const toast = (message, error = false) => {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = "toast"; }, 2800);
};

const fetchJSON = async (url, options = {}) => {
  if (window.CAD2GIS_DEMO?.active) {
    return window.CAD2GIS_DEMO.request(url, options);
  }
  const headers = new Headers(options.headers);
  if (applicationReviewId) headers.set("X-CAD2GIS-Review", applicationReviewId);
  const response = await fetch(url, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (applicationReviewId && response.status === 409
        && body.detail?.includes("此页面的审查结果已切换")) expireReviewPage();
    const message = body.detail || `${response.status} ${response.statusText}`;
    terminalEvent("error", `${options.method || "GET"} ${url}: ${message}`);
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
};

const format = new ol.format.GeoJSON();
const localLayers = new Map();
const previewLayers = new Map();
const layerCollections = new Map();
const geographicCollections = new Map();
let localExtent = ol.extent.createEmpty();
const gcpLocalSource = new ol.source.Vector();
const gcpMapSource = new ol.source.Vector();
let controls = [];
let pendingCad = null;
let pendingCadEvidence = null;
let registrationPreview = null;
let previewEpoch = 0;
let runSummary = null;
let lastTargetCoordinate = "";
let matchingBusy = false;
let controlsMutationPending = false;
let resultReloadPending = false;
let activePreviewKind = null;
let previewIsCurrent = false;
let selectedGcpId = null;
const evidenceLayers = new Map();
let evidenceLoaded = false;
let evidenceLoading = false;
let evidenceLoadRequest = 0;

const expireReviewPage = () => {
  reviewPageExpired = true;
  $("#connection-dot").className = "status-dot offline";
  $("#connection-label").textContent = "请重新打开结果";
  $("#matching-status").textContent = "此审查页面已失效。请返回 CAD2GIS 工作台，重新打开需要审查的结果。";
  updateMatchingActions();
};

const matchingRecord = () => {
  const matching = registrationPreview?.matching || runSummary?.osm_matching;
  return matching?.record || matching || null;
};

const matchingStatusText = (matching) => ({
  matched: "OSM 匹配已应用",
  coarse: "OSM 粗略定位已应用",
  unavailable: "OSM 参考暂不可用",
  abstained: "OSM 匹配待补充证据",
}[matching?.status] || "等待匹配状态");

const updateMatchingActions = () => {
  const demo = Boolean(window.CAD2GIS_DEMO?.active);
  const busy = matchingBusy || controlsMutationPending || reviewPageExpired;
  const matching = matchingRecord();
  $("#export-gcp").disabled = demo || busy || resultReloadPending
    || !registrationPreview?.matching_ready || !registrationPreview?.controls_sha256
    || !matching?.match_sha256 || matching.revision == null;
  $("#export-gcp").textContent = matchingBusy ? "正在二次匹配…" : "二次匹配并生成新结果";
  $("#preview-gcp").disabled = busy;
  $("#preview-gcp").textContent = resultReloadPending ? "重新加载匹配结果" : "预览人工校正";
  ["#send-coordinate", "#gcp-role", "#target-lon", "#target-lat", "#use-map-center", "#clear-pending"]
    .forEach((selector) => { $(selector).disabled = busy || resultReloadPending; });
  document.querySelectorAll("[data-delete-gcp], [data-select-gcp]").forEach((button) => {
    button.disabled = busy || resultReloadPending;
  });
  const selected = controls.find((pair) => pair.id === selectedGcpId);
  const canAdjust = Boolean(selected) && !demo && !busy && !resultReloadPending;
  document.querySelectorAll("[data-adjust-gcp], #gcp-step, #center-gcp").forEach((button) => {
    button.disabled = !canAdjust;
  });
  $("#undo-gcp").disabled = !canAdjust || !selected?.edit?.undo_targets?.length;
  $("#reset-gcp").disabled = !canAdjust || !selected?.edit?.initial_target;
  updatePrecisionState();
  updatePickGuide();
  $("#matching-version").textContent = matching?.revision != null ? `第 ${matching.revision} 版` : "—";
};

const palette = {
  CABLE: "#00a849", CABLE_SEGMENT: "#00a849", PTECH: "#d75173",
  BOITE: "#0a8ec1", SITE: "#d03024", INFRASTRUCTURE: "#65767b",
  ZNRO: "#8d5bc2", ZPM: "#e58522", IMB: "#7b8a55",
};

const displayLabel = (feature) => {
  // A map label is an asset/route identity backed by CAD text.  Length and
  // DIMENSION state stay in their dedicated evidence fields and review pane;
  // they must never masquerade as the feature's business label.
  return String(feature.get("display_label") || "");
};

const featureStyle = (feature, resolution) => {
  const name = String(feature.get("_layer") || feature.get("feature_class") || "").toUpperCase();
  const color = palette[name] || "#557b78";
  const label = displayLabel(feature);
  // Resolution-driven styling: at overview scale keep every symbol thin and
  // small so the drawing does not collapse into a pixel blob; as the view
  // zooms in, strokes/points grow to readable, clickable sizes and labels
  // appear.  CAD coordinates are local metres, so the resolution is
  // metres-per-pixel — a 550 m drawing at 800 px shows ~0.7 m/px.
  const scale = resolution != null ? 1.0 / Math.max(resolution, 1e-9) : 1.0;
  const lineWidth = Math.min(5, Math.max(0.6, scale * 0.8));
  const pointRadius = Math.min(9, Math.max(2.5, scale * 0.6));
  const showLabels = resolution != null && resolution < 2.0;
  return new ol.style.Style({
    stroke: new ol.style.Stroke({ color, width: name.startsWith("CABLE") ? lineWidth + 1 : lineWidth }),
    fill: new ol.style.Fill({ color: `${color}22` }),
    image: new ol.style.Circle({
      radius: pointRadius,
      fill: new ol.style.Fill({ color }),
      stroke: new ol.style.Stroke({ color: "#fff", width: Math.max(0.5, pointRadius * 0.25) }),
    }),
    text: label && showLabels ? new ol.style.Text({
      text: label,
      offsetY: -12,
      font: "500 11px 'Noto Sans SC', sans-serif",
      fill: new ol.style.Fill({ color: "#162126" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
    }) : undefined,
  });
};

const gcpStyle = (feature, resolution) => {
  const scale = resolution != null ? 1.0 / Math.max(resolution, 1e-9) : 1.0;
  const radius = Math.min(14, Math.max(6, scale * 0.9));
  const showLabel = resolution != null && resolution < 2.0;
  return new ol.style.Style({
    image: new ol.style.Circle({
      radius,
      fill: new ol.style.Fill({ color: feature.get("role") === "pending" ? "#007d91" : feature.get("role") === "check" ? "#7257d6" : "#f08b28" }),
      stroke: new ol.style.Stroke({ color: feature.get("gcp_id") === selectedGcpId ? "#173d68" : "#fff", width: feature.get("gcp_id") === selectedGcpId ? 3 : Math.max(1.5, radius * 0.22) }),
    }),
    text: showLabel || feature.get("gcp_id") === selectedGcpId || feature.get("role") === "pending" ? new ol.style.Text({
      text: String(feature.get("label") || ""),
      offsetY: -14,
      font: "600 11px 'Noto Sans SC', sans-serif",
      fill: new ol.style.Fill({ color: "#172328" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
    }) : undefined,
  });
};

const localProjection = new ol.proj.Projection({
  code: "CAD:LOCAL", units: "m", extent: [-1e12, -1e12, 1e12, 1e12],
});
// CAD drawings are small (a few hundred metres) and their points are only
// separable at high magnification.  No zoom ceiling below the OpenLayers
// maximum, and a one-click fit control to zoom back out to the full extent.
const mapControls = (extent) => {
  const controls = ol.control.defaults.defaults();
  return extent ? controls.extend([new ol.control.ZoomToExtent({ extent })]) : controls;
};
const localMap = new ol.Map({
  target: "local-map",
  layers: [new ol.layer.Vector({ source: gcpLocalSource, style: gcpStyle, zIndex: 100 })],
  controls: mapControls(pageConfig.localExtentControl ? localProjection.getExtent() : null),
  view: new ol.View({
    projection: localProjection, center: [0, 0], zoom: 2,
    maxZoom: 50, constrainResolution: false,
  }),
});
const osmBaseLayer = new ol.layer.Tile({ visible: false });
const worldMap = new ol.Map({
  target: "map",
  layers: [
    osmBaseLayer,
    new ol.layer.Vector({ source: gcpMapSource, style: gcpStyle, zIndex: 100 }),
  ],
  controls: mapControls(pageConfig.worldExtentControl),
  view: new ol.View({
    center: ol.proj.fromLonLat(pageConfig.worldCenterLonLat || [0, 0]),
    zoom: pageConfig.worldZoom ?? 2,
    maxZoom: 50, constrainResolution: false,
  }),
});

const refreshPreview = async ({ fitView = false } = {}) => {
  const epoch = ++previewEpoch;
  const fittedCollections = new Map();
  const fit = registrationPreview?.preview_fit || registrationPreview?.fit;
  let previewError = "";
  if (controls.length && !window.CAD2GIS_DEMO?.active) {
    if (!fit) {
      previewError = registrationPreview?.preview_error || "请补充有效训练点以预览人工校正";
    } else {
      try {
        const controlsSha = registrationPreview.controls_sha256;
        const collections = await Promise.all([...layerCollections.keys()].map(async (name) => [name,
          await fetchJSON(`/api/registration/preview/${encodeURIComponent(name)}?expected_controls_sha256=${encodeURIComponent(controlsSha)}`),
        ]));
        if (epoch !== previewEpoch) return;
        for (const [name, collection] of collections) fittedCollections.set(name, collection);
      } catch (error) {
        if (epoch !== previewEpoch) return;
        previewError = `人工校正预览失败：${error.message}`;
      }
    }
  }
  if (previewError && previewLayers.size) {
    const retained = activePreviewKind === "correction" ? "上次有效人工校正预览" : "当前已应用结果";
    $("#fit-model").textContent = `${previewError}；保留${retained}`;
    $("#fit-rmse").textContent = "—（本次校正尚不可用）";
    if (!matchingBusy) $("#matching-status").textContent = `控制点已保存，${retained}仍可查看。`;
    previewIsCurrent = false;
    updatePrecisionState();
    return false;
  }
  const nominalPreview = (
    geographicCollections.size > 0 || runSummary?.demo?.nominal_map_preview
  );
  const nominalTransform = runSummary?.demo?.nominal_transform
    || { a: 1, b: 0, tx: 0, ty: 0 };
  if (!fittedCollections.size && !nominalPreview) {
    $("#fit-model").textContent = previewError || "添加控制点后生成 GIS 匹配预览";
    $("#fit-rmse").textContent = "—";
    previewIsCurrent = false;
    updatePrecisionState();
    return false;
  }
  // Build the complete replacement before removing the last usable preview.
  const replacementLayers = new Map();
  for (const [name, collection] of layerCollections) {
    const geographicCollection = fittedCollections.get(name) || geographicCollections.get(name);
    const features = geographicCollection
      ? format.readFeatures(geographicCollection, {
        dataProjection: "EPSG:4326",
        featureProjection: "EPSG:3857",
      })
      : format.readFeatures(collection);
    for (const feature of features) {
      feature.set("_layer", name);
      if (geographicCollection) continue;
      feature.getGeometry()?.applyTransform((input, output, stride) => {
        const target = output || input;
        for (let i = 0; i < input.length; i += stride) {
          const [x, y] = [
              nominalTransform.a * input[i] - nominalTransform.b * input[i + 1]
                + nominalTransform.tx,
              nominalTransform.b * input[i] + nominalTransform.a * input[i + 1]
                + nominalTransform.ty,
            ];
          target[i] = x;
          target[i + 1] = y;
          for (let j = 2; j < stride; j += 1) target[i + j] = input[i + j];
        }
        return target;
      });
    }
    const layer = new ol.layer.Vector({
      source: new ol.source.Vector({ features }),
      style: featureStyle,
      declutter: true,
      zIndex: 10,
    });
    layer.setVisible(localLayers.get(name)?.getVisible() ?? true);
    replacementLayers.set(name, layer);
  }
  if (epoch !== previewEpoch) return false;
  const firstPreview = previewLayers.size === 0;
  for (const layer of previewLayers.values()) worldMap.removeLayer(layer);
  previewLayers.clear();
  for (const [name, layer] of replacementLayers) {
    worldMap.addLayer(layer);
    previewLayers.set(name, layer);
  }
  activePreviewKind = fittedCollections.size ? "correction" : "applied";
  previewIsCurrent = !previewError;
  const matching = matchingRecord();
  $("#fit-model").textContent = fittedCollections.size
    ? `人工平移校正预览 · ${fit.target_crs || runSummary?.crs?.target_crs || "目标 CRS"}`
    : !window.CAD2GIS_DEMO?.active && matching
      ? `${matchingStatusText(matching)} · ${runSummary?.crs?.target_crs || "GIS 坐标"}`
    : geographicCollections.size > 0
      ? `名义 ${runSummary?.crs?.target_crs || "目标 CRS"} → EPSG:4326 预览（仍需独立 GCP 验证）`
    : runSummary?.demo?.map_anchor
      ? `地图锚点预览 · ${runSummary.demo.map_anchor.place_name || "位置已知"}（仍需 GCP）`
      : "名义 EPSG:3857 预览（未证明绝对精度）";
  $("#fit-rmse").textContent = fittedCollections.size
    ? `训练 ${fit.train_metrics?.rmse_m?.toFixed(3) ?? "—"} m；独立检查 ${fit.check_metrics?.rmse_m?.toFixed(3) ?? "—"} m`
    : "—（请使用控制点验证）";
  if (window.CAD2GIS_DEMO?.active) {
    $("#fit-model").textContent += "；静态演示仅记录选点";
    $("#matching-status").textContent = "静态演示：控制点仅在浏览器保存；请在本地工作台执行人工校正和二次匹配。";
  } else if (!matchingBusy) {
    $("#matching-status").textContent = fittedCollections.size
      ? "正在查看人工校正预览。点击二次匹配生成新结果后，可继续修改同一组控制点。"
      : `${matchingStatusText(matching)}。可添加人工点校正；OSM 提供相对地图参考。`;
  }
  if (previewError) {
    $("#fit-model").textContent += `；${previewError}`;
  }
  const extents = [...previewLayers.values()].map((layer) => layer.getSource().getExtent());
  if (extents.length && (firstPreview || fitView)) {
    const extent = extents.reduce((acc, value) => ol.extent.extend(acc, value), ol.extent.createEmpty());
    if (!ol.extent.isEmpty(extent)) worldMap.getView().fit(extent, { padding: [50, 50, 50, 50] });
  }
  updatePrecisionState();
  return !previewError;
};

const renderControls = async (registration = null) => {
  registrationPreview = registration;
  previewIsCurrent = false;
  if (!controls.some((pair) => pair.id === selectedGcpId)) selectedGcpId = controls[controls.length - 1]?.id || null;
  gcpLocalSource.clear();
  gcpMapSource.clear();
  controls.forEach((pair, index) => {
    for (const [source, coordinate] of [[gcpLocalSource, pair.cad], [gcpMapSource, pair.map]]) {
      const feature = new ol.Feature(new ol.geom.Point(coordinate));
      feature.setProperties({ role: pair.role, gcp_id: pair.id, label: `${index + 1}${pair.role === "check" ? "C" : ""}` });
      source.addFeature(feature);
    }
  });
  const list = $("#gcp-list");
  list.textContent = "";
  if (!controls.length) {
    const empty = document.createElement("p");
    empty.className = "gcp-empty";
    empty.textContent = "还没有控制点。先在左侧 CAD 中选点。";
    list.append(empty);
  }
  controls.forEach((pair, index) => {
    const row = document.createElement("div");
    row.className = "gcp-row";
    const select = document.createElement("button");
    select.className = "gcp-select";
    select.dataset.selectGcp = pair.id;
    select.setAttribute("aria-pressed", String(pair.id === selectedGcpId));
    const title = document.createElement("strong");
    title.textContent = `${index + 1} · ${pair.role === "check" ? "独立检查" : "训练"}`;
    const coordinate = document.createElement("span");
    coordinate.textContent = `${pair.lonLat[0].toFixed(8)}, ${pair.lonLat[1].toFixed(8)}`;
    coordinate.title = `CAD: ${pair.cad[0].toFixed(3)}, ${pair.cad[1].toFixed(3)} → WGS84: ${coordinate.textContent}`;
    select.append(title, coordinate);
    select.addEventListener("click", () => selectControl(pair.id));
    const remove = document.createElement("button");
    remove.dataset.deleteGcp = pair.id;
    remove.className = "gcp-delete";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `删除第 ${index + 1} 个控制点`);
    row.append(select, remove);
    list.append(row);
  });
  updateSelectedControl();
  const trainCount = controls.filter((item) => item.role === "train").length;
  const checkCount = controls.filter((item) => item.role === "check").length;
  $("#fit-coverage").textContent = `${trainCount} 个训练点；${checkCount} 个独立检查点`;
  updateMatchingActions();
  document.querySelector('[data-stage="registration"]')?.classList.toggle("is-complete", Boolean(registration?.matching_ready));
  document.querySelectorAll("[data-delete-gcp]").forEach((button) => {
    button.addEventListener("click", async () => {
      const pair = controls.find((item) => item.id === button.dataset.deleteGcp);
      if (!pair || matchingBusy || controlsMutationPending || resultReloadPending) return;
      controlsMutationPending = true;
      updateMatchingActions();
      try {
        await fetchJSON(`/api/review/features/${encodeURIComponent(pair.id)}?expected_revision=${pair.revision}`, { method: "DELETE" });
        invalidateCorrection();
        terminalEvent("info", `已删除控制点 ${pair.id}`);
        await loadControls();
      } catch (error) {
        toast(error.message, true);
      } finally {
        controlsMutationPending = false;
        updateMatchingActions();
      }
    });
  });
  await refreshPreview();
};

let controlsRequest = 0;
const loadControls = async () => {
  const request = ++controlsRequest;
  const [collection, registration] = await Promise.all([
    fetchJSON("/api/review/features"),
    fetchJSON("/api/registration"),
  ]);
  if (request !== controlsRequest) return false;
  const transferred = new Map(registration.controls.map((item) => [item.point_id, item]));
  controls = collection.features
    .filter((feature) => feature.properties?._kind === "cad_map_gcp")
    .map((feature) => {
      const lonLat = feature.geometry.coordinates.map(Number);
      const target = transferred.get(String(feature.id));
      return {
        id: String(feature.id),
        revision: Number(feature.properties._review_revision || 0),
        role: feature.properties.role || "train",
        cad: [Number(feature.properties.cad_x), Number(feature.properties.cad_y)],
        map: ol.proj.fromLonLat(lonLat),
        lonLat,
        target,
        edit: feature.properties._gcp_edit || null,
      };
    });
  await renderControls(registration);
  return true;
};

const nearestCadEvidence = (coordinate) => {
  let best = null;
  for (const [layerName, layer] of localLayers) {
    if (!layer.getVisible()) continue;
    const candidate = layer.getSource().getClosestFeatureToCoordinate(coordinate);
    if (!candidate?.getGeometry()) continue;
    const closest = candidate.getGeometry().getClosestPoint(coordinate);
    const distance = Math.hypot(closest[0] - coordinate[0], closest[1] - coordinate[1]);
    if (!best || distance < best.distance) {
      best = { layerName, feature: candidate, coordinate: closest, distance };
    }
  }
  const tolerance = Math.max(localMap.getView().getResolution() * 14, 0.01);
  return best && best.distance <= tolerance ? best : null;
};

const selectCadPoint = (coordinate) => {
  if (reviewPageExpired || matchingBusy || controlsMutationPending || resultReloadPending) return;
  const evidence = nearestCadEvidence(coordinate);
  if (!evidence) {
    pendingCad = null;
    pendingCadEvidence = null;
    updatePickGuide();
    toast("请靠近真实 CAD 几何点击，不能用空白区或图框作为控制点", true);
    return;
  }
  pendingCad = [...evidence.coordinate];
  $("#target-lon").value = "";
  $("#target-lat").value = "";
  pendingCadEvidence = {
    layer: evidence.layerName,
    source_entity_key: evidence.feature.get("source_entity_key") || "",
    source_handle: evidence.feature.get("source_handle") || "",
  };
  $("#cad-coordinate").textContent = `${pendingCad[0].toFixed(3)}, ${pendingCad[1].toFixed(3)}（已吸附 ${evidence.layerName}）`;
  terminalEvent("info", `CAD 点已吸附到 ${evidence.layerName}：${pendingCad[0].toFixed(3)}, ${pendingCad[1].toFixed(3)}`);
  updatePickGuide();
  for (const feature of gcpLocalSource.getFeatures()) {
    if (feature.get("role") === "pending") gcpLocalSource.removeFeature(feature);
  }
  const marker = new ol.Feature(new ol.geom.Point(pendingCad));
  marker.setProperties({ role: "pending", label: "待配对" });
  gcpLocalSource.addFeature(marker);
  toast("CAD 点已吸附；请在右图点击同一位置或输入经纬度");
};

const savePair = async (cad, lonLat) => {
  if (reviewPageExpired || matchingBusy || controlsMutationPending || resultReloadPending) throw new Error("请等待当前操作完成后再修改控制点");
  if (!Number.isFinite(lonLat[0]) || !Number.isFinite(lonLat[1])
      || lonLat[0] < -180 || lonLat[0] > 180 || lonLat[1] < -90 || lonLat[1] > 90) {
    throw new Error("经纬度必须是有效的 EPSG:4326 坐标");
  }
  controlsMutationPending = true;
  updateMatchingActions();
  try {
    const randomBytes = crypto.getRandomValues(new Uint8Array(16));
    const id = `gcp:${Array.from(randomBytes, byte => byte.toString(16).padStart(2, "0")).join("")}`;
    const role = $("#gcp-role").value;
    await fetchJSON("/api/review/features", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: 0,
        actor: "registration-workspace",
        feature: {
          type: "Feature",
          id,
          geometry: { type: "Point", coordinates: lonLat },
          properties: {
            _kind: "cad_map_gcp",
            role,
            cad_x: cad[0],
            cad_y: cad[1],
            cad_layer: pendingCadEvidence?.layer || "",
            source_entity_key: pendingCadEvidence?.source_entity_key || "",
            source_handle: pendingCadEvidence?.source_handle || "",
          },
        },
      }),
    });
    pendingCad = null;
    pendingCadEvidence = null;
    selectedGcpId = id;
    invalidateCorrection();
    await loadControls();
    const saved = registrationPreview?.controls.find((item) => item.point_id === id);
    if (saved) {
      lastTargetCoordinate = `${saved.target_easting.toFixed(3)}, ${saved.target_northing.toFixed(3)} (${saved.target_crs})`;
      $("#target-coordinate").textContent = lastTargetCoordinate;
      $("#copy-coordinate").disabled = false;
      terminalEvent("ok", `坐标已传送：${lastTargetCoordinate}`);
    }
  } finally {
    controlsMutationPending = false;
    updateMatchingActions();
  }
};

const controlAtPixel = (map, pixel) => map.forEachFeatureAtPixel(pixel, (feature) => feature.get("gcp_id") || undefined, { hitTolerance: 7 });
localMap.on("singleclick", ({ coordinate, pixel }) => {
  if (reviewPageExpired || matchingBusy || controlsMutationPending || resultReloadPending) return;
  const controlId = controlAtPixel(localMap, pixel);
  if (controlId) return selectControl(controlId);
  selectCadPoint(coordinate);
});
worldMap.on("singleclick", async ({ coordinate, pixel }) => {
  if (reviewPageExpired || matchingBusy || controlsMutationPending || resultReloadPending) return;
  const controlId = controlAtPixel(worldMap, pixel);
  if (controlId) return selectControl(controlId);
  const lonLat = ol.proj.toLonLat(coordinate);
  $("#target-lon").value = lonLat[0].toFixed(8);
  $("#target-lat").value = lonLat[1].toFixed(8);
  if (!pendingCad) return;
  try {
    await savePair(pendingCad, lonLat);
    toast("控制点已保存，目标 CRS 坐标已传送");
  } catch (error) {
    toast(error.message, true);
  }
});

const showSelected = (map) => {
  map.on("singleclick", (event) => {
    map.forEachFeatureAtPixel(event.pixel, (feature) => {
      const properties = { ...feature.getProperties() };
      delete properties.geometry;
      $("#feature-properties").textContent = JSON.stringify(properties, null, 2);
      // When picking GCP pairs the operator must see the CAD detail before
      // clicking the map side.  Zooming to the clicked feature makes the
      // picked geometry resolvable instead of buried in an overview blob.
      const geometry = feature.getGeometry();
      if (geometry && map === localMap) {
        const currentZoom = localMap.getView().getZoom() || 0;
        localMap.getView().fit(geometry.getExtent(), {
          padding: [40, 40, 40, 40],
          maxZoom: Math.min(50, currentZoom + 3),
          duration: 300,
          size: localMap.getSize(),
        });
      }
      return true;
    }, { hitTolerance: 6 });
  });
};
showSelected(localMap);
showSelected(worldMap);

const loadLayer = async (descriptor) => {
  if (descriptor.feature_count === 0) return;
  const collection = await fetchJSON(`/api/layers/${encodeURIComponent(descriptor.name)}/local-geojson`);
  layerCollections.set(descriptor.name, collection);
  if (!window.CAD2GIS_DEMO?.active || runSummary?.demo?.has_geographic_layers) {
    const geographic = await fetchJSON(`/api/layers/${encodeURIComponent(descriptor.name)}/geojson`);
    geographicCollections.set(descriptor.name, geographic);
  }
  const features = format.readFeatures(collection);
  features.forEach((feature) => feature.set("_layer", descriptor.name));
  const source = new ol.source.Vector({ features });
  const layer = new ol.layer.Vector({
    source, style: featureStyle, declutter: true, zIndex: 10,
  });
  localMap.addLayer(layer);
  localLayers.set(descriptor.name, layer);
  if (!source.isEmpty()) ol.extent.extend(localExtent, source.getExtent());
};

const loadLayers = async () => {
  const { layers } = await fetchJSON("/api/layers");
  $("#layer-list").innerHTML = "";
  for (const descriptor of layers) {
    const row = document.createElement("label");
    row.className = "layer-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = descriptor.feature_count > 0;
    checkbox.disabled = descriptor.feature_count === 0;
    const name = document.createElement("span");
    name.textContent = descriptor.name;
    const count = document.createElement("small");
    count.textContent = descriptor.feature_count;
    row.append(checkbox, name, count);
    $("#layer-list").append(row);
    if (descriptor.feature_count) await loadLayer(descriptor);
    checkbox.addEventListener("change", (event) => {
      localLayers.get(descriptor.name)?.setVisible(event.target.checked);
      previewLayers.get(descriptor.name)?.setVisible(event.target.checked);
    });
  }
  if (!ol.extent.isEmpty(localExtent)) {
    localMap.getView().fit(localExtent, { padding: [40, 40, 40, 40] });
  }
  const visibleCount = layers.filter((descriptor) => descriptor.feature_count > 0).length;
  terminalEvent("ok", `已加载 ${visibleCount} 个非空图层，共 ${layers.length} 个图层定义`);
};

const clearProjectState = () => {
  for (const layer of localLayers.values()) localMap.removeLayer(layer);
  for (const layer of previewLayers.values()) worldMap.removeLayer(layer);
  localLayers.clear();
  previewLayers.clear();
  layerCollections.clear();
  geographicCollections.clear();
  localExtent = ol.extent.createEmpty();
  gcpLocalSource.clear();
  gcpMapSource.clear();
  controls = [];
  pendingCad = null;
  pendingCadEvidence = null;
  registrationPreview = null;
  activePreviewKind = null;
  previewIsCurrent = false;
  selectedGcpId = null;
  evidenceLoadRequest += 1;
  evidenceLoaded = false;
  evidenceLoading = false;
  for (const layer of evidenceLayers.values()) worldMap.removeLayer(layer);
  evidenceLayers.clear();
  document.querySelectorAll("[data-evidence-layer]").forEach((input) => { input.checked = false; input.disabled = true; });
  $("#evidence-layer-status").textContent = "展开后读取当前匹配证据。";
  $("#evidence-layer-menu").open = false;
  $("#gcp-adjust").hidden = true;
  controlsRequest += 1;
  previewEpoch += 1;
  runSummary = null;
  lastTargetCoordinate = "";
  $("#layer-list").innerHTML = "";
  $("#gcp-list").innerHTML = "";
  $("#feature-properties").textContent = "在任一地图点击对象。";
  $("#cad-coordinate").textContent = "—";
  $("#map-coordinate").textContent = "—";
  $("#target-coordinate").textContent = "—";
  $("#copy-coordinate").disabled = true;
  $("#export-gcp").disabled = true;
  $("#fit-model").textContent = "正在加载匹配结果…";
  $("#fit-rmse").textContent = "—";
  $("#fit-coverage").textContent = "正在读取控制点…";
  $("#matching-version").textContent = "—";
};

const renderProjectMeta = (project) => {
  if (!project) return;
  $("#demo-project-location").textContent = project.location;
  $("#demo-project-description").textContent = project.description;
  $("#demo-project-source-count").textContent = Number(project.source_entity_count).toLocaleString("en-US");
  $("#demo-project-delivery-count").textContent = Number(project.delivery_feature_count).toLocaleString("en-US");
  $("#demo-project-map-reference").textContent = project.map_reference;
  $("#demo-project-sha").textContent = `${project.source_sha256.slice(0, 12)}…`;
};

const setupProjectSelector = async () => {
  const selector = $("#demo-project-select");
  if (!selector || !window.CAD2GIS_DEMO?.active) return;
  const catalog = await window.CAD2GIS_DEMO.catalog();
  selector.innerHTML = catalog.projects.map((project) => (
    `<option value="${project.id}">${project.display_name}</option>`
  )).join("");
  selector.value = window.CAD2GIS_DEMO.activeProjectId;
  const initialProject = catalog.projects.find((project) => project.id === selector.value)
    || catalog.projects.find((project) => project.id === catalog.default_project);
  renderProjectMeta(initialProject);
  selector.addEventListener("change", async () => {
    selector.disabled = true;
    $("#demo-project-loading").hidden = false;
    try {
      const project = await window.CAD2GIS_DEMO.selectProject(selector.value);
      clearProjectState();
      renderProjectMeta(project);
      await boot();
      toast(`已切换到 ${project.display_name}`);
    } catch (error) {
      toast(error.message, true);
      terminalEvent("error", `项目切换失败：${error.message}`);
    } finally {
      selector.disabled = false;
      $("#demo-project-loading").hidden = true;
    }
  });
};

const svgStatusText = (svg) => ({
  completed: "已提取，等待对应复核",
  disabled: "本次转换已关闭 SVG 提取",
  failed: "SVG 提取失败",
  not_recorded: "此运行未记录 SVG 提取",
}[svg?.status] || "此运行未记录 SVG 提取");

const renderSvgCandidates = (svg) => {
  const state = svgStatusText(svg);
  const count = Number.isInteger(svg?.point_count) ? `；已列出 ${svg.point_count} 个交付点` : "";
  $("#svg-candidate-status").textContent = svg?.report_blocker
    ? `${state}；报告暂不可用：${svg.report_blocker}`
    : `${state}${count}。`;
  const link = $("#open-svg-candidates");
  link.hidden = true;
  link.removeAttribute("href");
  if (typeof svg?.report_url === "string"
      && /^\/api\/svg-candidates\/[0-9a-f]{64}\/correspondence\.html$/.test(svg.report_url)) {
    link.href = svg.report_url;
    link.hidden = false;
  }
};

const renderRun = (run) => {
  runSummary = run;
  const matching = run.osm_matching?.record || run.osm_matching;
  $("#run-status").textContent = run.run_status || "UNKNOWN";
  const sourcePath = run.source?.path || "";
  const sourceName = sourcePath.split(/[\\/]/).pop() || "未命名 CAD 项目";
  if (!window.CAD2GIS_DEMO?.active) {
    const selector = $("#demo-project-select");
    selector.textContent = "";
    const option = document.createElement("option");
    option.value = "live";
    option.textContent = sourceName;
    selector.append(option);
    selector.value = "live";
    selector.disabled = true;
    const deliveryCount = Object.values(run.delivery_counts || {})
      .reduce((sum, value) => sum + Number(value || 0), 0);
    $("#demo-project-location").textContent = `${run.crs?.target_crs || "CRS 未声明"} · 本地实时运行`;
    $("#demo-project-description").textContent = `${Object.keys(run.delivery_counts || {}).length} 个交付图层；源事实、坐标与审计工件均来自当前运行。`;
    $("#demo-project-source-count").textContent = Number(run.source_entity_count || 0).toLocaleString("en-US");
    $("#demo-project-delivery-count").textContent = deliveryCount.toLocaleString("en-US");
    $("#demo-project-map-reference").textContent = matching
      ? `${matchingStatusText(matching)} · OSM 相对参考`
      : `源 ${run.crs?.source_crs || "CRS 未声明"} → 交付 ${run.crs?.target_crs || "CRS 未声明"}`;
    $("#demo-project-sha").textContent = run.source?.sha256 ? `${run.source.sha256.slice(0, 12)}…` : "—";
  }
  $("#project-name").textContent = sourceName;
  $("#review-current-source").textContent = sourceName;
  $("#review-current-source").title = sourceName;
  if (!window.CAD2GIS_DEMO?.active) {
    document.body.classList.add("live-review");
    const landing = $("#hero-page");
    if (landing) landing.hidden = true;
  }
  $("#project-source").textContent = sourcePath || "未提供源路径";
  $("#map-reference-note").textContent = matching
    ? `${matchingStatusText(matching)} · 第 ${matching.revision ?? "—"} 版 · 相对地图参考`
    : run.demo?.map_anchor
    ? `${run.demo.map_anchor.display_name || run.demo.map_anchor.place_name} · ${run.demo.map_anchor.precision}`
    : "DWG 声明坐标域 · 尚未用测量 GCP 验证";
  document.querySelector('[data-stage="source"]')?.classList.add("is-complete");
  document.querySelector('[data-stage="validation"]')?.classList.toggle(
    "is-complete", ["VERIFIED", "CONDITIONAL"].includes(run.run_status),
  );
  document.querySelector('[data-stage="delivery"]')?.classList.toggle(
    "is-complete", Boolean(run.artifacts || run.delivery_counts),
  );
  const domain = run.crs?.coordinate_domain
    || run.validation?.georeference?.coordinate_domain
    || run.georeference?.coordinate_domain;
  $("#diagnosis").textContent = domain?.passed === false
    ? "坐标域校验失败：必须通过分布合理的控制点配准，不能直接把局部 CAD 坐标声明为名义 CRS。"
    : "源几何与坐标精度是两个独立问题。请用控制点验证名义 CRS；OSM 视觉重合不等于测量精度。";
  const measurement = run.validation?.segment_delivery || {};
  const rows = [
    ["源文件", run.source?.path || "—"],
    ["源 CRS", run.crs?.source_crs || "—"],
    ["目标 CRS", run.crs?.target_crs || "—"],
    ["运行状态", run.run_status || "—"],
    ["当前结果", run.current_run_dir || run.run_dir || run.run_id || "当前服务加载的运行"],
    ["OSM 匹配", matching ? `${matchingStatusText(matching)} · 第 ${matching.revision ?? "—"} 版` : "未提供匹配记录"],
    ["SVG 提取", svgStatusText(run.svg_candidates)],
    ["CAD 几何长度", `${measurement.count ?? 0} 段`],
    ["独立 DIMENSION", `${measurement.measured ?? 0} 段`],
    ["无 DIMENSION", `${measurement.unmeasured ?? 0} 段（仍有 CAD 几何长度）`],
    ["审查存储", run.review_store || "—"],
    ["源文件重连", run.source_available ? "已按 SHA-256 验证" : (run.source_blocker || "不可用")],
  ];
  $("#run-summary").textContent = "";
  for (const [key, value] of rows) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = key;
    detail.textContent = String(value);
    $("#run-summary").append(term, detail);
  }
  renderSvgCandidates(run.svg_candidates);
  $("#preview-source-note").textContent = window.CAD2GIS_DEMO?.active
    ? "静态演示仅显示已发布的派生结果并记录选点。请在本地工作台执行人工校正和二次匹配。"
    : "右图默认显示当前已应用的 GIS 结果。控制点变化后显示人工校正预览；二次匹配成功后自动加载新结果，并保留控制点供继续修改。";
  updateMatchingActions();
  terminalEvent("ok", `已加载 ${sourceName}，运行状态 ${run.run_status || "UNKNOWN"}`);
};

localMap.on("pointermove", ({ coordinate }) => {
  if (!pendingCad) $("#cad-coordinate").textContent = `${coordinate[0].toFixed(3)}, ${coordinate[1].toFixed(3)}`;
});
worldMap.on("pointermove", ({ coordinate }) => {
  const value = ol.proj.toLonLat(coordinate);
  $("#map-coordinate").textContent = `${value[0].toFixed(6)}, ${value[1].toFixed(6)}`;
});

$("#send-coordinate").addEventListener("click", async () => {
  if (!pendingCad) return toast("请先在左图选择并吸附一个 CAD 点", true);
  if (!$("#target-lon").value.trim() || !$("#target-lat").value.trim()) {
    return toast("请填写经度和纬度；空白值不能作为 0° 坐标保存", true);
  }
  const lon = Number($("#target-lon").value);
  const lat = Number($("#target-lat").value);
  try {
    await savePair(pendingCad, [lon, lat]);
    toast("经纬度已转换并传送到目标 CRS");
  } catch (error) {
    toast(error.message, true);
  }
});
$("#use-map-center").addEventListener("click", () => {
  const [lon, lat] = ol.proj.toLonLat(worldMap.getView().getCenter());
  $("#target-lon").value = lon.toFixed(8);
  $("#target-lat").value = lat.toFixed(8);
  terminalEvent("info", `已读取地图中心：${lon.toFixed(8)}, ${lat.toFixed(8)}`);
});
$("#clear-pending").addEventListener("click", () => {
  pendingCad = null;
  pendingCadEvidence = null;
  for (const feature of gcpLocalSource.getFeatures()) {
    if (feature.get("role") === "pending") gcpLocalSource.removeFeature(feature);
  }
  updatePickGuide();
  toast("已取消当前 CAD 点");
  terminalEvent("info", "已取消待配对 CAD 点");
});
$("#copy-coordinate").addEventListener("click", async () => {
  try {
    await copyText(lastTargetCoordinate, "目标坐标已复制");
  } catch (error) {
    toast(error.message, true);
    terminalEvent("error", error.message);
  }
});
const reloadCurrentResult = async () => {
  reportReviewState("loading");
  try {
    const run = await fetchJSON("/api/run");
    clearProjectState();
    renderRun(run);
    await loadLayers();
    await loadControls();
    resultReloadPending = false;
    updateMatchingActions();
    reportReviewState("ready", "", run.run_dir);
  } catch (error) {
    reportReviewState("error", error.message);
    throw error;
  }
};

$("#preview-gcp").addEventListener("click", async () => {
  if (reviewPageExpired || matchingBusy || controlsMutationPending) return;
  const reloadingResult = resultReloadPending;
  try {
    if (reloadingResult) {
      matchingBusy = true;
      updateMatchingActions();
      await reloadCurrentResult();
      $("#matching-status").textContent = "已加载最新匹配结果，原有控制点可继续修改。";
      toast("已加载二次匹配结果，控制点可继续修改");
    } else {
      await loadControls();
      toast(window.CAD2GIS_DEMO?.active
        ? "静态演示仅记录选点，请在本地工作台预览人工校正"
        : "已更新控制点与地图预览，请查看校正状态");
    }
  } catch (error) {
    toast(error.message, true);
    terminalEvent("error", `预览更新失败：${error.message}`);
  } finally {
    if (reloadingResult) matchingBusy = false;
    updateMatchingActions();
  }
});

$("#export-gcp").addEventListener("click", async () => {
  if (window.CAD2GIS_DEMO?.active) {
    toast("静态演示仅记录选点，请在本地工作台执行二次匹配");
    return;
  }
  if (reviewPageExpired || matchingBusy || controlsMutationPending || resultReloadPending || !registrationPreview?.matching_ready) return;
  const matching = matchingRecord();
  if (!matching?.match_sha256 || matching.revision == null || !registrationPreview.controls_sha256) {
    return toast("请先刷新并获取当前匹配版本", true);
  }
  matchingBusy = true;
  updateMatchingActions();
  $("#matching-status").textContent = "正在执行二次匹配并生成新结果；完成后将自动加载，请稍候。";
  terminalEvent("info", `正在基于第 ${matching.revision} 版执行二次匹配`);
  let completed = false;
  try {
    const result = await fetchJSON("/api/matching/rematch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_controls_sha256: registrationPreview.controls_sha256,
        expected_match_sha256: matching.match_sha256,
        expected_match_revision: matching.revision,
        execute: true,
      }),
    });
    $("#conversion-command").textContent = result.conversion_command || "本次操作未返回可复制的转换命令。";
    $("#copy-command").disabled = !result.conversion_command;
    activateTab("console");
    if (result.status === "completed") {
      completed = true;
      resultReloadPending = true;
      await reloadCurrentResult();
      const revision = matchingRecord()?.revision ?? result.matching?.revision ?? "—";
      $("#matching-status").textContent = `二次匹配已完成，当前为第 ${revision} 版。控制点已保留，可继续校正并再次匹配。`;
      $("#preview-source-note").textContent = `已加载新结果：${result.current_run_dir || result.next_run_dir || "当前运行"}。人工控制点已保留；右图可继续预览校正。`;
      terminalEvent("ok", `二次匹配完成，已加载第 ${revision} 版结果：${result.current_run_dir || result.next_run_dir || "当前运行"}`);
      toast("二次匹配完成，新结果与控制点已加载");
    } else if (result.status === "prepared") {
      const message = result.error || "匹配配置已准备，尚未完成转换。";
      $("#matching-status").textContent = `${message} 当前结果与人工点仍保留。`;
      $("#preview-source-note").textContent = `${message} ${result.next_run_dir ? `待生成结果：${result.next_run_dir}。` : ""}完成转换后才会加载新结果。`;
      terminalEvent("warn", message);
      toast(message, true);
    } else {
      throw new Error(result.error || "二次匹配未完成，请检查控制台后重试");
    }
  } catch (error) {
    const message = completed
      ? `二次匹配已完成，但新结果加载失败：${error.message}。请点击“重新加载匹配结果”。`
      : `二次匹配失败：${error.message}。当前结果与人工点仍保留，可调整后重试。`;
    $("#matching-status").textContent = message;
    $("#preview-source-note").textContent = message;
    terminalEvent("error", message);
    toast(message, true);
  } finally {
    matchingBusy = false;
    updateMatchingActions();
  }
});
$("#copy-command").addEventListener("click", async () => {
  try {
    await copyText($("#conversion-command").textContent, "转换命令已复制");
  } catch (error) {
    toast(error.message, true);
    terminalEvent("error", error.message);
  }
});
$("#tool-fit").addEventListener("click", () => {
  if (!ol.extent.isEmpty(localExtent)) localMap.getView().fit(localExtent, { padding: [40, 40, 40, 40] });
  refreshPreview({ fitView: true }).catch((error) => toast(error.message, true));
});
$("#open-visual").addEventListener("click", async () => {
  try {
    const manifest = await fetchJSON("/api/visual/manifest.json");
    $("#visual-grid").innerHTML = manifest.regions.map((region) => {
      const file = region.render_path.split("/").pop();
      return `<article><img src="/api/visual/${encodeURIComponent(file)}" alt=""><footer>${region.region_id}</footer></article>`;
    }).join("");
    $("#visual-dialog").showModal();
  } catch (error) {
    toast(error.message, true);
  }
});
$("#close-visual").addEventListener("click", () => $("#visual-dialog").close());

document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});
document.querySelectorAll(".workflow-step").forEach((button) => {
  button.addEventListener("click", () => setReviewStage(button.dataset.stage));
});
document.querySelectorAll("[data-copy-prompt]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await copyText(button.dataset.copyPrompt, "AI 审查提示词已复制");
    } catch (error) {
      toast(error.message, true);
      terminalEvent("error", error.message);
    }
  });
});

function invalidateCorrection() {
  registrationPreview = null;
  previewIsCurrent = false;
  previewEpoch += 1;
  updateMatchingActions();
}

function updatePrecisionState() {
  const fit = previewIsCurrent && activePreviewKind === "correction"
    ? registrationPreview?.preview_fit || registrationPreview?.fit : null;
  const metres = (value) => Number.isFinite(value) ? `${value.toFixed(3)} m` : "—";
  const checks = controls.filter((pair) => pair.role === "check").length;
  $("#precision-view").textContent = matchingBusy ? "正在生成新结果"
    : controlsMutationPending ? "正在保存控制点"
    : activePreviewKind === "correction" ? (previewIsCurrent ? "人工校正预览" : "上次预览 · 待刷新")
    : "已应用结果";
  $("#precision-train").textContent = metres(fit?.train_metrics?.rmse_m);
  $("#precision-check").textContent = Number.isFinite(fit?.check_metrics?.rmse_m)
    ? metres(fit.check_metrics.rmse_m) : checks ? "等待有效预览" : "尚无检查点";
  $("#precision-evidence").textContent = window.CAD2GIS_DEMO?.active ? "演示 · 不执行拟合"
    : checks ? `${checks} 个检查点 · 待验收` : "绝对精度尚未验证";
  $("#precision-check").classList.toggle("is-unverified", !Number.isFinite(fit?.check_metrics?.rmse_m));
}

function updatePickGuide() {
  $("#pick-guide").classList.toggle("has-pending", Boolean(pendingCad));
  $("#pick-guide-text").textContent = reviewPageExpired ? "请返回工作空间重新打开当前结果。"
    : controlsMutationPending ? "正在保存，请稍候。源点和目标点会保持成对。"
    : pendingCad ? "CAD 源点已吸附。现在点击右图的同一位置，或填写经纬度后保存。"
    : controls.length ? `已保存 ${controls.length} 个控制点。可点选已有点微调，也可继续在 CAD 中选新点。`
    : "在左侧 CAD 中放大并选择一个真实设施或清晰拐点。";
  $("#send-coordinate").disabled = !pendingCad || matchingBusy || controlsMutationPending || resultReloadPending || reviewPageExpired;
  $("#pick-guide-action").textContent = pendingCad ? "填写目标坐标" : selectedGcpId ? "微调选中点" : "选点操作提示";
}

function updateSelectedControl() {
  const pair = controls.find((item) => item.id === selectedGcpId);
  $("#gcp-adjust").hidden = !pair;
  document.querySelectorAll("[data-select-gcp]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.selectGcp === selectedGcpId));
  });
  gcpLocalSource.changed();
  gcpMapSource.changed();
  if (!pair) return;
  const index = controls.indexOf(pair) + 1;
  $("#gcp-adjust-title").textContent = `微调第 ${index} 个点`;
  $("#gcp-adjust-role").textContent = pair.role === "check" ? "独立检查" : "训练";
  $("#gcp-adjust-coordinate").textContent = `${pair.lonLat[0].toFixed(8)}, ${pair.lonLat[1].toFixed(8)} · WGS84`;
  $("#gcp-adjust-status").textContent = window.CAD2GIS_DEMO?.active
    ? "静态演示仅记录选点。请在本地工作台进行精确微调。"
    : "按实际距离移动目标点，CAD 源点保持不变。移动步长不代表测量精度。";
}

function selectControl(id) {
  if (matchingBusy || controlsMutationPending || resultReloadPending || reviewPageExpired) return;
  if (!controls.some((pair) => pair.id === id)) return;
  selectedGcpId = id;
  pendingCad = null;
  pendingCadEvidence = null;
  for (const feature of gcpLocalSource.getFeatures()) {
    if (feature.get("role") === "pending") gcpLocalSource.removeFeature(feature);
  }
  updateSelectedControl();
  updateMatchingActions();
}

async function adjustControl(operation, direction) {
  const pair = controls.find((item) => item.id === selectedGcpId);
  if (!pair || window.CAD2GIS_DEMO?.active || matchingBusy || controlsMutationPending || resultReloadPending || reviewPageExpired) return;
  const step = Number($("#gcp-step").value);
  if (operation === "nudge" && ![0.01, 0.1, 1].includes(step)) return;
  controlsMutationPending = true;
  updateMatchingActions();
  $("#gcp-adjust-status").textContent = "正在保存目标点微调…";
  try {
    await fetchJSON(`/api/review/features/${encodeURIComponent(pair.id)}/adjust`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation, direction, step_m: step, expected_revision: pair.revision, actor: "registration-workspace" }),
    });
    invalidateCorrection();
    await loadControls();
    const directions = { north: "北", south: "南", east: "东", west: "西" };
    const description = operation === "undo" ? "已撤销上次微调"
      : operation === "reset" ? "已恢复最初选定的目标点"
      : `目标点已向${directions[direction]}移动 ${step} m`;
    $("#gcp-adjust-status").textContent = description;
    terminalEvent("ok", description);
  } catch (error) {
    if (error.status === 409 && !reviewPageExpired) {
      invalidateCorrection();
      await loadControls().catch(() => {});
    }
    $("#gcp-adjust-status").textContent = `微调未完成：${error.message}`;
    toast(error.message, true);
  } finally {
    controlsMutationPending = false;
    updateMatchingActions();
  }
}

function toggleInspector(open, tab = null) {
  $("#review-inspector").hidden = !open;
  $("#toggle-evidence").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("evidence-open", open);
  if (tab) activateTab(tab);
  if (open) $("#close-inspector").focus();
  else $("#toggle-evidence").focus();
  requestAnimationFrame(() => { localMap.updateSize(); worldMap.updateSize(); });
}

function setReviewStage(stage) {
  document.body.classList.toggle("gcp-focused", ["registration", "validation", "delivery"].includes(stage));
  document.querySelectorAll(".workflow-step").forEach((button) => {
    const active = button.dataset.stage === stage;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  if (stage === "semantic") toggleInspector(true, "agent");
  if (stage === "validation") toggleInspector(true, "evidence");
  if (stage === "delivery") $("#export-gcp").scrollIntoView({ block: "center" });
  requestAnimationFrame(() => { localMap.updateSize(); worldMap.updateSize(); });
}

function setOnlineBasemap(enabled) {
  if (enabled && !osmBaseLayer.getSource()) osmBaseLayer.setSource(new ol.source.OSM({
    // OSM requires a Referer for browser tiles. Opt-in tiles disclose only the
    // service origin; the application's no-referrer policy remains elsewhere.
    tileLoadFunction: (tile, source) => {
      const image = tile.getImage();
      image.referrerPolicy = "strict-origin";
      image.src = source;
    },
  }));
  osmBaseLayer.setVisible(enabled);
  if (!enabled) osmBaseLayer.setSource(null);
  $("#online-basemap").checked = enabled;
  $("#basemap-status").textContent = enabled
    ? "在线 OSM 底图已开启：地图瓦片服务会收到当前视野相关请求。底图不作为测量精度证明。"
    : "在线底图未加载。启用后会向 OpenStreetMap 请求当前视野的地图瓦片。";
}

async function loadEvidenceLayers() {
  if (evidenceLoaded || evidenceLoading || reviewPageExpired) return;
  if (window.CAD2GIS_DEMO?.active) {
    $("#evidence-layer-status").textContent = "此静态案例未发布 OSM 匹配中间层，请在本地转换结果中查看。";
    return;
  }
  evidenceLoading = true;
  const epoch = ++evidenceLoadRequest;
  $("#evidence-layer-status").textContent = "正在读取当前匹配的矢量证据…";
  try {
    const expected = matchingRecord()?.match_sha256;
    const query = expected ? `?expected_match_sha256=${encodeURIComponent(expected)}` : "";
    const evidence = await fetchJSON(`/api/matching/evidence${query}`);
    if (epoch !== evidenceLoadRequest) return;
    if (expected && evidence.match_sha256 !== expected) throw new Error("匹配版本已变化，请重新打开证据层");
    const colors = { cad_routes: "#196cd2", osm_roads: "#687680", cad_nodes: "#00868c", osm_nodes: "#8953b7", residuals: "#c54c35" };
    let count = 0;
    for (const input of document.querySelectorAll("[data-evidence-layer]")) {
      const name = input.dataset.evidenceLayer;
      const collection = evidence.collections?.[name];
      if (!collection || collection.type !== "FeatureCollection") continue;
      const features = format.readFeatures(collection, { dataProjection: "EPSG:4326", featureProjection: "EPSG:3857" });
      const layer = new ol.layer.Vector({
        source: new ol.source.Vector({ features }),
        visible: false,
        zIndex: name === "residuals" ? 80 : 60,
        style: new ol.style.Style({
          stroke: new ol.style.Stroke({ color: colors[name], width: name === "residuals" ? 2 : 1.8, lineDash: name === "residuals" ? [5, 4] : undefined }),
          image: new ol.style.Circle({ radius: 4, fill: new ol.style.Fill({ color: colors[name] }), stroke: new ol.style.Stroke({ color: "#fff", width: 1.5 }) }),
        }),
      });
      evidenceLayers.set(name, layer);
      worldMap.addLayer(layer);
      input.disabled = features.length === 0;
      input.title = `${features.length} 个证据要素`;
      if (features.length) count += 1;
    }
    evidenceLoaded = true;
    $("#evidence-layer-status").textContent = count
      ? `第 ${evidence.revision ?? "—"} 版 · ${count} 类矢量证据。此层绑定已应用匹配，不随临时 GCP 预览移动。`
      : "当前结果没有可显示的匹配几何证据。";
  } catch (error) {
    if (epoch === evidenceLoadRequest) $("#evidence-layer-status").textContent = `证据层不可用：${error.message}`;
  } finally {
    if (epoch === evidenceLoadRequest) evidenceLoading = false;
  }
}

$("#toggle-evidence").addEventListener("click", () => toggleInspector($("#review-inspector").hidden));
$("#close-inspector").addEventListener("click", () => toggleInspector(false));
$("#start-guided-correction").addEventListener("click", () => {
  $("#review-guide").open = false;
  setReviewStage("registration");
  $("#local-map").focus();
});
$("#review-guide").addEventListener("toggle", () => requestAnimationFrame(() => { localMap.updateSize(); worldMap.updateSize(); }));
$("#pick-guide-action").addEventListener("click", () => {
  setReviewStage("registration");
  if (!pendingCad && !selectedGcpId) {
    $("#review-guide").open = true;
    $("#review-guide").scrollIntoView({ block: "start" });
    return;
  }
  const target = $(pendingCad ? "#target-lon" : "#gcp-step");
  target.scrollIntoView({ block: "center" });
  target.focus();
});
document.querySelectorAll("[data-adjust-gcp]").forEach((button) => button.addEventListener("click", () => adjustControl("nudge", button.dataset.adjustGcp)));
$("#undo-gcp").addEventListener("click", () => adjustControl("undo"));
$("#reset-gcp").addEventListener("click", () => adjustControl("reset"));
$("#center-gcp").addEventListener("click", () => {
  const pair = controls.find((item) => item.id === selectedGcpId);
  if (!pair) return;
  const duration = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200;
  localMap.getView().animate({ center: pair.cad, duration });
  worldMap.getView().animate({ center: pair.map, duration });
});
$("#fit-both").addEventListener("click", () => $("#tool-fit").click());
$("#online-basemap").addEventListener("change", (event) => setOnlineBasemap(event.target.checked));
setOnlineBasemap(Boolean(window.CAD2GIS_DEMO?.active));
$("#evidence-layer-menu").addEventListener("toggle", () => {
  if ($("#evidence-layer-menu").open) loadEvidenceLayers();
});
document.querySelectorAll("[data-evidence-layer]").forEach((input) => input.addEventListener("change", () => evidenceLayers.get(input.dataset.evidenceLayer)?.setVisible(input.checked)));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#review-inspector").hidden) toggleInspector(false);
});


const connectSocket = () => {
  if (reviewPageExpired) return;
  if (window.CAD2GIS_DEMO?.active) {
    $("#connection-dot").className = "status-dot online";
    $("#connection-label").textContent = "静态演示";
    terminalEvent(
      "warn",
      window.CAD2GIS_DEMO.publicationBoundary
        || "当前为静态派生数据演示；真实 DWG 读取、MCP 和 GeoPackage 生成必须在本地运行",
    );
    return;
  }
  const socketQuery = applicationReviewId ? `?review=${encodeURIComponent(applicationReviewId)}` : "";
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws${socketQuery}`);
  socket.onopen = () => {
    $("#connection-dot").className = "status-dot online";
    $("#connection-label").textContent = "正在同步";
  };
  socket.onmessage = async ({ data }) => {
    try {
      const type = JSON.parse(data).type;
      if (type === "matching_completed" && !matchingBusy) {
        resultReloadPending = true;
        updateMatchingActions();
        if (controlsMutationPending) {
          $("#matching-status").textContent = "另一页面已生成新的匹配结果。当前控制点操作完成后，请点击“重新加载匹配结果”。";
          return;
        }
        matchingBusy = true;
        updateMatchingActions();
        try {
          await reloadCurrentResult();
          $("#matching-status").textContent = "已同步其他页面生成的匹配结果，控制点可继续修改。";
          terminalEvent("ok", "已同步最新匹配结果与控制点");
        } catch (error) {
          $("#matching-status").textContent = `新匹配结果加载失败：${error.message}。请点击“重新加载匹配结果”。`;
          terminalEvent("error", `匹配结果同步失败：${error.message}`);
        } finally {
          matchingBusy = false;
          updateMatchingActions();
        }
        return;
      }
      if (type === "connected" || type === "review_event") {
        $("#connection-label").textContent = "正在同步";
        const applied = await loadControls();
        if (applied && socket.readyState === WebSocket.OPEN) {
          $("#connection-label").textContent = "实时同步";
        }
      }
    } catch (error) {
      terminalEvent("error", `审查状态同步失败：${error.message}`);
      // Reconnect to retry a failed handshake/state fetch, not just transport.
      socket.close();
    }
  };
  socket.onclose = (event) => {
    if (applicationReviewId && (event.code === 1008 || reviewPageExpired)) {
      expireReviewPage();
      terminalEvent("warn", "审查连接已结束，请返回工作台重新打开结果");
      return;
    }
    $("#connection-dot").className = "status-dot offline";
    $("#connection-label").textContent = "连接中断";
    terminalEvent("warn", "实时连接中断，正在重连");
    setTimeout(connectSocket, 1800);
  };
};

const reportReviewState = (state, message = "", runDir = "") => {
  document.body.dataset.reviewState = state;
  document.body.dataset.reviewMessage = message;
  if (applicationReviewId && window.parent !== window) {
    window.parent.postMessage({
      type: "cad2gis-review-state", reviewId: applicationReviewId, state, message, runDir,
    }, location.origin);
  }
};

const boot = async () => {
  reportReviewState("loading");
  $("#terminal-log").innerHTML = '<div class="terminal-empty">等待 CAD2GIS 服务…</div>';
  terminalEvent("info", "正在读取 run manifest 与图层目录");
  try {
    const run = await fetchJSON("/api/run");
    renderRun(run);
    await loadLayers();
    await loadControls();
    setReviewStage("registration");
    connectSocket();
    reportReviewState("ready", "", run.run_dir);
  } catch (error) {
    reportReviewState("error", error.message || "地图工作区初始化失败");
    toast(error.message, true);
    $("#connection-dot").className = "status-dot offline";
    terminalEvent("error", `工作台初始化失败：${error.message}`);
  }
};

const initialize = async () => {
  try {
    await setupProjectSelector();
  } catch (error) {
    terminalEvent("warn", `演示项目目录不可用：${error.message}`);
  }
  await boot();
};

initialize();

// Only the authenticated application owns an executable semantic workspace.
const semanticWorkspaceLink = document.getElementById("semantic-service-link");
if (semanticWorkspaceLink && applicationReviewId && !window.CAD2GIS_DEMO?.active) {
  semanticWorkspaceLink.hidden = false;
  document.getElementById("semantic-service-note").textContent = "打开当前服务器的共享工作区，选择源图和新的结果目录。";
}
