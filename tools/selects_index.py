#!/usr/bin/env python3
"""Read the marks of the output tree into monitor/selects_index.json, for sessions and scripts that cannot read them
themselves. Marks come from the `_marks.json` sidecars beside the files (shared across machines) and, on a Mac, from
Finder tags of files that have no sidecar entry (read through `mdls`, which also sees tags Finder has just written).

  python3 tools/selects_index.py [--group <id>] [--selects] [--pairs]

Each file: path (relative to the output root), collection, group, section, version, status (select / rejected / other),
pair (final pair?), by / when (who marked it, from the sidecar), run (the run that made it, from _sources.jsonl).
"""
import json, os, subprocess, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "monitor"))
import config, marks  # noqa: E402

CFG = config.load()
OUT = CFG["_paths"]["output_root"]
INDEX = os.path.join(CFG["_monitor"], "selects_index.json")
TAGS = CFG["selects"]["tags"]
MEANING = {TAGS.get("select", "Yellow"): "select", TAGS.get("ng", "Red"): "rejected"}
PAIR_TAG = (CFG.get("pairs") or {}).get("tag") or "Green"
EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mkv")


def tags_of(path):
    if sys.platform != "darwin" or not marks.use_tags():
        return []
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
            dirs[:] = [x for x in dirs if not x.startswith((".", "_", "Z_"))]
            side = marks.read_sidecar(d) if marks.use_sidecar() else {}
            for fn in fns:
                if not fn.lower().endswith(EXT) or fn.startswith((".", "_")):
                    continue
                p = os.path.join(d, fn)
                if p in seen:
                    continue
                seen.add(p)
                e = side.get(fn)
                if e is not None:
                    status = "select" if e.get("select") else "rejected" if e.get("ng") else "other"
                    pair, by, when, tags = bool(e.get("pair")), e.get("by"), e.get("when"), []
                    if status == "other" and not pair:
                        continue
                else:
                    tags = tags_of(p)
                    if not tags:
                        continue
                    status = next((MEANING[t] for t in tags if t in MEANING), "other"); pair = PAIR_TAG in tags; by = when = None
                rel = config.relp(p, OUT); hit = config.classify_path(CFG, rel)
                col, group, sid, ver = hit if hit else (None, None, None, "")
                files.append({"path": rel, "collection": col["id"] if col else None, "group": group, "section": sid, "version": ver or None,
                              "status": status, "pair": pair, "by": by, "when": when, "tags": tags, "run": run_of(p)})
    files.sort(key=lambda f: f["path"])
    json.dump({"generated": datetime.now().isoformat(timespec="seconds"), "output_root": OUT, "current_version": CFG["_cur"] or None,
               "marks": marks.mode(), "tags": MEANING, "files": files}, open(INDEX, "w"), indent=1)
    return files


if __name__ == "__main__":
    files = build()
    want = sys.argv[sys.argv.index("--group") + 1] if "--group" in sys.argv else None
    only_sel = "--selects" in sys.argv; only_pairs = "--pairs" in sys.argv
    for f in files:
        if (not want or f["group"] == want) and (not only_sel or f["status"] == "select") and (not only_pairs or f["pair"]):
            print(f"{f['status']:9}{' ◆pair' if f['pair'] else '      '}  {f['path']}  run={f['run']}{'  by ' + f['by'] if f.get('by') else ''}")
    print(f"{len(files)} marked files -> {config.relp(INDEX, ROOT)} (marks: {marks.mode()})")
