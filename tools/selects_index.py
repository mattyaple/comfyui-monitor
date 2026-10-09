#!/usr/bin/env python3
"""Index the Finder tags on generated images so any session (or a clone without the images) can see selects and rejects.

Scans every collection of monitor/project.json under the output root (all versions of a versioned one) and writes
monitor/selects_index.json: {"generated", "output_root", "tags": {"Yellow": "select", "Red": "rejected"},
"files": [{"path": <relative to the output root>, "collection", "group", "section", "version", "tags": [...],
"status": select|rejected|other, "run": <run id from _sources.jsonl when known>}]}, only files that carry a tag.

  python3 tools/selects_index.py                    # rewrite the index and list every tagged file
  python3 tools/selects_index.py --group Page-2b    # only that group (a shot, a reference subject, a test folder)
  python3 tools/selects_index.py --selects          # only the selects

Tags are read through `mdls` (Spotlight), which also sees tags Finder has just written; a raw xattr read can lag.
"""
import json, os, subprocess, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "monitor"))
import config  # noqa: E402

CFG = config.load()
OUT = CFG["_paths"]["output_root"]
INDEX = os.path.join(CFG["_monitor"], "selects_index.json")
TAGS = CFG["selects"]["tags"]
MEANING = {TAGS.get("select", "Yellow"): "select", TAGS.get("ng", "Red"): "rejected"}
EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov")


def tags_of(path):
    r = subprocess.run(["mdls", "-raw", "-name", "kMDItemUserTags", path], capture_output=True, text=True)
    t = r.stdout.strip()
    if not t or t == "(null)":
        return []
    return [x.strip().strip('"') for x in t.strip("()").split(",") if x.strip()]


def run_of(path):
    d, fn = os.path.split(path); src = os.path.join(d, "_sources.jsonl")
    if not os.path.isfile(src):
        return None
    for line in open(src):
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("file") == fn:
            return j.get("run")
    return None


def scan_dirs():
    for col in CFG["collections"]:
        for ver in (CFG["_versions"] if col["versioned"] else [""]):
            d = config.col_dir(CFG, col, ver)
            if os.path.isdir(d):
                yield d


def build():
    files, seen = [], set()
    for base in scan_dirs():
        for d, dirs, fns in os.walk(base):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for fn in fns:
                if not fn.lower().endswith(EXT) or fn.startswith("."):
                    continue
                p = os.path.join(d, fn)
                if p in seen:
                    continue
                seen.add(p); tags = tags_of(p)
                if not tags:
                    continue
                rel = os.path.relpath(p, OUT); hit = config.classify_path(CFG, rel)
                col, group, sid, ver = hit if hit else (None, None, None, "")
                status = next((MEANING[t] for t in tags if t in MEANING), "other")
                files.append({"path": rel, "collection": col["id"] if col else None, "group": group, "section": sid, "version": ver or None,
                              "tags": tags, "status": status, "run": run_of(p)})
    files.sort(key=lambda f: f["path"])
    json.dump({"generated": datetime.now().isoformat(timespec="seconds"), "output_root": OUT, "current_version": CFG["_cur"] or None,
               "tags": MEANING, "files": files}, open(INDEX, "w"), indent=1)
    return files


if __name__ == "__main__":
    files = build()
    want = sys.argv[sys.argv.index("--group") + 1] if "--group" in sys.argv else None
    only_sel = "--selects" in sys.argv
    for f in files:
        if (not want or f["group"] == want) and (not only_sel or f["status"] == "select"):
            print(f"{f['status']:9} {f['tags']}  {f['path']}  run={f['run']}")
    print(f"{len(files)} tagged files -> {os.path.relpath(INDEX, ROOT)}")
