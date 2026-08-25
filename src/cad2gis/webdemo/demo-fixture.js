(function registerCad2GisDemo() {
  const active = location.hostname.endsWith("github.io")
    || new URLSearchParams(location.search).get("demo") === "1";
  if (!active) return;

  const feature = (id, geometry, properties = {}) => ({
    type: "Feature",
    id,
    geometry,
    properties: { source_entity_key: id, ...properties },
  });
  const point = (id, x, y, properties) => feature(
    id, { type: "Point", coordinates: [x, y] }, properties,
  );
  const line = (id, coordinates, properties) => feature(
    id, { type: "LineString", coordinates }, properties,
  );
  const polygon = (id, x, y) => feature(id, {
    type: "Polygon",
    coordinates: [[
      [x - 18, y - 12], [x + 18, y - 12], [x + 18, y + 12],
      [x - 18, y + 12], [x - 18, y - 12],
    ]],
  });

  const routes = [
    [[80, 90], [210, 150], [350, 142], [490, 235], [650, 250]],
    [[350, 142], [370, 285], [500, 365], [615, 430]],
    [[210, 150], [145, 285], [210, 390]],
  ];
  const layers = {
    CABLE: routes.map((coordinates, index) => line(
      `cable:${index + 1}`,
      coordinates,
      { source_native_length_m: [604.2, 421.7, 288.4][index] },
    )),
    PTECH: routes.flatMap((coordinates, routeIndex) => coordinates.map(
      ([x, y], pointIndex) => point(
        `pole:${routeIndex}:${pointIndex}`,
        x,
        y,
        { display_label: `P-${routeIndex + 1}${String(pointIndex + 1).padStart(2, "0")}` },
      ),
    )),
    BOITE: [point("box:1", 350, 142, { display_label: "FDT-DEMO-01" })],
    SITE: [point("site:1", 80, 90, { display_label: "POP-DEMO" })],
    INFRASTRUCTURE: [line("road:1", [[40, 70], [230, 175], [420, 175], [690, 275]])],
    ZPM: [point("zpm:1", 490, 235, { display_label: "ZPM-DEMO" })],
    ZNRO: [point("znro:1", 80, 90, { display_label: "ZNRO-DEMO" })],
    IMB: [
      polygon("imb:1", 250, 120), polygon("imb:2", 420, 210),
      polygon("imb:3", 430, 340), polygon("imb:4", 170, 300),
    ],
  };
  const review = new Map();
  let revision = 0;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const layerDescriptors = Object.entries(layers).map(([name, features]) => ({
    name,
    feature_count: features.length,
  }));

  const registration = () => {
    const controls = [...review.values()]
      .filter((item) => item.properties?._kind === "cad_map_gcp")
      .map((item) => {
        const [lon, lat] = item.geometry.coordinates;
        const radius = 6378137;
        const targetEasting = radius * lon * Math.PI / 180;
        const targetNorthing = radius * Math.log(
          Math.tan(Math.PI / 4 + lat * Math.PI / 360),
        );
        return {
          point_id: String(item.id),
          role: item.properties.role || "train",
          source_x: Number(item.properties.cad_x),
          source_y: Number(item.properties.cad_y),
          target_easting: targetEasting,
          target_northing: targetNorthing,
          target_crs: "EPSG:3857",
        };
      });
    const trainCount = controls.filter((item) => item.role === "train").length;
    const checkCount = controls.filter((item) => item.role === "check").length;
    return {
      schema_version: "cad2gis.web_registration_capture.v1",
      source_crs: "LOCAL_DEMO",
      target_crs: "EPSG:3857",
      controls,
      train_count: trainCount,
      check_count: checkCount,
      minimum_train_count: 4,
      minimum_check_count: 3,
      distribution_gate_passed: trainCount >= 4 && checkCount >= 3,
      activation_ready: trainCount >= 4 && checkCount >= 3,
      accuracy_class: "RELATIVE_OSM_REFERENCE_ONLY",
      absolute_accuracy_verified: false,
    };
  };

  const request = async (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    if (url === "/api/run") return clone({
      schema_version: "cad2gis-run-manifest-v4",
      run_status: "CONDITIONAL",
      source: { path: "demo://synthetic-ftth-review.dwg", sha256: "demo" },
      source_available: false,
      source_blocker: "公开页面仅含合成证据，不包含或上传任何真实 DWG",
      crs: { source_crs: "LOCAL_DEMO", target_crs: "EPSG:3857" },
      validation: { segment_delivery: { count: 3, measured: 2, unmeasured: 1 } },
      review_store: "browser-memory",
    });
    if (url === "/api/layers") return clone({ layers: layerDescriptors });
    const layerMatch = url.match(/^\/api\/layers\/([^/]+)\/local-geojson$/);
    if (layerMatch) {
      const name = decodeURIComponent(layerMatch[1]);
      return clone({ type: "FeatureCollection", features: layers[name] || [] });
    }
    if (url === "/api/review/features" && method === "GET") {
      return clone({ type: "FeatureCollection", features: [...review.values()] });
    }
    if (url === "/api/review/features" && method === "POST") {
      const payload = JSON.parse(options.body || "{}");
      const saved = clone(payload.feature);
      revision += 1;
      saved.properties = { ...saved.properties, _review_revision: revision };
      review.set(String(saved.id), saved);
      return clone(saved);
    }
    const deleteMatch = url.match(/^\/api\/review\/features\/([^?]+)/);
    if (deleteMatch && method === "DELETE") {
      review.delete(decodeURIComponent(deleteMatch[1]));
      return { deleted: true };
    }
    if (url === "/api/registration") return clone(registration());
    if (url === "/api/registration/export" && method === "POST") {
      return clone({
        ...registration(),
        profile: { path: "browser-memory://web_gcp_profile.json", enabled: true },
        source_run_modes: { llm: "assist", domain: "auto" },
        next_run_dir: null,
        registered_delivery: null,
        source_available: false,
        source_blocker: "合成 Pages 演示不会执行真实转换；请在本地 MCP/CLI 中附加 DWG",
        conversion_command: null,
      });
    }
    if (url === "/api/visual/manifest.json") return { regions: [] };
    throw new Error(`静态演示未实现 API：${method} ${url}`);
  };

  window.CAD2GIS_DEMO = { active: true, request };
}());
