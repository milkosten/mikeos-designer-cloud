"""Static-site file management.

Each project is a folder `{SITES_DIR}/<id>/` of HTML files, served publicly by Caddy
at `{PUBLIC_BASE}/<id>/`. The id is a 6-char base62 string (collision-checked vs the DB
by the caller). We write/replace/delete the folder here.
"""
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import List, Dict

SITES_DIR = Path(os.environ.get("SITES_DIR", "/data/sites"))
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://designer.osmike.com").rstrip("/")

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.html$")


def new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(6))


def public_url(site_id: str) -> str:
    return f"{PUBLIC_BASE}/{site_id}/"


def _safe_file(name: str) -> str:
    """Sanitize a page file name to a flat, .html file inside the site dir."""
    name = os.path.basename((name or "").strip()) or "index.html"
    if not name.endswith(".html"):
        name = name + ".html"
    if not _SAFE_FILE_RE.match(name):
        # strip anything unexpected
        stem = re.sub(r"[^A-Za-z0-9._-]", "-", name[:-5]) or "index"
        name = stem + ".html"
    return name


def write_site(site_id: str, pages: List[Dict[str, str]]) -> None:
    """Write (replacing) the site folder with the given pages [{file, html}]."""
    site_dir = SITES_DIR / site_id
    # clear then rewrite so removed pages don't linger
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        fname = _safe_file(page.get("file", "index.html"))
        (site_dir / fname).write_text(page.get("html", ""), encoding="utf-8")


def delete_site(site_id: str) -> None:
    site_dir = SITES_DIR / site_id
    if site_dir.exists():
        shutil.rmtree(site_dir)
