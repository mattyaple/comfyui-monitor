#!/usr/bin/env python3
"""Comfy credit balance, per-run billed cost, and the cross-project credit log.

Bundled with the monitor so the Costs tab and run.py do not depend on anything outside the project. It needs comfy-cli's
sign-in, so it re-executes itself with the Python of the comfy-cli install named in project.json (paths.comfy_bin) when it
is not already running there.

  credits.py balance
  credits.py cost <prompt_id> [...]                   billed credits / usd per run, plus the balance (no logging)
  credits.py log --client C --project P --task T [--model M] [--note N] [--output FILE] [--job-folder DIR] <prompt_id> [...]
  credits.py report [--client C] [--project P] [--since YYYY-MM-DD]

Units (checked 2026-10-02 on a legacy_stripe account):
- $1 = 211 credits (costs.credits_per_usd in project.json).
- /api/billing/balance now returns explicit `*_cents` fields (used first). Older responses only had `*_micros`, which on the
  legacy_stripe and stripe rails hold CENTS (the frontend divides by 100) and only on the comfy rail micro-dollars.
  `comfy cloud status` gets the cents case wrong by 10,000x; do not use it for the balance.
- /api/billing/events lists one `api_usage_completed` event per partner API call with `params.job_id` = the ComfyUI
  prompt_id and `params.credits_used`. Events can appear a few seconds after the job finishes, so `cost` and `log` wait.

The log is a shared, append-only CSV (costs.credit_log, default ~/AI_Credit_Log/ai_credit_log.csv): one row per paid
run, so AI costs can be billed back per client and project across every project on this machine.
"""
from __future__ import annotations

import argparse, csv, datetime as dt, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def _cfg():
    sys.path.insert(0, HERE)
    import config
    return config.load()


def _reexec_if_needed():
    """comfy_cli must be importable; otherwise run again with the Python next to the configured comfy binary."""
    try:
        import comfy_cli  # noqa: F401
        return
    except ImportError:
        pass
    cfg = _cfg()
    py = os.path.join(os.path.dirname(cfg["_paths"]["comfy_bin"]), "python")
    if os.path.isfile(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
        os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit(f"comfy_cli is not importable and no Python was found next to {cfg['_paths']['comfy_bin']}; "
             f"install comfy-cli (see INSTALL.md) or fix paths.comfy_bin in project.json")


def _get(*parts, query: str = ""):
    from comfy_cli.http import request_json
    from comfy_cli.target import resolve_target
    target = resolve_target(where="cloud")
    url = target.url(*parts) + (("?" + query) if query else "")
    _, body = request_json(url, target, timeout=30, max_bytes=4_000_000)
    return body


def balance(credits_per_usd=211) -> dict:
    body = _get("billing", "balance")
    status = _get("billing", "status")
    raw, unit = None, None
    for key in ("effective_balance_cents", "amount_cents", "prepaid_balance_cents"):   # explicit cents (2026-10 API): authoritative
        if body.get(key) is not None:
            raw, unit = float(body[key]), "cents"; break
    if raw is None:
        for key in ("effective_balance_micros", "amount_micros", "prepaid_balance_micros"):
            if body.get(key):
                raw = float(body[key]); break
        # older responses: the legacy_stripe rail (and "stripe") report cents under the _micros keys; only "comfy" is micro-dollars
        unit = "micros" if status.get("billing_rail") not in ("legacy_stripe", "stripe") else "cents"
    usd = (raw or 0.0) / (100.0 if unit == "cents" else 1_000_000.0)
    return {"usd": round(usd, 4), "credits": round(usd * credits_per_usd, 1), "raw": raw, "unit": unit, "rail": status.get("billing_rail"),
            "checked": dt.datetime.now().isoformat(timespec="seconds")}


def events_for(prompt_ids: list[str], pages: int = 6, wait: float = 0.0) -> dict:
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


def summarize(events: list, credits_per_usd=211) -> dict:
    credits = sum(float((e.get("params") or {}).get("credits_used") or 0) for e in events)
    params = [(e.get("params") or {}) for e in events]
    models = sorted({str(p.get("model")) for p in params if p.get("model")})
    services = sorted({str(p.get("api_name")) for p in params if p.get("api_name")})
    resolutions = sorted({str(p.get("resolution")) for p in params if p.get("resolution")})
    return {"api_calls": len(events), "credits": round(credits, 2), "usd": round(credits / credits_per_usd, 4),
            "models": models, "services": services, "resolutions": resolutions}


def event_local_time(events: list) -> str:
    stamps = sorted(e.get("createdAt", "") for e in events if e.get("createdAt"))
    if not stamps:
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    utc = dt.datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


FIELDS = ["date_local", "client", "project", "task", "service", "model", "resolution", "api_calls", "credits", "usd",
          "balance_after_credits", "balance_after_usd", "prompt_id", "output", "job_folder", "note"]


def read_log(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def append_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FIELDS})


def cmd_log(args, cfg) -> int:
    path = cfg["_paths"]["credit_log"]; cpu = cfg["costs"]["credits_per_usd"]
    already = {r["prompt_id"] for r in read_log(path)}
    ids = [p for p in args.prompt_ids if p not in already]
    skipped = [p for p in args.prompt_ids if p in already]
    found = events_for(ids, wait=args.wait) if ids else {}
    bal = balance(cpu)
    rows, out = [], []
    for pid in ids:
        s = summarize(found.get(pid, []), cpu)
        rows.append({"date_local": event_local_time(found.get(pid, [])), "client": args.client, "project": args.project, "task": args.task,
                     "service": "comfy-api:" + ",".join(s["services"]) if s["services"] else "comfy-api",
                     "model": args.model or ",".join(s["models"]), "resolution": ",".join(s["resolutions"]),
                     "api_calls": s["api_calls"], "credits": s["credits"], "usd": s["usd"],
                     "balance_after_credits": bal["credits"], "balance_after_usd": bal["usd"], "prompt_id": pid,
                     "output": args.output or "", "job_folder": args.job_folder or "",
                     "note": args.note or ("" if s["api_calls"] else "no billing event found (failed, cached or free run)")})
        out.append({"prompt_id": pid, **s})
    append_rows(path, rows)
    print(json.dumps({"logged": out, "skipped_already_logged": skipped, "balance": bal, "log": path}, indent=1))
    return 0


def cmd_report(args, cfg) -> int:
    rows = read_log(cfg["_paths"]["credit_log"])
    if not rows:
        print("no log yet:", cfg["_paths"]["credit_log"]); return 0
    totals = {}
    for r in rows:
        if args.client and args.client.lower() not in r["client"].lower(): continue
        if args.project and args.project.lower() not in r["project"].lower(): continue
        if args.since and r["date_local"][:10] < args.since: continue
        t = totals.setdefault((r["client"], r["project"], r["task"]), [0, 0.0, 0.0])
        t[0] += 1; t[1] += float(r["credits"] or 0); t[2] += float(r["usd"] or 0)
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
    c = sub.add_parser("cost"); c.add_argument("prompt_ids", nargs="+"); c.add_argument("--wait", type=float, default=30)
    lg = sub.add_parser("log"); lg.add_argument("prompt_ids", nargs="+")
    for k in ("--client", "--project", "--task"):
        lg.add_argument(k, required=True)
    for k in ("--model", "--note", "--output", "--job-folder"):
        lg.add_argument(k)
    lg.add_argument("--wait", type=float, default=45)
    rp = sub.add_parser("report"); rp.add_argument("--client"); rp.add_argument("--project"); rp.add_argument("--since")
    args = ap.parse_args()
    _reexec_if_needed()
    cfg = _cfg(); cpu = cfg["costs"]["credits_per_usd"]
    if args.cmd == "balance":
        print(json.dumps(balance(cpu), indent=1)); return 0
    if args.cmd == "cost":
        found = events_for(args.prompt_ids, wait=args.wait)
        print(json.dumps({"runs": {p: summarize(e, cpu) for p, e in found.items()}, "balance": balance(cpu)}, indent=1)); return 0
    if args.cmd == "log":
        return cmd_log(args, cfg)
    return cmd_report(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
