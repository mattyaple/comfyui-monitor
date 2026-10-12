#!/usr/bin/env python3
"""Write `_marks.json` sidecars from the Finder tags on every file of the output tree (macOS), so marks made before
the sidecar store existed (or made by hand in Finder) are shared with every machine.

  python3 tools/marks_migrate.py            # report what would be written
  python3 tools/marks_migrate.py --write    # write the sidecars (existing sidecar entries win; nothing is removed)
"""
import os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "monitor"))
import config, marks, tags  # noqa: E402

if sys.platform != "darwin":
    sys.exit("Finder tags exist only on macOS; nothing to migrate here")
CFG = config.load(); OUT = CFG["_paths"]["output_root"]
EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mkv")
write = "--write" in sys.argv
n_files = n_folders = 0
for d, dirs, fns in os.walk(OUT):
    dirs[:] = [x for x in dirs if not x.startswith((".", "_", "Z_"))]
    data = dict(marks.read_sidecar(d)); changed = False
    for fn in fns:
        if not fn.lower().endswith(EXT) or fn.startswith((".", "_")) or fn in data:
            continue
        t = tags.marks(os.path.join(d, fn))
        if t.get("tagged") or t.get("ng") or t.get("pair"):
            data[fn] = {"select": bool(t.get("tagged")), "ng": bool(t.get("ng")), "pair": bool(t.get("pair")), "by": "finder tags", "when": datetime.now().isoformat(timespec="seconds")}
            changed = True; n_files += 1
            print(f"{'write' if write else 'would write'}: {os.path.relpath(os.path.join(d, fn), OUT)}  {[k for k, v in t.items() if v]}")
    if changed:
        n_folders += 1
        if write:
            marks.write_sidecar(d, data)
print(f"{n_files} tagged file(s) in {n_folders} folder(s)" + ("" if write else " — run again with --write"))
