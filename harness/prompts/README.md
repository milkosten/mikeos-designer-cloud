# The Designer harness prompts

The "designer brain" that gives the local GPU (`qwen3:8b`) Claude-Code-level modern-UI
judgement, expressed as concrete rules a smaller model can follow.

## Flow (per user prompt)
1. **PLAN** — `plan_system.md` (GPU, `format`=JSON, `think:false`) → a build spec:
   brand/subject, palette+type direction, and the page set with real section copy.
2. **GENERATE** — `system_generate.md` (GPU, `think:false`), once per page. Placeholders
   filled from the plan + the pickers:
   - `{{PAGE_TYPE}}`, `{{PAGE_STRUCTURE}}` ← `page_types.md`
   - `{{STYLE_NAME}}`, `{{STYLE_DIRECTIVE}}` ← `styles.md`
   - `{{FILE_NAME}}`, `{{PAGE_LIST}}`, `{{USER_PROMPT}}`
   Output: one self-contained HTML doc (inline CSS, no JS, no external assets, inline SVG).
3. **SANITIZE (programmatic — do NOT trust the model)** — parse the HTML and hard-strip:
   `<script>`, inline event handlers, `javascript:` URLs, `<link rel=stylesheet>`, `@import`
   URLs, webfont URLs, any remote `src`/`href`/`url()`, external `<iframe>`. This is the
   real enforcement of the self-contained contract — the LLM instruction is only the first
   line of defence. (Same ethos as "never trust HTTP 200": verify the artifact.)
4. **REPAIR** — `repair_system.md` (GPU) only if the sanitizer had to remove something
   substantive or a quality check fails (missing `:root` tokens, placeholder text, no focus
   states). The model returns a corrected full document, which is sanitized again.
5. **WRITE** — the page files land in `sites/<id>/*.html`, live immediately at
   `designer.osmike.com/<id>/`.

## Design intent
- `system_generate.md` encodes the fundamentals: deliberate type scale + system-font stacks
  (webfonts are banned by the self-contained rule), tinted neutrals + one restrained accent,
  layout/space via grid+gap tokens, restrained depth, real copy, accessibility, and an
  explicit list of AI-generated-design clichés to avoid.
- `styles.md` makes the 8 styles genuinely distinct (palette/type/layout signature + do &
  don't), so "page-type × style" yields real variety, not one page recolored.
- `page_types.md` gives each of the 6 page types a required section skeleton to fill.

## Tuning notes for `qwen3:8b`
- Always send `think:false` (qwen3 returns empty otherwise).
- Keep the system prompt prescriptive and concrete — small models follow rules and skeletons
  far better than abstract principles.
- One page per GENERATE call (don't ask for multiple files in one response); the harness
  loops pages from the plan.
- If output includes markdown fences or prose, strip to the `<!doctype …</html>` span before
  sanitizing.
