"""UI-run watcher: runs queued from the ComfyUI interface get the same run records as runs started through run.py.

Started by server.py as a daemon thread (runs.watch_ui). Every few seconds it reads each local server's /queue and
/history, and for every prompt the records do not know yet that saves into this project's output tree it writes a
record: queued/running while in the queue, done/error with its outputs, timing and billed cost once in the history.
The API graph is kept under workflows/ui/<run id>.api.json so the card's re-roll works. Prompts that save elsewhere
(another project's folders) are ignored, so several monitors can watch the same ComfyUI.

The run title comes from a string node in the UI graph titled like runs.ui_title_node ("Run Title") when the
workflow has one (read from the prompt's extra_pnginfo), else from the save folder.
"""
import json, os, re, threading, time, traceback, urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
import config  # noqa: E402
import run as runner  # noqa: E402  (graph_details, engine_of, new_id, ref_hashes, bill, clean_names)

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mkv")   # outputs the watcher looks for: images and videos
STRING_KEYS = ("text", "Text", "value", "string")   # Text / DF_Text / primitives
_state = {"log": [], "seen_final": set(), "started": None, "error": None, "ticks": 0}


def log(msg):
    _state["log"] = (_state["log"] + [f"{time.strftime('%H:%M:%S')} {msg}"])[-50:]


def status():
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in _state.items() if k != "seen_final"} | {"finalized": len(_state["seen_final"])}


def _get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ---- graph reading --------------------------------------------------------------------------------------------------

def static_path(g, v, depth=0):
    """A string input resolved as far as it is static: (text, complete). Linked StringConcatenate chains are followed;
    a link into a node whose text is not a literal (an LLM, a splitter) ends the text there with complete=False."""
    if isinstance(v, str):
        return v, True
    if not isinstance(v, list) or depth > 12:
        return "", not isinstance(v, list)
    src = g.get(str(v[0]), g.get(v[0], {})); si = src.get("inputs", {}); ct = src.get("class_type")
    if ct == "StringConcatenate":
        a, ok = static_path(g, si.get("string_a"), depth + 1)
        if not ok:
            return a, False
        b, ok2 = static_path(g, si.get("string_b"), depth + 1)
        return a + (si.get("delimiter") or "") + b, ok2
    for k in STRING_KEYS:
        if k in si:
            return static_path(g, si[k], depth + 1)
    return "", False


def save_folders(g):
    """[(node id, static save folder, complete)] for every save node of an API graph."""
    out = []
    for nid, n in g.items():
        ct, inp = n.get("class_type"), n.get("inputs", {})
        if ct in ("Image Save", "SaveVideoToPath"):
            p, ok = static_path(g, inp.get("output_path"))
            out.append((nid, p, ok))
        elif ct in ("SaveImage", "SaveVideo", "SaveAnimatedWEBP", "SaveWEBM"):
            p, ok = static_path(g, inp.get("filename_prefix"))
            out.append((nid, os.path.dirname(p) if ok else p, ok))
    return out


def project_rel(path, c, scfg):
    """A save folder as a path relative to the project's output root, or None when it is not this project's."""
    p = os.path.expanduser(path or "").rstrip("/")
    root = os.path.expanduser(scfg.get("output_root") or c["_paths"]["output_root"]).rstrip("/")
    pre = scfg.get("output_prefix") or ""
    if os.path.isabs(p):
        if p == root:
            return ""
        return p[len(root) + 1:] if p.startswith(root + "/") else None
    if pre and p.startswith(pre.rstrip("/")):
        return p[len(pre.rstrip("/")):].lstrip("/")
    if pre:
        return None
    return p


def belongs(g, c, scfg):
    """The project-relative save folder of a graph that saves into this project's tree ("." for the root itself), else None."""
    for _, folder, _ in save_folders(g):
        rel = project_rel(folder, c, scfg)
        if rel is None:
            continue
        rel = re.sub(r"/{2,}", "/", rel).strip("/")
        if rel == "":
            return "."
        if config.classify_path(c, rel + "/x.png"):
            return rel
    return None


def title_from_workflow(extra, c):
    """The value of a string node titled like runs.ui_title_node in the UI graph (extra_pnginfo), or None."""
    want = (c["runs"].get("ui_title_node") or "Run Title").strip().lower()
    wf = ((extra or {}).get("extra_pnginfo") or {}).get("workflow") or {}
    pools = [wf.get("nodes") or []] + [s.get("nodes") or [] for s in ((wf.get("definitions") or {}).get("subgraphs") or [])]
    for nodes in pools:
        for n in nodes:
            t = str(n.get("title") or "").strip().lower()
            if t == want or t.startswith(want + " "):
                for v in n.get("widgets_values") or []:
                    if isinstance(v, str) and v.strip() and not v.strip().lower().startswith("describe what this run"):
                        return v.strip()[:160]
    return None


# ---- records --------------------------------------------------------------------------------------------------------

def records_by_prompt():
    """prompt id -> (record path, record), including records the user removed from the page (monitor/runs/_removed/),
    so a removed run is not imported again from the history after a restart."""
    out = {}
    for d in (os.path.join(runner.RUNS, "_removed"), runner.RUNS):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            try:
                r = json.load(open(os.path.join(d, f)))
            except Exception:
                continue
            if r.get("prompt_id"):
                out[r["prompt_id"]] = (os.path.join(d, f), r)
    return out


def save_record(path, rec):
    tmp = path + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, path)


def new_record(pid, g, extra, server, c, scfg, started=None):
    det = runner.graph_details(g); engine = runner.engine_of(g)
    rel = belongs(g, c, scfg) or ""
    rel = "" if rel == "." else rel
    cls = config.classify_path(c, rel + "/x.png") if rel else None
    rid = runner.new_id()
    wdir = os.path.join(c["_paths"]["workflows"], "ui"); os.makedirs(wdir, exist_ok=True)
    wf = os.path.join(wdir, rid + ".api.json"); json.dump(g, open(wf, "w"), indent=1)
    group = cls[1] if cls else (rel.split("/")[0] if rel else "—")
    title = title_from_workflow(extra, c) or f"ComfyUI · {rel.rstrip('/').split('/')[-1] if rel else 'run'}"
    rec = {
        "id": rid, "group": group, "title": title, "via": "ui",
        "started": (started or datetime.now()).isoformat(timespec="seconds"), "finished": None, "status": "queued",
        "prompt": det["prompt"], "refs": det["refs"], "ref_hashes": runner.ref_hashes(det["refs"]), "seed": det["seed"], "model": det["model"],
        "batch": det["batch"], "aspect": det["aspect"], "resolution": det["resolution"], "quality": det["quality"],
        "workflow": os.path.relpath(wf, c["_root"]), "prompt_id": pid, "outputs": [], "events": [{"t": time.strftime("%H:%M:%S"), "node": "queued from the ComfyUI interface"}],
        "current_node": None, "error": None, "server": server, "engine": engine, "loras": det.get("loras"), "steps": det.get("steps"),
        "cfg": det.get("cfg"), "sampler": det.get("sampler"), "negative": det.get("negative"), "expected": det.get("batch") or 0,
        "kind": cls[0]["id"] if cls else None, "section": cls[2] if cls else None, "version": cls[3] if cls else None,
        "tags": [], "note": None, "gen_seconds": None, "total_seconds": None, "cost": None, "prompt_link": det.get("prompt_link"),
        "duration": det.get("duration"), "mode": det.get("mode"),
    }
    path = os.path.join(runner.RUNS, rid + ".json"); save_record(path, rec)
    log(f"{rid} new ui run {pid[:8]} -> {rel or '?'} ({title})")
    return path, rec


def outputs_from_history(entry, g, c, scfg, t0, t1):
    """Server-relative output paths (prefix stripped): from the history's reported files, else new files on disk under the
    graph's save folders written between t0 and t1 (WAS Image Save reports nothing unless show_previews is on)."""
    root = os.path.expanduser(scfg.get("output_root") or c["_paths"]["output_root"]).rstrip("/")
    comfy_out = os.path.join(os.path.dirname(c["_paths"]["comfy_input"].rstrip("/")), "output")
    found = []
    for node_out in (entry.get("outputs") or {}).values():
        for o in node_out.get("images") or []:
            if o.get("type") != "output" or not o.get("filename"):
                continue
            sub = o.get("subfolder") or ""
            for cand in (os.path.join(root, project_rel(sub, c, scfg) or "\x00", o["filename"]), "/" + os.path.join(sub, o["filename"]), os.path.join(comfy_out, sub, o["filename"])):
                if "\x00" not in cand and os.path.isfile(cand) and cand.startswith(root + "/"):
                    found.append(os.path.relpath(cand, root)); break
    if found:
        return list(dict.fromkeys(found))
    for _, folder, _ in save_folders(g):
        rel = project_rel(folder, c, scfg)
        if rel is None:
            continue
        base = os.path.join(root, rel) if rel else root
        if not os.path.isdir(base):
            continue
        for d, dirs, files in os.walk(base):
            if d[len(base):].count("/") >= 3:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith((".", "_", "Z_"))]
            for f in files:
                if f.lower().endswith(IMG_EXT) and not f.startswith("."):
                    p = os.path.join(d, f)
                    try:
                        m = os.path.getmtime(p)
                    except OSError:
                        continue
                    if t0 - 3 <= m <= t1 + 8:
                        found.append(os.path.relpath(p, root))
    return sorted(dict.fromkeys(found))


def finalize(path, rec, entry, g, c, scfg):
    st = entry.get("status") or {}
    msgs = {m[0]: (m[1] or {}).get("timestamp") for m in st.get("messages") or [] if isinstance(m, list) and len(m) > 1}
    t0 = (msgs.get("execution_start") or 0) / 1000 or datetime.fromisoformat(rec["started"]).timestamp()
    t1 = (msgs.get("execution_success") or msgs.get("execution_error") or msgs.get("execution_interrupted") or 0) / 1000 or time.time()
    rec["outputs"] = outputs_from_history(entry, g, c, scfg, t0, t1)
    if not rec.get("prompt") and rec.get("prompt_link"):
        rec["prompt"] = runner.history_prompt(g, rec["prompt_link"], rec["prompt_id"], scfg.get("port", 8188)) or ""
    runner.clean_names(rec, scfg)
    err = next((m[1] for m in st.get("messages") or [] if isinstance(m, list) and m[0] == "execution_error"), None)
    if err:
        rec["error"] = (err.get("exception_message") or json.dumps(err))[:800]
    ok = st.get("status_str") == "success"
    rec["status"] = "done" if ok and rec["outputs"] else "error"
    if rec["status"] == "error" and not rec["error"]:
        rec["error"] = "run ended without saved outputs" if ok else (st.get("status_str") or "failed")
    rec["started"] = datetime.fromtimestamp(t0).isoformat(timespec="seconds")
    rec["finished"] = datetime.fromtimestamp(t1).isoformat(timespec="seconds")
    rec["total_seconds"] = round(t1 - t0, 1); rec["current_node"] = None
    rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": f"finished in the ComfyUI interface: {len(rec['outputs'])} file(s)"})
    save_record(path, rec)
    log(f"{rec['id']} {rec['status']}: {len(rec['outputs'])} file(s) in {rec['total_seconds']}s")
    if rec["prompt_id"] and rec.get("engine") == "api" and c["costs"].get("enabled") and c["costs"].get("bill_runs", True):
        rec["current_node"] = "billing"; save_record(path, rec)
        rec["cost"] = runner.bill(rec); rec["current_node"] = None; save_record(path, rec)
        try:
            import costs; costs.write_shared()
        except Exception as e:
            log(f"shared ledger not written: {e}")


# ---- the loop -------------------------------------------------------------------------------------------------------

def tick():
    c = config.load(); _state["ticks"] += 1
    known = records_by_prompt()
    lookback = float(c["runs"].get("ui_lookback_h", 24)) * 3600
    for alias, scfg in config.servers().items():
        if scfg.get("kind", "local") != "local":
            continue
        port = scfg.get("port", 8188)
        try:
            q = _get(f"http://127.0.0.1:{port}/queue")
        except Exception:
            continue
        for state, items in (("running", q.get("queue_running") or []), ("queued", q.get("queue_pending") or [])):
            for it in items:
                if not isinstance(it, list) or len(it) < 3:
                    continue
                pid, g, extra = it[1], it[2], it[3] if len(it) > 3 else {}
                if pid in known:
                    path, rec = known[pid]
                    if "_removed" not in path and rec.get("via") == "ui" and rec.get("status") != state and rec.get("status") in ("queued", "running"):
                        rec["status"] = state; save_record(path, rec)
                    continue
                if not belongs(g, c, scfg):
                    continue
                path, rec = new_record(pid, g, extra, alias, c, scfg)
                rec["status"] = state; save_record(path, rec); known[pid] = (path, rec)
        try:
            h = _get(f"http://127.0.0.1:{port}/history?max_items={int(c['runs'].get('ui_history_items', 40))}", timeout=8)
        except Exception:
            continue
        for pid, entry in h.items():
            if pid in _state["seen_final"]:
                continue
            st = entry.get("status") or {}
            if not st.get("completed") and st.get("status_str") not in ("success", "error"):
                continue
            p = entry.get("prompt") or []
            g, extra = (p[2] if len(p) > 2 else {}), (p[3] if len(p) > 3 else {})
            if pid in known:
                path, rec = known[pid]
                if "_removed" in path or rec.get("via") != "ui" or rec.get("status") not in ("queued", "running"):
                    _state["seen_final"].add(pid); continue
            else:
                msgs = {m[0]: (m[1] or {}).get("timestamp") for m in st.get("messages") or [] if isinstance(m, list) and len(m) > 1}
                t0 = (msgs.get("execution_start") or 0) / 1000
                if not t0 or time.time() - t0 > lookback or not belongs(g, c, scfg):
                    _state["seen_final"].add(pid); continue
                path, rec = new_record(pid, g, extra, alias, c, scfg, started=datetime.fromtimestamp(t0))
            try:
                finalize(path, rec, entry, g, c, scfg)
            except Exception as e:
                log(f"{rec.get('id')} finalize failed: {e}"); rec["status"] = "error"; rec["error"] = f"monitor: {e}"[:300]; save_record(path, rec)
            _state["seen_final"].add(pid)


def loop():
    _state["started"] = datetime.now().isoformat(timespec="seconds")
    while True:
        try:
            tick(); _state["error"] = None
        except Exception as e:
            _state["error"] = f"{e}"; log(f"tick failed: {e}\n{traceback.format_exc()[-400:]}")
        try:
            time.sleep(float(config.load()["runs"].get("ui_watch_interval_s", 4)))
        except Exception:
            time.sleep(4)


def start():
    t = threading.Thread(target=loop, name="ui-watch", daemon=True); t.start(); return t


# ---- manual import ----------------------------------------------------------------------------------------------------

def import_prompts(prompt_ids, group=None, title=None, server="local"):
    """Give history entries records even when their save path is not this project's (a run before the monitor existed,
    a graph that saved elsewhere). Outputs are taken when they can be found; the billed cost is looked up either way."""
    c = config.load(); scfg = config.servers().get(server) or {}
    known = records_by_prompt(); out = []
    port = scfg.get("port", 8188)
    for pid in prompt_ids:
        if pid in known:
            print(f"[import] {pid[:8]} already recorded as {known[pid][1]['id']}"); continue
        try:
            entry = _get(f"http://127.0.0.1:{port}/history/{pid}", timeout=10).get(pid)
        except Exception as e:
            print(f"[import] {pid[:8]}: history not readable: {e}"); continue
        if not entry:
            print(f"[import] {pid[:8]}: not in ComfyUI's history"); continue
        p = entry.get("prompt") or []; g, extra = (p[2] if len(p) > 2 else {}), (p[3] if len(p) > 3 else {})
        msgs = {m[0]: (m[1] or {}).get("timestamp") for m in (entry.get("status") or {}).get("messages") or [] if isinstance(m, list) and len(m) > 1}
        t0 = (msgs.get("execution_start") or 0) / 1000
        path, rec = new_record(pid, g, extra, server, c, scfg, started=datetime.fromtimestamp(t0) if t0 else None)
        if group:
            rec["group"] = group
        if title:
            rec["title"] = title
        rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": "imported from ComfyUI's history"})
        finalize(path, rec, entry, g, c, scfg)
        if not rec["outputs"] and (entry.get("status") or {}).get("status_str") == "success":
            rec["status"] = "done"; rec["error"] = None
            rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": "no output files found on disk (saved elsewhere, moved or deleted)"})
            save_record(path, rec)
        cost = rec.get("cost") or {}
        print(f"[import] {rec['id']} {rec['status']} · {len(rec['outputs'])} file(s) · cost " + (f"${cost['usd']:.3f}" if cost.get("usd") is not None else cost.get("error") or "n/a"))
        out.append(rec)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Import ComfyUI history entries as run records (runs the watcher did not record: before it existed, or saved outside the project tree).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    im = sub.add_parser("import"); im.add_argument("prompt_ids", nargs="+"); im.add_argument("--group", help="the unit these runs belong to")
    im.add_argument("--title", help="card title for the imported runs"); im.add_argument("--server", default="local")
    a = ap.parse_args()
    if a.cmd == "import":
        import_prompts(a.prompt_ids, a.group, a.title, a.server)
