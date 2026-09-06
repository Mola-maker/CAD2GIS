(function registerCad2GisDemo() {
  const active = location.hostname.endsWith("github.io")
    || new URLSearchParams(location.search).get("demo") === "1";
  if (!active) return;

  const FIXTURE_KIND = "CAD2GIS_DERIVED_FIXTURE";
  const PUBLICATION_BOUNDARY = "浏览器地图使用派生数据；DWG 不公开，交付下载以发布清单为准。";
  const pageConfig = JSON.parse(document.getElementById("cad2gis-page-config")?.textContent || "{}");
  // The standalone public page shares this script but keeps its own catalog.
  const baseUrl = pageConfig.fixtureBaseUrl
    ? new URL(pageConfig.fixtureBaseUrl, location.href)
    : new URL(document.currentScript?.src || location.href);
  const fixtureCacheVersion = pageConfig.fixtureCacheVersion || "runtime-fix-20260901";
  const catalogUrl = new URL("./demo-catalog.json?v=multi-demo-20260829", baseUrl);
  const requestedProject = new URLSearchParams(location.search).get("project");
  const reviewByProject = new Map();
  const revisionByProject = new Map();
  let activeProjectId = requestedProject || "hutabohu";
  let catalogPromise;
  let fixturePromise;

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const loadCatalog = async () => {
    catalogPromise ||= fetch(catalogUrl).then(async (response) => {
      if (!response.ok) throw new Error(`无法加载演示项目目录：${response.status}`);
      return response.json();
    });
    return catalogPromise;
  };
  const resolveProject = async (projectId = activeProjectId) => {
    const catalog = await loadCatalog();
    const selected = catalog.projects.find((project) => project.id === projectId)
      || catalog.projects.find((project) => project.id === catalog.default_project);
    if (!selected) throw new Error("演示项目目录为空");
    activeProjectId = selected.id;
    return selected;
  };
  const loadFixture = async () => {
    if (!fixturePromise) {
      fixturePromise = resolveProject().then(async (project) => {
        const fixtureUrl = new URL(`./${project.fixture}?v=${fixtureCacheVersion}`, baseUrl);
        const response = await fetch(fixtureUrl);
        if (!response.ok) throw new Error(`无法加载 ${project.display_name} 派生数据：${response.status}`);
        return response.json();
      });
    }
    return fixturePromise;
  };
  const activeReview = () => {
    if (!reviewByProject.has(activeProjectId)) reviewByProject.set(activeProjectId, new Map());
    return reviewByProject.get(activeProjectId);
  };

  const registration = () => {
    const controls = [...activeReview().values()]
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
    const geographicMatch = url.match(/^\/api\/layers\/([^/]+)\/geojson$/);
    if (geographicMatch && fixture.geographic_layers) {
      return clone(fixture.geographic_layers[decodeURIComponent(geographicMatch[1])]
        || { type: "FeatureCollection", features: [] });
    }
    const layerMatch = url.match(/^\/api\/layers\/([^/]+)\/local-geojson$/);
    if (layerMatch) {
      const name = decodeURIComponent(layerMatch[1]);
      return clone(layers[name] || { type: "FeatureCollection", features: [] });
    }
    if (url === "/api/review/features" && method === "GET") {
      return clone({ type: "FeatureCollection", features: [...activeReview().values()] });
    }
    if (url === "/api/review/features" && method === "POST") {
      const payload = JSON.parse(options.body || "{}");
      const saved = clone(payload.feature);
      const revision = (revisionByProject.get(activeProjectId) || 0) + 1;
      revisionByProject.set(activeProjectId, revision);
      saved.properties = { ...saved.properties, _review_revision: revision };
      activeReview().set(String(saved.id), saved);
      return clone(saved);
    }
    const deleteMatch = url.match(/^\/api\/review\/features\/([^?]+)/);
    if (deleteMatch && method === "DELETE") {
      activeReview().delete(decodeURIComponent(deleteMatch[1]));
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

  const selectProject = async (projectId) => {
    const project = await resolveProject(projectId);
    activeProjectId = project.id;
    fixturePromise = null;
    const nextUrl = new URL(location.href);
    nextUrl.searchParams.set("project", project.id);
    history.replaceState({}, "", nextUrl);
    return clone(project);
  };

  window.CAD2GIS_DEMO = {
    active: true,
    fixtureKind: FIXTURE_KIND,
    publicationBoundary: PUBLICATION_BOUNDARY,
    get activeProjectId() { return activeProjectId; },
    catalog: async () => clone(await loadCatalog()),
    selectProject,
    request,
  };
}());
