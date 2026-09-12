# molamaker interaction artwork

The project uses the existing `desktop/assets/molamaker-reference.png` character identity: blue hair and eyes, a hair loop, white/blue outfit, and pink accents. The built-in image generation tool produced the artwork; it did not expose a selectable `image2.5` model identifier. These illustrations are interface artwork and never replace CAD source geometry, SVG evidence, engineering symbols, or GCP observations.

## Shipped assets

| Workspace asset, relative to `src/cad2gis/` | Use | Format |
| --- | --- | --- |
| `desktop/assets/molamaker-actions-v1.png` | Six button/operation states, three columns by two rows | 1536 × 1024 RGB, pale background |
| `desktop/assets/molamaker-semantic-layers-v1.png` | Expandable semantic extraction explanation | 1536 × 1024 RGB |

Sprite order is idle, press, working, success, error, cancelled. JavaScript ties operation states to real task results; CSS handles transition motion. These are state illustrations, not an animated video. Reduced-motion users receive the same state information without motion. Assets are stored inside the package-data tree and included in application wheels and source delivery.

The first sprite generation drew a checkerboard instead of producing an alpha channel. It was rejected for shipping and regenerated through the image tool as solid-background artwork. No programmatic background removal or hand-painted substitute was used.

## Final sprite edit prompt

> Edit this existing 1536x1024 character UI sprite sheet. Preserve exactly the same six molamaker blue-haired chibi poses, props, face identity, outfit, colors, and row-major order. Maintain exactly 3 columns x 2 rows in equal 512x512 square cells. IMPORTANT replace every gray checkerboard background pixel with a perfectly flat, uniform very pale blue-white background color #F4F8FD. NO checkerboard, no texture, no shadows on background, no typography, no frame or grid lines. Treat as a solid background asset, not transparent. Shrink each complete character+props to fit within its own cell with at least 30 pixels clear margin on every side so no content is clipped or crosses a cell boundary. The six states remain idle, pointing press, inspecting working, happy success, worried error, palm-up pause. Keep polished crisp anime illustrations, consistent scale and baseline. All background one flat color.

## Feature illustration prompt

> Use case: illustration-story. Asset type: production feature illustration for CAD2GIS semantic extraction panel, landscape 1536x1024. Use reference image as identity reference for molamaker blue-haired anime character. Clean polished soft cel-shaded 2D product illustration, pale blue-white #F4F8FD background. Show a small delightful blue-haired girl guide on the left examining an engineering map on a drafting desk. On the right, a visually clear exploded stack of three CAD map layers: cobalt cable routes, small amber rectangular fiber boxes, blue and pink circular pole symbols; a magnifier and three small cards depict separating point, line, and text entities, linking original symbols to matching organized cards using thin precise dotted lines. Preserve blue hair loop, blue eyes and white-blue outfit, subtle pink accents. The scene conveys inspect original drawing, identify symbols, prepare candidates for human review, without claiming auto-correct truth. Elegant minimal composition with generous breathing room, carefully controlled warm amber and cobalt, no UI screenshot, no dashboard, no buttons embedded, NO TEXT, no letters, no watermark, no checkerboard. All subjects fully framed. This is a companion explanatory illustration to actual accessible HTML labels, not the source of engineering geometry.
