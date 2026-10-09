#!/usr/bin/env python3
"""Comfy credit balance, per-run cost lookup, and the cross-project credit log.

Re-executes itself with the comfy-mcp uv tool Python (~/.local/share/uv/tools/comfy-mcp/bin/python) so it can reuse comfy-cli's sign-in.

  comfy_credits.py balance
  comfy_credits.py cost <prompt_id> [<prompt_id> ...]
  comfy_credits.py log --client C --project P --task T [--model M] [--note N]
                       [--output FILE] [--job-folder DIR] <prompt_id> [...]
  comfy_credits.py report [--client C] [--project P] [--since YYYY-MM-DD]

Units (checked 2026-10-02):
- $1 = 211 credits.
- /api/billing/balance on the legacy_stripe rail returns `amount_micros` in
  CENTS, not micro-dollars (the ComfyUI frontend's formatMetronomeCurrency
  divides by 100). `comfy cloud status` divides by 1e6 and under-reports the
  balance by 10,000x on this account; do not use it for the balance.
- /api/billing/events lists one `api_usage_completed` event per partner API
  call, with `params.job_id` = the ComfyUI prompt_id and `params.credits_used`.
  Events can appear a few seconds after the job finishes.
"""
from __future__ import annotations

import os as _os, sys as _sys
try:
    import comfy_cli  # noqa: F401
except ImportError:  # run again with comfy-cli's own Python
    _py = _os.path.expanduser("~/.local/share/uv/tools/comfy-mcp/bin/python")
    if _os.path.isfile(_py) and _os.path.realpath(_py) != _os.path.realpath(_sys.executable):
        _os.execv(_py, [_py, _os.path.abspath(__file__)] + _sys.argv[1:])
    raise SystemExit("comfy_cli is not importable: install comfy-cli in the comfy-mcp uv tool env (INSTALL.md)")

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

CREDITS_PER_USD = 211
LOG_DIR = os.path.expanduser("~/AI_Credit_Log")
LOG_CSV = os.path.join(LOG_DIR, "ai_credit_log.csv")
FIELDS = [
    "date_local", "client", "project", "task", "service", "model", "resolution",
    "api_calls", "credits", "usd", "balance_after_credits", "balance_after_usd",
    "prompt_id", "output", "job_folder", "note",
]


def _get(*parts, query: str = ""):
    from comfy_cli.http import request_json
    from comfy_cli.target import resolve_target

    target = resolve_target(where="cloud")
    url = target.url(*parts) + (("?" + query) if query else "")
    _, body = request_json(url, target, timeout=30, max_bytes=4_000_000)
    return body


def balance() -> dict:
    body = _get("billing", "balance")
    raw = None
    for key in ("effective_balance_micros", "amount_micros", "prepaid_balance_micros"):
        value = body.get(key)
        if value:
            raw = float(value)
            break
    status = _get("billing", "status")
    legacy = status.get("billing_rail") == "legacy_stripe"
    usd = (raw or 0.0) / (100.0 if legacy else 1_000_000.0)
    return {"usd": round(usd, 4), "credits": round(usd * CREDITS_PER_USD, 1),
            "raw": raw, "rail": status.get("billing_rail")}


def events_for(prompt_ids: list[str], pages: int = 6, wait: float = 0.0) -> dict:
    """Return {prompt_id: [event, ...]} from the newest `pages` pages of events."""
    want = set(prompt_ids)
    deadline = time.time() + wait
    while True:
        found: dict[str, list] = {p: [] for p in prompt_ids}
        for page in range(1, pages + 1):
            body = _get("billing", "events", query=f"page={page}&limit=50")
            for event in body.get("events", []):
                job = (event.get("params") or {}).get("job_id")
                if job in want:
                    found[job].append(event)
            if page >= int(body.get("totalPages") or 1):
                break
        if all(found.values()) or time.time() >= deadline:
            return found
        time.sleep(5)


def summarize(events: list) -> dict:
    credits = sum(float((e.get("params") or {}).get("credits_used") or 0) for e in events)
    params = [(e.get("params") or {}) for e in events]
    models = sorted({str(p.get("model")) for p in params if p.get("model")})
    services = sorted({str(p.get("api_name")) for p in params if p.get("api_name")})
    resolutions = sorted({str(p.get("resolution")) for p in params if p.get("resolution")})
    return {"api_calls": len(events), "credits": round(credits, 2),
            "usd": round(credits / CREDITS_PER_USD, 4), "models": models,
            "services": services, "resolutions": resolutions}


def event_local_time(events: list) -> str:
    """Local time of the last billing event, or now when there is none."""
    stamps = sorted(e.get("createdAt", "") for e in events if e.get("createdAt"))
    if not stamps:
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    utc = dt.datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


def append_rows(rows: list[dict]) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def logged_ids() -> set[str]:
    if not os.path.exists(LOG_CSV):
        return set()
    with open(LOG_CSV, newline="") as handle:
        return {r["prompt_id"] for r in csv.DictReader(handle)}


def cmd_log(args) -> int:
    already = logged_ids()
    ids = [p for p in args.prompt_ids if p not in already]
    skipped = [p for p in args.prompt_ids if p in already]
    found = events_for(ids, wait=args.wait) if ids else {}
    bal = balance()
    rows, out = [], []
    for pid in ids:
        s = summarize(found.get(pid, []))
        row = {
            "date_local": event_local_time(found.get(pid, [])),
            "client": args.client, "project": args.project, "task": args.task,
            "service": "comfy-api:" + ",".join(s["services"]) if s["services"] else "comfy-api",
            "model": args.model or ",".join(s["models"]),
            "resolution": ",".join(s["resolutions"]),
            "api_calls": s["api_calls"], "credits": s["credits"], "usd": s["usd"],
            "balance_after_credits": bal["credits"], "balance_after_usd": bal["usd"],
            "prompt_id": pid, "output": args.output or "", "job_folder": args.job_folder or "",
            "note": args.note or ("" if s["api_calls"] else "no billing event found (failed, cached or free run)"),
        }
        rows.append(row)
        out.append({"prompt_id": pid, **s})
    append_rows(rows)
    print(json.dumps({"logged": out, "skipped_already_logged": skipped,
                      "balance": bal, "log": LOG_CSV}, indent=1))
    return 0


def cmd_report(args) -> int:
    if not os.path.exists(LOG_CSV):
        print("no log yet:", LOG_CSV)
        return 0
    totals: dict[tuple, list] = {}
    with open(LOG_CSV, newline="") as handle:
        for r in csv.DictReader(handle):
            if args.client and args.client.lower() not in r["client"].lower():
                continue
            if args.project and args.project.lower() not in r["project"].lower():
                continue
            if args.since and r["date_local"][:10] < args.since:
                continue
            key = (r["client"], r["project"], r["task"])
            t = totals.setdefault(key, [0, 0.0, 0.0])
            t[0] += 1
            t[1] += float(r["credits"] or 0)
            t[2] += float(r["usd"] or 0)
    grand = 0.0
    for (client, project, task), (n, credits, usd) in sorted(totals.items()):
        grand += usd
        print(f"{client} | {project} | {task}: {n} runs, {credits:.1f} credits, ${usd:.2f}")
    print(f"TOTAL ${grand:.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balance")
    c = sub.add_parser("cost")
    c.add_argument("prompt_ids", nargs="+")
    c.add_argument("--wait", type=float, default=30)
    lg = sub.add_parser("log")
    lg.add_argument("prompt_ids", nargs="+")
    lg.add_argument("--client", required=True)
    lg.add_argument("--project", required=True)
    lg.add_argument("--task", required=True)
    lg.add_argument("--model")
    lg.add_argument("--note")
    lg.add_argument("--output")
    lg.add_argument("--job-folder")
    lg.add_argument("--wait", type=float, default=45)
    rp = sub.add_parser("report")
    rp.add_argument("--client")
    rp.add_argument("--project")
    rp.add_argument("--since")
    args = ap.parse_args()
    if args.cmd == "balance":
        print(json.dumps(balance(), indent=1))
        return 0
    if args.cmd == "cost":
        found = events_for(args.prompt_ids, wait=args.wait)
        print(json.dumps({"runs": {p: summarize(e) for p, e in found.items()},
                          "balance": balance()}, indent=1))
        return 0
    if args.cmd == "log":
        return cmd_log(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
