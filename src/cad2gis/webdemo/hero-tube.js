import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.185.1/build/three.module.js";

const host = document.querySelector(".flow-word");
const canvas = document.querySelector("#hero-tube-canvas");
const heroScene = document.querySelector(".hero-scene");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const SVG_WIDTH = 1440;
const SVG_HEIGHT = 560;
const WORLD_SCALE = 88;
const TUBE_RADIUS = 0.43;

const toWorld = ([x, y], z = 0) => new THREE.Vector3(
  (x - SVG_WIDTH / 2) / WORLD_SCALE,
  (SVG_HEIGHT / 2 - y) / WORLD_SCALE,
  z,
);

const cubic = (p0, p1, p2, p3) => [p0, p1, p2, p3];

class MultiBezierCurve extends THREE.Curve {
  constructor(segments, phase = 0) {
    super();
    this.segments = segments;
    this.phase = phase;
  }

  getPoint(progress, target = new THREE.Vector3()) {
    const total = this.segments.length;
    const scaled = Math.min(Math.max(progress, 0), 0.999999) * total;
    const index = Math.min(Math.floor(scaled), total - 1);
    const t = scaled - index;
    const [a, b, c, d] = this.segments[index];
    const inverse = 1 - t;
    const x = inverse ** 3 * a[0]
      + 3 * inverse ** 2 * t * b[0]
      + 3 * inverse * t ** 2 * c[0]
      + t ** 3 * d[0];
    const y = inverse ** 3 * a[1]
      + 3 * inverse ** 2 * t * b[1]
      + 3 * inverse * t ** 2 * c[1]
      + t ** 3 * d[1];
    const z = Math.sin((progress * 2.35 + this.phase) * Math.PI) * 0.095
      + Math.sin((progress * 5.1 + this.phase * 0.7) * Math.PI) * 0.025;
    const point = toWorld([x, y], z);
    return target.copy(point);
  }
}

const letterCurves = [
  new MultiBezierCurve([
    cubic([267, 178], [217, 95], [92, 107], [70, 276]),
    cubic([70, 276], [49, 446], [190, 490], [286, 380]),
  ], 0.12),
  new MultiBezierCurve([
    cubic([410, 206], [347, 143], [286, 210], [286, 323]),
    cubic([286, 323], [286, 428], [388, 442], [425, 330]),
  ], 0.28),
  new MultiBezierCurve([
    cubic([463, 160], [459, 245], [458, 339], [493, 414]),
  ], 0.34),
  new MultiBezierCurve([
    cubic([618, 209], [555, 144], [494, 213], [495, 325]),
    cubic([495, 325], [497, 427], [598, 440], [631, 329]),
  ], 0.42),
  new MultiBezierCurve([
    cubic([650, 74], [650, 232], [663, 351], [693, 414]),
  ], 0.5),
  new MultiBezierCurve([
    cubic([990, 209], [923, 145], [860, 211], [860, 324]),
    cubic([860, 324], [859, 427], [960, 442], [1000, 328]),
  ], 0.58),
  new MultiBezierCurve([
    cubic([1037, 165], [1026, 314], [1014, 444], [956, 505]),
    cubic([956, 505], [918, 546], [864, 523], [868, 475]),
  ], 0.66),
  new MultiBezierCurve([
    cubic([1101, 208], [1098, 275], [1092, 362], [1110, 414]),
  ], 0.74),
  new MultiBezierCurve([
    cubic([1300, 206], [1257, 150], [1169, 173], [1171, 248]),
    cubic([1171, 248], [1174, 318], [1280, 294], [1280, 361]),
    cubic([1280, 361], [1280, 431], [1187, 455], [1149, 401]),
  ], 0.9),
];

const makeEnvironment = () => {
  const colors = ["#dfe4ff", "#15247f", "#91a2ff", "#050b39", "#566dff", "#101b67"];
  const faces = colors.map((color, index) => {
    const face = document.createElement("canvas");
    face.width = 32;
    face.height = 32;
    const context = face.getContext("2d");
    const gradient = context.createLinearGradient(0, 0, 32, 32);
    gradient.addColorStop(0, color);
    gradient.addColorStop(1, index % 2 ? "#09134c" : "#f0f2ff");
    context.fillStyle = gradient;
    context.fillRect(0, 0, 32, 32);
    return face;
  });
  const environment = new THREE.CubeTexture(faces);
  environment.colorSpace = THREE.SRGBColorSpace;
  environment.needsUpdate = true;
  return environment;
};

const addTube = (group, curve, material, radius = TUBE_RADIUS, radialSegments = 28) => {
  const tubularSegments = Math.max(72, curve.segments?.length * 42 || 72);
  const geometry = new THREE.TubeGeometry(curve, tubularSegments, radius, radialSegments, false);
  const mesh = new THREE.Mesh(geometry, material);
  group.add(mesh);

  const capGeometry = new THREE.SphereGeometry(radius * 1.002, radialSegments, Math.max(14, radialSegments / 2));
  const start = new THREE.Mesh(capGeometry, material);
  const end = new THREE.Mesh(capGeometry, material);
  start.position.copy(curve.getPoint(0));
  end.position.copy(curve.getPoint(0.999999));
  group.add(start, end);
  return mesh;
};

const addLineTube = (group, points, material, radius) => {
  const worldPoints = points.map(([x, y, z = 0]) => toWorld([x, y], z));
  const curve = worldPoints.length === 2
    ? new THREE.LineCurve3(worldPoints[0], worldPoints[1])
    : new THREE.CatmullRomCurve3(worldPoints, false, "centripetal");
  const geometry = new THREE.TubeGeometry(curve, 44, radius, 22, false);
  const capGeometry = new THREE.SphereGeometry(radius, 22, 12);
  const mesh = new THREE.Mesh(geometry, material);
  const start = new THREE.Mesh(capGeometry, material);
  const end = new THREE.Mesh(capGeometry, material);
  start.position.copy(curve.getPoint(0));
  end.position.copy(curve.getPoint(1));
  group.add(mesh, start, end);
};

const start = () => {
  if (!host || !canvas) return;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: "high-performance",
      premultipliedAlpha: true,
    });
  } catch {
    return;
  }

  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.environment = makeEnvironment();
  const camera = new THREE.OrthographicCamera(-8, 8, 3.4, -3.4, 0.1, 100);
  camera.position.set(0, 0, 18);
  camera.lookAt(0, 0, 0);

  const word = new THREE.Group();
  word.rotation.set(-0.08, 0.055, -0.018);
  word.position.y = -0.03;
  scene.add(word);

  const themePalettes = {
    blueprint: {
      tube: new THREE.Color("#38b9ff"),
      emissive: new THREE.Color("#00345f"),
      sheen: new THREE.Color("#ddf5ff"),
      flow: new THREE.Color("#effcff"),
      flowSecondary: new THREE.Color("#5dd4ff"),
      arrow: new THREE.Color("#c7ff2f"),
      arrowEmissive: new THREE.Color("#5d8f00"),
      particle: new THREE.Color("#63c8ff"),
      edgeLight: new THREE.Color("#6bc9ff"),
      frontLight: new THREE.Color("#d8f3ff"),
      causticLight: new THREE.Color("#7cddff"),
      flowStrength: 0.92,
      exposure: 1.02,
    },
    light: {
      tube: new THREE.Color("#087fc9"),
      emissive: new THREE.Color("#001e3b"),
      sheen: new THREE.Color("#cceeff"),
      flow: new THREE.Color("#e7faff"),
      flowSecondary: new THREE.Color("#2fb7ef"),
      arrow: new THREE.Color("#78ba00"),
      arrowEmissive: new THREE.Color("#315800"),
      particle: new THREE.Color("#1688cf"),
      edgeLight: new THREE.Color("#248bd5"),
      frontLight: new THREE.Color("#a8dcf5"),
      causticLight: new THREE.Color("#179de1"),
      flowStrength: 0.72,
      exposure: 1.1,
    },
  };
  const getThemePalette = () => document.body.classList.contains("light-mode")
    ? themePalettes.light
    : themePalettes.blueprint;
  let themeTarget = getThemePalette();
  renderer.toneMappingExposure = themeTarget.exposure;

  const tubeMaterial = new THREE.MeshPhysicalMaterial({
    color: themeTarget.tube,
    emissive: themeTarget.emissive,
    emissiveIntensity: 0.14,
    metalness: 0.1,
    roughness: 0.13,
    clearcoat: 1,
    clearcoatRoughness: 0.075,
    sheen: 0.26,
    sheenColor: themeTarget.sheen,
    sheenRoughness: 0.22,
    iridescence: 0.14,
    iridescenceIOR: 1.36,
    transmission: 0.035,
    thickness: 0.8,
    ior: 1.42,
    envMapIntensity: 2.1,
  });

  // Keep Three's physically based lighting, then add a restrained optical
  // current that travels through each tube's longitudinal UV coordinate.
  // The moving bands tighten roughness as they pass, so they read as a real
  // reflection/refraction event rather than a flat color animation.
  const flowUniforms = {
    uFlowTime: { value: 0 },
    uFlowColor: { value: themeTarget.flow.clone() },
    uFlowSecondary: { value: themeTarget.flowSecondary.clone() },
    uFlowStrength: { value: themeTarget.flowStrength },
  };
  tubeMaterial.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, flowUniforms);
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        `#include <common>
varying vec2 vCadFlowUv;`,
      )
      .replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
vCadFlowUv = uv;`,
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
varying vec2 vCadFlowUv;
uniform float uFlowTime;
uniform vec3 uFlowColor;
uniform vec3 uFlowSecondary;
uniform float uFlowStrength;

float cadOpticalFlow(vec2 flowUv) {
  const float TAU = 6.28318530718;
  float longitudinal = flowUv.x * 2.35 - uFlowTime * 0.16;
  float mainBand = pow(max(0.0, cos(longitudinal * TAU)), 18.0);
  float echoBand = pow(max(0.0, cos((longitudinal * 1.73 + 0.38) * TAU)), 32.0) * 0.46;
  float curvedFace = 0.34 + 0.66 * pow(max(0.0, cos((flowUv.y - 0.17) * TAU)), 5.0);
  return clamp((mainBand + echoBand) * curvedFace, 0.0, 1.0);
}`,
      )
      .replace(
        "#include <map_fragment>",
        `#include <map_fragment>
float cadFlowMask = cadOpticalFlow(vCadFlowUv);
float cadFlowCore = pow(cadFlowMask, 1.45);
diffuseColor.rgb = mix(
  diffuseColor.rgb,
  mix(uFlowSecondary, uFlowColor, cadFlowCore),
  cadFlowMask * uFlowStrength * 0.42
);`,
      )
      .replace(
        "#include <roughnessmap_fragment>",
        `#include <roughnessmap_fragment>
roughnessFactor = max(0.035, roughnessFactor * (1.0 - cadFlowMask * 0.68));`,
      )
      .replace(
        "#include <opaque_fragment>",
        `outgoingLight += uFlowColor * cadFlowMask * uFlowStrength * 0.13;
#include <opaque_fragment>`,
      );
  };
  tubeMaterial.customProgramCacheKey = () => "cad2gis-optical-flow-v1";

  const arrowMaterial = new THREE.MeshPhysicalMaterial({
    color: themeTarget.arrow,
    emissive: themeTarget.arrowEmissive,
    emissiveIntensity: 0.45,
    metalness: 0.02,
    roughness: 0.2,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    envMapIntensity: 1.5,
  });

  letterCurves.forEach((curve) => addTube(word, curve, tubeMaterial));

  const iDot = new THREE.Mesh(new THREE.SphereGeometry(0.31, 30, 20), tubeMaterial);
  iDot.position.copy(toWorld([1104, 119], 0.12));
  word.add(iDot);

  const arrow = new THREE.Group();
  addLineTube(arrow, [[720, 284, 0.06], [824, 284, 0.08]], arrowMaterial, 0.105);
  addLineTube(arrow, [[789, 235, 0.08], [840, 284, 0.1], [789, 333, 0.08]], arrowMaterial, 0.105);
  word.add(arrow);

  let particleSeed = 37;
  const random = () => {
    particleSeed = particleSeed * 16807 % 2147483647;
    return (particleSeed - 1) / 2147483646;
  };
  const particleCount = 170;
  const particlePositions = new Float32Array(particleCount * 3);
  const particleColors = new Float32Array(particleCount * 3);
  for (let index = 0; index < particleCount; index += 1) {
    const spread = random() ** 1.75;
    const point = toWorld([
      1260 + spread * 195,
      306 + (random() - 0.5) * (105 + spread * 185),
    ], (random() - 0.5) * 0.8);
    particlePositions[index * 3] = point.x;
    particlePositions[index * 3 + 1] = point.y;
    particlePositions[index * 3 + 2] = point.z;
    const brightness = 0.5 + random() * 0.5;
    particleColors[index * 3] = brightness;
    particleColors[index * 3 + 1] = brightness;
    particleColors[index * 3 + 2] = brightness;
  }
  const particleGeometry = new THREE.BufferGeometry();
  particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
  particleGeometry.setAttribute("color", new THREE.BufferAttribute(particleColors, 3));
  const particleMaterial = new THREE.PointsMaterial({
    color: themeTarget.particle,
    size: 0.035,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.58,
    vertexColors: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const particles = new THREE.Points(particleGeometry, particleMaterial);
  particles.position.z = -0.08;
  word.add(particles);

  scene.add(new THREE.HemisphereLight(0xe5f6ff, 0x030833, 1.75));
  const keyLight = new THREE.DirectionalLight(0xf7f8ff, 4.15);
  keyLight.position.set(-7, 8, 12);
  scene.add(keyLight);
  const edgeLight = new THREE.DirectionalLight(themeTarget.edgeLight, 2.6);
  edgeLight.position.set(8, -2, 9);
  scene.add(edgeLight);
  const frontLight = new THREE.PointLight(themeTarget.frontLight, 38, 24, 1.7);
  frontLight.position.set(-3, 3.5, 8);
  scene.add(frontLight);
  const causticLight = new THREE.PointLight(themeTarget.causticLight, 26, 18, 1.85);
  causticLight.position.set(-6, 1.4, 5.5);
  scene.add(causticLight);
  const refractedEdgeLight = new THREE.PointLight(themeTarget.flowSecondary, 17, 15, 2);
  refractedEdgeLight.position.set(5, -1.8, 4.8);
  scene.add(refractedEdgeLight);
  const accentLight = new THREE.PointLight(0x9dff1b, 16, 9, 2);
  accentLight.position.set(0.5, -0.5, 5);
  scene.add(accentLight);

  const pointer = new THREE.Vector2();
  const pointerTarget = new THREE.Vector2();
  let frameId = 0;
  let lastTime = 0;
  let disposed = false;

  const resize = () => {
    const bounds = host.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const dprLimit = window.innerWidth < 760 ? 1.3 : 1.75;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprLimit));
    renderer.setSize(bounds.width, bounds.height, false);
    const halfHeight = 3.42;
    const halfWidth = halfHeight * bounds.width / bounds.height;
    camera.left = -halfWidth;
    camera.right = halfWidth;
    camera.top = halfHeight;
    camera.bottom = -halfHeight;
    camera.updateProjectionMatrix();
  };

  const render = (time) => {
    if (disposed) return;
    const delta = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 1 / 60;
    lastTime = time;
    const active = heroScene?.classList.contains("is-active") && !document.hidden;

    if (active) {
      const themeBlend = 1 - Math.exp(-delta * 7.5);
      tubeMaterial.color.lerp(themeTarget.tube, themeBlend);
      tubeMaterial.emissive.lerp(themeTarget.emissive, themeBlend);
      tubeMaterial.sheenColor.lerp(themeTarget.sheen, themeBlend);
      flowUniforms.uFlowColor.value.lerp(themeTarget.flow, themeBlend);
      flowUniforms.uFlowSecondary.value.lerp(themeTarget.flowSecondary, themeBlend);
      flowUniforms.uFlowStrength.value = THREE.MathUtils.lerp(
        flowUniforms.uFlowStrength.value,
        themeTarget.flowStrength,
        themeBlend,
      );
      arrowMaterial.color.lerp(themeTarget.arrow, themeBlend);
      arrowMaterial.emissive.lerp(themeTarget.arrowEmissive, themeBlend);
      particleMaterial.color.lerp(themeTarget.particle, themeBlend);
      edgeLight.color.lerp(themeTarget.edgeLight, themeBlend);
      frontLight.color.lerp(themeTarget.frontLight, themeBlend);
      causticLight.color.lerp(themeTarget.causticLight, themeBlend);
      refractedEdgeLight.color.lerp(themeTarget.flowSecondary, themeBlend);
      renderer.toneMappingExposure = THREE.MathUtils.lerp(
        renderer.toneMappingExposure,
        themeTarget.exposure,
        themeBlend,
      );
      if (!reducedMotion.matches) {
        const seconds = time * 0.001;
        const blend = 1 - Math.exp(-delta * 5.2);
        pointer.lerp(pointerTarget, blend);
        flowUniforms.uFlowTime.value = seconds;
        word.rotation.x = THREE.MathUtils.lerp(word.rotation.x, -0.08 + pointer.y * 0.045, blend);
        word.rotation.y = THREE.MathUtils.lerp(word.rotation.y, 0.055 + pointer.x * 0.085, blend);
        word.rotation.z = THREE.MathUtils.lerp(word.rotation.z, -0.018 - pointer.x * 0.012, blend);
        word.position.y = -0.03 + Math.sin(seconds * 0.72) * 0.035;
        tubeMaterial.clearcoatRoughness = 0.065 + (Math.sin(seconds * 0.83) * 0.5 + 0.5) * 0.025;
        tubeMaterial.transmission = 0.045 + (Math.sin(seconds * 0.61 + 0.8) * 0.5 + 0.5) * 0.035;
        tubeMaterial.iridescence = 0.1 + (Math.sin(seconds * 0.47) * 0.5 + 0.5) * 0.1;
        if (scene.environmentRotation) scene.environmentRotation.y = seconds * 0.075;
        causticLight.position.set(
          Math.sin(seconds * 0.58) * 7.2 + pointer.x * 1.1,
          1.5 + Math.cos(seconds * 0.76) * 2.5 + pointer.y * 0.6,
          5.6 + Math.sin(seconds * 0.41) * 1.1,
        );
        causticLight.intensity = 22 + (Math.sin(seconds * 1.24) * 0.5 + 0.5) * 12;
        refractedEdgeLight.position.set(
          Math.cos(seconds * 0.43 + 1.4) * 6.4,
          -1.2 + Math.sin(seconds * 0.67) * 2.2,
          4.2,
        );
        particles.rotation.z = seconds * 0.035;
        particles.position.x = Math.sin(seconds * 0.52) * 0.07;
        particleMaterial.opacity = 0.46 + (Math.sin(seconds * 1.45) * 0.5 + 0.5) * 0.2;
        const arrowPulse = 1 + Math.sin(seconds * 2.1) * 0.018;
        arrow.scale.setScalar(arrowPulse);
      }
      renderer.render(scene, camera);
    }
    frameId = window.requestAnimationFrame(render);
  };

  const onPointerMove = (event) => {
    pointerTarget.set(
      (event.clientX / Math.max(window.innerWidth, 1) - 0.5) * 2,
      (0.5 - event.clientY / Math.max(window.innerHeight, 1)) * 2,
    );
  };

  const observer = new ResizeObserver(resize);
  const themeObserver = new MutationObserver(() => {
    themeTarget = getThemePalette();
  });
  observer.observe(host);
  themeObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    host.classList.remove("is-webgl-ready");
  });
  window.addEventListener("pagehide", () => {
    disposed = true;
    window.cancelAnimationFrame(frameId);
    observer.disconnect();
    themeObserver.disconnect();
    window.removeEventListener("pointermove", onPointerMove);
    renderer.dispose();
    scene.environment?.dispose();
    tubeMaterial.dispose();
    arrowMaterial.dispose();
    particleMaterial.dispose();
    scene.traverse((object) => object.geometry?.dispose?.());
  }, { once: true });

  resize();
  renderer.render(scene, camera);
  host.classList.add("is-webgl-ready");
  frameId = window.requestAnimationFrame(render);
};

start();
