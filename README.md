# mikeos-designer-cloud

The AI harness behind **designer.osmike.com** — a Replit-style **prompt → website**
builder for MikeOS. A user describes a site, picks a **page type** and a **visual
style**, and the service generates a self-contained static website (inline CSS, inline
SVG, **no JS, no external assets**), writes it to disk, and serves it at
`designer.osmike.com/<id>/`.

Runs on **242** in Docker (its own Postgres), on the shared Caddy network. **Not
Railway.** Deploy: see [`DEPLOY.md`](DEPLOY.md).

## The design brain
The `harness/prompts/*.md` files ARE the design brain — a plan/generate/repair prompt
set that gives the local GPU (`qwen3:8b`) concrete modern-UI judgement. This service
loads and uses them; it does not embed its own design rules.

## Pipeline (`server/harness.py`)
```
plan (plan_system.md, format=JSON)         → a build spec (brand, palette, page set, copy)
  → generate_page (system_generate.md)     → one self-contained HTML doc per page
    → sanitize (server/sanitize.py)        → HARD-strip scripts/remote assets/@import/handlers
      → repair (repair_system.md) IF the sanitizer stripped something substantive
         or a quality gate fails (no :root, placeholder text, …) → re-sanitize
        → write to {SITES_DIR}/<id>/*.html  (live immediately)
```
The **sanitizer is the real guardrail** (never trust the model): BeautifulSoup+lxml
removes `<script>`, `on*=` handlers, `javascript:` URLs, external `<link>`/`<base>`,
`@import`, remote `src`/`href`/`url()`, and external `<iframe>`/`embed`/`object`.

## Modules
| File | Role |
|---|---|
| `server/gpu.py` | Ollama client (parses `OLLAMA_GPU_URL`, `think:false`, `format` for JSON, 503 backoff, serialized) |
| `server/harness.py` | the plan→generate→sanitize→repair pipeline; slices style/page-type sections from the prompt md |
| `server/sanitize.py` | programmatic self-contained enforcement (the guardrail) |
| `server/identity.py` | dual-auth: RS256 JWT via account.osmike.com JWKS + legacy `X-API-KEY` |
| `server/db.py` | asyncpg pool + idempotent migration runner |
| `server/sites.py` | 6-char base62 ids; write/delete site folders under `SITES_DIR` |
| `server/http_server.py` | the FastAPI app + routes |

## API (`https://designer-api.osmike.com`)
All `/api/*` except `health`/`meta` require auth (`current_user` → `user_id`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{"status":"ok","database":"ok"}` (no auth) |
| GET | `/api/meta` | `{styles:[…8], page_types:[…6]}` (no auth) |
| POST | `/api/projects` | `{prompt,page_type,style,title?}` → run pipeline, write folder, insert → full project |
| POST | `/api/projects/{id}/prompt` | `{instruction}` → refine (regenerate, append prompt_history) → full project |
| GET | `/api/projects` | current user's projects (metadata, no html) |
| GET | `/api/projects/{id}` | full project incl. page html (ownership-checked) |
| GET | `/api/projects/{id}/files` | `{pages:[{file,html}]}` |
| POST | `/api/projects/{id}/publish` | flip published/unlisted → `{url,visibility}` |
| DELETE | `/api/projects/{id}` | delete row + remove folder → `{deleted:true}` |

Generation is **rate-limited per user** to protect the shared GPU.

## Local dev
```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://…            # a local/throwaway Postgres
export SITES_DIR=./sites PUBLIC_BASE=http://localhost:8000
uvicorn server.http_server:app --reload
```
`GET /api/health` → ok. Protected routes need a real account.osmike.com Bearer or a
legacy `X-API-KEY`.
