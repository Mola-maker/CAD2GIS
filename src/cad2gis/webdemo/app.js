const $ = (selector) => document.querySelector(selector);
const pageConfig = JSON.parse(document.getElementById("cad2gis-page-config")?.textContent || "{}");

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
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body.detail || `${response.status} ${response.statusText}`;
    terminalEvent("error", `${options.method || "GET"} ${url}: ${message}`);
    throw new Error(message);
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
let similarity = null;
let runSummary = null;
let lastTargetCoordinate = "";

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
      fill: new ol.style.Fill({ color: feature.get("role") === "check" ? "#7257d6" : "#f08b28" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: Math.max(1.5, radius * 0.22) }),
    }),
    text: showLabel ? new ol.style.Text({
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
const worldMap = new ol.Map({
  target: "map",
  layers: [
    new ol.layer.Tile({ source: new ol.source.OSM() }),
    new ol.layer.Vector({ source: gcpMapSource, style: gcpStyle, zIndex: 100 }),
  ],
  controls: mapControls(pageConfig.worldExtentControl),
  view: new ol.View({
    center: ol.proj.fromLonLat(pageConfig.worldCenterLonLat || [0, 0]),
    zoom: pageConfig.worldZoom ?? 2,
    maxZoom: 50, constrainResolution: false,
  }),
});

const fitSimilarity = (pairs) => {
  const train = pairs.filter((pair) => pair.role === "train");
  if (train.length < 2) return null;
  const mx = train.reduce((sum, p) => sum + p.cad[0], 0) / train.length;
  const my = train.reduce((sum, p) => sum + p.cad[1], 0) / train.length;
  const mu = train.reduce((sum, p) => sum + p.map[0], 0) / train.length;
  const mv = train.reduce((sum, p) => sum + p.map[1], 0) / train.length;
  let denominator = 0;
  let numeratorA = 0;
  let numeratorB = 0;
  for (const pair of train) {
    const x = pair.cad[0] - mx;
    const y = pair.cad[1] - my;
    const u = pair.map[0] - mu;
    const v = pair.map[1] - mv;
    denominator += x * x + y * y;
    numeratorA += x * u + y * v;
    numeratorB += x * v - y * u;
  }
  if (denominator <= Number.EPSILON) return null;
  const a = numeratorA / denominator;
  const b = numeratorB / denominator;
  const tx = mu - a * mx + b * my;
  const ty = mv - b * mx - a * my;
  const apply = ([x, y]) => [a * x - b * y + tx, b * x + a * y + ty];
  const residuals = pairs.map((pair) => {
    const predicted = apply(pair.cad);
    return Math.hypot(predicted[0] - pair.map[0], predicted[1] - pair.map[1]);
  });
  const trainResiduals = residuals.filter((_, index) => pairs[index].role === "train");
  const rmse = Math.sqrt(trainResiduals.reduce((sum, value) => sum + value * value, 0) / trainResiduals.length);
  return { a, b, tx, ty, apply, residuals, rmse, scale: Math.hypot(a, b), rotation: Math.atan2(b, a) };
};

const refreshPreview = () => {
  similarity = fitSimilarity(controls);
  for (const layer of previewLayers.values()) worldMap.removeLayer(layer);
  previewLayers.clear();
  const nominalPreview = !similarity && (
    geographicCollections.size > 0 || runSummary?.demo?.nominal_map_preview
  );
  const nominalTransform = runSummary?.demo?.nominal_transform
    || { a: 1, b: 0, tx: 0, ty: 0 };
  if (!similarity && !nominalPreview) {
    $("#fit-model").textContent = "至少需要 2 个训练点进行预览";
    $("#fit-rmse").textContent = "—";
    return;
  }
  for (const [name, collection] of layerCollections) {
    const geographicCollection = !similarity ? geographicCollections.get(name) : null;
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
          const [x, y] = similarity
            ? similarity.apply([input[i], input[i + 1]])
            : [
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
    worldMap.addLayer(layer);
    previewLayers.set(name, layer);
  }
  $("#fit-model").textContent = similarity
    ? `相似变换 · 比例 ${similarity.scale.toFixed(6)} · 旋转 ${(similarity.rotation * 180 / Math.PI).toFixed(3)}°`
    : geographicCollections.size > 0
      ? `名义 ${runSummary?.crs?.target_crs || "目标 CRS"} → EPSG:4326 预览（仍需独立 GCP 验证）`
    : runSummary?.demo?.map_anchor
      ? `地图锚点预览 · ${runSummary.demo.map_anchor.place_name || "位置已知"}（仍需 GCP）`
      : "名义 EPSG:3857 预览（未证明绝对精度）";
  $("#fit-rmse").textContent = similarity
    ? `${similarity.rmse.toFixed(3)} m（Web Mercator 预览）`
    : "—（请使用控制点验证）";
  const extents = [...previewLayers.values()].map((layer) => layer.getSource().getExtent());
  if (extents.length) {
    const extent = extents.reduce((acc, value) => ol.extent.extend(acc, value), ol.extent.createEmpty());
    worldMap.getView().fit(extent, { padding: [50, 50, 50, 50] });
  }
};

const renderControls = (registration = null) => {
  gcpLocalSource.clear();
  gcpMapSource.clear();
  controls.forEach((pair, index) => {
    for (const [source, coordinate] of [[gcpLocalSource, pair.cad], [gcpMapSource, pair.map]]) {
      const feature = new ol.Feature(new ol.geom.Point(coordinate));
      feature.setProperties({ role: pair.role, label: `${index + 1}${pair.role === "check" ? "C" : ""}` });
      source.addFeature(feature);
    }
  });
  $("#gcp-list").innerHTML = controls.map((pair, index) => `
    <div>
      <strong>${index + 1} · ${pair.role === "check" ? "检查" : "训练"}</strong>
      <span>${pair.cad[0].toFixed(2)}, ${pair.cad[1].toFixed(2)} → ${pair.lonLat[0].toFixed(6)}, ${pair.lonLat[1].toFixed(6)}</span>
      <button data-delete-gcp="${pair.id}" title="删除">×</button>
    </div>`).join("");
  const trainCount = controls.filter((item) => item.role === "train").length;
  const checkCount = controls.filter((item) => item.role === "check").length;
  $("#fit-coverage").textContent = `${trainCount} / 4 训练；${checkCount} / 3 检查`;
  $("#export-gcp").disabled = !(registration?.activation_ready);
  document.querySelector('[data-stage="registration"]')?.classList.toggle("is-complete", Boolean(registration?.activation_ready));
  document.querySelectorAll("[data-delete-gcp]").forEach((button) => {
    button.addEventListener("click", async () => {
      const pair = controls.find((item) => item.id === button.dataset.deleteGcp);
      if (!pair) return;
      await fetchJSON(`/api/review/features/${encodeURIComponent(pair.id)}?expected_revision=${pair.revision}`, { method: "DELETE" });
      terminalEvent("info", `已删除控制点 ${pair.id}`);
      await loadControls();
    });
  });
  refreshPreview();
};

const loadControls = async () => {
  const [collection, registration] = await Promise.all([
    fetchJSON("/api/review/features"),
    fetchJSON("/api/registration"),
  ]);
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
      };
    });
  renderControls(registration);
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
  const evidence = nearestCadEvidence(coordinate);
  if (!evidence) {
    pendingCad = null;
    pendingCadEvidence = null;
    toast("请靠近真实 CAD 几何点击，不能用空白区或图框作为控制点", true);
    return;
  }
  pendingCad = [...evidence.coordinate];
  pendingCadEvidence = {
    layer: evidence.layerName,
    source_entity_key: evidence.feature.get("source_entity_key") || "",
    source_handle: evidence.feature.get("source_handle") || "",
  };
  $("#cad-coordinate").textContent = `${pendingCad[0].toFixed(3)}, ${pendingCad[1].toFixed(3)}（已吸附 ${evidence.layerName}）`;
  terminalEvent("info", `CAD 点已吸附到 ${evidence.layerName}：${pendingCad[0].toFixed(3)}, ${pendingCad[1].toFixed(3)}`);
  toast("CAD 点已吸附；请在右图点击同一位置或输入经纬度");
};

const savePair = async (cad, lonLat) => {
  if (!Number.isFinite(lonLat[0]) || !Number.isFinite(lonLat[1])
      || lonLat[0] < -180 || lonLat[0] > 180 || lonLat[1] < -90 || lonLat[1] > 90) {
    throw new Error("经纬度必须是有效的 EPSG:4326 坐标");
  }
  const id = `gcp:${crypto.randomUUID()}`;
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
  await loadControls();
  const registration = await fetchJSON("/api/registration");
  const saved = registration.controls.find((item) => item.point_id === id);
  if (saved) {
    lastTargetCoordinate = `${saved.target_easting.toFixed(3)}, ${saved.target_northing.toFixed(3)} (${saved.target_crs})`;
    $("#target-coordinate").textContent = lastTargetCoordinate;
    $("#copy-coordinate").disabled = false;
    terminalEvent("ok", `坐标已传送：${lastTargetCoordinate}`);
  }
};

localMap.on("singleclick", ({ coordinate }) => selectCadPoint(coordinate));
worldMap.on("singleclick", async ({ coordinate }) => {
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
  if (!window.CAD2GIS_DEMO?.active) {
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
  similarity = null;
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

const renderRun = (run) => {
  runSummary = run;
  $("#run-status").textContent = run.run_status || "UNKNOWN";
  const sourcePath = run.source?.path || "";
  const sourceName = sourcePath.split(/[\\/]/).pop() || "未命名 CAD 项目";
  if (!window.CAD2GIS_DEMO?.active) {
    const selector = $("#demo-project-select");
    selector.innerHTML = `<option value="live">${sourceName}</option>`;
    selector.value = "live";
    selector.disabled = true;
    const deliveryCount = Object.values(run.delivery_counts || {})
      .reduce((sum, value) => sum + Number(value || 0), 0);
    $("#demo-project-location").textContent = `${run.crs?.target_crs || "CRS 未声明"} · 本地实时运行`;
    $("#demo-project-description").textContent = `${Object.keys(run.delivery_counts || {}).length} 个交付图层；源事实、坐标与审计工件均来自当前运行。`;
    $("#demo-project-source-count").textContent = Number(run.source_entity_count || 0).toLocaleString("en-US");
    $("#demo-project-delivery-count").textContent = deliveryCount.toLocaleString("en-US");
    $("#demo-project-map-reference").textContent = `DWG declared ${run.crs?.target_crs || "CRS unavailable"}`;
    $("#demo-project-sha").textContent = run.source?.sha256 ? `${run.source.sha256.slice(0, 12)}…` : "—";
  }
  $("#project-name").textContent = sourceName;
  $("#project-source").textContent = sourcePath || "未提供源路径";
  $("#map-reference-note").textContent = run.demo?.map_anchor
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
    ["CAD 几何长度", `${measurement.count ?? 0} 段`],
    ["独立 DIMENSION", `${measurement.measured ?? 0} 段`],
    ["无 DIMENSION", `${measurement.unmeasured ?? 0} 段（仍有 CAD 几何长度）`],
    ["审查存储", run.review_store || "—"],
    ["源文件重连", run.source_available ? "已按 SHA-256 验证" : (run.source_blocker || "不可用")],
  ];
  $("#run-summary").innerHTML = rows.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
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
$("#export-gcp").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/registration/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activate: true }),
    });
    $("#conversion-command").textContent = result.conversion_command;
    $("#copy-command").disabled = !result.conversion_command;
    const sourceNote = $("#preview-source-note");
    if (sourceNote) {
      sourceNote.textContent = result.registered_delivery
        ? `修正结果已存在：${result.registered_delivery}。当前预览仍为配准前的原始 run。`
        : result.conversion_command
          ? `已生成修正命令（llm=${result.source_run_modes?.llm || "off"}）。执行后在 ${result.next_run_dir}/delivery.gpkg 查看修正结果；当前预览仍为配准前的原始 run。`
          : `${result.source_blocker}。GCP 配置已保存，但不会生成不可执行的命令。`;
    }
    activateTab("console");
    terminalEvent("ok", `GCP 配置已导出：${result.profile_path || "web_gcp_profile.json"}`);
    terminalEvent(
      result.conversion_command ? "info" : "error",
      result.conversion_command
        ? "转换命令已就绪；复制并执行后会创建新的不可变 run"
        : result.source_blocker,
    );
    toast("GCP 配置已通过规范校验并生成");
  } catch (error) {
    toast(error.message, true);
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
  refreshPreview();
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
  button.addEventListener("click", () => {
    document.querySelectorAll(".workflow-step").forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    const stage = button.dataset.stage;
    if (stage === "source" || stage === "registration") {
      document.querySelector(`#stage-${stage}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (stage === "semantic") {
      activateTab("agent");
    } else {
      activateTab("evidence");
    }
    terminalEvent("info", `已切换到 ${button.querySelector("strong")?.textContent || stage}`);
  });
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

const connectSocket = () => {
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
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  socket.onopen = () => {
    $("#connection-dot").className = "status-dot online";
    $("#connection-label").textContent = "实时同步";
    terminalEvent("ok", "WebSocket 已连接，审查 revision 将实时同步");
  };
  socket.onmessage = async ({ data }) => {
    if (JSON.parse(data).type === "review_event") await loadControls();
  };
  socket.onclose = () => {
    $("#connection-dot").className = "status-dot offline";
    $("#connection-label").textContent = "连接中断";
    terminalEvent("warn", "实时连接中断，正在重连");
    setTimeout(connectSocket, 1800);
  };
};

const boot = async () => {
  $("#terminal-log").innerHTML = '<div class="terminal-empty">等待 CAD2GIS 服务…</div>';
  terminalEvent("info", "正在读取 run manifest 与图层目录");
  try {
    const run = await fetchJSON("/api/run");
    renderRun(run);
    await loadLayers();
    await loadControls();
    connectSocket();
  } catch (error) {
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
