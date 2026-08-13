"""Canonical design tokens per style.

The #1 source of instability on a small model is letting it *invent* the palette and
type for each page — so "Minimal" drifts into a loud blue Bootstrap block. We fix that
by (a) injecting an exact `:root` token block into the generation prompt and (b)
enforcing it deterministically in `autofix` (a later `:root`/`body` rule wins), so a
style's visual identity — background, surfaces, ink, accent, font, radius — is constant
regardless of model drift. The style directive still governs *layout* and *signature*;
the model fills content. Colors/type it does not get to reinvent.
"""
from __future__ import annotations
from typing import Dict

SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
SERIF = 'Georgia, "Times New Roman", ui-serif, serif'
MONO = 'ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace'

# Shared type scale (>=1.05rem body, strong jumps) + spacing scale.
_SCALE = {
    "step-0": "0.85rem", "step-1": "1.05rem", "step-2": "1.35rem",
    "step-3": "1.9rem", "step-4": "2.8rem", "step-5": "4rem",
    "s1": "4px", "s2": "8px", "s3": "16px", "s4": "32px", "s5": "64px", "s6": "96px",
}

# Per-style palette + font + radius. Muted values are chosen to keep >=4.5:1 on the bg.
STYLE_TOKENS: Dict[str, Dict[str, str]] = {
    "Modern": dict(bg="#f7f8fa", surface="#ffffff", ink="#0f1729", muted="#51607d",
                   line="#e6eaf1", accent="#4f46e5", accent2="#0ea5e9", font=SANS, radius="14px"),
    "Minimal": dict(bg="#fafaf9", surface="#ffffff", ink="#1a1a19", muted="#57574f",
                    line="#e7e7e3", accent="#3a5a40", accent2="#33507a", font=SANS, radius="6px"),
    "Brutalist": dict(bg="#ffffff", surface="#ffffff", ink="#0a0a0a", muted="#1f1f1f",
                      line="#000000", accent="#ff3b00", accent2="#0a00ff", font=MONO, radius="0px"),
    "Glassmorphism": dict(bg="#0f1b3d", surface="rgba(255,255,255,0.08)", ink="#f3f6ff",
                          muted="#c3ccdf", line="rgba(255,255,255,0.20)", accent="#5eead4",
                          accent2="#a78bfa", font=SANS, radius="20px"),
    "Editorial": dict(bg="#fbfaf7", surface="#ffffff", ink="#20201d", muted="#54514a",
                      line="#dcd8cf", accent="#7a1f1f", accent2="#1f3a5f", font=SERIF, radius="2px"),
    "Corporate": dict(bg="#f6f8fb", surface="#ffffff", ink="#0f172a", muted="#4c5a72",
                      line="#e6ebf2", accent="#2563eb", accent2="#0d9488", font=SANS, radius="10px"),
    "Playful": dict(bg="#fffaf5", surface="#ffffff", ink="#2a1a3e", muted="#665473",
                    line="#f0e6dc", accent="#ff5c8a", accent2="#f59e0b", font=SANS, radius="22px"),
    "Dark / Terminal": dict(bg="#0b0e0f", surface="#14181a", ink="#e6edf0", muted="#9aa7b0",
                            line="#232a2d", accent="#00e08a", accent2="#4ea1ff", font=MONO, radius="6px"),
    "Retro": dict(bg="#f4ece0", surface="#fbf6ee", ink="#2e2417", muted="#6a5c46",
                  line="#d8c9b0", accent="#c8551b", accent2="#3f7d6e", font=SANS, radius="4px"),
}

# Common style-name aliases -> canonical key (the picker/plan may send a slug or synonym).
_ALIASES = {
    "modern": "Modern", "minimal": "Minimal", "minimalist": "Minimal", "brutalist": "Brutalist",
    "brutal": "Brutalist", "glass": "Glassmorphism", "glassmorphism": "Glassmorphism",
    "editorial": "Editorial", "magazine": "Editorial", "corporate": "Corporate", "saas": "Corporate",
    "playful": "Playful", "fun": "Playful", "dark": "Dark / Terminal", "terminal": "Dark / Terminal",
    "dark / terminal": "Dark / Terminal", "dark/terminal": "Dark / Terminal", "retro": "Retro",
    "vintage": "Retro",
}


def resolve(style_name: str) -> str:
    if not style_name:
        return "Modern"
    s = style_name.strip()
    if s in STYLE_TOKENS:
        return s
    return _ALIASES.get(s.lower(), "Modern")


def tokens_for(style_name: str) -> Dict[str, str]:
    return STYLE_TOKENS[resolve(style_name)]


def root_css(style_name: str) -> str:
    """The canonical `:root {…}` block for a style (for the generation prompt)."""
    t = tokens_for(style_name)
    lines = [
        f"  --bg: {t['bg']};", f"  --surface: {t['surface']};", f"  --ink: {t['ink']};",
        f"  --muted: {t['muted']};", f"  --line: {t['line']};",
        f"  --accent: {t['accent']};", f"  --accent-2: {t['accent2']};",
        f"  --font: {t['font']};", f"  --radius: {t['radius']};",
    ]
    for k, v in _SCALE.items():
        lines.append(f"  --{k}: {v};")
    return ":root {\n" + "\n".join(lines) + "\n}"


def enforce(html: str, style_name: str) -> str:
    """Deterministic backstop: append the canonical :root + a body rule at the end of the
    first <style> so the style's palette/type/radius win over anything the model invented
    (later same-specificity declarations override earlier ones)."""
    if "<style" not in html.lower():
        return html
    t = tokens_for(style_name)
    block = ("\n/* enforced style tokens */\n" + root_css(style_name) +
             "\nbody{background:var(--bg);color:var(--ink);"
             "font-family:var(--font);}\n")
    idx = html.lower().rfind("</style>")
    if idx == -1:
        return html
    return html[:idx] + block + html[idx:]
