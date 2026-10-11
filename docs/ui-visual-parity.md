# UI visual parity acceptance

## Model and scope

The October 11, 2026 user update selects GPT-6.1 Sol for UI work. No Luna or
other-model fallback is authorized. The dispatcher currently does not expose
GPT-6.1 Sol, so the GPT-6.1 Sol primary performs this pass directly.
The existing F1-only stage gate still applies; production page replacement and
backend slices are not part of this pass.

## Reference

Compare against the original five-panel raster in docs/assets/ui-reference.png.
The panel windows are approximately 505, 484, 532, 991, and 549 pixels wide,
respectively. This montage is not a set of full-resolution browser screenshots:
the font family, native viewport sizes, and original scaling are not supplied.
Do not equate a visually plausible font with verified font identity.

## Foundation pass

- Bundle Inter regular, medium, semibold, and bold locally with their OFL license.
  Use an isolated font-family name to avoid the old UI's Google Fonts styles.
  Disable synthesized weights. Inter remains a candidate, not a verified match.
- Replace geometric navigation placeholders with headset, document, and settings
  SVGs; replace the italic text mark with vector artwork. These are reconstructed
  artwork, not the original source assets.
- Tighten header, sidebar, main gutters, corner radii, and active-navigation color.
  The desktop dimensions are working tokens, not an accepted pixel baseline.
- Preserve fixture disclosure, truthful capability states, accessibility, and
  separate runtime/selection context even where the approved corrections differ
  from the illustrative image.

The bundled TTFs were retrieved from the Google Fonts Inter v20 stylesheet for
weights 400, 500, 600, and 700. web/public/fonts/ui-foundation/OFL.txt is the
upstream Inter license from rsms/inter. Only the isolated foundation uses them.

## Required visual gate

1. Capture rendered desktop and 390px views after document.fonts.ready.
2. Record viewport, device scale, browser, panel crop, and reference scale before
   comparison. Do not stretch either image independently to hide differences.
3. Compare typeface, weight, line height, text baselines, sidebar/header bounds,
   card bounds, gutters, icon shape, borders, colors, and corner radii.
4. Separate intentional semantic/accessibility changes from visual deviations.
5. Record overlay/difference results and unresolved deviations. Component tests,
   type checks, and builds cannot establish visual parity.

Exact parity is not accepted until these rendered comparisons are complete.
If the original font or vector assets cannot be established from the raster,
record that uncertainty rather than describing reconstructed assets as exact.

## Validation on October 11, 2026

- Foundation and fixture tests: 20 passed, including bundled-font and SVG checks.
- TypeScript no-emit check and optimized production build: passed.
- Desktop at 1280 x 800: local regular/semibold/bold fonts loaded; header 48px,
  sidebar 156px; no horizontal overflow. The unused medium face stays unloaded.
- Mobile at 390 x 844: no horizontal overflow; disconnected runtime retains the
  selected Monaco session; disabled action retains its accessible reason.
- Tab reaches the skip link. Focus paint could not be verified because the
  collaborative browser reported document.hasFocus() as false.
- Screenshots captured with the T3 preview. Full mock-panel overlays, original
  font identity, and exact pixel parity remain unverified. F1 is not accepted.
