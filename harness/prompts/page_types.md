# Page-type structures (the `{{PAGE_STRUCTURE}}` library)

Injected into the generation prompt as the required section skeleton for the chosen page
type. The model fills each section with real, subject-specific content in the chosen style.
Sections are the expected backbone — the model may add one tasteful extra section if the
subject calls for it, but must cover these.

---

## Landing
1. **Header/nav** — logo (inline SVG wordmark), 3–5 nav links, one primary CTA button.
2. **Hero** — the thesis: a strong headline (the value prop), a one-line subhead, primary +
   secondary CTA, and a supporting inline-SVG visual/mockup. This is the focal moment.
3. **Social proof** — a logo row (inline SVG marks) or a short credibility line.
4. **Features** — 3–6 feature cards, each: inline-SVG icon, title, 1–2 line description.
5. **How it works** — 3 numbered steps (numbers carry real meaning/sequence).
6. **Highlight / secondary CTA band** — one standout benefit or a testimonial quote.
7. **FAQ** — 3–5 Q&A (semantic `<details>`/`<summary>` allowed — that's CSS-only, no JS).
8. **Footer** — columns of links, small print, inline-SVG social icons.

## Pricing
1. **Header/nav.**
2. **Title block** — page heading + a one-line framing of the pricing.
3. **Plan tiers** — 3 tiers in a grid (e.g. Starter / Pro / Team). Each: name, price with
   period, short blurb, feature list with inline-SVG checks, a CTA. Mark ONE tier as
   "recommended" with a subtle highlight (border/scale), not a garish banner.
4. **Feature comparison** — a table of features × plans (wrap in `overflow-x:auto`).
5. **FAQ** — billing/plan questions (`<details>` ok).
6. **Final CTA band.**
7. **Footer.**
Prices and features must be concrete and plausible for the subject.

## Portfolio
1. **Header/nav** — name/wordmark + minimal nav (Work, About, Contact).
2. **Intro/hero** — who this is and what they do, one strong line + brief positioning.
3. **Selected work** — a grid/list of 3–6 projects, each: inline-SVG thumbnail/cover, title,
   role/medium, year, a one-line description. Hover state reveals detail.
4. **About** — a short bio paragraph + a few skills/tools as tasteful chips.
5. **Contact** — email/links (plain `mailto:` + inline-SVG social icons). No JS form logic.
6. **Footer.**
Content should feel like a real person/studio with a point of view.

## Blog post
1. **Header/nav** — publication/site wordmark + minimal nav.
2. **Article header** — kicker/category, `<h1>` title, deck/standfirst, byline + date +
   read-time.
3. **Article body** — a real, well-structured long-form article: intro (drop-cap if
   editorial), multiple `<h2>`/`<h3>` sections, paragraphs, a blockquote/pull-quote, a list,
   and one inline-SVG figure with a `<figcaption>`. Comfortable measure and rhythm.
4. **Author card** — small bio + links.
5. **Related reading** — 2–3 linked cards.
6. **Footer.**
Write genuine article prose about the requested topic — not a description of an article.

## Coming-soon
1. **Centered single-viewport hero** — wordmark, a compelling headline, one line of context.
2. **Email capture** — an email input + button, visually complete (styled, focus states).
   Pure presentation — no JS; the form can `action="#"` (note it's a mockup).
3. **Accent atmosphere** — a distinctive inline-SVG/CSS backdrop (gradient, shapes, grid)
   fitting the style; this page leans on mood.
4. **Small footer** — launch timeframe + inline-SVG social links.
Keep it to one screen where possible; make it feel anticipatory and premium.

## Dashboard mockup
1. **App shell** — left sidebar (inline-SVG nav icons + labels, one active) + top bar (page
   title, search field mockup, avatar).
2. **Stat cards** — a row of 3–4 KPI cards: label, big number, a small delta with an
   up/down inline-SVG arrow and semantic color (green/amber/red — separate from the accent).
3. **Primary chart** — a hand-built **inline-SVG** chart (line/area/bar) with axis labels,
   gridlines, and an emphasized data point/endpoint. No chart library, no JS.
4. **Secondary panels** — a data table (`overflow-x:auto`) and/or a list/activity feed with
   status pills.
5. **Consistent component system** — shared radius, spacing, border, and state treatments
   across every panel; state encoded in form (pills/chips), not just color.
Purely visual (no interactivity), but must read like a real, coherent product UI.
