#!/usr/bin/env python3
"""Collect a run's outputs from any server into the project's local output tree (project.json decides where).

Every output path is classified against the collections in project.json (config.classify_path): a file of a versioned
"shots" collection, a "folders" collection (references, tests) or an extra category.

- A file a *remote* server saved into a collection's staging folder (versions.staging, e.g. V3_Pages/<group>/) is
  downloaded through the server's tunnel (falling back to its NAS mirror) into the local collection folder
  (V3/<group>/) and, for the base section, renamed after collections[].file_name ({group}{file_suffix}_{n:03d}, numbered
  per group and version). Files of other sections (_edits/, _plates/) keep their names. Files of "folders" collections
  are mirrored to the same relative path with the same name (a clash gets a _<server> suffix).
- A file a *local* server saved straight into its final folder is only recorded; one still sitting in a local staging
  folder (an older graph) is moved in and the emptied staging folders removed.

Every collected file gets a line in <folder>/_sources.jsonl (run id, server, source path, seed, title, model, workflow),
and the run record a "collected" map {server-relative output: path relative to the local output root}.

run.py calls collect_run() when a run ends. Manual backfill:
  python3 monitor/collect.py <run_id> [<run_id> ...]      # or --all for every run not yet collected
"""
import json, os, re, shutil, sys, urllib.parse, urllib.request
try:
    import fcntl
except ImportError:   # Windows: no file locks (collect runs one at a time there)
    fcntl = None
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

CFG = config.load()
RUNS = CFG["_paths"]["runs"]
LOCAL_ROOT = CFG["_paths"]["output_root"]
SERVERS = config.servers()


def _local(server):
    return SERVERS.get(server or "local", {}).get("kind", "local") == "local"


def classify(rel):
    return config.classify_path(CFG, rel)


def is_collectable(rec):
    return any(classify(o) for o in rec.get("outputs") or [])


def _strip_prefix(rec, rel):
    pre = SERVERS.get(rec.get("server") or "local", {}).get("output_prefix", "")
    return rel[len(pre):] if pre and rel.startswith(pre) else rel


def _fetch(rec, rel, dest):
    """Copy one output to dest: through the server's tunnel, else from its NAS mirror. Returns how, or None."""
    server = rec.get("server") or "local"; cfg = SERVERS[server]
    if not _local(server):
        try:
            import tunnel
            port = tunnel.up(server)
            full = cfg.get("output_prefix", "") + rel
            sub, fn = os.path.split(full)
            q = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": "output"})
            urllib.request.urlretrieve(f"http://127.0.0.1:{port}/view?{q}", dest)
            return "server"
        except BaseException as e:  # tunnel.up exits on a stopped instance; fall back to the mirror
            if os.path.exists(dest):
                os.remove(dest)
            print(f"[collect] server fetch failed ({e}); trying the output root")
    src = os.path.join(cfg["output_root"], rel)
    if os.path.isfile(src):
        if _local(server):
            shutil.move(src, dest); _prune(cfg["output_root"], rel); return "moved"
        shutil.copy2(src, dest); return "disk"
    return None


def _prune(root, rel):
    """Remove the staging folders a moved file leaves empty."""
    if not CFG["versions"].get("enabled"):
        return
    stg = re.escape(CFG["versions"]["staging"]).replace(r"\{n\}", r"\d+")
    parts = rel.split("/")[:-1]
    while any(re.fullmatch(stg, x) for x in parts):
        d = os.path.join(root, *parts)
        try:
            if os.listdir(d) == [".DS_Store"]:
                os.remove(os.path.join(d, ".DS_Store"))
            os.rmdir(d)
        except OSError:
            return
        parts.pop()


def _record(d, name, rec, rel, how):
    with open(os.path.join(d, "_sources.jsonl"), "a") as fh:
        fh.write(json.dumps({"file": name, "run": rec.get("id"), "server": rec.get("server"), "source": rel, "seed": rec.get("seed"),
                             "title": rec.get("title"), "model": rec.get("model"), "workflow": rec.get("workflow"), "tags": rec.get("tags"),
                             "via": how, "collected": datetime.now().isoformat(timespec="seconds")}) + "\n")


def _next_name(d, pattern, group, vv):
    """The next free numbered name in d for pattern ({group}{file_suffix}_{n:03d}); numbering continues per folder."""
    probe = config.fmt(pattern, group=group, **vv, n=0)
    head, _, tail = re.escape(probe).rpartition("000")   # the counter is the last 000 in the probe
    rx = re.compile("^" + head + r"(\d+)" + tail + r"\.(?:png|jpg|jpeg|webp|mp4)$")
    nums = [int(m.group(1)) for f in os.listdir(d) if (m := rx.match(f))]
    return config.fmt(pattern, group=group, **vv, n=max(nums, default=0) + 1) + ".png"


def collect_run(rec, save=None):
    """Collect every classified output of a run. Returns the number of new files."""
    done = rec.setdefault("collected", {}); new = 0
    todo = [(o, classify(_strip_prefix(rec, o))) for o in rec.get("outputs") or [] if o not in done]
    todo = [(o, c) for o, c in todo if c]
    if not todo:
        return 0
    os.makedirs(LOCAL_ROOT, exist_ok=True)
    with open(os.path.join(LOCAL_ROOT, ".collect.lock"), "w") as lock:
        (fcntl.flock(lock, fcntl.LOCK_EX) if fcntl else None)  # two servers can finish at the same moment
        for rel, (col, group, sid, ver) in todo:
            local = _local(rec.get("server"))
            srel = _strip_prefix(rec, rel)
            if col.get("kind") == "shots":
                d = config.group_dir(CFG, col, group, sid, ver)
                sec = config.section(col, sid) if sid else None
                final_rel = config.relp(os.path.join(config.col_dir(CFG, col, ver), group, (sec or {}).get("sub") or ""), LOCAL_ROOT)
                in_place = local and srel.startswith(final_rel + "/")
            else:
                d = os.path.join(LOCAL_ROOT, os.path.dirname(srel)); in_place = local
            if in_place:
                dest = os.path.join(LOCAL_ROOT, srel); how = "direct" if os.path.isfile(dest) else None; name = os.path.basename(dest)
            else:
                os.makedirs(d, exist_ok=True)
                if col.get("kind") == "shots" and not (sid and config.section(col, sid).get("sub")):
                    name = _next_name(d, col.get("file_name") or "{group}{file_suffix}_{n:03d}", group, config.version_vars(CFG, ver))
                else:
                    name = _clean(os.path.basename(rel)); base, ext = os.path.splitext(name); k = 2
                    while os.path.exists(os.path.join(d, name)):
                        name = f"{base}_{rec.get('server') if k == 2 else k}{ext}"; k += 1
                dest = os.path.join(d, name)
                how = _fetch(rec, rel, dest)
            if not how:
                print(f"[collect] could not fetch {rel}"); continue
            done[rel] = config.relp(dest, LOCAL_ROOT); new += 1
            if how != "direct":
                _record(d, name, rec, rel, how)
            elif not _recorded(d, name):
                _record(d, name, rec, rel, how)
            print(f"[collect] {rel} -> {done[rel]}")
    if save:
        save()
    return new


def _clean(name):
    """name_00001_.png -> name_00001.png when naming.strip_trailing_underscore is on."""
    if not CFG.get("naming", {}).get("strip_trailing_underscore", True):
        return name
    base, ext = os.path.splitext(name)
    m = re.match(r"^(.*_\d+)_$", base)
    return (m.group(1) + ext) if m else name


def _recorded(d, name):
    try:
        for line in open(os.path.join(d, "_sources.jsonl")):
            if json.loads(line).get("file") == name:
                return True
    except (OSError, ValueError):
        pass
    return False


if __name__ == "__main__":
    ids = sys.argv[1:]
    if ids == ["--all"]:
        ids = sorted(f[:-5] for f in os.listdir(RUNS) if f.endswith(".json"))
    for rid in ids:
        p = os.path.join(RUNS, rid + ".json")
        rec = json.load(open(p))

        def save(p=p, rec=rec):
            tmp = p + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, p)
        print(rid, collect_run(rec, save), "new")
