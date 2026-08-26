const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const hero = document.querySelector("#hero-page");
const visual = document.querySelector("[data-parallax]");
const motion = {
  frame: 0,
  lastTime: 0,
  targetScroll: 0,
  scroll: 0,
  targetPointerX: 0,
  targetPointerY: 0,
  pointerX: 0,
  pointerY: 0,
};

const requestMotionFrame = () => {
  if (!motion.frame) motion.frame = requestAnimationFrame(updateMotion);
};

const updateMotion = (time) => {
  motion.frame = 0;
  const delta = Math.min((time - (motion.lastTime || time)) / 1000, 0.05);
  motion.lastTime = time;
  const blend = 1 - Math.exp(-10 * Math.max(delta, 1 / 120));
  motion.scroll += (motion.targetScroll - motion.scroll) * blend;
  motion.pointerX += (motion.targetPointerX - motion.pointerX) * blend;
  motion.pointerY += (motion.targetPointerY - motion.pointerY) * blend;
  if (hero) {
    hero.style.setProperty("--hero-scroll", motion.scroll.toFixed(4));
    hero.style.setProperty("--hero-grid-shift", `${(-18 * motion.scroll).toFixed(2)}px`);
    hero.style.setProperty("--hero-scroll-y", `${(-28 * motion.scroll).toFixed(2)}px`);
  }
  if (visual) {
    visual.style.setProperty("--hero-pointer-x", `${motion.pointerX.toFixed(2)}px`);
    visual.style.setProperty("--hero-pointer-y", `${motion.pointerY.toFixed(2)}px`);
  }
  const moving = Math.abs(motion.targetScroll - motion.scroll) > 0.0005
    || Math.abs(motion.targetPointerX - motion.pointerX) > 0.05
    || Math.abs(motion.targetPointerY - motion.pointerY) > 0.05;
  if (moving) requestMotionFrame();
};

const setHeroScroll = () => {
  if (!hero || reduceMotion) return;
  const max = Math.max(hero.offsetHeight - window.innerHeight, 1);
  motion.targetScroll = Math.min(window.scrollY / max, 1);
  requestMotionFrame();
};

const attachParallax = () => {
  if (!visual || reduceMotion) return;
  window.addEventListener("pointermove", (event) => {
    motion.targetPointerX = (event.clientX / Math.max(window.innerWidth, 1) - .5) * 5.6;
    motion.targetPointerY = (event.clientY / Math.max(window.innerHeight, 1) - .5) * 4.2;
    requestMotionFrame();
  }, { passive: true });
  visual.addEventListener("pointerleave", () => {
    motion.targetPointerX = 0;
    motion.targetPointerY = 0;
    requestMotionFrame();
  }, { passive: true });
};

const attachSmoothAnchors = () => {
  if (reduceMotion) return;
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const selector = anchor.getAttribute("href");
      const target = selector && document.querySelector(selector);
      if (!target) return;
      event.preventDefault();
      history.pushState(null, "", selector);
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
};

const animateCount = (node) => {
  if (node.dataset.counted === "true") return;
  node.dataset.counted = "true";
  const target = Number(node.dataset.count || 0);
  if (reduceMotion || !Number.isFinite(target)) {
    node.textContent = target.toLocaleString("en-US");
    return;
  }
  const start = performance.now();
  const duration = 1100;
  const tick = (now) => {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - ((1 - progress) ** 3);
    node.textContent = Math.round(target * eased).toLocaleString("en-US");
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

const observeMotion = () => {
  const counters = [...document.querySelectorAll("[data-count]")];
  const cards = [...document.querySelectorAll("[data-stage-card]")];
  if (!("IntersectionObserver" in window)) {
    counters.forEach(animateCount);
    return;
  }
  const counterObserver = new IntersectionObserver((entries, observer) => {
    entries.filter((entry) => entry.isIntersecting).forEach((entry) => {
      animateCount(entry.target);
      observer.unobserve(entry.target);
    });
  }, { threshold: .45 });
  counters.forEach((node) => counterObserver.observe(node));

  const cardObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    cards.forEach((card) => card.classList.toggle("is-active", card === visible.target));
  }, { threshold: [.35, .7], rootMargin: "-12% 0px -24%" });
  cards.forEach((card) => cardObserver.observe(card));
  cards.forEach((card) => card.addEventListener("focus", () => {
    cards.forEach((candidate) => candidate.classList.toggle("is-active", candidate === card));
  }));
};

window.addEventListener("scroll", setHeroScroll, { passive: true });
setHeroScroll();
attachParallax();
attachSmoothAnchors();
observeMotion();
