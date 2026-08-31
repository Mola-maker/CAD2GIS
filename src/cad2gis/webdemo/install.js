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
  { at: 0, kind: "command", text: "winget install --id=astral-sh.uv -e" },
  { at: 520, kind: "muted", text: "Found uv [astral-sh.uv] · resolving package" },
  { at: 1040, kind: "info", text: "Downloading uv-x86_64-pc-windows-msvc   18.7 MB" },
  { at: 1680, kind: "success", text: "uv installed · PATH registration complete" },
  { at: 2210, kind: "command", text: "uv tool install --python 3.12 --force cad2gis[…]@main.zip" },
  { at: 2590, kind: "info", text: "Fetching source archive · isolated Python 3.12 tool environment" },
  { at: 3020, kind: "info", text: "Resolving PROJ · Shapely · MCP · review UI" },
  { at: 3670, kind: "info", text: "Compiling cad2gis command entrypoints" },
  { at: 4210, kind: "muted", text: "Built non-editable wheel · registered cad2gis-agent-mcp" },
  { at: 4770, kind: "success", text: "Installed cad2gis with 2 executables" },
  { at: 5200, kind: "command", text: "codex plugin marketplace add Mola-maker/CAD2GIS --ref main" },
  { at: 5580, kind: "success", text: "Marketplace cad2gis registered from main" },
  { at: 6030, kind: "command", text: "cad2gis-agent-mcp --help && cad2gis doctor --deep --json" },
  { at: 6540, kind: "info", text: "[1/8] Python runtime                         PASS" },
  { at: 6810, kind: "info", text: "[2/8] Installed package import               PASS" },
  { at: 7080, kind: "info", text: "[3/8] PROJ / Shapely control plane           PASS" },
  { at: 7350, kind: "info", text: "[4/8] Console script after forced repair      PASS" },
  { at: 7620, kind: "info", text: "[5/8] MCP stdio framing                      PASS" },
  { at: 7890, kind: "muted", text: "[6/8] GDAL / OGR conversion bindings         CONDA REQUIRED" },
  { at: 8160, kind: "muted", text: "[7/8] DWG reader                             ENV REQUIRED" },
  { at: 8430, kind: "muted", text: "[8/8] Full conversion readiness              LIMITED" },
  { at: 8860, kind: "success", text: "MCP READY · 5/8 · use Conda GIS runtime for conversion" },
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
    metricStage.textContent = elapsed >= 8860 ? "MCP" : "DOCTOR";
    metricThroughput.textContent = (0.6 + Math.max(0, Math.sin(elapsed / 150)) * .4).toFixed(1);
    metricEntities.textContent = "12480";
    metricHealth.textContent = `${Math.min(5, Math.max(0, Math.floor((elapsed - 6270) / 270)))}/8`;
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
      showCopyToast("命令已复制，可以粘贴到终端");
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
