#!/usr/bin/env python3
"""Team provenance: each monitor publishes its run records (and the source images they used) into the shared output
tree, so the Selects tab can show workflow info and reference frames for a teammate's generations too. The Runs tab
stays local: it reads monitor/runs/ only.

  <team.dir>/runs/<person>/<run id>.json    a run record (no event log) + "files": its outputs under the output root
  <team.dir>/refs/<sha256[:16]><ext>        each source image, stored once by content (the run's ref_hashes)
  <team.dir>/workflows/<person>/<file>      the run's API graph

team.dir defaults to {output_root}/_monitor (an "_" folder, never listed as images). Publishing is incremental (a record
is rewritten only when it changed) and runs in the server's background thread every team.publish_interval_s.

  python3 monitor/team.py              publish now, print what was written
"""
import hashlib, json, os, re, shutil, sys, threading, time

MON = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MON)
import config  # noqa: E402

DROP = ("events", "current_node", "images", "syncing", "compare", "ref_versions", "category")
_idx = {"sig": None, "map": {}, "runs": {}}
_lock = threading.Lock()


def cfg():
    return config.load()


def enabled(c=None):
    return bool((c or cfg()).get("team", {}).get("enabled", True))


def me():
    import costs
    return costs.person()


def slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "unknown").strip("_") or "unknown"


def root(c=None):
    return (c or cfg())["_paths"]["team"]


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]   # the same short sha256 as run.py's ref_hashes


def _outputs(c, r, servers):
    """The run's files relative to the local output root (the same paths on every machine that shares the tree)."""
    coll = r.get("collected") or {}
    s = servers.get(r.get("server") or "local", servers.get("local", {}))
    local = config.expand(s.get("output_root") or "", c["_vars"]) in ("", c["_paths"]["output_root"])
    return list(dict.fromkeys(list(coll.values()) + (list(r.get("outputs") or []) if local else [])))


def _ref_store(c, r):
    """Copy each input the run used into refs/ (by content). Returns {ref rel path: stored file name}."""
    inp = c["_paths"]["comfy_input"]; d = os.path.join(root(c), "refs"); out = {}
    hashes = r.get("ref_hashes") or {}
    for rel in r.get("refs") or []:
        p = os.path.join(inp, rel)
        want = hashes.get(rel)
        src = p if os.path.isfile(p) else None
        if want and src and _sha(src) != want:          # replaced since the run: look for the retired copy in Z_OLD/
            old = os.path.join(os.path.dirname(p), "Z_OLD"); src = None
            if os.path.isdir(old):
                src = next((os.path.join(old, f) for f in sorted(os.listdir(old)) if os.path.isfile(os.path.join(old, f)) and _sha(os.path.join(old, f)) == want), None)
        if not src:
            continue
        h = want or _sha(src)
        name = h + os.path.splitext(rel)[1].lower()
        dest = os.path.join(d, name)
        if not os.path.isfile(dest):
            os.makedirs(d, exist_ok=True)
            tmp = dest + ".tmp"; shutil.copy2(src, tmp); os.replace(tmp, dest)
        out[rel] = name
    return out


def publish(verbose=False):
    """Write every local run record that changed since its last publish. Returns the number written."""
    c = cfg()
    if not enabled(c):
        return 0
    rd = c["_paths"]["runs"]; who = me(); pd = os.path.join(root(c), "runs", slug(who)); n = 0
    if not os.path.isdir(rd):
        return 0
    try:
        servers = config.servers()
    except Exception:
        servers = {}
    with _lock:
        for f in sorted(os.listdir(rd)):
            if not f.endswith(".json"):
                continue
            src = os.path.join(rd, f); dest = os.path.join(pd, f)
            try:
                if os.path.isfile(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
                    continue
                r = json.load(open(src))
                if r.get("status") in ("queued", "running"):
                    continue
                r.setdefault("id", f[:-5])
                files = _outputs(c, r, servers)
                if not files:
                    continue
                rec = {k: v for k, v in r.items() if k not in DROP}
                rec.update(person=who, files=files, ref_store=_ref_store(c, r), published=time.strftime("%Y-%m-%dT%H:%M:%S"))
                wf = r.get("workflow"); wfp = os.path.join(c["_root"], wf) if wf else None
                if wfp and os.path.isfile(wfp):
                    wd = os.path.join(root(c), "workflows", slug(who)); os.makedirs(wd, exist_ok=True)
                    shutil.copy2(wfp, os.path.join(wd, os.path.basename(wfp)))
                    rec["workflow_shared"] = f"workflows/{slug(who)}/{os.path.basename(wfp)}"
                os.makedirs(pd, exist_ok=True)
                tmp = dest + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, dest); n += 1
                if verbose:
                    print(f"[team] {f} ({len(files)} files, {len(rec['ref_store'])} refs)")
            except Exception as e:
                print(f"[team] {f}: {type(e).__name__}: {e}", file=sys.stderr)
    return n


def index():
    """(path under the output root -> "person/run id", {"person/run id": record}) for every teammate's published record
    (this machine's own are skipped: the local run records are the truth for them). Cached until a runs folder changes."""
    c = cfg(); d = os.path.join(root(c), "runs"); mine = slug(me())
    if not enabled(c) or not os.path.isdir(d):
        return {}, {}
    people = [p for p in sorted(os.listdir(d)) if p != mine and os.path.isdir(os.path.join(d, p))]
    sig = tuple((p, os.path.getmtime(os.path.join(d, p)), len(os.listdir(os.path.join(d, p)))) for p in people)
    if sig != _idx["sig"]:
        m, runs = {}, {}
        for p in people:
            for f in os.listdir(os.path.join(d, p)):
                if not f.endswith(".json"):
                    continue
                try:
                    r = json.load(open(os.path.join(d, p, f)))
                except Exception:
                    continue
                key = f"{p}/{r.get('id') or f[:-5]}"; runs[key] = r
                for o in r.get("files") or []:
                    m[o] = key
        _idx.update(sig=sig, map=m, runs=runs)
    return _idx["map"], _idx["runs"]


def find(rel, run_id=None, by=None):
    """The teammate's record key for a file: by its path, else by (person, run id) from a _sources.jsonl line."""
    m, runs = index()
    if rel in m:
        return m[rel]
    if run_id and by:
        k = f"{slug(by)}/{run_id}"
        return k if k in runs else None
    return None


def record(key):
    return index()[1].get(key)


def loop():
    while True:
        try:
            publish()
        except Exception as e:
            print(f"[team] publish: {e}", file=sys.stderr)
        time.sleep(max(10, int(cfg().get("team", {}).get("publish_interval_s", 30))))


def start():
    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    print("published", publish(verbose=True), "records to", root())
