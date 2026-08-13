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
    "Landing": "A marketing landing page: hero, features, how-it-works, FAQ, footer.",
    "Pricing": "A pricing page with 3 plan tiers, a comparison table, and FAQ.",
    "Portfolio": "A personal/studio portfolio: selected work, about, and contact.",
    "Blog post": "A long-form article page with header, body, author card, and related reading.",
    "Coming-soon": "A single-screen teaser with an email-capture mockup and atmosphere.",
    "Dashboard mockup": "A product UI mockup: sidebar, KPI stat cards, an inline-SVG chart, and panels.",
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
                     style_body: str, page_list: List[str]) -> Dict[str, str]:
    """Full per-page pipeline: generate -> sanitize -> repair? -> re-sanitize."""
    raw = await generate_page(user_prompt, plan_spec, page, page_type_name,
                              structure_body, style_name, style_body, page_list)
    cleaned, removed = sanitize_html(raw)
    ok, issues = _quality_ok(cleaned)

    if has_violation(removed) or not ok:
        reason_parts = []
        if has_violation(removed):
            reason_parts.append("sanitizer removed: " + ", ".join(sorted(set(removed))))
        if not ok:
            reason_parts.append("quality: " + ", ".join(issues))
        reason = "; ".join(reason_parts)
        logger.info("repairing %s: %s", page.get("file"), reason)
        try:
            repaired = await repair(cleaned, reason)
            if repaired and "</html>" in repaired.lower():
                cleaned, _removed2 = sanitize_html(repaired)
        except Exception as e:
            logger.warning("repair failed for %s (keeping sanitized original): %s",
                           page.get("file"), e)

    return {"file": page.get("file", "index.html"), "html": cleaned}


async def build_project(prompt: str, page_type: str, style: str,
                        edit_pages: Optional[List[Dict[str, str]]] = None,
                        instruction: Optional[str] = None) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Run the whole pipeline. Returns (plan_spec, pages[{file,html}]).

    When `edit_pages`/`instruction` are given (refine flow), the plan is asked to
    revise the existing pages per the instruction and each page is regenerated.
    """
    page_type_name, structure_body = page_structure(page_type)
    style_name, style_body = style_directive(style)

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

    plan_spec = await plan(effective_prompt, page_type_name, style_name,
                           structure_body, style_body)
    pages_spec = plan_spec.get("pages") or [{"file": "index.html"}]
    page_list = [p.get("file", "index.html") for p in pages_spec]

    pages: List[Dict[str, str]] = []
    for page in pages_spec:
        built = await build_page(effective_prompt, plan_spec, page, page_type_name,
                                 structure_body, style_name, style_body, page_list)
        pages.append(built)

    return plan_spec, pages
