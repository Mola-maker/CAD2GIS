const $ = (selector) => document.querySelector(selector);

const toast = (message, error = false) => {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = "toast"; }, 2800);
};

const fetchJSON = async (url, options = {}) => {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
};

const format = new ol.format.GeoJSON();
const localLayers = new Map();
const previewLayers = new Map();
const layerCollections = new Map();
const localExtent = ol.extent.createEmpty();
const gcpLocalSource = new ol.source.Vector();
const gcpMapSource = new ol.source.Vector();
let controls = [];
let pendingCad = null;
let pendingCadEvidence = null;
let similarity = null;
let runSummary = null;

const palette = {
  CABLE: "#00a849", CABLE_SEGMENT: "#00a849", PTECH: "#d75173",
  BOITE: "#0a8ec1", SITE: "#d03024", INFRASTRUCTURE: "#65767b",
  ZNRO: "#8d5bc2", ZPM: "#e58522", IMB: "#7b8a55",
};

const displayLabel = (feature) => {
  const layer = String(feature.get("_layer") || "").toUpperCase();
  if (layer === "CABLE_SEGMENT") {
    const value = Number(feature.get("source_native_length_m"));
    if (feature.get("measurement_native_m") != null) {
      return `${Number(feature.get("measurement_native_m")).toFixed(3)} m [DWG DIMENSION]`;
    }
    if (Number.isFinite(value)) return `${value.toFixed(3)} m [CAD geometry; no DIMENSION]`;
  }
  return String(feature.get("display_label") || "");
};

const featureStyle = (feature) => {
  const name = String(feature.get("_layer") || feature.get("feature_class") || "").toUpperCase();
  const color = palette[name] || "#557b78";
  const label = displayLabel(feature);
  return new ol.style.Style({
    stroke: new ol.style.Stroke({ color, width: name.startsWith("CABLE") ? 3 : 2 }),
    fill: new ol.style.Fill({ color: `${color}22` }),
    image: new ol.style.Circle({
      radius: 5,
      fill: new ol.style.Fill({ color }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 1.5 }),
    }),
    text: label ? new ol.style.Text({
      text: label,
      offsetY: -12,
      font: "12px Microsoft YaHei, sans-serif",
      fill: new ol.style.Fill({ color: "#162126" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
    }) : undefined,
  });
};

const gcpStyle = (feature) => new ol.style.Style({
  image: new ol.style.Circle({
    radius: 7,
    fill: new ol.style.Fill({ color: feature.get("role") === "check" ? "#7257d6" : "#f08b28" }),
    stroke: new ol.style.Stroke({ color: "#fff", width: 2 }),
  }),
  text: new ol.style.Text({
    text: String(feature.get("label") || ""),
    offsetY: -14,
    fill: new ol.style.Fill({ color: "#172328" }),
    stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
  }),
});

const localProjection = new ol.proj.Projection({
  code: "CAD:LOCAL", units: "m", extent: [-1e12, -1e12, 1e12, 1e12],
});
const localMap = new ol.Map({
  target: "local-map",
  layers: [new ol.layer.Vector({ source: gcpLocalSource, style: gcpStyle, zIndex: 100 })],
  view: new ol.View({ projection: localProjection, center: [0, 0], zoom: 2 }),
});
const worldMap = new ol.Map({
  target: "map",
  layers: [
    new ol.layer.Tile({ source: new ol.source.OSM() }),
    new ol.layer.Vector({ source: gcpMapSource, style: gcpStyle, zIndex: 100 }),
  ],
  view: new ol.View({ center: ol.proj.fromLonLat([112.7, -7.45]), zoom: 5 }),
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
  if (!similarity) {
    $("#fit-model").textContent = "至少需要 2 个训练点进行预览";
    $("#fit-rmse").textContent = "—";
    return;
  }
  for (const [name, collection] of layerCollections) {
    const features = format.readFeatures(collection);
    for (const feature of features) {
      feature.set("_layer", name);
      feature.getGeometry()?.applyTransform((input, output, stride) => {
        const target = output || input;
        for (let i = 0; i < input.length; i += stride) {
          const [x, y] = similarity.apply([input[i], input[i + 1]]);
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
      zIndex: 10,
    });
    worldMap.addLayer(layer);
    previewLayers.set(name, layer);
  }
  $("#fit-model").textContent = `相似变换 · 比例 ${similarity.scale.toFixed(6)} · 旋转 ${(similarity.rotation * 180 / Math.PI).toFixed(3)}°`;
  $("#fit-rmse").textContent = `${similarity.rmse.toFixed(3)} m（Web Mercator 预览）`;
  const extents = [...previewLayers.values()].map((layer) => layer.getSource().getExtent());
  if (extents.length) {
    const extent = extents.reduce((acc, value) => ol.extent.extend(acc, value), ol.extent.createEmpty());
    worldMap.getView().fit(extent, { padding: [50, 50, 50, 50], maxZoom: 19 });
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
  document.querySelectorAll("[data-delete-gcp]").forEach((button) => {
    button.addEventListener("click", async () => {
      const pair = controls.find((item) => item.id === button.dataset.deleteGcp);
      if (!pair) return;
      await fetchJSON(`/api/review/features/${encodeURIComponent(pair.id)}?expected_revision=${pair.revision}`, { method: "DELETE" });
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
    $("#target-coordinate").textContent = `${saved.target_easting.toFixed(3)}, ${saved.target_northing.toFixed(3)} (${saved.target_crs})`;
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
  const features = format.readFeatures(collection);
  features.forEach((feature) => feature.set("_layer", descriptor.name));
  const source = new ol.source.Vector({ features });
  const layer = new ol.layer.Vector({ source, style: featureStyle, zIndex: 10 });
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
};

const renderRun = (run) => {
  runSummary = run;
  $("#run-status").textContent = run.run_status || "UNKNOWN";
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
  ];
  $("#run-summary").innerHTML = rows.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
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
});
$("#clear-pending").addEventListener("click", () => {
  pendingCad = null;
  pendingCadEvidence = null;
  toast("已取消当前 CAD 点");
});
$("#export-gcp").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/registration/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activate: true }),
    });
    $("#conversion-command").textContent = result.conversion_command;
    toast("GCP 配置已通过规范校验并生成");
  } catch (error) {
    toast(error.message, true);
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

const connectSocket = () => {
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  socket.onopen = () => {
    $("#connection-dot").className = "status-dot online";
    $("#connection-label").textContent = "实时同步";
  };
  socket.onmessage = async ({ data }) => {
    if (JSON.parse(data).type === "review_event") await loadControls();
  };
  socket.onclose = () => {
    $("#connection-dot").className = "status-dot offline";
    $("#connection-label").textContent = "连接中断";
    setTimeout(connectSocket, 1800);
  };
};

const boot = async () => {
  try {
    const run = await fetchJSON("/api/run");
    renderRun(run);
    await loadLayers();
    await loadControls();
    connectSocket();
  } catch (error) {
    toast(error.message, true);
    $("#connection-dot").className = "status-dot offline";
  }
};

boot();
