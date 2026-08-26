import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { getCapsule, oklch2rgb_abs } from "@wenhaoqi/wasm_design_utils";

const root = fileURLToPath(new URL("..", import.meta.url));
const output = path.join(root, "src", "cad2gis", "webdemo", "assets");

const hex = ({ R, G, B }) => `#${[R, G, B].map((value) => value.toString(16).padStart(2, "0")).join("")}`;

const build = async () => {
  await mkdir(output, { recursive: true });
  const [teal, lime, violet] = await Promise.all([
    oklch2rgb_abs(0.68, 0.14, 166),
    oklch2rgb_abs(0.88, 0.18, 112),
    oklch2rgb_abs(0.62, 0.15, 300),
  ]);
  const colors = { teal: hex(teal), lime: hex(lime), violet: hex(violet) };
  const [capsuleWide, capsuleSmall] = await Promise.all([
    getCapsule(224, 48, 24),
    getCapsule(148, 38, 19),
  ]);

  const grid = `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" fill="none">
  <defs><pattern id="minor" width="80" height="80" patternUnits="userSpaceOnUse"><path d="M80 0H0V80" stroke="#173f43" stroke-opacity=".10"/></pattern><pattern id="major" width="320" height="320" patternUnits="userSpaceOnUse"><rect width="320" height="320" fill="url(#minor)"/><path d="M320 0H0V320" stroke="#14676a" stroke-opacity=".16"/><circle cx="0" cy="0" r="2.5" fill="#14676a" fill-opacity=".30"/></pattern></defs>
  <rect width="1600" height="900" fill="url(#major)"/><g stroke="#14676a" stroke-opacity=".25"><path d="M800 0V900M0 450H1600"/><path d="M28 0V900M0 28H1600" stroke-dasharray="2 12"/></g><g fill="#173f43" fill-opacity=".44"><circle cx="28" cy="28" r="4"/><circle cx="1572" cy="28" r="4"/><circle cx="28" cy="872" r="4"/><circle cx="1572" cy="872" r="4"/></g>
</svg>`;

  const graph = `<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="680" viewBox="0 0 1120 680" fill="none">
  <defs><linearGradient id="flow" x1="80" y1="88" x2="1000" y2="590" gradientUnits="userSpaceOnUse"><stop stop-color="${colors.teal}"/><stop offset=".55" stop-color="#6be0c3"/><stop offset="1" stop-color="${colors.lime}"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="8" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter><style>.flow{stroke-dasharray:12 12;animation:dash 7s linear infinite}.node{transform-box:fill-box;transform-origin:center;animation:node 4s ease-in-out infinite}.node:nth-child(2n){animation-delay:-1.6s}.node:nth-child(3n){animation-delay:-2.8s}@keyframes dash{to{stroke-dashoffset:-240}}@keyframes node{50%{transform:scale(1.08);opacity:.78}}</style></defs>
  <g opacity=".22" stroke="#1a4d50" stroke-width="1"><path d="M78 92H1040M78 180H1040M78 268H1040M78 356H1040M78 444H1040M78 532H1040M78 620H1040"/><path d="M166 42V640M342 42V640M518 42V640M694 42V640M870 42V640"/></g><path class="flow" d="M116 184C212 76 288 106 368 218S514 390 598 250 742 124 822 264 930 490 1010 518" stroke="url(#flow)" stroke-width="3" stroke-linecap="round"/><path class="flow" d="M116 184C210 276 282 268 368 218S516 104 598 250 742 430 822 264 928 142 1010 518" stroke="#11383c" stroke-opacity=".18" stroke-width="1.5"/>
  <g font-family="ui-monospace, SFMono-Regular, Consolas, monospace" font-size="11" fill="#163b3f"><g transform="translate(34 38)"><path d="${capsuleSmall}" fill="#fff" fill-opacity=".86" stroke="#bfdad5"/><text x="19" y="24">01 / SOURCE FACTS</text></g><g transform="translate(254 116)"><path d="${capsuleSmall}" fill="#fff" fill-opacity=".86" stroke="#bfdad5"/><text x="19" y="24">02 / SEMANTIC</text></g><g transform="translate(490 352)"><path d="${capsuleSmall}" fill="#fff" fill-opacity=".86" stroke="#bfdad5"/><text x="19" y="24">03 / REGISTER</text></g><g transform="translate(718 106)"><path d="${capsuleSmall}" fill="#fff" fill-opacity=".86" stroke="#bfdad5"/><text x="19" y="24">04 / VALIDATE</text></g><g transform="translate(878 442)"><path d="${capsuleSmall}" fill="#fff" fill-opacity=".86" stroke="#bfdad5"/><text x="19" y="24">05 / DELIVER</text></g></g>
  <g stroke="#fff" stroke-width="5" filter="url(#glow)"><circle class="node" cx="116" cy="184" r="14" fill="${colors.teal}"/><circle class="node" cx="368" cy="218" r="14" fill="#52bfa4"/><circle class="node" cx="598" cy="250" r="14" fill="#4b9e8c"/><circle class="node" cx="822" cy="264" r="14" fill="#8fbd5d"/><circle class="node" cx="1010" cy="518" r="14" fill="${colors.lime}"/></g><g font-family="ui-monospace, SFMono-Regular, Consolas, monospace" font-size="12" fill="#163b3f"><text x="86" y="228">9,896 entities</text><text x="338" y="262">281 unresolved</text><text x="568" y="294">GCP / residuals</text><text x="792" y="308">4 tracks</text><text x="944" y="558">1,150 features</text></g>
</svg>`;

  const tunnelLines = Array.from({ length: 28 }, (_, index) => {
    const angle = (Math.PI * 2 * index) / 28;
    return `<path d="M800 450L${(800 + Math.cos(angle) * 720).toFixed(1)} ${(450 + Math.sin(angle) * 720).toFixed(1)}"/>`;
  }).join("");
  const tunnel = `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" fill="none"><defs><radialGradient id="core"><stop stop-color="${colors.teal}" stop-opacity=".70"/><stop offset=".45" stop-color="#173f43" stop-opacity=".08"/><stop offset="1" stop-color="#08171a" stop-opacity="0"/></radialGradient><style>.ray{animation:ray 8s ease-in-out infinite;transform-origin:800px 450px}.ray:nth-child(3n){animation-delay:-2s}.ray:nth-child(3n+1){animation-delay:-4s}@keyframes ray{50%{opacity:.22;transform:scale(.86)}}</style></defs><circle cx="800" cy="450" r="460" fill="url(#core)"/><g class="ray" stroke="#65d6bf" stroke-opacity=".36" stroke-width="1">${tunnelLines}</g><g stroke="#d5fa9b" stroke-opacity=".54" fill="none"><circle cx="800" cy="450" r="270" stroke-dasharray="2 12"/><circle cx="800" cy="450" r="160" stroke-dasharray="1 9"/></g><circle cx="800" cy="450" r="5" fill="${colors.lime}"/></svg>`;

  const stickers = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="220" viewBox="0 0 720 220" fill="none"><g font-family="ui-monospace, SFMono-Regular, Consolas, monospace" font-size="14" fill="#103b3d"><g transform="translate(8 32) rotate(-7)"><path d="${capsuleWide}" fill="#d9fb9c" stroke="#103b3d" stroke-width="2"/><text x="24" y="30">SHA-256 / IMMUTABLE</text></g><g transform="translate(250 18) rotate(4)"><path d="${capsuleWide}" fill="#fff" stroke="#103b3d" stroke-width="2"/><text x="24" y="30">GCP / UNVERIFIED</text></g><g transform="translate(112 116) rotate(5)"><path d="${capsuleWide}" fill="#a7e4d4" stroke="#103b3d" stroke-width="2"/><text x="24" y="30">DWG DIMENSION</text></g><g transform="translate(398 104) rotate(-5)"><path d="${capsuleWide}" fill="#fff" stroke="#103b3d" stroke-width="2"/><text x="24" y="30">GPKG / REPLAYABLE</text></g></g></svg>`;

  const manifest = { generator: "@wenhaoqi/wasm_design_utils@0.2.0", palette: colors, shapes: { capsuleWide, capsuleSmall }, assets: ["hero-grid.svg", "hero-evidence-graph.svg", "hero-tunnel.svg", "hero-stickers.svg"] };
  await Promise.all([
    writeFile(path.join(output, "hero-grid.svg"), grid),
    writeFile(path.join(output, "hero-evidence-graph.svg"), graph),
    writeFile(path.join(output, "hero-tunnel.svg"), tunnel),
    writeFile(path.join(output, "hero-stickers.svg"), stickers),
    writeFile(path.join(output, "hero-geometry.json"), JSON.stringify(manifest, null, 2)),
  ]);
  console.log(JSON.stringify({ output, ...manifest }, null, 2));
};

await build();
