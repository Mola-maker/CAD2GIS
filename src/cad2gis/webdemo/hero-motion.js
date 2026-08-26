const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const hero = document.querySelector("#hero-page");

const setHeroScroll = () => {
  if (!hero || reduceMotion) return;
  const max = Math.max(hero.offsetHeight - window.innerHeight, 1);
  hero.style.setProperty("--hero-scroll", `${Math.min(window.scrollY / max, 1)}`);
};

const attachParallax = () => {
  const visual = document.querySelector("[data-parallax]");
  if (!visual || reduceMotion) return;
  let raf = 0;
  window.addEventListener("pointermove", (event) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const x = (event.clientX / Math.max(window.innerWidth, 1) - .5) * 16;
      const y = (event.clientY / Math.max(window.innerHeight, 1) - .5) * 12;
      visual.style.transform = `translate3d(${x * .35}px, ${y * .35}px, 0)`;
    });
  }, { passive: true });
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
observeMotion();
