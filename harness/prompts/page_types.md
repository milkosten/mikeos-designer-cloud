# Page-type structures (the `{{PAGE_STRUCTURE}}` library)

Injected into the generation prompt as the required skeleton for the chosen page type.
**Most outputs here are PRODUCT DESIGNS — real application UIs — not company homepages.**
Build them like a working product screen: real navigation and chrome, realistic data and
microcopy, visible component states, and small line-icons (NOT illustrations). The model
fills each section with subject-specific content in the chosen style.

---

## App Dashboard
A data-dense product dashboard — the app's main screen. Build the real UI, not a marketing page.
1. **App shell** — a left **sidebar** (product wordmark + small line-icon nav, one item active) and
   a **top bar** (page title, a search field, and an avatar/menu). This is real app chrome.
2. **Toolbar row** — page heading + a couple of controls (a segmented control / date-range /
   primary button), so it reads as an operable screen.
3. **KPI cards** — a row of 3–4 metric cards: label, big number, and a small delta with an up/down
   line-arrow in semantic color (green/amber/red — separate from the brand accent).
4. **Primary chart** — ONE real, hand-built **inline-SVG** chart (line/area/bar) with axis labels,
   gridlines, and an emphasized endpoint/point. A real labelled chart — never a filled blob.
5. **Data table** — a realistic table (wrap in `overflow-x:auto`) with a header row, ~5 rows of
   plausible data, and **status pills**; include a hover row state.
6. **Secondary panel** — an activity feed / list with items, timestamps, and small icons.
Every panel shares one component system (radius, border, spacing, state treatments).

## App Screen
A functional application screen — e.g. a project board, an inbox, a task list/detail, a chat, a
file browser, or an editor (pick what fits the subject). Design the working UI.
1. **App shell** — sidebar/rail + top bar as above (product nav, active item, user menu).
2. **Primary work area** — the actual feature: e.g. a **kanban** (3 columns of cards), an **inbox**
   (list + reading pane), a **table/list** with rows, or an **editor** (toolbar + canvas). Use
   realistic items and copy, not lorem.
3. **Item components** — cards/rows/messages with real structure: title, meta, tags/labels, avatars
   (initials in a circle, not photos), and small line-icon actions. Show at least one **hover** and
   one **selected/active** state.
4. **Right rail or detail** — a details/properties panel or a contextual sidebar for the selected item.
5. **Empty / count affordances** — a section count, a subtle empty-state hint, or a "+ add" control,
   so the screen feels alive and operable.
Interaction is implied visually (no JS): make states legible through styling.

## Settings
A product settings / account screen.
1. **App shell** — sidebar/top bar (as above), with "Settings" active.
2. **Settings nav** — a secondary nav (tabs or a left list): Profile, Account, Notifications,
   Billing, Security, etc. — one active.
3. **Grouped form sections** — 2–4 cards, each a labelled group of real controls: text inputs with
   labels + helper text, **toggles/switches** (pure-CSS look), selects, a segmented control, radio rows.
   Style focus, checked, and disabled states.
4. **A destructive/danger zone** — a clearly-differentiated section (e.g. delete account) with a
   secondary/danger button.
5. **Save bar** — a footer or inline actions (Save / Cancel) with clear primary vs secondary buttons.
Realistic labels and helper microcopy throughout; this must read like a real settings page.

## Onboarding
A signup / login / onboarding flow screen (a single screen of the flow).
1. **Split or centered layout** — a focused auth/onboarding card on one side and a **product value
   panel** (a short pitch + a small framed UI preview or feature list) on the other; or a clean
   centered card.
2. **The form** — real fields (email, password, or profile/setup inputs) with labels, helper text,
   visible focus states, a primary CTA, and secondary options (SSO buttons as line-icon+label, "or
   continue with", a link to switch login/signup). No JS — it's a styled mockup.
3. **Progress** — if it's multi-step onboarding, a step indicator (1–2–3) showing where the user is.
4. **Trust/footer** — small print, links, a reassurance line.
Make it feel premium and effortless — generous spacing, one clear primary action.

## Pricing
A product pricing page.
1. **Header** — product wordmark + minimal nav (or the app top bar).
2. **Title block** — heading + a one-line framing; optional monthly/annual segmented toggle (styled).
3. **Plan tiers** — 3 tiers in a grid (e.g. Starter / Pro / Team): name, price + period, blurb, a
   feature list with small line-icon checks, and a CTA. Mark ONE tier "recommended" with a subtle
   highlight (border/scale), not a garish banner.
4. **Comparison table** — features × plans (wrap in `overflow-x:auto`); use small **check / dash**
   line-icons per cell — never large or random glyphs.
5. **FAQ** — 3–5 billing questions (`<details>`/`<summary>` allowed — CSS only, no JS).
6. **Footer.**
Prices and features must be concrete and plausible for the product.

## Landing
A PRODUCT landing page (still product-first — show the product, don't just sell).
1. **Header/nav** — wordmark, a few links, a primary CTA.
2. **Hero** — a strong **typographic** headline (the value prop) + subhead + primary/secondary CTA,
   and a **framed product UI preview** (a small mock of the app built from real HTML/CSS — a card,
   a mini dashboard, a window chrome — NOT an illustration blob).
3. **Feature highlights** — 3–4 features, each with a small line-icon, title, and one line.
4. **A product detail band** — a larger feature shown with a small UI mock or a benefit + supporting
   points.
5. **Social proof** — a logo row (simple wordmarks) or a short testimonial.
6. **CTA band** + **footer** (real links, small print).
The hero visual is a framed UI preview or pure type — never a decorative illustration.
