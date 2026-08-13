# Deploying mikeos-designer-cloud on 242

The AI harness behind **designer.osmike.com** (a Replit-style prompt→website builder).
Runs on **242** (`root@91.98.177.242`, key `~/.ssh/mikeos_media`) in Docker, alongside
its own Postgres, on the shared Caddy network `deploy_default`. It generates
self-contained static sites into a host dir that **Caddy serves directly**.

> This is a 242-Docker service (like `mikeos-appstore`), **not** Railway. There is no
> `nixpacks.toml`; deploy with `docker compose`.

## 1. Prereqs on 242
- The external Caddy network already exists: `docker network ls | grep deploy_default`.
- Host dirs for persistent state (both on `/data`, the 117 TB RAID6):
  ```bash
  mkdir -p /data/mikeos-designer/pg          # this service's Postgres data dir
  mkdir -p /data/mikeos-designer/sites       # generated sites (Caddy serves these)
  chmod 755 /data/mikeos-designer/sites
  ```

## 2. Clone + env
```bash
cd /opt   # or wherever mikeos-* compose stacks live on 242
git clone git@github.com:milkosten/mikeos-designer-cloud.git
cd mikeos-designer-cloud
cp .env.example .env      # then edit .env — set a strong DESIGNER_DB_PASSWORD
```

`.env` (compose reads it):
```
DESIGNER_DB_PASSWORD=<pick a strong password>
OLLAMA_GPU_URL=ollama://mikeos:uB49VXwMDy7R2JE0H7mI@81.8.177.182:11443
```
All other env is baked into `docker-compose.yml`:

| Var | Value | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://mikeos:<pw>@mikeos-designer-db:5432/mikeos_designer` | service DB (set from `DESIGNER_DB_PASSWORD`) |
| `OLLAMA_GPU_URL` | `ollama://mikeos:…@81.8.177.182:11443` | the free shared GPU (qwen3:8b) |
| `ACCOUNT_OSMIKE_ISSUER` | `https://account.osmike.com` | JWKS issuer (RS256, verified live) |
| `MIKEOSCOMPUTERS_URL` | `https://account.osmike.com` | legacy `X-API-KEY→user_id` resolver |
| `SITES_DIR` | `/data/sites` (container) ← `/data/mikeos-designer/sites` (host) | generated site folders |
| `PUBLIC_BASE` | `https://designer.osmike.com` | public URL of each site (`/<id>/`) |
| `CORS_ORIGINS` | `https://designer.osmike.com` | allowed browser origin |

No OAuth audience/secret is needed — the JWT is validated by signature + `iss` + `exp`
(JWKS is public; `aud` is not enforced).

## 3. Bring it up
```bash
docker compose up -d --build
docker compose logs -f mikeos-designer      # wait for "designer-cloud up"
```
Migrations self-apply on boot (`migrations/001_init.sql`, tracked in `_migrations`).

## 4. Health check (from inside the network / on 242)
```bash
docker exec mikeos-designer curl -s localhost:8000/api/health   # -> {"status":"ok","database":"ok"}
```

## 5. Caddy vhosts (add to 242's Caddyfile, then `caddy reload` / restart the caddy container)

Two hosts (per the DNS naming rule: bare host = UI, `-api` host = backend):

```caddyfile
# Backend API — the FastAPI harness container
designer-api.osmike.com {
    reverse_proxy mikeos-designer:8000
}

# The SPA + the generated static sites.
# The SPA is served from its build dir; every generated site lives at /<id>/*
# and is served straight off the same host dir the container writes to.
designer.osmike.com {
    encode zstd gzip

    # Serve generated project sites: /<6-char-id>/...  -> /data/mikeos-designer/sites/<id>/...
    # (6-char base62 ids; index.html is the default file per folder)
    handle_path /* {
        # Try a generated site first, else fall back to the SPA (below).
        root * /data/mikeos-designer/sites
        @site path_regexp site ^/[0-9A-Za-z]{6}(/.*)?$
        handle @site {
            file_server {
                index index.html
            }
        }
        # SPA fallback for everything else (app shell / client routing)
        handle {
            root * /srv/designer-web        # <-- put the built SPA here
            try_files {path} /index.html
            file_server
        }
    }
}
```

Notes for the Caddy step (orchestrator handles it):
- The container and Caddy share the SAME host dir (`/data/mikeos-designer/sites` →
  container `/data/sites`), so a site is live the instant a project is created.
- The container is reachable by name `mikeos-designer` because both join
  `deploy_default`. `caddy` must be on that network too (it is — ~60 vhosts run there).
- The SPA build output path (`/srv/designer-web` above) is illustrative — point it at
  wherever the `designer-web` SPA is deployed. If the SPA is served by another
  container, replace the SPA `handle` with `reverse_proxy <spa-container>:<port>` and
  keep the `@site` file_server block for `/<id>/`.

## 6. DNS (already done by the orchestrator)
`designer.osmike.com` and `designer-api.osmike.com` → 242 (Cloudflare, DNS-only /
proxied per house convention). No further DNS action needed.

## 7. Ops
- **Logs:** `docker compose logs -f mikeos-designer`
- **Update:** `git pull && docker compose up -d --build`
- **GPU load:** generation is serialized (one GPU call at a time) with 503 backoff, and
  rate-limited per user (`DESIGNER_RATE_MAX=6` / `DESIGNER_RATE_WINDOW=600s`, override via
  env). A page takes ~60–90s on the shared GPU.
- **Storage:** project rows are tiny; the sites dir grows with generated HTML (each site
  is a handful of small self-contained files). Both live on `/data` (bulk, ~110 TB free).
