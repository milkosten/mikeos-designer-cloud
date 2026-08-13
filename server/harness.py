"""The design pipeline: plan -> generate (per page) -> sanitize -> repair? -> re-sanitize.

The prompt files under harness/prompts/ ARE the design brain — we load and use them, we
do not embed our own design rules. We slice the relevant `## Heading` section out of
styles.md / page_types.md for the chosen style / page type, fill the {{PLACEHOLDERS}} in
system_generate.md, and run the GPU (qwen3:8b) once per page.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server import gpu
from server import style_tokens
from server.sanitize import sanitize_html, has_violation
from server.analyze import analyze, autofix

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "prompts"


# ---- prompt / section loading ---------------------------------------------
def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _sections(md: str) -> Dict[str, str]:
    """Split a markdown doc into {heading-title: body} for level-2 (## ) headings.
    The body runs until the next ## heading. A leading `# Title` and intro are ignored."""
    out: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


# Load once at import (files are small and static).
_SYSTEM_GENERATE = _load("system_generate.md")
_PLAN_SYSTEM = _load("plan_system.md")
_REPAIR_SYSTEM = _load("repair_system.md")
_STYLES = _sections(_load("styles.md"))
_PAGE_TYPES = _sections(_load("page_types.md"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _match_section(sections: Dict[str, str], key: str) -> Tuple[str, str]:
    """Return (canonical_name, body) for a style/page-type id or name (loose match)."""
    nk = _norm(key)
    for name, body in sections.items():
        if _norm(name) == nk:
            return name, body
    # startswith / contains fallback (e.g. "dark" -> "Dark / Terminal", "blog" -> "Blog post")
    for name, body in sections.items():
        n = _norm(name)
        if n.startswith(nk) or nk.startswith(n) or nk in n or n in nk:
            return name, body
    raise KeyError(key)


def style_directive(style: str) -> Tuple[str, str]:
    return _match_section(_STYLES, style)


def page_structure(page_type: str) -> Tuple[str, str]:
    return _match_section(_PAGE_TYPES, page_type)


def list_styles() -> List[Dict[str, str]]:
    """Meta for /api/meta: id + name + short description (from the Feeling line)."""
    return [_meta_entry(name, body) for name, body in _STYLES.items()]


def list_page_types() -> List[Dict[str, str]]:
    return [_meta_entry(name, body) for name, body in _PAGE_TYPES.items()]


# Human blurbs for the page types (their md is a section skeleton, not prose).
_PAGE_TYPE_DESC = {
    "App Dashboard": "A data-dense product dashboard: sidebar + top bar, KPI cards, a real inline-SVG chart, and data tables.",
    "App Screen": "A functional app screen (board, inbox, list/detail, or editor) with real chrome, data, and component states.",
    "Settings": "A product settings/account screen: section nav, grouped forms, toggles, and tabs.",
    "Onboarding": "A signup / login / onboarding flow screen with a form, steps, and value framing.",
    "Pricing": "A product pricing page: plan tiers, a comparison table, and FAQ.",
    "Landing": "A product landing page: a hero with a framed UI preview, features, and CTA.",
}


def _meta_entry(name: str, body: str) -> Dict[str, str]:
    # description = the "**Feeling:** ..." line (styles) or a curated blurb (page types).
    desc = ""
    m = re.search(r"\*\*Feeling:\*\*\s*(.+)", body)
    if m:
        desc = m.group(1).strip().rstrip(".")
    elif name in _PAGE_TYPE_DESC:
        desc = _PAGE_TYPE_DESC[name]
    else:
        for line in body.splitlines():
            t = re.sub(r"^[\d.\-*\s]+", "", line).strip()
            if t:
                desc = re.sub(r"[*_`]", "", t)
                desc = re.split(r"[.—]", desc)[0].strip()
                break
    return {"id": _slug(name), "name": name, "description": desc}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---- HTML extraction ------------------------------------------------------
_DOC_RE = re.compile(r"<!doctype html>.*?</html>", re.IGNORECASE | re.DOTALL)


def extract_html(text: str) -> str:
    """Pull the <!doctype html>…</html> span out of model output (strip fences/prose)."""
    if not text:
        return ""
    # strip code fences
    text = re.sub(r"```[a-zA-Z]*\n?", "", text).replace("```", "")
    m = _DOC_RE.search(text)
    if m:
        return m.group(0).strip()
    # fallback: from first <html to last </html>
    lo = text.lower()
    a = lo.find("<html")
    b = lo.rfind("</html>")
    if a != -1 and b != -1:
        return text[a:b + len("</html>")].strip()
    return text.strip()


# ---- quality check --------------------------------------------------------
def _quality_ok(html: str) -> Tuple[bool, List[str]]:
    """Cheap structural quality gate. Returns (ok, issues[])."""
    issues: List[str] = []
    low = html.lower()
    if ":root" not in low:
        issues.append("no :root token block")
    if "<style" not in low:
        issues.append("no <style> block")
    if "</html>" not in low or "<!doctype" not in low:
        issues.append("incomplete document")
    for placeholder in ("lorem ipsum", "your text here", "feature 1", "feature 2",
                        "{{", "[placeholder", "click here"):
        if placeholder in low:
            issues.append(f"placeholder text: {placeholder}")
            break
    if "viewport" not in low:
        issues.append("no viewport meta")
    # exactly one <h1> — the hero/lead headline
    h1n = len(re.findall(r"<h1[\s>]", low))
    if h1n == 0:
        issues.append("no <h1> (the lead headline must be the single h1)")
    elif h1n > 1:
        issues.append(f"{h1n} <h1> elements (use exactly one)")
    # duplicate id attributes — invalid, and the reused-SVG-gradient bug
    ids = re.findall(r'\bid=["\']([^"\']+)["\']', html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        issues.append("duplicate id(s): " + ", ".join(dupes[:6]))
    # hero art that is just a flat filled rectangle (a lone rect / full-canvas path)
    if re.search(r"<svg[^>]*>\s*<(rect|path)[^>]*(fill=)[^>]*/?>\s*(<defs>.*?</defs>\s*)?</svg>",
                 html, re.I | re.S):
        issues.append("hero/graphic is a single flat filled shape (make it a real composition)")
    return (len(issues) == 0, issues)


# ---- steps ----------------------------------------------------------------
_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "brand": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tagline": {"type": "string"},
                "tone": {"type": "string"},
            },
            "required": ["name", "tagline", "tone"],
        },
        "palette_hint": {"type": "string"},
        "type_hint": {"type": "string"},
        "needs_app": {"type": "boolean"},
        "data_model": {
            "type": "object",
            "properties": {
                "storage": {"type": "string"},                      # "localStorage" | "indexeddb"
                "keys": {"type": "array", "items": {"type": "string"}},
                "entities": {"type": "string"},
            },
        },
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "title": {"type": "string"},
                    "meta_description": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "purpose": {"type": "string"},
                                "copy": {"type": "string"},
                            },
                            "required": ["id", "purpose", "copy"],
                        },
                    },
                },
                "required": ["file", "title", "sections"],
            },
        },
    },
    "required": ["brand", "pages"],
}


async def plan(prompt: str, page_type_name: str, style_name: str,
               structure_body: str, style_body: str) -> Dict[str, Any]:
    """PLAN step — a JSON build spec via plan_system.md (format=schema)."""
    user = (
        f"User request:\n{prompt}\n\n"
        f"Chosen page type: {page_type_name}\n"
        f"Page-type structure to follow:\n{structure_body}\n\n"
        f"Chosen visual style: {style_name}\n"
        f"Style directive:\n{style_body}\n\n"
        f"Produce the build plan JSON now."
    )
    content = await gpu.chat(
        [{"role": "system", "content": _PLAN_SYSTEM},
         {"role": "user", "content": user}],
        schema=_PLAN_SCHEMA, temperature=0.6, num_predict=4096,
    )
    try:
        return json.loads(content)
    except Exception:
        # tolerate leading/trailing prose around the JSON
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# Extra generation guidance appended when the project opts into interactivity.
_INTERACTIVE_DIRECTIVE = (
    "\n\n## INTERACTIVITY (this project is INTERACTIVE)\n"
    "You MAY include self-contained vanilla JavaScript for REAL interactivity — tabs, "
    "modals/dialogs, accordions, form validation, a mobile nav toggle, filtering, small "
    "stateful widgets. Rules: use a single inline `<script>` at the end of `<body>` (or a few "
    "inline handlers); NO external `src`, NO libraries/CDNs, NO fetch/XHR/WebSocket to any host. "
    "Keep it small, accessible (keyboard + aria-expanded/aria-selected), and progressive. If the "
    "project has multiple pages, link them with plain relative `<a href=\"other.html\">` nav.")


def _app_directive(files: List[str], data_model: Optional[Dict[str, Any]]) -> str:
    """Per-page directive for multi-file consistency + a browser (frontend) data layer."""
    d = ""
    if len(files) > 1:
        d += ("\n\n## MULTI-FILE PROJECT\n"
              f"This project has {len(files)} files: {', '.join(files)}. Build THIS file so it is "
              "CONSISTENT with the others — identical design tokens, and a shared nav (top bar or "
              "sidebar) that links every file with relative `<a href=\"file.html\">`. `index.html` is "
              "the home/entry. Do not invent files that aren't in that list.")
    dm = data_model or {}
    if dm:
        storage = dm.get("storage") or "localStorage"
        keys = dm.get("keys") or []
        entities = dm.get("entities") or ""
        d += ("\n\n## FRONTEND DATABASE (no backend)\n"
              f"Persist data in the browser with **{storage}** — implement a REAL data layer with "
              "add / edit / delete / read so data survives navigation AND page reload. There is NO "
              "server and NO network. "
              + (f"Use these EXACT storage keys on every page: {', '.join(keys)}. " if keys else "")
              + (f"Data model: {entities}. " if entities else "")
              + "Seed a few realistic example records on first load if empty. Every page reads/writes "
                "the SAME keys so the whole app stays coherent across files.")
    return d


_EXPAND_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "audience": {"type": "string"},
        "purpose": {"type": "string"},
        "tone": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
    },
    "required": ["subject", "description"],
}


async def expand_prompt(prompt: str, page_type_hint: str, style_name: str) -> Dict[str, Any]:
    """EXPANSION step (before planning): turn a raw prompt into a richer creative brief —
    infer the product/subject, audience, purpose, key sections/content and tone. Fast, cheap,
    tolerant. Returns {subject,audience,purpose,tone,sections[],description}."""
    sys = (
        "You are a creative director interpreting a short website request. Expand it into a "
        "concise but concrete brief: infer the product/subject, the target audience, the "
        "purpose of the site, the key sections/content it should include, and the tone of voice. "
        "Be specific and plausible — invent a believable brand/subject if the request is vague. "
        "Do NOT write HTML. Respond with JSON only: "
        '{"subject","audience","purpose","tone","sections":[...],"description"}. '
        "`description` is a rich 2-4 sentence paragraph a designer can build from.")
    user = (f"Request: {prompt}\n"
            f"Intended kind of page: {page_type_hint or 'auto'}\n"
            f"Visual style: {style_name}\n\nProduce the brief JSON now.")
    try:
        out = await gpu.chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            schema=_EXPAND_SCHEMA, temperature=0.5, num_predict=1200)
        data = json.loads(out)
        if isinstance(data, dict) and data.get("description"):
            return data
    except Exception as e:
        logger.warning("prompt expansion failed (%s); using raw prompt", e)
    return {"subject": "", "description": prompt}


async def generate_page(user_prompt: str, plan_spec: Dict[str, Any],
                        page: Dict[str, Any], page_type_name: str, structure_body: str,
                        style_name: str, style_body: str,
                        page_list: List[str], interactive: bool = False,
                        on_token=None) -> str:
    """GENERATE step — one self-contained HTML page via system_generate.md (streamed)."""
    file_name = page.get("file", "index.html")
    # Inject the plan's per-page content so the page follows the plan, not just the prompt.
    plan_note = json.dumps({"brand": plan_spec.get("brand"),
                            "palette_hint": plan_spec.get("palette_hint"),
                            "type_hint": plan_spec.get("type_hint"),
                            "files": page_list,
                            "data_model": plan_spec.get("data_model"),
                            "this_page": page}, ensure_ascii=False)
    filled = (
        _SYSTEM_GENERATE
        .replace("{{PAGE_TYPE}}", page_type_name)
        .replace("{{PAGE_STRUCTURE}}", structure_body)
        .replace("{{STYLE_NAME}}", style_name)
        .replace("{{STYLE_DIRECTIVE}}", style_body)
        .replace("{{ROOT_TOKENS}}", style_tokens.root_css(style_name))
        .replace("{{FILE_NAME}}", file_name)
        .replace("{{PAGE_LIST}}", ", ".join(page_list) or file_name)
        .replace("{{USER_PROMPT}}",
                 f"{user_prompt}\n\nBUILD PLAN for this page (follow it):\n{plan_note}")
    )
    if interactive:
        filled += _INTERACTIVE_DIRECTIVE
    if plan_spec.get("needs_app") or len(page_list) > 1 or plan_spec.get("data_model"):
        filled += _app_directive(page_list, plan_spec.get("data_model"))
    messages = [
        {"role": "system", "content": filled},
        {"role": "user", "content":
            f"Produce the complete self-contained HTML document for `{file_name}` now."}]
    content = await _stream_collect(messages, file_name, on_token,
                                    temperature=0.4, num_predict=8192)
    return extract_html(content)


async def _stream_collect(messages, file_name, on_token, *, temperature, num_predict) -> str:
    """Stream a generation, forwarding raw deltas to on_token(file, delta), and return the
    accumulated text. Falls back to a non-streamed call if streaming fails mid-flight."""
    parts: List[str] = []
    try:
        async for delta in gpu.stream_chat(messages, temperature=temperature,
                                           num_predict=num_predict):
            parts.append(delta)
            if on_token and delta:
                try:
                    on_token(file_name, delta)
                except Exception:  # a slow/broken consumer must not kill generation
                    pass
    except Exception as e:  # network / provider stream error -> fall back to buffered call
        if parts:
            logger.warning("stream for %s errored after %d chunks (%s); using partial",
                           file_name, len(parts), e)
            return "".join(parts)
        logger.warning("stream for %s failed (%s); falling back to buffered chat",
                       file_name, e)
        return await gpu.chat(messages, temperature=temperature, num_predict=num_predict)
    return "".join(parts)


async def repair(html: str, reason: str) -> str:
    """REPAIR step — hand the current HTML back to the GPU to fix issues."""
    user = (
        f"Problems to fix: {reason}\n\n"
        f"Here is the current HTML. Return the full corrected document only.\n\n{html}"
    )
    content = await gpu.chat(
        [{"role": "system", "content": _REPAIR_SYSTEM},
         {"role": "user", "content": user}],
        temperature=0.4, num_predict=8192,
    )
    return extract_html(content)


def _hard_problems(cleaned: str, removed: List[str]) -> List[str]:
    """Only HARD problems justify a GPU repair round — everything soft/stylistic is already
    fixed deterministically by autofix + style_tokens.enforce, so we do NOT spend a GPU round
    on it. Hard = sanitizer removed disallowed content, a broken document shell, no <h1>, or
    duplicate ids."""
    problems: List[str] = []
    if has_violation(removed):
        problems.append("removed disallowed content: " + ", ".join(sorted(set(removed))))
    low = cleaned.lower()
    if "<!doctype" not in low or "</html>" not in low:
        problems.append("incomplete document (missing <!doctype>/</html>)")
    if len(re.findall(r"<h1[\s>]", low)) == 0:
        problems.append("no <h1> (the lead headline must be the single h1)")
    ids = re.findall(r'\bid=["\']([^"\']+)["\']', cleaned)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append("duplicate id(s): " + ", ".join(dupes[:6]))
    return problems


async def build_page(user_prompt: str, plan_spec: Dict[str, Any], page: Dict[str, Any],
                     page_type_name: str, structure_body: str, style_name: str,
                     style_body: str, page_list: List[str], progress=None,
                     interactive: bool = False, on_token=None) -> Dict[str, str]:
    """Full per-page pipeline: generate -> sanitize -> autofix -> repair? -> re-check."""
    emit = progress or (lambda *a, **k: None)
    file_name = page.get("file", "index.html")
    emit("Designing the page", file_name)
    raw = await generate_page(user_prompt, plan_spec, page, page_type_name,
                              structure_body, style_name, style_body, page_list,
                              interactive=interactive, on_token=on_token)
    cleaned, removed = sanitize_html(raw, interactive)
    cleaned = autofix(cleaned, style_name, page_type_name)   # deterministic repairs first
    cleaned = style_tokens.enforce(cleaned, style_name)      # lock the style palette/type/font
    emit("Checking the design", file_name)

    # Closed-loop QA — but only for HARD problems (Kimi output is usually clean; the soft/style
    # findings are already fixed deterministically). One repair round at most.
    MAX_ROUNDS = 1
    for rnd in range(MAX_ROUNDS):
        problems = _hard_problems(cleaned, removed)
        if not problems:
            break
        reason = "; ".join(problems)
        emit("Polishing the design", file_name)
        logger.info("repair round %d for %s: %s", rnd + 1, file_name, reason)
        try:
            repaired = await repair(cleaned, reason)
        except Exception as e:
            logger.warning("repair failed for %s (keeping current): %s", file_name, e)
            break
        if repaired and "</html>" in repaired.lower():
            cleaned, removed = sanitize_html(repaired, interactive)
            cleaned = autofix(cleaned, style_name, page_type_name)
            cleaned = style_tokens.enforce(cleaned, style_name)
        else:
            break

    return {"file": file_name, "html": cleaned}


_CLASSIFY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"page_type": {"type": "string", "enum": list(_PAGE_TYPES.keys())}},
    "required": ["page_type"],
}


async def classify_page_type(prompt: str) -> str:
    """Infer which page type the user is building, from their prompt (no picker needed)."""
    names = list(_PAGE_TYPES.keys())
    guide = "\n".join(f"- {n}: {_PAGE_TYPE_DESC.get(n, '')}" for n in names)
    sys = ("You classify what kind of web page or app screen the user wants to build, so the "
           "builder can choose the right layout. Choose the SINGLE best-fitting type.\n"
           f"Allowed types:\n{guide}\n"
           'Respond with JSON only: {"page_type": "<one of the exact type names above>"}.')
    try:
        out = await gpu.chat(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"Build request: {prompt}"}],
            schema=_CLASSIFY_SCHEMA, temperature=0.0, num_predict=200)
        pt = (json.loads(out) or {}).get("page_type", "").strip()
    except Exception as e:
        logger.warning("page-type classify failed: %s", e)
        pt = ""
    if pt in _PAGE_TYPES:
        logger.info("inferred page type: %s", pt)
        return pt
    try:
        return _match_section(_PAGE_TYPES, pt)[0]
    except Exception:
        return "Landing"


_EDIT_SYSTEM = (
    "You are editing an existing self-contained HTML page. Apply the user's change and "
    "return the COMPLETE updated document (start `<!doctype html>`, end `</html>`, no "
    "commentary, no fences). Change as little as possible; keep all other markup, styles, "
    "and content identical. The page must stay fully self-contained: no external resources.")


async def edit_page(current_html: str, instruction: str, style_name: str,
                    page_type_name: str = "", *, interactive: bool = False,
                    selector: Optional[str] = None, outer_html: Optional[str] = None,
                    file_name: str = "index.html", on_token=None) -> str:
    """FAST targeted edit — ONE streamed model call that rewrites this single page applying
    the change minimally. No plan, no multi-page. Returns the sanitized+enforced HTML."""
    focus = ""
    if outer_html:
        focus = ("\n\nFocus your change on THIS element (leave the rest of the page "
                 f"untouched):\n{outer_html[:3000]}")
    elif selector:
        focus = f"\n\nThe user is pointing at the element matching CSS selector `{selector}`."
    if interactive:
        focus += ("\n\nInline vanilla JavaScript is allowed on this page (no external src, no "
                  "network) — keep or add small interactivity as needed.")
    user = (f"Current page (`{file_name}`):\n{current_html}\n\n"
            f"Change to apply: {instruction}{focus}\n\nReturn the full updated document only.")
    messages = [{"role": "system", "content": _EDIT_SYSTEM},
                {"role": "user", "content": user}]
    content = await _stream_collect(messages, file_name, on_token,
                                    temperature=0.3, num_predict=8192)
    html = extract_html(content)
    cleaned, _removed = sanitize_html(html, interactive)
    cleaned = autofix(cleaned, style_name, page_type_name)
    cleaned = style_tokens.enforce(cleaned, style_name)
    return cleaned


def _brief_from_plan(plan_spec: Dict[str, Any], enriched: Dict[str, Any]) -> Dict[str, Any]:
    """The stored `brief` = the plan spec (brand/tagline/tone/pages/sections) PLUS the
    enriched interpretation from the expansion step, so the UI can show + edit it."""
    brief = dict(plan_spec)
    brief["enriched"] = enriched
    return brief


def _hero_keywords(plan_spec: Dict[str, Any]) -> str:
    """Keywords for a hero stock image, from the brand + hero section copy."""
    brand = (plan_spec.get("brand") or {})
    parts = [brand.get("name", ""), plan_spec.get("type_hint", "")]
    for page in (plan_spec.get("pages") or []):
        for sec in (page.get("sections") or []):
            if "hero" in (sec.get("id", "") + sec.get("purpose", "")).lower():
                parts.append(sec.get("copy", "")[:120])
                break
        break
    words = re.findall(r"[A-Za-z]{4,}", " ".join(p for p in parts if p))
    stop = {"with", "that", "this", "your", "from", "have", "will", "into", "page", "hero",
            "landing", "website", "product", "section", "modern", "clean"}
    keep = [w.lower() for w in words if w.lower() not in stop][:3]
    return ",".join(keep) or (brand.get("name", "") or "abstract")


async def _maybe_inject_hero_image(pages: List[Dict[str, str]],
                                   plan_spec: Dict[str, Any]) -> None:
    """Best-effort: if the plan marks a hero/photo slot, fetch ONE relevant free image and
    inline it as a data: URI CSS background variable (`--hero-image`) on the first page, so a
    hero that uses `background-image: var(--hero-image)` gets a real photo. Degrades to a
    no-op on any failure — never breaks generation."""
    if not pages:
        return
    plan_txt = json.dumps(plan_spec).lower()
    if not any(k in plan_txt for k in ("hero", "photo", "image", "cover")):
        return
    try:
        from server import chrome
        data_uri = await chrome.fetch_image_data_uri(_hero_keywords(plan_spec))
    except Exception as e:  # noqa: BLE001
        logger.info("hero image step skipped: %s", e)
        return
    if not data_uri:
        return
    html = pages[0].get("html", "")
    idx = html.lower().rfind("</style>")
    if idx == -1:
        return
    inject = f"\n:root{{--hero-image:url('{data_uri}');}}\n"
    pages[0]["html"] = html[:idx] + inject + html[idx:]


async def build_project(prompt: str, page_type: str, style: str,
                        edit_pages: Optional[List[Dict[str, str]]] = None,
                        instruction: Optional[str] = None,
                        progress=None, interactive: bool = False, on_token=None,
                        from_brief: Optional[Dict[str, Any]] = None,
                        images: bool = False,
                        ) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Run the whole pipeline. Returns (brief_spec, pages[{file,html}]).

    Normal flow: expand the prompt -> emit `brief` -> plan -> generate each page (streamed).
    When `edit_pages`/`instruction` are given (legacy refine flow), the plan revises the
    existing pages per the instruction. When `from_brief` is given (PUT brief / restyle),
    expansion AND planning are skipped and the brief IS the plan — same content, regenerated
    (with a possibly new style). `progress(stage, detail)` / `on_token(file, delta)` drive the
    live UI.
    """
    emit = progress or (lambda *a, **k: None)
    style_name, style_body = style_directive(style)

    if from_brief is not None:
        # Reuse the edited/prior brief as the plan verbatim (content-vs-design iteration).
        plan_spec = dict(from_brief)
        enriched = plan_spec.get("enriched") or {}
        pt = plan_spec.get("page_type") or page_type
        if not pt or str(pt).strip().lower() in ("auto", ""):
            pt = "Landing"
        page_type_name, structure_body = page_structure(pt)
        plan_spec["page_type"] = page_type_name
        emit("Planning the layout and content", "")
    else:
        # Resolve the page type: infer it from the prompt when the caller didn't pick one.
        if not page_type or page_type.strip().lower() in ("auto", ""):
            emit("Understanding your request", "")
            resolved = await classify_page_type(prompt or instruction or "")
            page_type_name, structure_body = page_structure(resolved)
            emit("Detected page type", page_type_name)
        else:
            emit("Understanding your request", "")
            page_type_name, structure_body = page_structure(page_type)

        # EXPANSION — enrich the raw prompt into a creative brief (skipped on the legacy
        # instruction/refine flow, which already has a subject to preserve).
        enriched: Dict[str, Any] = {}
        effective_prompt = prompt
        if instruction:
            existing = "\n\n".join(
                f"[current page {p['file']}]\n{p.get('html','')[:4000]}"
                for p in (edit_pages or []))
            effective_prompt = (
                f"{prompt}\n\nThe user wants to REFINE the existing site with this instruction:\n"
                f"{instruction}\n\nKeep the same subject/brand and page set unless the "
                f"instruction says otherwise. Existing pages (truncated):\n{existing}")
        else:
            enriched = await expand_prompt(prompt, page_type_name, style_name)
            desc = enriched.get("description") or prompt
            effective_prompt = f"{prompt}\n\nInterpreted brief:\n{desc}"
            secs = enriched.get("sections") or []
            if secs:
                effective_prompt += "\nKey sections to include: " + ", ".join(secs)

        emit("Planning the layout and content", "")
        plan_spec = await plan(effective_prompt, page_type_name, style_name,
                               structure_body, style_body)
        plan_spec["page_type"] = page_type_name   # surface the (possibly inferred) type
        # Emit the brief early so the UI can show the interpretation before pages stream.
        brief_spec = _brief_from_plan(plan_spec, enriched)
        emit("__brief__", brief_spec)   # special stage the SSE layer turns into a `brief` event

    brand = (plan_spec.get("brand") or {}).get("name")
    if brand:
        emit("Planned", brand)
    # The plan decides the full file structure — cap at 5 files, entry is index.html.
    pages_spec = (plan_spec.get("pages") or [{"file": "index.html"}])[:5]
    if not any(p.get("file") == "index.html" for p in pages_spec):
        pages_spec[0]["file"] = "index.html"
    plan_spec["pages"] = pages_spec
    # A functional app (needs a JS/data layer) always gets JS, regardless of the toggle.
    needs_app = bool(plan_spec.get("needs_app")) or bool(plan_spec.get("data_model"))
    interactive = interactive or needs_app
    plan_spec["interactive"] = interactive
    page_list = [p.get("file", "index.html") for p in pages_spec]
    build_prompt = prompt if from_brief is not None else effective_prompt

    pages: List[Dict[str, str]] = []
    for i, page in enumerate(pages_spec):
        if len(pages_spec) > 1:
            emit("Building page", f"{i + 1}/{len(pages_spec)}: {page.get('file', 'index.html')}")
        built = await build_page(build_prompt, plan_spec, page, page_type_name,
                                 structure_body, style_name, style_body, page_list,
                                 progress=progress, interactive=interactive, on_token=on_token)
        pages.append(built)

    if images:
        try:
            await _maybe_inject_hero_image(pages, plan_spec)
        except Exception as e:  # noqa: BLE001 — never let the optional image step break a build
            logger.info("hero image injection failed: %s", e)

    result_brief = plan_spec if from_brief is not None else _brief_from_plan(plan_spec, enriched)
    result_brief["interactive"] = interactive   # persist the effective value (apps auto-enable JS)
    return result_brief, pages
