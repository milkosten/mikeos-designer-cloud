"""mikeos-designer-cloud — the AI harness behind designer.osmike.com.

Prompt -> website builder. Runs on 242 in Docker (its own Postgres). The prompt files
under harness/prompts/ are the design brain; this file wires the FastAPI surface around
the pipeline in server/harness.py.
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

from server import db, harness, sites
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
    title: Optional[str] = None


class PromptBody(BaseModel):
    instruction: str = Field(..., min_length=1)


# ---- serialization --------------------------------------------------------
def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _project_full(row: asyncpg.Record) -> Dict[str, Any]:
    pages = row["pages"]
    if isinstance(pages, str):
        pages = json.loads(pages)
    return {
        "id": row["id"],
        "title": row["title"],
        "page_type": row["page_type"],
        "style": row["style"],
        "pages": pages,
        "visibility": row["visibility"],
        "published": row["published"],
        "url": sites.public_url(row["id"]),
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


# ---- projects -------------------------------------------------------------
@app.post("/api/projects")
async def create_project(body: CreateBody, user_id: str = Depends(current_user_write)):
    _rate_check(user_id)
    # validate the pickers early -> 400 rather than a mid-pipeline failure
    try:
        harness.style_directive(body.style)
        if body.page_type and body.page_type.strip().lower() not in ("auto", ""):
            harness.page_structure(body.page_type)   # validate only an explicit type
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown page_type/style: {e}")

    plan_spec, pages = await harness.build_project(body.prompt, body.page_type, body.style)
    if not pages:
        raise HTTPException(status_code=502, detail="generation produced no pages")

    title = body.title or (plan_spec.get("brand") or {}).get("name") or "Untitled"
    site_id = await _fresh_id()

    # Write files FIRST so the public URL works the instant the row exists.
    sites.write_site(site_id, pages)

    row = await db.pool().fetchrow(
        "INSERT INTO projects (id, user_id, title, page_type, style, prompt, "
        " prompt_history, pages, visibility, published) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,'unlisted',true) RETURNING *",
        site_id, user_id, title, plan_spec.get("page_type") or body.page_type, body.style, body.prompt,
        json.dumps([]), json.dumps(pages),
    )
    if not row:  # never-trust-200: verify the row actually landed
        sites.delete_site(site_id)
        raise HTTPException(status_code=500, detail="insert failed")
    return _project_full(row)


@app.post("/api/projects/stream")
async def create_project_stream(body: CreateBody, user_id: str = Depends(current_user_write)):
    """Same as create_project, but streams stage progress as Server-Sent Events; the final
    `done` event carries the created project. Each line is `data: {json}\\n\\n` with a
    `type` of progress | done | error."""
    _rate_check(user_id)
    try:
        harness.style_directive(body.style)
        if body.page_type and body.page_type.strip().lower() not in ("auto", ""):
            harness.page_structure(body.page_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown page_type/style: {e}")

    async def event_gen():
        q: "asyncio.Queue" = asyncio.Queue()

        def progress(stage: str, detail: str = ""):
            q.put_nowait({"type": "progress", "stage": stage, "detail": detail})

        async def run():
            try:
                plan_spec, pages = await harness.build_project(
                    body.prompt, body.page_type, body.style, progress=progress)
                if not pages:
                    q.put_nowait({"type": "error", "message": "generation produced no pages"})
                    return
                progress("Publishing", "")
                title = body.title or (plan_spec.get("brand") or {}).get("name") or "Untitled"
                site_id = await _fresh_id()
                sites.write_site(site_id, pages)
                row = await db.pool().fetchrow(
                    "INSERT INTO projects (id, user_id, title, page_type, style, prompt, "
                    " prompt_history, pages, visibility, published) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,'unlisted',true) RETURNING *",
                    site_id, user_id, title,
                    plan_spec.get("page_type") or body.page_type, body.style, body.prompt,
                    json.dumps([]), json.dumps(pages))
                if not row:
                    sites.delete_site(site_id)
                    q.put_nowait({"type": "error", "message": "insert failed"})
                    return
                q.put_nowait({"type": "done", "project": _project_full(row)})
            except Exception as e:  # noqa: BLE001
                logger.exception("stream generation failed")
                q.put_nowait({"type": "error", "message": str(e)})
            finally:
                q.put_nowait(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.post("/api/projects/{site_id}/prompt")
async def refine_project(site_id: str, body: PromptBody,
                         user_id: str = Depends(current_user_write)):
    _rate_check(user_id)
    row = await _load_owned(site_id, user_id)
    existing = row["pages"]
    if isinstance(existing, str):
        existing = json.loads(existing)

    plan_spec, pages = await harness.build_project(
        row["prompt"] or "", row["page_type"], row["style"],
        edit_pages=existing, instruction=body.instruction,
    )
    if not pages:
        raise HTTPException(status_code=502, detail="generation produced no pages")

    sites.write_site(site_id, pages)

    history = row["prompt_history"]
    if isinstance(history, str):
        history = json.loads(history)
    history = (history or []) + [{"instruction": body.instruction,
                                  "at": int(time.time())}]

    updated = await db.pool().fetchrow(
        "UPDATE projects SET pages = $1::jsonb, prompt_history = $2::jsonb, "
        " updated_at = now() WHERE id = $3 RETURNING *",
        json.dumps(pages), json.dumps(history), site_id,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="update failed")
    return _project_full(updated)


@app.get("/api/projects")
async def list_projects(user_id: str = Depends(current_user)):
    rows = await db.pool().fetch(
        "SELECT id,title,page_type,style,updated_at FROM projects "
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
    pages = row["pages"]
    if isinstance(pages, str):
        pages = json.loads(pages)
    return {"pages": pages}


@app.post("/api/projects/{site_id}/publish")
async def publish_project(site_id: str, user_id: str = Depends(current_user_write)):
    row = await _load_owned(site_id, user_id)
    updated = await db.pool().fetchrow(
        "UPDATE projects SET published = true, visibility = 'unlisted', "
        " updated_at = now() WHERE id = $1 RETURNING *", site_id,
    )
    return {"url": sites.public_url(site_id), "visibility": updated["visibility"]}


@app.delete("/api/projects/{site_id}")
async def delete_project(site_id: str, user_id: str = Depends(current_user_write)):
    await _load_owned(site_id, user_id)
    await db.pool().execute("DELETE FROM projects WHERE id = $1", site_id)
    sites.delete_site(site_id)
    return {"deleted": True}
