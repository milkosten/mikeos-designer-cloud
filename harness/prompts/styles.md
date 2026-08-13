# Style directives (the `{{STYLE_DIRECTIVE}}` library)

Each block is injected verbatim into the generation prompt when the user picks that style.
They are deliberately concrete — palette, type, layout signature, texture, and do/don'ts —
so a smaller model produces a genuinely different design per style, not the same page
recolored. Values are *starting directions*; the model should adapt them to the subject,
not copy them literally.

---

## Modern
- **Feeling:** clean, spacious, contemporary product/SaaS. Confident and friendly, not flashy.
- **Palette:** light and airy — near-white ground, white surfaces, deep-slate ink, a confident
  indigo accent with a sky-blue secondary (use the provided tokens; don't invent color).
- **Type:** system sans, clear scale, medium-to-semibold headings, comfortable readable body.
- **Layout:** generous whitespace and an airy grid; rounded cards (medium radius) with SOFT,
  layered shadows and hairline borders; a clean top nav; a strong typographic hero, optionally
  beside a small framed UI preview built from real HTML/CSS. One focal accent moment.
- **Signature:** polish through spacing, soft depth and restraint — the accent used sparingly on
  the primary action and one highlight.
- **Avoid:** loud full-bleed saturated color blocks, heavy borders, clashing hues, gradients as a
  crutch, and the generic purple→blue hero. Calm and confident, never Bootstrap-generic.

## Minimal
- **Feeling:** calm, Swiss, confident whitespace. Content-first, almost no ornament.
- **Palette:** near-white tinted bg `#fafaf9`, ink `#1a1a19`, muted `#6b6b66`, ONE quiet
  accent (a single desaturated tone e.g. `#3a5a40` or `#33507a`). Surfaces = the bg with a
  hairline `#e7e7e3` border.
- **Type:** modern system sans. Big size jumps, light-to-regular weights, tight headings.
- **Layout:** wide margins, single readable column, lots of vertical air, left-aligned.
  Thin 1px dividers instead of boxes. Almost no shadows.
- **Signature:** restraint. Impact comes from scale + space, not color.
- **Avoid:** gradients, drop shadows, decorative blobs, more than one accent.

## Brutalist
- **Feeling:** raw, structural, loud. Web-as-document, exposed grid.
- **Palette:** stark `#ffffff` / `#000000` with ONE electric accent (`#ff3b00`, `#0000ff`,
  or `#00e000`). No in-between greys.
- **Type:** system sans or **mono**, heavy weights, oversized headlines, tight tracking,
  ALL-CAPS labels. Underlined links, visible focus boxes.
- **Layout:** thick `2–4px` solid black borders, hard rectangles (radius 0), visible grid
  lines, offset/asymmetric blocks, no soft shadows (use hard `4px 4px 0 #000` offsets).
- **Signature:** unapologetic contrast, exposed structure, monospace numerals.
- **Avoid:** soft gradients, rounded corners, pastel tones, gentle shadows.

## Glassmorphism
- **Feeling:** modern, layered, translucent depth over a colorful ground.
- **Palette:** a rich gradient ground (pick two harmonious hues for the subject, e.g. deep
  indigo→teal), white text, frosted panels: `background: rgba(255,255,255,0.08)` +
  `backdrop-filter: blur(14px)` + `border:1px solid rgba(255,255,255,0.18)`.
- **Type:** clean system sans, medium weights, generous spacing, light text on the ground.
- **Layout:** floating cards with soft large-radius (`16–24px`), layered depth via subtle
  shadows and blur, glowing accent highlights.
- **Signature:** translucency and depth; content sits on glass over color.
- **Avoid:** flat opaque cards, hard borders, muddy low-contrast text (keep text legible on
  the blur — add a subtle dark overlay behind text if needed).

## Editorial
- **Feeling:** a serious online magazine / long-read. Typographic, print-influenced.
- **Palette:** paper `#fbfaf7`, rich ink `#20201d`, a single ink-accent (`#7a1f1f` oxblood
  or `#1f3a5f` navy), hairline rules `#dcd8cf`.
- **Type:** **serif** for headlines and body (Georgia/ui-serif), generous measure, drop-cap
  on the lead paragraph, italic pull-quotes, small-caps bylines/labels.
- **Layout:** classic columns, strong baseline rhythm, large figure blocks, thin rules
  separating sections, an actual byline/date line.
- **Signature:** typography as the whole design; quiet color; print elegance.
- **Avoid:** sans-serif body, cards with shadows, buttony CTAs, techy gradients.

## Corporate
- **Feeling:** trustworthy modern SaaS. Polished, clear, confident but not flashy.
- **Palette:** white/`#f6f8fb` surfaces, slate ink `#0f172a`, muted `#5b6b82`, a professional
  blue or teal accent (`#2563eb` / `#0d9488`) used for primary actions; soft `#e6ebf2`
  borders.
- **Type:** system sans, clear hierarchy, medium weights, readable and even.
- **Layout:** tidy grid of feature cards, soft radius `10–12px`, subtle layered shadows,
  clear primary/secondary buttons, logo cloud, stat row with *meaningful* numbers.
- **Signature:** clarity and polish; obvious primary action; calm competence.
- **Avoid:** neon, brutalism, heavy gradients, gimmicks. Keep the accent disciplined.

## Playful
- **Feeling:** friendly, energetic, rounded, delightful (consumer/startup).
- **Palette:** 2–3 bright but harmonious hues (e.g. coral + sunshine + mint) on off-white,
  cheerful but coordinated; strong but not garish.
- **Type:** rounded-feeling system sans, bold headlines, big and friendly.
- **Layout:** generous radii (`18–28px`), soft colorful shadows, blobby inline-SVG shapes
  and doodads, chunky buttons, sticker-like badges, gentle hover bounce (reduced-motion safe).
- **Signature:** warmth and movement; playful inline-SVG illustration.
- **Avoid:** clashing neon, more than ~3 hues, childish clip-art vibes; keep it tasteful.

## Dark / Terminal
- **Feeling:** developer-grade, technical, high-contrast dark UI (think a dev tool).
- **Palette:** near-black tinted ground `#0b0e0f`, surface `#14181a`, light ink `#e6edf0`,
  muted `#8a97a0`, an accent that glows on dark (`#00e08a` green, `#4ea1ff` blue, or amber
  `#ffb02e`). Tinted, never flat `#000`.
- **Type:** **monospace** for code/labels/numerals, system sans for prose; small uppercase
  tracked labels.
- **Layout:** crisp 1px `#232a2d` borders, subtle inner glows, code-block treatments,
  keyboard-key chips, a faint grid or scanline texture via CSS, terminal-style prompts.
- **Signature:** precise, contrasty, "for people who build things".
- **Avoid:** pastel, paper textures, serif body, soft blurry glass.

## Retro
- **Feeling:** nostalgic — pick ONE era and commit: 70s warm print, 80s neon synthwave, or
  90s web. State the era in the design.
- **Palette (choose per subject):** 70s = mustard/rust/cream/olive; 80s = magenta/cyan/purple
  on deep navy with neon glow; 90s = primary web colors on grey.
- **Type:** era-appropriate — chunky rounded sans (70s), wide tracked caps + glow (80s),
  system default look (90s). Use letter-spacing and weight to evoke it.
- **Layout:** era motifs via inline SVG/CSS — sunburst rays, grids receding to a horizon,
  scanlines, starbursts, beveled buttons. Committed but still legible and responsive.
- **Signature:** a clear, coherent throwback that still reads as modern-quality craft.
- **Avoid:** a vague "old" mush; mixing eras; sacrificing readability for the gimmick.
