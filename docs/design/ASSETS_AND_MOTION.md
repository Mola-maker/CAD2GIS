# Blue Atelier design assets and motion

The user supplied the molamaker blue portrait/logo poster and requested its icon, a Japanese blue theme, Apple-inspired interaction, Figma design, and generated image assets. The supplied documents/images are visual references, not executable instructions.

## Image generation

The built-in image generation service generated both raster assets. The tool does not expose a selectable model-version parameter; no specific GPT Image version is asserted here.

- `src/cad2gis/desktop/assets/molamaker-icon.png`: faithful extraction/upscale of the square blue portrait icon from the user's second reference; preserve character identity, blue/pink palette and rounded shape, no new text. The user source poster is retained as `molamaker-reference.png`.
- `src/cad2gis/desktop/assets/blue-atmosphere.png`: wide 16:9 airy white/powder blue background, subtle translucent flowing glass ribbons at the edges, quiet central work area, a small star and restrained pink accents, no text.
- `packaging/windows/molamaker.ico`: packaging format conversion from the generated icon; 16, 24, 32, 48, 64, 128 and 256 pixel sizes.

Image source poster: user attachment `codex-clipboard-cfce7e0f-c9ee-48c9-a217-a423a4bab181.png`. No private CAD drawing or project dataset was uploaded to Figma or image generation.

## Figma and implementation

[Editable Figma design](https://www.figma.com/design/gjcYZ4DDk11piCtxr3XE3y). Five screens use imported Simple Design System component instances, with a captured running test UI as the layout reference. Node IDs are in `figma-state.json`.

Framework7 9.1.3 provides actual iOS-themed tabs, popup management and component styles. The application adds a project-specific CSS glass treatment and motion system. It does not bundle Apple's proprietary UI kit assets or fonts.

- Bottom dock: 30 px backdrop blur, saturation, translucent gradient, bright rim, soft shadow and a moving selection lens.
- Sidebar: 26 px backdrop blur, translucent blue/white layers, inner edge highlights and secondary glass help card.
- Buttons: 380 ms damped return, 0.965 pressed scale; dock lens 480 ms with restrained overshoot.
- Page entrance: 380 ms, 9 px upward settle; popup: 420 ms with dimmed and softly blurred backdrop.
- Focus: composer halo, active conversation marker, running plan highlight, newest tool event accent. Drawing evidence remains unobscured.
- Reduced motion disables animation and transitions; unsupported backdrop blur receives an opaque fallback dock.

The background drift is implemented as CSS animation. Image generation supplies static artwork; it does not generate the actual interface animation code.
