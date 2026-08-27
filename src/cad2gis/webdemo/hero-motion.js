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

const setupProcessTerminal = () => {
  const terminal = document.querySelector("[data-process-terminal]");
  if (!terminal) return;
  const lines = [...terminal.querySelectorAll("[data-terminal-line]")];
  const stages = [...terminal.querySelectorAll("[data-terminal-stage]")];
  const lineViewport = terminal.querySelector("[data-terminal-lines]");
  const replayButton = document.querySelector("[data-terminal-replay]");
  const pauseButton = document.querySelector("[data-terminal-pause]");
  const stateLabel = terminal.querySelector("[data-terminal-state]");
  const statusLabel = terminal.querySelector("[data-terminal-status]");
  if (!lines.length || !replayButton || !pauseButton || !stateLabel || !statusLabel) return;

  const stageOrder = stages.map((stage) => stage.dataset.terminalStage);
  let timer = 0;
  let index = -1;
  let paused = false;
  let autoPaused = false;
  let complete = false;
  let started = false;

  terminal.classList.add("is-enhanced");

  const clearTimer = () => {
    window.clearTimeout(timer);
    timer = 0;
  };

  const setState = (state, label) => {
    terminal.dataset.state = state;
    stateLabel.textContent = label;
  };

  const setStages = (stageName, allComplete = false) => {
    const activeIndex = stageOrder.indexOf(stageName);
    stages.forEach((stage, stageIndex) => {
      stage.classList.toggle("is-active", !allComplete && stageIndex === activeIndex);
      stage.classList.toggle("is-complete", allComplete || (activeIndex >= 0 && stageIndex < activeIndex));
    });
  };

  const scrollLineIntoView = (line) => {
    if (!lineViewport) return;
    const lineBottom = line.offsetTop + line.offsetHeight;
    const visibleBottom = lineViewport.scrollTop + lineViewport.clientHeight - 20;
    if (lineBottom <= visibleBottom) return;
    lineViewport.scrollTo({
      top: lineBottom - lineViewport.clientHeight + 20,
      behavior: "smooth",
    });
  };

  const finish = () => {
    clearTimer();
    complete = true;
    paused = false;
    terminal.style.setProperty("--terminal-progress", "100%");
    setStages("delivery", true);
    setState("complete", "COMPLETE");
    pauseButton.disabled = true;
    pauseButton.setAttribute("aria-pressed", "false");
    pauseButton.textContent = "暂停";
    statusLabel.textContent = "回放完成：交付门状态为 CONDITIONAL，需要人工复核。";
  };

  const revealNext = () => {
    if (paused || complete) return;
    lines.forEach((line) => line.classList.remove("is-current"));
    index += 1;
    if (index >= lines.length) {
      finish();
      return;
    }
    const line = lines[index];
    line.classList.add("is-visible", "is-current");
    terminal.style.setProperty("--terminal-progress", `${((index + 1) / lines.length) * 100}%`);
    setStages(line.dataset.stage || "");
    scrollLineIntoView(line);
    const nextLine = lines[index + 1];
    timer = window.setTimeout(revealNext, Number(nextLine?.dataset.delay || 360));
  };

  const start = () => {
    clearTimer();
    index = -1;
    paused = false;
    autoPaused = false;
    complete = false;
    started = true;
    lines.forEach((line) => line.classList.remove("is-visible", "is-current"));
    if (lineViewport) lineViewport.scrollTop = 0;
    terminal.style.setProperty("--terminal-progress", "0%");
    setStages("");
    setState("running", "RUNNING");
    pauseButton.disabled = false;
    pauseButton.setAttribute("aria-pressed", "false");
    pauseButton.textContent = "暂停";
    statusLabel.textContent = "正在按 Hutabohu 证据顺序回放转换过程。";
    revealNext();
  };

  const togglePause = () => {
    if (!started || complete) return;
    if (paused) {
      paused = false;
      autoPaused = false;
      setState("running", "RUNNING");
      pauseButton.setAttribute("aria-pressed", "false");
      pauseButton.textContent = "暂停";
      statusLabel.textContent = "继续回放 Hutabohu 转换过程。";
      timer = window.setTimeout(revealNext, 180);
      return;
    }
    clearTimer();
    paused = true;
    setState("paused", "PAUSED");
    pauseButton.setAttribute("aria-pressed", "true");
    pauseButton.textContent = "继续";
    statusLabel.textContent = "回放已暂停。";
  };

  replayButton.addEventListener("click", start);
  pauseButton.addEventListener("click", togglePause);

  if (reduceMotion) {
    lines.forEach((line) => line.classList.add("is-visible"));
    terminal.style.setProperty("--terminal-progress", "100%");
    setStages("delivery", true);
    setState("complete", "EXPANDED");
    replayButton.disabled = true;
    pauseButton.disabled = true;
    statusLabel.textContent = "已按减少动态效果偏好展开完整转换记录。";
    return;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && started && !paused && !complete) {
      clearTimer();
      paused = true;
      autoPaused = true;
      setState("paused", "HOLD");
      return;
    }
    if (!document.hidden && autoPaused && !complete) {
      paused = false;
      autoPaused = false;
      setState("running", "RUNNING");
      timer = window.setTimeout(revealNext, 180);
    }
  });

  if (!("IntersectionObserver" in window)) {
    start();
    return;
  }
  const terminalObserver = new IntersectionObserver((entries, observer) => {
    if (!entries.some((entry) => entry.isIntersecting) || started) return;
    start();
    observer.disconnect();
  }, { threshold: .28 });
  terminalObserver.observe(terminal);
};

window.addEventListener("scroll", setHeroScroll, { passive: true });
setHeroScroll();
attachParallax();
attachSmoothAnchors();
observeMotion();
setupProcessTerminal();
