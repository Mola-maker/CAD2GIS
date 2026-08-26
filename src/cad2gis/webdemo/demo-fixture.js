(function registerCad2GisDemo() {
  const active = location.hostname.endsWith("github.io")
    || new URLSearchParams(location.search).get("demo") === "1";
  if (!active) return;

  const FIXTURE_KIND = "HUTABOHU_DERIVED_FIXTURE";
  const PUBLICATION_BOUNDARY = "公开页面仅含 Hutabohu 真实转换的筛选派生证据，不包含任何 DWG/GPKG 原始文件";
  const fixtureUrl = new URL(
    "./demo-data.json?v=hutabohu-20260826",
    document.currentScript?.src || location.href,
  );
  let fixturePromise;
  const loadFixture = async () => {
    fixturePromise ||= fetch(fixtureUrl).then(async (response) => {
      if (!response.ok) throw new Error(`无法加载 Hutabohu demo 数据：${response.status}`);
      return response.json();
    });
    return fixturePromise;
  };
  const review = new Map();
  let revision = 0;
  const clone = (value) => JSON.parse(JSON.stringify(value));

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
      source_crs: "EPSG:3857",
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
    const fixture = await loadFixture();
    const layers = fixture.layers || {};
    const layerDescriptors = Object.entries(layers).map(([name, collection]) => ({
      name,
      feature_count: collection.features?.length || 0,
    }));
    if (url === "/api/run") return clone(fixture.run);
    if (url === "/api/layers") return clone({ layers: layerDescriptors });
    const layerMatch = url.match(/^\/api\/layers\/([^/]+)\/local-geojson$/);
    if (layerMatch) {
      const name = decodeURIComponent(layerMatch[1]);
      return clone(layers[name] || { type: "FeatureCollection", features: [] });
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
        source_blocker: `${PUBLICATION_BOUNDARY}；请在本地 MCP/CLI 中附加 DWG`,
        conversion_command: null,
      });
    }
    if (url === "/api/visual/manifest.json") return { regions: [] };
    throw new Error(`静态演示未实现 API：${method} ${url}`);
  };

  window.CAD2GIS_DEMO = {
    active: true,
    fixtureKind: FIXTURE_KIND,
    publicationBoundary: PUBLICATION_BOUNDARY,
    request,
  };
}());
