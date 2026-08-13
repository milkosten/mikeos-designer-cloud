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


async def generate_page(user_prompt: str, plan_spec: Dict[str, Any],
                        page: Dict[str, Any], page_type_name: str, structure_body: str,
                        style_name: str, style_body: str,
                        page_list: List[str]) -> str:
    """GENERATE step — one self-contained HTML page via system_generate.md."""
    file_name = page.get("file", "index.html")
    # Inject the plan's per-page content so the page follows the plan, not just the prompt.
    plan_note = json.dumps({"brand": plan_spec.get("brand"),
                            "palette_hint": plan_spec.get("palette_hint"),
                            "type_hint": plan_spec.get("type_hint"),
                            "this_page": page}, ensure_ascii=False)
    filled = (
        _SYSTEM_GENERATE
        .replace("{{PAGE_TYPE}}", page_type_name)
        .replace("{{PAGE_STRUCTURE}}", structure_body)
        .replace("{{STYLE_NAME}}", style_name)
        .replace("{{STYLE_DIRECTIVE}}", style_body)
        .replace("{{FILE_NAME}}", file_name)
        .replace("{{PAGE_LIST}}", ", ".join(page_list) or file_name)
        .replace("{{USER_PROMPT}}",
                 f"{user_prompt}\n\nBUILD PLAN for this page (follow it):\n{plan_note}")
    )
    content = await gpu.chat(
        [{"role": "system", "content": filled},
         {"role": "user", "content":
             f"Produce the complete self-contained HTML document for `{file_name}` now."}],
        temperature=0.8, num_predict=8192,
    )
    return extract_html(content)


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


async def build_page(user_prompt: str, plan_spec: Dict[str, Any], page: Dict[str, Any],
                     page_type_name: str, structure_body: str, style_name: str,
                     style_body: str, page_list: List[str], progress=None) -> Dict[str, str]:
    """Full per-page pipeline: generate -> sanitize -> autofix -> repair? -> re-check."""
    emit = progress or (lambda *a, **k: None)
    file_name = page.get("file", "index.html")
    emit("Designing the page", file_name)
    raw = await generate_page(user_prompt, plan_spec, page, page_type_name,
                              structure_body, style_name, style_body, page_list)
    cleaned, removed = sanitize_html(raw)
    cleaned = autofix(cleaned, style_name, page_type_name)   # deterministic repairs first
    emit("Checking the design", file_name)

    # Closed-loop QA: quality gate + design linter -> targeted GPU repair -> autofix -> re-check.
    MAX_ROUNDS = 2
    for rnd in range(MAX_ROUNDS):
        _ok, issues = _quality_ok(cleaned)
        findings = analyze(cleaned, style_name, page_type_name)
        problems: List[str] = list(issues) + list(findings)
        if has_violation(removed):
            problems.insert(0, "removed disallowed content: " + ", ".join(sorted(set(removed))))
        if not problems:
            break
        reason = "; ".join(problems)
        emit("Polishing the design", file_name)
        logger.info("repair round %d for %s: %s", rnd + 1, page.get("file"), reason)
        try:
            repaired = await repair(cleaned, reason)
        except Exception as e:
            logger.warning("repair failed for %s (keeping current): %s", page.get("file"), e)
            break
        if repaired and "</html>" in repaired.lower():
            cleaned, removed = sanitize_html(repaired)
            cleaned = autofix(cleaned, style_name, page_type_name)
        else:
            break

    return {"file": page.get("file", "index.html"), "html": cleaned}


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


async def build_project(prompt: str, page_type: str, style: str,
                        edit_pages: Optional[List[Dict[str, str]]] = None,
                        instruction: Optional[str] = None,
                        progress=None) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Run the whole pipeline. Returns (plan_spec, pages[{file,html}]).

    When `edit_pages`/`instruction` are given (refine flow), the plan is asked to
    revise the existing pages per the instruction and each page is regenerated.
    `progress(stage, detail)` is called as each stage begins (for live UI updates).
    """
    emit = progress or (lambda *a, **k: None)
    style_name, style_body = style_directive(style)
    # Resolve the page type: infer it from the prompt when the caller didn't pick one.
    if not page_type or page_type.strip().lower() in ("auto", ""):
        emit("Understanding your request", "")
        resolved = await classify_page_type(prompt or instruction or "")
        page_type_name, structure_body = page_structure(resolved)
        emit("Detected page type", page_type_name)
    else:
        page_type_name, structure_body = page_structure(page_type)

    effective_prompt = prompt
    if instruction:
        existing = "\n\n".join(
            f"[current page {p['file']}]\n{p.get('html','')[:4000]}" for p in (edit_pages or [])
        )
        effective_prompt = (
            f"{prompt}\n\nThe user wants to REFINE the existing site with this instruction:\n"
            f"{instruction}\n\nKeep the same subject/brand and page set unless the instruction "
            f"says otherwise. Existing pages (truncated) for reference:\n{existing}"
        )

    emit("Planning the layout and content", "")
    plan_spec = await plan(effective_prompt, page_type_name, style_name,
                           structure_body, style_body)
    plan_spec["page_type"] = page_type_name   # surface the (possibly inferred) type
    brand = (plan_spec.get("brand") or {}).get("name")
    if brand:
        emit("Planned", brand)
    pages_spec = plan_spec.get("pages") or [{"file": "index.html"}]
    page_list = [p.get("file", "index.html") for p in pages_spec]

    pages: List[Dict[str, str]] = []
    for i, page in enumerate(pages_spec):
        if len(pages_spec) > 1:
            emit("Building page", f"{i + 1}/{len(pages_spec)}: {page.get('file', 'index.html')}")
        built = await build_page(effective_prompt, plan_spec, page, page_type_name,
                                 structure_body, style_name, style_body, page_list,
                                 progress=progress)
        pages.append(built)

    return plan_spec, pages
