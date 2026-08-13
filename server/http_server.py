"""mikeos-designer-cloud — the AI harness behind designer.osmike.com.

Prompt -> website builder. Runs on 242 in Docker (its own Postgres). The prompt files
under harness/prompts/ are the design brain; this file wires the FastAPI surface around
the pipeline in server/harness.py.

SSE contract (all streaming endpoints): media_type=text/event-stream, each event is a line
`data: {json}\\n\\n` carrying a `type` in {progress,brief,page_start,token,page_done,done,error}.
"""
import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, List, Optional

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server import chrome, db, harness, sites
from server.identity import current_user, current_user_write

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("designer")

# Simple per-user generation rate limit to protect the shared GPU.
_RATE_MAX = int(os.environ.get("DESIGNER_RATE_MAX", "6"))       # N generations
_RATE_WINDOW = int(os.environ.get("DESIGNER_RATE_WINDOW", "600"))  # per this many seconds
_gen_history: Dict[str, Deque[float]] = defaultdict(deque)


def _rate_check(user_id: str) -> None:
    now = time.monotonic()
    dq = _gen_history[user_id]
    while dq and dq[0] < now - _RATE_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_MAX:
        raise HTTPException(status_code=429,
                            detail=f"rate limit: max {_RATE_MAX} generations per "
                                   f"{_RATE_WINDOW}s")
    dq.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    logger.info("designer-cloud up; sites dir=%s public=%s",
                sites.SITES_DIR, sites.PUBLIC_BASE)
    yield
    await db.close_pool()


app = FastAPI(title="mikeos-designer-cloud", lifespan=lifespan)

_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "https://designer.osmike.com").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- request bodies -------------------------------------------------------
class CreateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    page_type: str = "auto"   # "auto" -> the GPU infers the page type from the prompt
    style: str
    interactive: bool = False
    title: Optional[str] = None


class EditBody(BaseModel):
    instruction: str = Field(..., min_length=1)
    file: str = "index.html"
    selector: Optional[str] = None
    outer_html: Optional[str] = None


class PromptBody(BaseModel):
    instruction: str = Field(..., min_length=1)


class BriefBody(BaseModel):
    brief: Dict[str, Any]


class RestyleBody(BaseModel):
    style: str


class RevertBody(BaseModel):
    version_id: int


# ---- serialization --------------------------------------------------------
def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _jsonb(v, default):
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v


def _project_full(row: asyncpg.Record) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "page_type": row["page_type"],
        "style": row["style"],
        "interactive": bool(row["interactive"]) if "interactive" in row else False,
        "brief": _jsonb(row["brief"], {}) if "brief" in row else {},
        "pages": _jsonb(row["pages"], []),
        "url": sites.public_url(row["id"]),
        "thumbnail": row["thumbnail"] if "thumbnail" in row else None,
        "visibility": row["visibility"],
        "published": row["published"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _project_meta(row: asyncpg.Record) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "page_type": row["page_type"],
        "style": row["style"],
        "updated_at": _iso(row["updated_at"]),
        "url": sites.public_url(row["id"]),
        "thumbnail": row["thumbnail"] if "thumbnail" in row else None,
    }


async def _load_owned(site_id: str, user_id: str) -> asyncpg.Record:
    row = await db.pool().fetchrow("SELECT * FROM projects WHERE id = $1", site_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return row


async def _fresh_id() -> str:
    for _ in range(10):
        sid = sites.new_id()
        exists = await db.pool().fetchval("SELECT 1 FROM projects WHERE id = $1", sid)
        if not exists:
            return sid
    raise HTTPException(status_code=500, detail="could not allocate a site id")


async def _snapshot_version(site_id: str, pages: List[Dict[str, str]],
                            brief: Dict[str, Any], note: str) -> None:
    """Store an immutable version of the pages/brief so the project can be reverted."""
    try:
        await db.pool().execute(
            "INSERT INTO project_versions (project_id, pages, brief, note) "
            "VALUES ($1, $2::jsonb, $3::jsonb, $4)",
            site_id, json.dumps(pages), json.dumps(brief or {}), note)
    except Exception as e:  # noqa: BLE001 — versioning is best-effort, never fail the write
        logger.warning("version snapshot failed for %s: %s", site_id, e)


def _fire_thumbnail(site_id: str) -> None:
    """Best-effort, non-blocking: screenshot the live site and store a small data: URI.
    Fires as a detached task so a slow chrome-pool never delays the response."""
    async def _run():
        try:
            data_uri = await chrome.screenshot_data_uri(sites.public_url(site_id))
            if data_uri:
                await db.pool().execute(
                    "UPDATE projects SET thumbnail = $1 WHERE id = $2", data_uri, site_id)
        except Exception as e:  # noqa: BLE001
            logger.info("thumbnail update skipped for %s: %s", site_id, e)
    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


# ---- SSE plumbing ---------------------------------------------------------
def _sse(item: Dict[str, Any]) -> str:
    return f"data: {json.dumps(item)}\n\n"


async def _stream_pipeline(runner) -> StreamingResponse:
    """Wrap an async `runner(emit)` that produces SSE dicts via a queue into a response.

    `runner` is `async def runner(put)` where `put(dict)` enqueues an SSE event; it should
    enqueue a terminal `done`/`error` itself. Exceptions become an `error` event."""
    async def event_gen():
        q: "asyncio.Queue" = asyncio.Queue()

        async def task_body():
            try:
                await runner(q.put_nowait)
            except Exception as e:  # noqa: BLE001
                logger.exception("stream pipeline failed")
                q.put_nowait({"type": "error", "message": str(e)})
            finally:
                q.put_nowait(None)

        task = asyncio.create_task(task_body())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield _sse(item)
        finally:
            await task

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


def _make_callbacks(put):
    """Build (progress, on_token) callbacks that translate harness stages into SSE events.
    A `__brief__` progress stage carries the brief dict -> a `brief` event; a change of the
    active file -> a `page_start`/`page_done` pair around its `token` stream."""
    state = {"file": None}

    def progress(stage: str, detail: Any = ""):
        if stage == "__brief__":
            put({"type": "brief", "brief": detail})
            return
        put({"type": "progress", "stage": stage, "detail": detail
             if isinstance(detail, str) else ""})

    def on_token(file: str, delta: str):
        if state["file"] != file:
            if state["file"] is not None:
                put({"type": "page_done", "file": state["file"]})
            state["file"] = file
            put({"type": "page_start", "file": file})
        put({"type": "token", "file": file, "delta": delta})

    def finish_pages():
        if state["file"] is not None:
            put({"type": "page_done", "file": state["file"]})
            state["file"] = None

    return progress, on_token, finish_pages


# ---- meta / health --------------------------------------------------------
@app.get("/api/health")
async def health():
    database = "unknown"
    try:
        await db.pool().fetchval("SELECT 1")
        database = "ok"
    except Exception as e:
        database = f"error: {e}"
    return {"status": "ok", "database": database}


@app.get("/api/meta")
async def meta():
    return {
        "styles": harness.list_styles(),
        "page_types": harness.list_page_types(),
    }


# ---- create ---------------------------------------------------------------
def _validate_pickers(style: str, page_type: str) -> None:
    try:
        harness.style_directive(style)
        if page_type and page_type.strip().lower() not in ("auto", ""):
            harness.page_structure(page_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown page_type/style: {e}")


async def _insert_project(user_id: str, body: CreateBody, brief: Dict[str, Any],
                          pages: List[Dict[str, str]]) -> asyncpg.Record:
    title = body.title or ((brief.get("brand") or {}).get("name")) or "Untitled"
    site_id = await _fresh_id()
    sites.write_site(site_id, pages)   # write files FIRST so the URL is live immediately
    row = await db.pool().fetchrow(
        "INSERT INTO projects (id, user_id, title, page_type, style, prompt, "
        " prompt_history, pages, brief, interactive, visibility, published) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,'unlisted',true) "
        "RETURNING *",
        site_id, user_id, title, brief.get("page_type") or body.page_type, body.style,
        body.prompt, json.dumps([]), json.dumps(pages), json.dumps(brief),
        bool(brief.get("interactive", body.interactive)))
    if not row:  # never-trust-200: verify the row actually landed
        sites.delete_site(site_id)
        raise HTTPException(status_code=500, detail="insert failed")
    await _snapshot_version(site_id, pages, brief, "created")
    _fire_thumbnail(site_id)
    return row


@app.post("/api/projects")
async def create_project(body: CreateBody, user_id: str = Depends(current_user_write)):
    _rate_check(user_id)
    _validate_pickers(body.style, body.page_type)
    brief, pages = await harness.build_project(
        body.prompt, body.page_type, body.style, interactive=body.interactive,
        images=body.interactive)
    if not pages:
        raise HTTPException(status_code=502, detail="generation produced no pages")
    row = await _insert_project(user_id, body, brief, pages)
    return _project_full(row)


@app.post("/api/projects/stream")
async def create_project_stream(body: CreateBody, user_id: str = Depends(current_user_write)):
    """Streaming create (SSE). Event order: progress -> brief -> progress(Planning) ->
    per page(page_start, token*, page_done) -> progress(Publishing) -> done{project}."""
    _rate_check(user_id)
    _validate_pickers(body.style, body.page_type)

    async def runner(put):
        progress, on_token, finish_pages = _make_callbacks(put)
        brief, pages = await harness.build_project(
            body.prompt, body.page_type, body.style, progress=progress, on_token=on_token,
            interactive=body.interactive, images=body.interactive)
        finish_pages()
        if not pages:
            put({"type": "error", "message": "generation produced no pages"})
            return
        put({"type": "progress", "stage": "Publishing", "detail": ""})
        row = await _insert_project(user_id, body, brief, pages)
        put({"type": "done", "project": _project_full(row)})

    return await _stream_pipeline(runner)


# ---- fast targeted edit (Phase 2) -----------------------------------------
async def _apply_edit_and_save(row: asyncpg.Record, file: str, new_html: str,
                               instruction: str) -> asyncpg.Record:
    site_id = row["id"]
    pages = _jsonb(row["pages"], [])
    replaced = False
    for p in pages:
        if p.get("file") == file:
            p["html"] = new_html
            replaced = True
            break
    if not replaced:
        pages.append({"file": file, "html": new_html})
    sites.write_site(site_id, pages)
    history = _jsonb(row["prompt_history"], [])
    history = history + [{"instruction": instruction, "file": file, "at": int(time.time())}]
    updated = await db.pool().fetchrow(
        "UPDATE projects SET pages = $1::jsonb, prompt_history = $2::jsonb, "
        " updated_at = now() WHERE id = $3 RETURNING *",
        json.dumps(pages), json.dumps(history), site_id)
    if not updated:
        raise HTTPException(status_code=500, detail="update failed")
    await _snapshot_version(site_id, pages, _jsonb(row["brief"], {}), f"edit: {instruction[:80]}")
    _fire_thumbnail(site_id)
    return updated


@app.post("/api/projects/{site_id}/edit")
async def edit_project(site_id: str, body: EditBody,
                       user_id: str = Depends(current_user_write)):
    """FAST targeted edit (SSE) — a SINGLE model call rewriting one page minimally."""
    _rate_check(user_id)
    row = await _load_owned(site_id, user_id)
    file = sites._safe_file(body.file or "index.html")
    pages = _jsonb(row["pages"], [])
    current = next((p.get("html", "") for p in pages if p.get("file") == file), None)
    if current is None:
        current = pages[0].get("html", "") if pages else ""
        file = pages[0].get("file", "index.html") if pages else file
    interactive = bool(row["interactive"])

    async def runner(put):
        put({"type": "progress", "stage": "Understanding your request", "detail": ""})
        _progress, on_token, finish_pages = _make_callbacks(put)
        new_html = await harness.edit_page(
            current, body.instruction, row["style"], row["page_type"] or "",
            interactive=interactive, selector=body.selector, outer_html=body.outer_html,
            file_name=file, on_token=on_token)
        finish_pages()
        put({"type": "progress", "stage": "Publishing", "detail": ""})
        updated = await _apply_edit_and_save(row, file, new_html, body.instruction)
        put({"type": "done", "project": _project_full(updated)})

    return await _stream_pipeline(runner)


@app.post("/api/projects/{site_id}/prompt")
async def refine_project(site_id: str, body: PromptBody,
                         user_id: str = Depends(current_user_write)):
    """Back-compat alias for the fast /edit path (non-SSE, index.html)."""
    _rate_check(user_id)
    row = await _load_owned(site_id, user_id)
    pages = _jsonb(row["pages"], [])
    file = "index.html"
    current = next((p.get("html", "") for p in pages if p.get("file") == file), None)
    if current is None and pages:
        file = pages[0].get("file", "index.html")
        current = pages[0].get("html", "")
    new_html = await harness.edit_page(
        current or "", body.instruction, row["style"], row["page_type"] or "",
        interactive=bool(row["interactive"]), file_name=file)
    updated = await _apply_edit_and_save(row, file, new_html, body.instruction)
    return _project_full(updated)


# ---- editable brief (Phase 4) ---------------------------------------------
@app.get("/api/projects/{site_id}/brief")
async def get_brief(site_id: str, user_id: str = Depends(current_user)):
    row = await _load_owned(site_id, user_id)
    return {"brief": _jsonb(row["brief"], {})}


async def _regenerate(row: asyncpg.Record, brief: Dict[str, Any], style: str,
                      note: str, put) -> None:
    """Shared body for brief-PUT and restyle: regenerate pages from a brief, save, done."""
    site_id = row["id"]
    progress, on_token, finish_pages = _make_callbacks(put)
    new_brief, pages = await harness.build_project(
        row["prompt"] or "", brief.get("page_type") or row["page_type"] or "auto", style,
        progress=progress, on_token=on_token, interactive=bool(row["interactive"]),
        images=bool(row["interactive"]), from_brief=brief)
    finish_pages()
    if not pages:
        put({"type": "error", "message": "generation produced no pages"})
        return
    put({"type": "progress", "stage": "Publishing", "detail": ""})
    sites.write_site(site_id, pages)
    updated = await db.pool().fetchrow(
        "UPDATE projects SET pages = $1::jsonb, brief = $2::jsonb, style = $3, "
        " page_type = $4, updated_at = now() WHERE id = $5 RETURNING *",
        json.dumps(pages), json.dumps(new_brief), style,
        new_brief.get("page_type") or row["page_type"], site_id)
    if not updated:
        put({"type": "error", "message": "update failed"})
        return
    await _snapshot_version(site_id, pages, new_brief, note)
    _fire_thumbnail(site_id)
    put({"type": "done", "project": _project_full(updated)})


@app.put("/api/projects/{site_id}/brief")
async def put_brief(site_id: str, body: BriefBody,
                    user_id: str = Depends(current_user_write)):
    """Regenerate the pages FROM an edited brief (SSE) — content iteration, no re-expansion."""
    _rate_check(user_id)
    row = await _load_owned(site_id, user_id)
    if not body.brief:
        raise HTTPException(status_code=400, detail="empty brief")

    async def runner(put):
        await _regenerate(row, body.brief, row["style"], "brief edit", put)

    return await _stream_pipeline(runner)


@app.post("/api/projects/{site_id}/restyle")
async def restyle_project(site_id: str, body: RestyleBody,
                          user_id: str = Depends(current_user_write)):
    """Regenerate the same brief/content with a NEW style (SSE) — design iteration."""
    _rate_check(user_id)
    _validate_pickers(body.style, "auto")
    row = await _load_owned(site_id, user_id)
    brief = _jsonb(row["brief"], {})
    if not brief:
        raise HTTPException(status_code=400, detail="project has no brief to restyle")

    async def runner(put):
        await _regenerate(row, brief, body.style, f"restyle: {body.style}", put)

    return await _stream_pipeline(runner)


# ---- listing / read -------------------------------------------------------
@app.get("/api/projects")
async def list_projects(user_id: str = Depends(current_user)):
    rows = await db.pool().fetch(
        "SELECT id,title,page_type,style,updated_at,thumbnail FROM projects "
        "WHERE user_id = $1 ORDER BY updated_at DESC", user_id,
    )
    return [_project_meta(r) for r in rows]


@app.get("/api/projects/{site_id}")
async def get_project(site_id: str, user_id: str = Depends(current_user)):
    row = await _load_owned(site_id, user_id)
    return _project_full(row)


@app.get("/api/projects/{site_id}/files")
async def get_files(site_id: str, user_id: str = Depends(current_user)):
    row = await _load_owned(site_id, user_id)
    return {"pages": _jsonb(row["pages"], [])}


# ---- versions (Phase 5) ---------------------------------------------------
@app.get("/api/projects/{site_id}/versions")
async def list_versions(site_id: str, user_id: str = Depends(current_user)):
    await _load_owned(site_id, user_id)
    rows = await db.pool().fetch(
        "SELECT version_id, note, created_at FROM project_versions "
        "WHERE project_id = $1 ORDER BY version_id DESC", site_id)
    return [{"version_id": r["version_id"], "note": r["note"],
             "created_at": _iso(r["created_at"])} for r in rows]


@app.post("/api/projects/{site_id}/revert")
async def revert_project(site_id: str, body: RevertBody,
                         user_id: str = Depends(current_user_write)):
    row = await _load_owned(site_id, user_id)
    snap = await db.pool().fetchrow(
        "SELECT * FROM project_versions WHERE version_id = $1 AND project_id = $2",
        body.version_id, site_id)
    if not snap:
        raise HTTPException(status_code=404, detail="version not found")
    pages = _jsonb(snap["pages"], [])
    brief = _jsonb(snap["brief"], {})
    sites.write_site(site_id, pages)
    updated = await db.pool().fetchrow(
        "UPDATE projects SET pages = $1::jsonb, brief = $2::jsonb, updated_at = now() "
        "WHERE id = $3 RETURNING *", json.dumps(pages), json.dumps(brief), site_id)
    if not updated:
        raise HTTPException(status_code=500, detail="revert failed")
    await _snapshot_version(site_id, pages, brief, f"revert to v{body.version_id}")
    _fire_thumbnail(site_id)
    return _project_full(updated)


# ---- publish / delete -----------------------------------------------------
@app.post("/api/projects/{site_id}/publish")
async def publish_project(site_id: str, user_id: str = Depends(current_user_write)):
    await _load_owned(site_id, user_id)
    updated = await db.pool().fetchrow(
        "UPDATE projects SET published = true, visibility = 'unlisted', "
        " updated_at = now() WHERE id = $1 RETURNING *", site_id,
    )
    return {"url": sites.public_url(site_id), "visibility": updated["visibility"]}


@app.delete("/api/projects/{site_id}")
async def delete_project(site_id: str, user_id: str = Depends(current_user_write)):
    await _load_owned(site_id, user_id)
    await db.pool().execute("DELETE FROM project_versions WHERE project_id = $1", site_id)
    await db.pool().execute("DELETE FROM projects WHERE id = $1", site_id)
    sites.delete_site(site_id)
    return {"deleted": True}
