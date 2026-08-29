const scenes = [...document.querySelectorAll(".scene")];
const progressButtons = [...document.querySelectorAll("[data-scene-jump]")];
const sceneStatus = document.querySelector("#scene-status");
const sceneCoordinate = document.querySelector("#scene-coordinate");
const themeToggle = document.querySelector("#theme-toggle");
const ribbon = document.querySelector(".ribbon-main");
const canvas = document.querySelector("#flow-canvas");
const context = canvas.getContext("2d", { alpha: true });
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let target = 0;
let current = 0;
let activeScene = 0;
let pointerX = window.innerWidth * 0.5;
let pointerY = window.innerHeight * 0.5;
let touchY = null;
let lastWheelJump = 0;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const mix = (from, to, amount) => from + (to - from) * amount;

const selectScene = (index) => {
  target = clamp(index, 0, scenes.length - 1);
  if (reducedMotion.matches) current = target;
};

const updateScenes = () => {
  activeScene = clamp(Math.round(current), 0, scenes.length - 1);
  scenes.forEach((scene, index) => {
    const distance = index - current;
    const visibility = clamp(1 - Math.abs(distance) * 1.35, 0, 1);
    const scale = 1 - Math.min(Math.abs(distance) * 0.035, 0.07);
    scene.classList.toggle("is-near", Math.abs(distance) < 1.25);
    scene.classList.toggle("is-active", index === activeScene);
    scene.style.opacity = visibility.toFixed(4);
    scene.style.transform = `translate3d(0, ${(distance * 100).toFixed(3)}%, 0) scale(${scale.toFixed(4)})`;
    scene.style.pointerEvents = index === activeScene ? "auto" : "none";
    scene.setAttribute("aria-hidden", index === activeScene ? "false" : "true");
  });
  progressButtons.forEach((button) => {
    button.classList.toggle("is-active", Number(button.dataset.sceneJump) === activeScene);
  });
  sceneStatus.textContent = `SCENE ${String(activeScene + 1).padStart(2, "0")} / ${String(scenes.length).padStart(2, "0")}`;
  if (ribbon) {
    const reveal = clamp(1 - current * 0.9, 0, 1);
    ribbon.style.strokeDasharray = "1600";
    ribbon.style.strokeDashoffset = String((1 - reveal) * 1600);
  }
};

const resizeCanvas = () => {
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(window.innerWidth * ratio);
  canvas.height = Math.round(window.innerHeight * ratio);
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
};

const seeded = (index) => {
  const value = Math.sin(index * 9283.31 + 17.7) * 43758.5453;
  return value - Math.floor(value);
};

const drawFlow = (time) => {
  const width = window.innerWidth;
  const height = window.innerHeight;
  const light = document.body.classList.contains("light-mode");
  context.clearRect(0, 0, width, height);

  const flowOpacity = clamp(1 - Math.max(0, current - 2.7), 0, 1);
  context.save();
  context.globalAlpha = 0.19 * flowOpacity;
  context.lineWidth = 1;
  context.strokeStyle = light ? "#183d87" : "#8da5ff";
  const travel = current * height * 0.11 + time * 0.004;
  for (let line = 0; line < 18; line += 1) {
    context.beginPath();
    for (let point = 0; point <= 22; point += 1) {
      const x = point / 22 * width;
      const baseline = height * (0.08 + line * 0.055);
      const wave = Math.sin(point * 0.52 + line * 0.8 + travel * 0.018) * (14 + line * 1.6);
      const pointerPull = (pointerY / height - 0.5) * 16 * Math.sin(point / 22 * Math.PI);
      const y = baseline + wave + pointerPull;
      if (point === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.stroke();
  }
  context.restore();

  const warp = clamp((current - 3.15) / 0.75, 0, 1);
  if (warp > 0) {
    const centerX = mix(width * 0.5, pointerX, 0.035);
    const centerY = mix(height * 0.5, pointerY, 0.035);
    context.save();
    context.globalCompositeOperation = "lighter";
    for (let index = 0; index < 145; index += 1) {
      const angle = seeded(index) * Math.PI * 2;
      const radius = seeded(index + 300) * Math.min(width, height) * 0.43;
      const speed = 60 + seeded(index + 600) * 210;
      const pulse = ((time * 0.00022 * speed + radius) % Math.max(width, height));
      const start = radius + pulse * warp * 0.28;
      const length = (18 + seeded(index + 900) * 120) * warp;
      const hue = index % 7 === 0 ? "#a8ff19" : index % 3 === 0 ? "#7d8dff" : "#65d9ff";
      context.globalAlpha = (0.28 + seeded(index + 1000) * 0.6) * warp;
      context.lineWidth = 1 + seeded(index + 1200) * 2.4;
      context.strokeStyle = light ? "#1d54f5" : hue;
      context.beginPath();
      context.moveTo(centerX + Math.cos(angle) * start, centerY + Math.sin(angle) * start);
      context.lineTo(centerX + Math.cos(angle) * (start + length), centerY + Math.sin(angle) * (start + length));
      context.stroke();
    }
    context.restore();
  }
};

const frame = (time) => {
  const easing = reducedMotion.matches ? 1 : 0.085;
  current += (target - current) * easing;
  if (Math.abs(target - current) < 0.0005) current = target;
  updateScenes();
  drawFlow(time);
  window.requestAnimationFrame(frame);
};

window.addEventListener("wheel", (event) => {
  event.preventDefault();
  if (reducedMotion.matches) {
    const now = performance.now();
    if (now - lastWheelJump < 380) return;
    lastWheelJump = now;
    selectScene(activeScene + Math.sign(event.deltaY));
    return;
  }
  target = clamp(target + event.deltaY / 640, 0, scenes.length - 1);
}, { passive: false });

window.addEventListener("touchstart", (event) => {
  touchY = event.touches[0]?.clientY ?? null;
}, { passive: true });

window.addEventListener("touchmove", (event) => {
  if (touchY == null || !event.touches[0]) return;
  const nextY = event.touches[0].clientY;
  const delta = touchY - nextY;
  touchY = nextY;
  target = clamp(target + delta / 260, 0, scenes.length - 1);
  event.preventDefault();
}, { passive: false });

window.addEventListener("touchend", () => {
  touchY = null;
  target = Math.round(target);
}, { passive: true });

window.addEventListener("keydown", (event) => {
  if (["ArrowDown", "PageDown", " "].includes(event.key)) {
    event.preventDefault();
    selectScene(activeScene + 1);
  }
  if (["ArrowUp", "PageUp"].includes(event.key)) {
    event.preventDefault();
    selectScene(activeScene - 1);
  }
  if (event.key === "Home") selectScene(0);
  if (event.key === "End") selectScene(scenes.length - 1);
});

window.addEventListener("pointermove", (event) => {
  pointerX = event.clientX;
  pointerY = event.clientY;
  sceneCoordinate.textContent = `${String(Math.round(pointerX)).padStart(4, "0")} X ${String(Math.round(pointerY)).padStart(4, "0")} Y`;
});

window.addEventListener("resize", resizeCanvas);
progressButtons.forEach((button) => button.addEventListener("click", () => selectScene(Number(button.dataset.sceneJump))));

themeToggle.addEventListener("click", () => {
  const light = document.body.classList.toggle("light-mode");
  themeToggle.textContent = light ? "主题[B]" : "主题[A]";
  localStorage.setItem("cad2gis-theme", light ? "light" : "blueprint");
});

if (localStorage.getItem("cad2gis-theme") === "light") {
  document.body.classList.add("light-mode");
  themeToggle.textContent = "主题[B]";
}

resizeCanvas();
updateScenes();
window.requestAnimationFrame(frame);
document.body.classList.add("is-ready");
