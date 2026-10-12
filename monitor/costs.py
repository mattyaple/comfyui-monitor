#!/usr/bin/env python3
"""Cost tracking: this machine's billed runs, manual expenses, and the team's ledgers merged into one project total.

Where the numbers come from
- run records with a "cost" (run.py bills every partner-API run through credits.py: the credits Comfy actually charged)
- the shared credit log CSV (costs.credit_log; rows of this project made outside the monitor, e.g. through the comfyui skill)
- monitor/expenses.json: manual entries (other services, licenses) added in the Costs tab

Teams on one Comfy account
Billing events are account-wide but carry only a prompt_id, so a machine can only attribute its own runs. Each machine
therefore keeps its own ledger (person = costs.person, default the macOS account's full name) and shares it:
- automatically, when costs.shared_dir names a folder every machine can reach (Dropbox, Drive): run.py and the Costs
  tab write <shared_dir>/<code>_costs_<person>.json after every billed run / export, and every monitor reads the others'
- manually: "Export my usage" in the Costs tab makes the same file in exports/, a teammate imports it (stored in
  monitor/costs_imports/). The newest export per person wins, and prompt ids are never counted twice.
The Costs tab then shows per-person usage and the combined project total for the client invoice.

  python3 monitor/costs.py summary [--period 30]      python3 monitor/costs.py export [--csv]
"""
import csv, getpass, hashlib, json, os, pwd, re, socket, subprocess, sys, time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config, tags  # noqa: E402

EXPENSES = os.path.join(HERE, "expenses.json")
IMPORTS = os.path.join(HERE, "costs_imports")
_balance = {"t": 0, "v": None, "busy": False}


def person():
    c = config.load()["costs"]
    if c.get("person"):
        return c["person"]
    try:
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except Exception:
        full = ""
    return full or getpass.getuser()


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_").lower() or "unknown"


def read_runs():
    d = config.load()["_paths"]["runs"]; out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith(".json"):
            try:
                with open(os.path.join(d, f)) as fh:
                    r = json.load(fh); r.setdefault("id", f[:-5]); out.append(r)
            except Exception:
                continue
    return out


def read_expenses():
    try:
        return json.load(open(EXPENSES))
    except Exception:
        return []


def write_expenses(rows):
    tmp = EXPENSES + ".tmp"; json.dump(rows, open(tmp, "w"), indent=1); os.replace(tmp, EXPENSES)


def add_expense(b):
    rows = read_expenses()
    e = {"id": hashlib.md5(f"{time.time()}{b}".encode()).hexdigest()[:8], "date": (b.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10],
         "service": (b.get("service") or "other")[:60], "note": (b.get("note") or "")[:200], "usd": float(b.get("usd") or 0), "person": person()}
    rows.append(e); write_expenses(rows)
    return e


def credit_log_rows():
    c = config.load(); p = c["_paths"]["credit_log"]
    if not p or not os.path.isfile(p):
        return []
    want = (c["costs"].get("project") or "").strip().lower()
    with open(p, newline="") as fh:
        return [r for r in csv.DictReader(fh) if want and (r.get("project") or "").strip().lower() == want]


def _out_root(server):
    try:
        s = config.servers(); return s.get(server or "local", s.get("local", {})).get("output_root") or config.load()["_paths"]["output_root"]
    except Exception:
        return config.load()["_paths"]["output_root"]


VID_EXT = (".mp4", ".mov", ".webm", ".mkv")


def my_rows():
    """This machine's billed runs for the project, one row each, plus credit-log rows not covered by a run record."""
    c = config.load(); lr = c["_paths"]["output_root"]; me = person(); rows = []; seen = set()
    for r in read_runs():
        cost = r.get("cost") or {}
        if cost.get("usd") is None:
            continue
        seen.add(r.get("prompt_id"))
        coll = r.get("collected") or {}
        locs = [os.path.join(lr, coll[o]) for o in r.get("outputs") or [] if o in coll] or \
               [os.path.join(_out_root(r.get("server")), o) for o in r.get("outputs") or [] if _out_root(r.get("server")) == lr]
        nsel = sum(1 for p in locs if os.path.isfile(p) and tags.marks(p).get("tagged"))
        rows.append({"id": r["id"], "prompt_id": r.get("prompt_id"), "day": (r.get("started") or "")[:10], "usd": float(cost.get("usd") or 0),
                     "credits": float(cost.get("credits") or 0), "model": r.get("model") or "—", "group": r.get("group") or r.get("page") or "—",
                     "images": sum(1 for o in r.get("outputs") or [] if not str(o).lower().endswith(VID_EXT)),
                     "videos": sum(1 for o in r.get("outputs") or [] if str(o).lower().endswith(VID_EXT)),
                     "selects": nsel, "gen_seconds": r.get("gen_seconds"), "server": r.get("server") or "local",
                     "title": r.get("title"), "api_calls": cost.get("api_calls"), "source": "run", "person": me})
    for lg in credit_log_rows():
        if lg.get("prompt_id") in seen:
            continue
        try:
            usd, credits = float(lg.get("usd") or 0), float(lg.get("credits") or 0)
        except ValueError:
            continue
        rows.append({"id": (lg.get("prompt_id") or "")[:8], "prompt_id": lg.get("prompt_id"), "day": (lg.get("date_local") or "")[:10], "usd": usd,
                     "credits": credits, "model": lg.get("model") or lg.get("service") or "—", "group": (lg.get("task") or "").split(":")[0] or "—",
                     "images": 0, "selects": 0, "gen_seconds": None, "server": "log", "title": lg.get("task"), "api_calls": lg.get("api_calls"),
                     "source": "credit log", "person": me})
    return rows


def ledger():
    c = config.load(); pr = c["project"]
    return {"format": "comfyui-monitor-costs/1", "person": person(), "machine": socket.gethostname(), "project": c["costs"].get("project"),
            "code": pr.get("code"), "client": c["costs"].get("client"), "exported": datetime.now().isoformat(timespec="seconds"),
            "rows": my_rows(), "expenses": read_expenses()}


def shared_dir():
    d = config.load()["costs"].get("shared_dir") or ""
    return config.expand(d, config.load()["_vars"]) if d else ""


def shared_name(code, who):
    return f"{code}_costs_{slug(who)}.json"


def write_shared():
    """Write this machine's ledger into the shared folder (no-op without one, or when the folder is not reachable)."""
    d = shared_dir()
    if not d or not os.path.isdir(d):
        return None
    led = ledger(); p = os.path.join(d, shared_name(led["code"], led["person"]))
    tmp = p + ".tmp"; json.dump(led, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return p


def _read_ledger(p):
    try:
        j = json.load(open(p))
        if isinstance(j, dict) and j.get("format", "").startswith("comfyui-monitor-costs") and j.get("person"):
            j["_file"] = p; return j
    except Exception:
        pass
    return None


def team_ledgers():
    """{person: ledger} from the shared folder and the imports folder, newest export per person; never this machine's own."""
    me = person(); code = config.load()["project"].get("code"); out = {}
    cands = []
    for d in (shared_dir(), IMPORTS):
        if d and os.path.isdir(d):
            cands += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json") and not f.startswith(".")]
    for p in cands:
        led = _read_ledger(p)
        if not led or led["person"] == me or (code and led.get("code") and led["code"] != code):
            continue
        cur = out.get(led["person"])
        if not cur or (led.get("exported") or "") > (cur.get("exported") or ""):
            out[led["person"]] = led
    return out


def import_ledger(data):
    """Store a teammate's exported ledger under monitor/costs_imports/ (replacing an older one of the same person)."""
    if not isinstance(data, dict) or not str(data.get("format", "")).startswith("comfyui-monitor-costs") or not data.get("person"):
        return {"ok": False, "error": "not a cost export of this monitor (expected format comfyui-monitor-costs/1)"}
    code = config.load()["project"].get("code")
    if code and data.get("code") and data["code"] != code:
        return {"ok": False, "error": f"this export belongs to project {data['code']}, not {code}"}
    if data["person"] == person():
        return {"ok": False, "error": "that is your own export; it is already counted"}
    os.makedirs(IMPORTS, exist_ok=True)
    p = os.path.join(IMPORTS, shared_name(code or "project", data["person"]))
    tmp = p + ".tmp"; json.dump(data, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return {"ok": True, "person": data["person"], "rows": len(data.get("rows") or []), "exported": data.get("exported")}


def forget_person(who):
    p = os.path.join(IMPORTS, shared_name(config.load()["project"].get("code") or "project", who))
    if os.path.isfile(p):
        os.remove(p); return {"ok": True}
    return {"ok": False, "error": "no imported ledger for that person (a shared-folder ledger is removed by its owner)"}


def balance(refresh=False):
    c = config.load()
    if not refresh and _balance["v"] and time.time() - _balance["t"] < c["costs"].get("balance_cache_s", 120):
        return _balance["v"]
    if _balance["busy"]:
        return _balance["v"] or {"pending": True}
    _balance["busy"] = True
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "credits.py"), "balance"], capture_output=True, text=True, timeout=60)
        v = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else {"error": (r.stderr or r.stdout).strip()[-300:] or "balance lookup failed"}
    except Exception as e:
        v = {"error": str(e)[:300]}
    _balance.update(t=time.time(), v=v, busy=False)
    return v


def summary(period="all"):
    c = config.load(); cpu = c["costs"].get("credits_per_usd") or 211; me = person()
    since = (datetime.now() - timedelta(days=int(period))).strftime("%Y-%m-%d") if period in ("7", "30", "90") else None
    team = team_ledgers()
    rows, seen = [], set()
    for who, rs, ex in [(me, my_rows(), read_expenses())] + [(p, l.get("rows") or [], l.get("expenses") or []) for p, l in team.items()]:
        for r in rs:
            pid = r.get("prompt_id") or f"{who}:{r.get('id')}"
            if pid in seen or (since and (r.get("day") or "") < since):
                continue
            seen.add(pid); rows.append({**r, "person": who})
        for e in ex:
            if since and (e.get("date") or "") < since:
                continue
            rows.append({"id": e.get("id"), "day": (e.get("date") or "")[:10], "usd": float(e.get("usd") or 0), "credits": 0.0, "model": e.get("service") or "other",
                         "group": "—", "images": 0, "selects": 0, "gen_seconds": None, "server": "—", "title": e.get("note"), "source": "expense", "person": who, "expense": True})
    runs = [r for r in rows if not r.get("expense")]; exps = [r for r in rows if r.get("expense")]
    tot = {"usd": sum(r["usd"] for r in runs), "credits": sum(r["credits"] for r in runs), "runs": len(runs), "images": sum(r["images"] for r in runs),
           "videos": sum(r.get("videos", 0) for r in runs),
           "selects": sum(r["selects"] for r in runs), "expenses_usd": sum(r["usd"] for r in exps)}
    tot["total_usd"] = tot["usd"] + tot["expenses_usd"]
    mine = [r for r in runs if r["person"] == me]
    tot["mine_usd"] = sum(r["usd"] for r in mine) + sum(r["usd"] for r in exps if r["person"] == me)

    def agg(key, src=runs):
        by = {}
        for r in src:
            b = by.setdefault(r[key], {"key": r[key], "runs": 0, "images": 0, "videos": 0, "usd": 0.0, "credits": 0.0, "selects": 0, "gen": 0.0, "gen_n": 0, "expenses_usd": 0.0,
                                       "usd_images": 0.0, "usd_videos": 0.0})
            if r.get("expense"):
                b["expenses_usd"] += r["usd"]; continue
            b["runs"] += 1; b["images"] += r["images"]; b["videos"] += r.get("videos", 0); b["usd"] += r["usd"]; b["credits"] += r["credits"]; b["selects"] += r["selects"]
            if r.get("videos", 0) and not r["images"]:
                b["usd_videos"] += r["usd"]
            elif r["images"]:
                b["usd_images"] += r["usd"]
            if r.get("gen_seconds") is not None:
                b["gen"] += r["gen_seconds"]; b["gen_n"] += 1
        for b in by.values():
            b["total_usd"] = b["usd"] + b["expenses_usd"]
        return sorted(by.values(), key=lambda b: -b["total_usd"])
    by_day = {}
    for r in rows:
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + r["usd"]
    budget = c["costs"].get("budget_usd")
    spent_all = summary("all")["totals"]["total_usd"] if (period != "all" and budget) else tot["total_usd"]
    return {"period": period, "person": me, "totals": tot, "by_person": agg("person", rows), "by_model": agg("model"), "by_group": agg("group"), "by_server": agg("server"),
            "by_day": sorted(({"day": k, "usd": v} for k, v in by_day.items() if k), key=lambda x: x["day"]),
            "recent": sorted(runs, key=lambda r: (r["day"], r["id"] or ""), reverse=True)[:80],
            "expenses": sorted(exps, key=lambda e: e.get("day") or "", reverse=True),
            "team": [{"person": p, "exported": l.get("exported"), "machine": l.get("machine"), "rows": len(l.get("rows") or []),
                      "via": "shared folder" if shared_dir() and (l.get("_file") or "").startswith(shared_dir()) else "import"} for p, l in sorted(team.items())],
            "shared_dir": shared_dir(), "shared_ok": bool(shared_dir()) and os.path.isdir(shared_dir()),
            "budget_usd": budget, "spent_all_usd": spent_all, "credits_per_usd": cpu, "balance": _balance["v"],
            "client": c["costs"].get("client"), "project": c["costs"].get("project"), "log": c["_paths"]["credit_log"],
            "unbilled": sum(1 for r in read_runs() if r.get("prompt_id") and r.get("engine") == "api" and r.get("status") == "done" and (r.get("cost") or {}).get("usd") is None)}


def export_file(fmt="json", team=False):
    """Write this machine's ledger (json) or the merged team ledger (csv) into exports/. Returns {ok, file, url, path}."""
    c = config.load(); d = c["_paths"]["exports"]; os.makedirs(d, exist_ok=True)
    code = c["project"].get("code") or "project"; stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    if fmt == "csv":
        s = summary("all"); name = f"{code}_costs_team_{stamp}.csv"; p = os.path.join(d, name)
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["person", "date", "run", "group", "title", "model", "server", "images", "videos", "selects", "api_calls", "credits", "usd", "source", "prompt_id"])
            for r in sorted(s["recent_all"] if "recent_all" in s else _all_rows(), key=lambda r: (r["person"], r["day"], r["id"] or "")):
                w.writerow([r["person"], r["day"], r["id"], r["group"], r.get("title"), r["model"], r["server"], r["images"], r.get("videos", 0), r["selects"], r.get("api_calls"),
                            f"{r['credits']:.2f}", f"{r['usd']:.4f}", r.get("source"), r.get("prompt_id") or ""])
            w.writerow([]); w.writerow(["TOTAL", "", "", "", "", "", "", s["totals"]["images"], s["totals"]["videos"], s["totals"]["selects"], "", f"{s['totals']['credits']:.2f}", f"{s['totals']['total_usd']:.2f}", "runs + expenses", ""])
    else:
        led = ledger(); name = f"{code}_costs_{slug(led['person'])}_{stamp}.json"; p = os.path.join(d, name)
        json.dump(led, open(p, "w"), indent=1)
        write_shared()
    return {"ok": True, "file": name, "url": "/export/" + name, "path": p}


def _all_rows():
    s = summary("all")
    me = s["person"]; rows = []
    for who, rs, ex in [(me, my_rows(), read_expenses())] + [(p, l.get("rows") or [], l.get("expenses") or []) for p, l in team_ledgers().items()]:
        rows += [{**r, "person": who} for r in rs]
        rows += [{"id": e.get("id"), "day": (e.get("date") or "")[:10], "group": "—", "title": e.get("note"), "model": e.get("service") or "other", "server": "—",
                  "images": 0, "selects": 0, "credits": 0.0, "usd": float(e.get("usd") or 0), "source": "expense", "person": who, "prompt_id": ""} for e in ex]
    seen, out = set(), []
    for r in rows:
        k = r.get("prompt_id") or f"{r['person']}:{r.get('id')}"
        if k in seen:
            continue
        seen.add(k); out.append(r)
    return out


def backfill():
    """Look the billed cost up for finished API runs that have none yet (credits.py cost, in batches of 15)."""
    runs_dir = config.load()["_paths"]["runs"]
    todo = [r for r in read_runs() if r.get("prompt_id") and r.get("engine") == "api" and r.get("status") == "done" and (r.get("cost") or {}).get("usd") is None]
    done = 0; errors = []
    for i in range(0, len(todo), 15):
        batch = todo[i:i + 15]
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "credits.py"), "cost", *[x["prompt_id"] for x in batch], "--wait", "0"],
                               capture_output=True, text=True, timeout=300)
            j = json.loads(r.stdout); bal = j.get("balance") or {}
        except Exception as e:
            errors.append(str(e)[:200]); continue
        for rec in batch:
            row = (j.get("runs") or {}).get(rec["prompt_id"]) or {}
            if not row.get("api_calls"):
                continue
            rec["cost"] = {"credits": row.get("credits"), "usd": row.get("usd"), "api_calls": row.get("api_calls"), "models": row.get("models"),
                           "balance_usd": bal.get("usd"), "balance_credits": bal.get("credits"), "checked": datetime.now().isoformat(timespec="seconds"), "via": "backfill"}
            p = os.path.join(runs_dir, rec["id"] + ".json")
            tmp = p + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, p); done += 1
    if done:
        write_shared()
    return {"ok": True, "checked": len(todo), "billed": done, "errors": errors}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "export":
        print(json.dumps(export_file("csv" if "--csv" in sys.argv else "json"), indent=1))
    else:
        per = sys.argv[sys.argv.index("--period") + 1] if "--period" in sys.argv else "all"
        s = summary(per); print(json.dumps({k: s[k] for k in ("person", "totals", "by_person", "by_model", "team", "shared_dir", "unbilled")}, indent=1))
