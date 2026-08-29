const output = document.querySelector("#terminal-output");
const replayButton = document.querySelector("#replay-terminal");
const metricStage = document.querySelector("#metric-stage");
const metricThroughput = document.querySelector("#metric-throughput");
const metricEntities = document.querySelector("#metric-entities");
const metricHealth = document.querySelector("#metric-health");
const throughputFill = document.querySelector("#throughput-fill");
const copyToast = document.querySelector("#copy-toast");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const transcript = [
  { at: 0, kind: "command", text: "conda env create -f env/environment.yml" },
  { at: 520, kind: "muted", text: "Collecting package metadata (repodata.json)" },
  { at: 1040, kind: "info", text: "Fetching GDAL 3.9.2 · PROJ 9.4.1 · GEOS 3.12.2   212.4 MB" },
  { at: 1680, kind: "success", text: "Environment cad2gis created · 84 packages linked" },
  { at: 2210, kind: "command", text: "conda activate cad2gis" },
  { at: 2590, kind: "success", text: "Environment active: Python 3.12.4" },
  { at: 3020, kind: "command", text: "pip install -e \".[mcp,review,test]\"" },
  { at: 3670, kind: "info", text: "Building editable wheel for cad2gis" },
  { at: 4210, kind: "muted", text: "Resolved fastapi · pyproj · shapely · geopandas · mcp" },
  { at: 4770, kind: "success", text: "Successfully installed cad2gis 0.2.0" },
  { at: 5200, kind: "command", text: "$env:CAD2GIS_READER_BACKEND = \"autocad\"" },
  { at: 5580, kind: "info", text: "Reader probe: AutoCAD Core Console available" },
  { at: 6030, kind: "command", text: "cad2gis doctor --deep --strict --json" },
  { at: 6540, kind: "info", text: "[1/8] Python runtime                         PASS" },
  { at: 6810, kind: "info", text: "[2/8] GDAL / PROJ axis information          PASS" },
  { at: 7080, kind: "info", text: "[3/8] DWG reader handshake                  PASS" },
  { at: 7350, kind: "info", text: "[4/8] Curve and bulge recovery               PASS" },
  { at: 7620, kind: "info", text: "[5/8] GeoPackage write transaction           PASS" },
  { at: 7890, kind: "info", text: "[6/8] MCP stdio framing                      PASS" },
  { at: 8160, kind: "info", text: "[7/8] Review workspace assets                PASS" },
  { at: 8430, kind: "info", text: "[8/8] Evidence hash verification             PASS" },
  { at: 8860, kind: "success", text: "READY · 8/8 required checks passed" },
];

let timers = [];
let metricTimer = null;
let hasPlayed = false;

const timestamp = (milliseconds) => {
  const seconds = Math.floor(milliseconds / 1000);
  const rest = Math.floor(milliseconds % 1000);
  return `00:${String(seconds).padStart(2, "0")}.${String(rest).padStart(3, "0")}`;
};

const addLine = (entry) => {
  const line = document.createElement("div");
  line.className = "terminal-line";
  const time = document.createElement("time");
  time.textContent = timestamp(entry.at);
  const message = document.createElement("span");
  message.className = entry.kind;
  message.textContent = entry.kind === "command" ? `› ${entry.text}` : entry.text;
  line.append(time, message);
  output.append(line);
  output.parentElement.scrollTop = output.parentElement.scrollHeight;
};

const updateMetrics = (elapsed) => {
  const progress = Math.min(elapsed / 9000, 1);
  throughputFill.style.width = `${(progress * 100).toFixed(1)}%`;
  if (elapsed < 2100) {
    metricStage.textContent = "ENV";
    metricThroughput.textContent = (8 + Math.sin(elapsed / 220) * 4 + progress * 25).toFixed(1);
    metricEntities.textContent = "0";
    metricHealth.textContent = "0/8";
  } else if (elapsed < 5000) {
    metricStage.textContent = "BUILD";
    metricThroughput.textContent = (4.2 + Math.sin(elapsed / 180) * 1.4).toFixed(1);
    metricEntities.textContent = String(Math.round((elapsed - 2100) * 3.7));
    metricHealth.textContent = "0/8";
  } else if (elapsed < 6200) {
    metricStage.textContent = "READER";
    metricThroughput.textContent = (1.4 + Math.sin(elapsed / 160) * .4).toFixed(1);
    metricEntities.textContent = "12480";
    metricHealth.textContent = "0/8";
  } else {
    metricStage.textContent = elapsed >= 8860 ? "READY" : "DOCTOR";
    metricThroughput.textContent = (0.6 + Math.max(0, Math.sin(elapsed / 150)) * .4).toFixed(1);
    metricEntities.textContent = "12480";
    metricHealth.textContent = `${Math.min(8, Math.max(0, Math.floor((elapsed - 6270) / 270)))}/8`;
  }
};

const replay = () => {
  timers.forEach(window.clearTimeout);
  timers = [];
  window.clearInterval(metricTimer);
  output.replaceChildren();
  throughputFill.style.width = "0%";
  const started = performance.now();
  if (reducedMotion) {
    transcript.forEach(addLine);
    updateMetrics(9000);
    return;
  }
  transcript.forEach((entry) => {
    timers.push(window.setTimeout(() => addLine(entry), entry.at));
  });
  metricTimer = window.setInterval(() => {
    const elapsed = performance.now() - started;
    updateMetrics(elapsed);
    if (elapsed >= 9100) window.clearInterval(metricTimer);
  }, 110);
};

const showCopyToast = (message) => {
  copyToast.textContent = message;
  copyToast.classList.add("is-visible");
  window.clearTimeout(showCopyToast.timer);
  showCopyToast.timer = window.setTimeout(() => copyToast.classList.remove("is-visible"), 1800);
};

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      showCopyToast("命令已复制，可以粘贴到 PowerShell");
    } catch {
      showCopyToast("浏览器未允许复制，请手动选择命令");
    }
  });
});

replayButton.addEventListener("click", replay);

const observer = new IntersectionObserver((entries) => {
  if (!hasPlayed && entries.some((entry) => entry.isIntersecting)) {
    hasPlayed = true;
    replay();
  }
}, { threshold: 0.35 });
observer.observe(document.querySelector(".terminal-window"));
