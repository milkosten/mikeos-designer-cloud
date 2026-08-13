#!/usr/bin/env python3
"""Dry-run CLI for the designer harness — runs the REAL pipeline (plan -> generate ->
sanitize -> repair) locally so we can validate output quality and tune the prompts.

    OLLAMA_GPU_URL=ollama://... .venv/bin/python harness_cli.py \
        --style "Editorial" --page-type "Landing" \
        --prompt "a small-batch coffee roaster called North Meridian" --out out/coffee
"""
import asyncio, argparse, pathlib, json, time
from server.harness import build_project

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", required=True)
    ap.add_argument("--page-type", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    t0 = time.time()
    plan_spec, pages = await build_project(a.prompt, a.page_type, a.style)
    outdir = pathlib.Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        (outdir / p["file"]).write_text(p["html"], encoding="utf-8")
        print(f"  wrote {outdir/p['file']}  ({len(p['html'])} bytes)")
    (outdir / "_plan.json").write_text(json.dumps(plan_spec, indent=2), encoding="utf-8")
    print(f"  brand: {plan_spec.get('brand')}")
    print(f"  pages: {[p['file'] for p in pages]}   in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    asyncio.run(main())
