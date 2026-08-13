"""Programmatic HARD enforcement of the self-contained contract.

Do NOT trust the model — this is the real guardrail (same ethos as "never trust
HTTP 200": verify the artifact). We parse with BeautifulSoup(lxml) and remove or
neutralize anything that would make the page reach off-host or run script:

  - <script> tags (any)
  - inline on*= event handlers
  - javascript: URLs (href/src/etc.)
  - <link rel=stylesheet> and any external <link> (fonts/CSS/preload/etc.)
  - <base> (would rewrite relative URL resolution)
  - external <iframe> / <embed> / <object> (remote data)
  - @import in <style> CSS
  - any remote src/href/poster/srcset (http/https/protocol-relative //)
  - url(...) in inline CSS / style attrs / <style> pointing at a remote/absolute URL

Returns (cleaned_html, removed[]) where removed is a list of short strings naming
what was stripped, so the harness can decide whether a REPAIR pass is warranted.
"""
import re
from typing import List, Tuple

from bs4 import BeautifulSoup, Comment

# A URL that reaches off this host: http(s):// or protocol-relative //host
_REMOTE_URL_RE = re.compile(r"""^\s*(?:https?:)?//""", re.IGNORECASE)
_JS_URL_RE = re.compile(r"""^\s*javascript:""", re.IGNORECASE)
_DATA_URL_RE = re.compile(r"""^\s*data:""", re.IGNORECASE)
# @import ... ; in CSS (with or without url())
_CSS_IMPORT_RE = re.compile(r"""@import\b[^;]*;?""", re.IGNORECASE)
# url( ... ) capture of the inner reference
_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.IGNORECASE)

# Attributes that can carry a URL we must keep local.
_URL_ATTRS = ("href", "src", "poster", "srcset", "data", "ping", "background", "cite", "formaction")


def _is_remote(url: str) -> bool:
    if not url:
        return False
    return bool(_REMOTE_URL_RE.match(url))


def _strip_remote_css_urls(css: str, removed: List[str]) -> str:
    """Remove @import and neutralize remote url(...) inside a CSS string."""
    if _CSS_IMPORT_RE.search(css):
        css = _CSS_IMPORT_RE.sub("", css)
        removed.append("css:@import")

    def _repl(m: "re.Match") -> str:
        ref = m.group(2)
        if _is_remote(ref):
            removed.append("css:url(remote)")
            return "none"  # neutralize (e.g. background-image: none)
        return m.group(0)

    css = _CSS_URL_RE.sub(_repl, css)
    return css


def sanitize_html(html: str, interactive: bool = False) -> Tuple[str, List[str]]:
    """Enforce the self-contained contract. Returns (cleaned_html, removed[]).

    When `interactive` is True, INLINE `<script>` (no `src`) and inline `on*=` handlers
    are ALLOWED (opt-in vanilla JS). External resource loads are still stripped in BOTH
    modes: `<script src>`, external `<link>`, `@import` URLs, remote `src`/`href`/`url()`
    (except `data:`), external `<iframe>`/`<embed>`/`<object>`, and `<base>`."""
    removed: List[str] = []
    soup = BeautifulSoup(html, "lxml")

    # 1. <script> — drop entirely, OR (interactive) keep only inline scripts, dropping any
    #    with an external `src`. `javascript:` in a src is treated as external/blocked too.
    for tag in soup.find_all("script"):
        src = tag.get("src", "")
        if interactive and not src:
            continue  # inline script allowed in interactive mode
        tag.decompose()
        removed.append("script[src]" if src else "script")

    # 2. <base> — would change relative URL resolution.
    for tag in soup.find_all("base"):
        tag.decompose()
        removed.append("base")

    # 3. <link> — drop external stylesheets/fonts/preload/etc. Keep only local
    #    icon/manifest links that are not remote. In practice a self-contained page
    #    needs no <link> at all, so we drop any external one.
    for tag in soup.find_all("link"):
        href = tag.get("href", "")
        rel = " ".join(tag.get("rel", [])) if tag.get("rel") else ""
        if _is_remote(href) or "stylesheet" in rel.lower() or "preload" in rel.lower() \
                or "prefetch" in rel.lower() or "dns-prefetch" in rel.lower() \
                or "preconnect" in rel.lower() or "font" in rel.lower():
            tag.decompose()
            removed.append(f"link[{rel or 'external'}]")

    # 4. external iframe / embed / object — remote content.
    for name in ("iframe", "embed", "object"):
        for tag in soup.find_all(name):
            ref = tag.get("src") or tag.get("data") or ""
            if _is_remote(ref) or not ref:
                tag.decompose()
                removed.append(name)

    # 5. Walk every element: inline handlers, js:/remote URLs, style attrs.
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs)
        for attr, val in attrs.items():
            la = attr.lower()
            # inline event handlers on*= — stripped unless interactive JS is opted in.
            if la.startswith("on"):
                if interactive:
                    continue
                del tag.attrs[attr]
                removed.append(f"handler:{la}")
                continue
            # style="" attribute may carry url()/@import
            if la == "style" and isinstance(val, str):
                new = _strip_remote_css_urls(val, removed)
                tag.attrs[attr] = new
                continue
            # URL-bearing attributes
            if la in _URL_ATTRS and isinstance(val, str):
                if _JS_URL_RE.match(val):
                    del tag.attrs[attr]
                    removed.append(f"javascript-url:{la}")
                    continue
                if la == "srcset":
                    # srcset is a comma list of "url descriptor" — drop remote entries.
                    kept = []
                    for part in val.split(","):
                        u = part.strip().split(" ")[0] if part.strip() else ""
                        if u and _is_remote(u):
                            removed.append("remote:srcset")
                            continue
                        if part.strip():
                            kept.append(part.strip())
                    if kept:
                        tag.attrs[attr] = ", ".join(kept)
                    else:
                        del tag.attrs[attr]
                    continue
                if _is_remote(val):
                    removed.append(f"remote:{la}")
                    if la == "href":
                        # keep the element (e.g. a link) but neutralize the target
                        tag.attrs[attr] = "#"
                    elif tag.name == "img" and la == "src":
                        # blank remote images so nothing loads off-host
                        tag.attrs[attr] = ("data:image/svg+xml,"
                                           "%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E")
                    else:
                        del tag.attrs[attr]

    # 6. <style> blocks — strip @import + remote url().
    for tag in soup.find_all("style"):
        if tag.string is not None or tag.contents:
            css = tag.decode_contents()
            new = _strip_remote_css_urls(css, removed)
            if new != css:
                tag.clear()
                tag.append(new)

    # 7. Drop HTML comments containing conditional/script leftovers (cosmetic).
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if "<script" in str(c).lower():
            c.extract()
            removed.append("comment-script")

    cleaned = str(soup)
    return cleaned, removed


def has_violation(removed: List[str]) -> bool:
    """True if the sanitizer removed something SUBSTANTIVE (worth a repair pass).
    A stray handler or a single link is minor; scripts / remote assets / @import are not."""
    substantive_prefixes = ("script", "remote:", "css:url(remote)", "css:@import",
                             "iframe", "embed", "object", "javascript-url")
    for r in removed:
        if any(r.startswith(p) or r == p for p in substantive_prefixes):
            return True
    return False
