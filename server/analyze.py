"""Hard-coded design linter for generated pages — the closed-loop QA step.

`analyze(html, style_name)` parses the HTML/CSS and returns a list of concrete,
actionable findings (plain-English instructions). The harness feeds these straight
into the GPU repair pass, then re-analyzes — a closed loop that fixes the exact
failures we can detect mechanically (giant meaningless SVGs, a light page for a dark
style, low-contrast text, reused icons, emoji-as-icons, missing focus states).

This catches what a prompt cannot reliably prevent on a small model.
"""
from __future__ import annotations
import re
from typing import List, Dict, Tuple, Optional

# ---- color / contrast helpers (WCAG) --------------------------------------

def _hex_to_rgb(h: str) -> Optional[Tuple[int, int, int]]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _rgb_from_any(v: str) -> Optional[Tuple[int, int, int]]:
    v = v.strip()
    if v.startswith("#"):
        return _hex_to_rgb(v)
    m = re.match(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    named = {"white": (255, 255, 255), "black": (0, 0, 0)}
    return named.get(v.lower())


def _lum(rgb: Tuple[int, int, int]) -> float:
    def f(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    l1, l2 = _lum(c1), _lum(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---- parsing ---------------------------------------------------------------

def _style_css(html: str) -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.I | re.S))


def _root_vars(css: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in re.finditer(r"--([\w-]+)\s*:\s*([^;}]+)[;}]", css):
        out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _resolve(val: str, vars: Dict[str, str], depth: int = 0) -> str:
    """Resolve a var(--x) reference (one level of fallback), else return as-is."""
    if depth > 4 or not val:
        return val
    m = re.match(r"var\(\s*--([\w-]+)\s*(?:,\s*([^)]+))?\)", val.strip())
    if not m:
        return val
    ref = vars.get(m.group(1).strip().lower())
    if ref:
        return _resolve(ref, vars, depth + 1)
    return (m.group(2) or "").strip()


def _applied_bg(css: str, vars: Dict[str, str]) -> Tuple[Optional[Tuple[int, int, int]], bool]:
    """The background actually APPLIED to the page (body/html), and whether one was set.
    A `--bg` token that is never applied to body/html does NOT color the page — it renders
    default white. Returns (rgb_or_None, was_set)."""
    val = None
    for m in re.finditer(r"\b(?:body|html)\s*\{([^}]*)\}", css, re.I | re.S):
        b = re.search(r"background(?:-color)?\s*:\s*([^;]+)", m.group(1), re.I)
        if b:
            val = b.group(1).strip()
    if val is None:
        return None, False
    return _rgb_from_any(_resolve(val, vars)), True


# ---- the checks ------------------------------------------------------------

_DARK_STYLES = ("dark", "terminal", "brutal")  # styles whose ground should be dark-ish (brutalist optional)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF←-⇿✀-➿]"
)


def _strip_media_blocks(html: str, must_contain: List[str]) -> str:
    """Remove @media blocks whose prelude contains all `must_contain` substrings
    (brace-balanced). Used to kill a prefers-color-scheme override that fights the
    chosen style (e.g. a light-mode override on a dark design)."""
    low = html.lower()
    out: List[str] = []
    i = 0
    while True:
        j = low.find("@media", i)
        if j == -1:
            out.append(html[i:]); break
        k = html.find("{", j)
        if k == -1:
            out.append(html[i:]); break
        prelude = low[j:k]
        if all(s in prelude for s in must_contain):
            depth, m = 0, k
            while m < len(html):
                if html[m] == "{":
                    depth += 1
                elif html[m] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                m += 1
            out.append(html[i:j])          # drop the whole @media block
            i = m + 1
        else:
            out.append(html[i:k + 1]); i = k + 1
    return "".join(out)


def _append_to_style(html: str, css_text: str) -> str:
    idx = html.lower().rfind("</style>")
    if idx == -1:
        return html
    return html[:idx] + css_text + html[idx:]


def autofix(html: str, style_name: str = "", page_type: str = "") -> str:
    """Deterministic repairs for failures the weak GPU can't reliably fix in prose.
    Runs before/after the GPU repair loop. Fixes: an unapplied background (the #1 'dark
    style renders white' bug — which also resolves the false contrast failures), missing
    focus styles, and unsized small-viewBox icons that render at the ~300x150 default."""
    # 0) Kill a prefers-color-scheme override that fights the chosen style — the #1 reason a
    #    "dark" design renders white (the model gates dark behind a light-mode override).
    intent_dark = any(k in (style_name or "").lower() for k in ("dark", "terminal"))
    html = _strip_media_blocks(html, ["prefers-color-scheme", "light" if intent_dark else "dark"])

    css = _style_css(html)
    vars = _root_vars(css)

    # 1) Ensure the page actually applies its bg/ink tokens (else it renders default white).
    _bg, bg_set = _applied_bg(css, vars)
    if not bg_set and "bg" in vars:
        ink = "var(--ink)" if "ink" in vars else "inherit"
        html = _append_to_style(html, f"\nbody{{background:var(--bg);color:{ink};}}\n")
        css = _style_css(html)

    # 2) Guarantee visible focus styles.
    if ":focus" not in css:
        accent = "var(--accent)" if "accent" in vars else "#4ea1ff"
        html = _append_to_style(
            html,
            f"\na:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,"
            f"textarea:focus-visible,summary:focus-visible{{outline:2px solid {accent};"
            f"outline-offset:2px;}}\n")

    # 3) Size unsized small-viewBox icons so they don't render at the huge browser default.
    def _fix_svg(m: "re.Match") -> str:
        tag = m.group(0)
        if re.search(r"(?<!-)\b(width|height)\s*=", tag, re.I):  # not stroke-width
            return tag
        vb = re.search(r'viewbox\s*=\s*["\']?\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)', tag, re.I)
        if vb and max(float(vb.group(1)), float(vb.group(2))) <= 64:
            return re.sub(r"^<svg\b", '<svg width="20" height="20"', tag, flags=re.I)
        return tag

    html = re.sub(r"<svg\b[^>]*>", _fix_svg, html, flags=re.I)
    return html


def analyze(html: str, style_name: str = "", page_type: str = "") -> List[str]:
    findings: List[str] = []
    css = _style_css(html)
    vars = _root_vars(css)
    style_l = (style_name or "").lower()
    # a dashboard may keep ONE large SVG: a real chart.
    allowed_large = 1 if "dashboard" in (page_type or "").lower() else 0
    # is there a global CSS rule sizing svgs (e.g. `svg{width:20px}` or `.icon svg{...}`)?
    global_svg_sized = bool(re.search(r"svg[^{}]*\{[^}]*(?:width|height)\s*:", css, re.I))

    def _px(n: float, unit: Optional[str]) -> float:
        return n * (16 if (unit or "px") in ("rem", "em") else 1)

    def _too_big(dim: Optional[str]) -> bool:
        if not dim:
            return False
        if "100%" in dim or "vw" in dim or "vh" in dim:
            return True
        m = re.match(r"([\d.]+)(px|rem|em)?", dim)
        return bool(m) and _px(float(m.group(1)), m.group(2)) > 120

    # 1) SVG sizing — the #1 failure class: oversized/decorative art, OR unsized icons that
    #    render at the browser default (~300x150) and look like giant blobs.
    big_svgs = 0
    unsized = 0
    for tag in re.findall(r"<svg\b[^>]*>", html, re.I):
        w = re.search(r'(?<!-)\bwidth\s*=\s*["\']?([^"\'\s>]+)', tag)   # not stroke-width
        h = re.search(r'(?<!-)\bheight\s*=\s*["\']?([^"\'\s>]+)', tag)
        style = (re.search(r'style\s*=\s*"([^"]*)"', tag) or [None, ""])[1]
        wv, hv = (w.group(1) if w else None), (h.group(1) if h else None)
        big = _too_big(wv) or _too_big(hv)
        if style and re.search(r"(width|height)\s*:\s*(100%|[\d.]+vw|[\d.]+vh)", style):
            big = True
        for mm in re.finditer(r"(?:width|height)\s*:\s*([\d.]+)(px|rem|em)", style or ""):
            if _px(float(mm.group(1)), mm.group(2)) > 120:
                big = True
        # large coordinate space (viewBox) => a hero/chart-sized graphic, not an icon
        vb = re.search(r'viewbox\s*=\s*["\']?\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)', tag, re.I)
        if vb and max(float(vb.group(1)), float(vb.group(2))) > 100:
            big = True
        # is it sized at all? (explicit attr / inline style / class rule / global svg rule)
        class_sized = False
        for cls in re.findall(r'class\s*=\s*["\']([^"\']+)["\']', tag):
            for c in cls.split():
                r = re.search(r"\." + re.escape(c) + r"\s*\{([^}]*)\}", css)
                if r and re.search(r"(width|height|max-width)\s*:", r.group(1)):
                    class_sized = True
        sized = bool(wv or hv) or bool(re.search(r"(?<![-\w])(width|height)\s*:", style or "")) \
            or class_sized or global_svg_sized
        if big:
            big_svgs += 1
        elif not sized:
            unsized += 1
    if big_svgs > allowed_large:
        findings.append(
            f"{big_svgs} oversized/decorative SVG(s): inline SVGs must be SMALL line-icons "
            "(width/height 20-24px). Remove decorative or 'hero-art' SVG entirely — for a product "
            "design use a typographic hero or a framed HTML/CSS UI mock, not an illustration. "
            "(Only a dashboard's single real labelled chart may be large.)")
    if unsized:
        findings.append(
            f"{unsized} SVG(s) have NO explicit size and will render at the ~300x150 browser "
            "default (huge blobs). Give every icon width=\"20\" height=\"20\" (or size svg in CSS).")

    # 2) Reused identical icon paths — each icon must be a distinct shape.
    paths = [p for p in re.findall(r'<path[^>]*\bd\s*=\s*"([^"]+)"', html) if len(p) > 24]
    dupe_paths = {p for p in paths if paths.count(p) > 1}
    if dupe_paths:
        findings.append(
            f"{len(dupe_paths)} SVG path(s) reused for multiple icons: every icon must be a "
            "distinct, recognizable shape relevant to its label (draw different line-icons).")

    # 3) Background actually applied? (a --bg token that is never used = default WHITE page)
    bg, bg_set = _applied_bg(css, vars)
    if not bg_set:
        findings.append(
            "No background is applied to the page: set `body{ background: var(--bg); color: "
            "var(--ink); }` — otherwise the page renders default white regardless of your tokens.")
    applied = bg if bg_set else (255, 255, 255)

    # 4) Dark style but light effective background.
    if applied is not None and any(k in style_l for k in ("dark", "terminal")) and _lum(applied) > 0.30:
        findings.append(
            "This is a DARK/terminal style but the effective page background is LIGHT. Use a "
            "near-black tinted ground (e.g. #0b0e0f) applied to body, with light text and a glowing "
            "accent — the dark ground is the whole point of the style.")

    # 5) Low-contrast text tokens (only meaningful once a real bg is set).
    if bg_set and bg is not None:
        for tok, label in (("muted", "secondary text (--muted)"), ("ink", "body text (--ink)")):
            if tok in vars:
                rgb = _rgb_from_any(_resolve(vars[tok], vars))
                if rgb:
                    ratio = _contrast(rgb, bg)
                    if ratio < 4.5:
                        findings.append(
                            f"{label} = {vars[tok]} has only {ratio:.1f}:1 contrast on the "
                            "background — must be >= 4.5:1. Darken/lighten it (muted should be the "
                            "ink at ~65% strength, NOT a pale tint).")

    # 5) Emoji used as icons/bullets (an AI tell).
    body = re.sub(r"<style.*?</style>", "", html, flags=re.I | re.S)
    if _EMOJI.search(body):
        findings.append(
            "Emoji are used in the content (as icons/bullets): remove them and use small inline "
            "line-SVG icons instead.")

    # 6) No focus styles at all.
    if ":focus" not in css:
        findings.append(
            "No focus styles: add visible :focus-visible outlines on links/buttons/inputs.")

    # 7) A Pricing page must actually show prices.
    if "pricing" in (page_type or "").lower():
        body_txt = re.sub(r"<style.*?</style>", "", html, flags=re.I | re.S)
        if not re.search(r"[$£€]\s?\d|\b\d+\s?(?:/mo|/month|per month|a month)\b", body_txt, re.I):
            findings.append(
                "This is a Pricing page but shows NO prices: every plan tier must have a concrete "
                "price (e.g. $19/mo), the tiers must differ (distinct feature lists), and each tier "
                "needs a call-to-action button.")

    return findings
