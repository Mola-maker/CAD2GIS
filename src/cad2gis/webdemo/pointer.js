const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

if (finePointer.matches && !reducedMotion.matches) {
  const pointer = document.createElement("div");
  const ghost = document.createElement("div");
  const frame = document.createElement("div");
  const existingReadout = document.querySelector("[data-pointer-coordinates]");
  const readout = existingReadout || document.createElement("div");

  pointer.className = "cad-pointer";
  pointer.setAttribute("aria-hidden", "true");
  pointer.innerHTML = `
    <svg viewBox="0 0 64 82" aria-hidden="true">
      <path class="pointer-shadow" d="M11 9 61 48 39 52 52 76 37 82 25 56 9 72Z" transform="translate(2 2)"/>
      <path class="pointer-face" d="M7 4 58 44 36 48 49 72 34 79 22 52 6 68Z"/>
      <path class="pointer-shine" d="M12 13v43l11-11 8 3"/>
    </svg>`;
  ghost.className = "cad-pointer-ghost";
  ghost.setAttribute("aria-hidden", "true");
  frame.className = "cad-pointer-frame";
  frame.setAttribute("aria-hidden", "true");

  if (!existingReadout) {
    readout.className = "cad-pointer-hud";
    readout.setAttribute("aria-hidden", "true");
    readout.textContent = "0000 X 0000 Y";
  }

  document.body.append(pointer, ghost, frame);
  if (!existingReadout) document.body.append(readout);

  let targetX = window.innerWidth * 0.5;
  let targetY = window.innerHeight * 0.5;
  let pointerX = targetX;
  let pointerY = targetY;
  let ghostX = targetX;
  let ghostY = targetY;
  let previousX = targetX;
  let previousY = targetY;
  let interactive = null;
  let visible = false;

  const interactiveSelector = "a, button, select, summary, [role='button'], [data-cursor-target]";
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  const setInteractive = (element) => {
    interactive = element?.closest?.(interactiveSelector) || null;
    pointer.classList.toggle("is-interactive", Boolean(interactive));
    ghost.classList.toggle("is-interactive", Boolean(interactive));
    frame.classList.toggle("is-visible", Boolean(interactive));
    if (!interactive) return;
    const rect = interactive.getBoundingClientRect();
    frame.style.width = `${rect.width}px`;
    frame.style.height = `${rect.height}px`;
    frame.style.transform = `translate3d(${rect.left}px, ${rect.top}px, 0)`;
  };

  const updateFrame = () => {
    if (!interactive?.isConnected) {
      setInteractive(null);
      return;
    }
    const rect = interactive.getBoundingClientRect();
    frame.style.width = `${rect.width}px`;
    frame.style.height = `${rect.height}px`;
    frame.style.transform = `translate3d(${rect.left}px, ${rect.top}px, 0)`;
  };

  const show = () => {
    if (visible) return;
    visible = true;
    pointer.classList.add("is-visible");
    ghost.classList.add("is-visible");
  };

  window.addEventListener("pointermove", (event) => {
    if (event.pointerType && event.pointerType !== "mouse" && event.pointerType !== "pen") return;
    targetX = event.clientX;
    targetY = event.clientY;
    readout.textContent = `${String(Math.round(targetX)).padStart(4, "0")} X ${String(Math.round(targetY)).padStart(4, "0")} Y`;
    setInteractive(event.target);
    show();
  }, { passive: true });

  window.addEventListener("pointerdown", () => pointer.classList.add("is-pressing"), { passive: true });
  window.addEventListener("pointerup", () => pointer.classList.remove("is-pressing"), { passive: true });
  window.addEventListener("blur", () => {
    visible = false;
    pointer.classList.remove("is-visible", "is-pressing");
    ghost.classList.remove("is-visible");
    frame.classList.remove("is-visible");
  });
  document.addEventListener("mouseleave", () => {
    visible = false;
    pointer.classList.remove("is-visible", "is-pressing");
    ghost.classList.remove("is-visible");
    frame.classList.remove("is-visible");
  });
  window.addEventListener("scroll", updateFrame, { passive: true });
  window.addEventListener("resize", updateFrame, { passive: true });

  const render = () => {
    pointerX += (targetX - pointerX) * 0.34;
    pointerY += (targetY - pointerY) * 0.34;
    ghostX += (targetX - ghostX) * 0.11;
    ghostY += (targetY - ghostY) * 0.11;

    const velocityX = pointerX - previousX;
    const velocityY = pointerY - previousY;
    const angle = clamp(velocityX * 0.42 + velocityY * 0.14, -13, 13);
    const speed = clamp(Math.hypot(velocityX, velocityY) / 22, 0, 0.16);
    const pressedScale = pointer.classList.contains("is-pressing") ? 0.82 : 1;
    const hoverScale = pointer.classList.contains("is-interactive") ? 0.78 : 1;
    const scale = pressedScale * hoverScale * (1 + speed);

    pointer.style.transform = `translate3d(${pointerX + 14}px, ${pointerY + 15}px, 0) rotate(${angle}deg) scale(${scale})`;
    ghost.style.transform = `translate3d(${ghostX - 10}px, ${ghostY - 10}px, 0) rotate(${angle * -0.5}deg)`;
    previousX = pointerX;
    previousY = pointerY;
    window.requestAnimationFrame(render);
  };

  window.requestAnimationFrame(render);
}
