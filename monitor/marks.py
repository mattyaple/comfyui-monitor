"""Marks (select, NG, final pair) for the files in the output tree, with two stores:

- the **sidecar**: a `_marks.json` in the file's own folder ({file name: {"select", "ng", "pair", "by", "when"}}). It travels
  with the files, so on a shared volume (LucidLink, a NAS) every machine, Mac or Windows, sees the same marks; folders
  and files starting with "_" are invisible to the listings, so the sidecar never shows up as an image.
- **Finder tags** (macOS only, tags.py): the Yellow / Red / Green labels Finder shows.

`selects.marks` in project.json: "sidecar", "tags" or "both"; "auto" (the default) is "both" on a Mac and "sidecar"
elsewhere. With "both", the sidecar is the source of truth and the tags mirror it; a file that has a tag but no
sidecar entry yet still reads from the tag (so nothing is lost on the way over; `tools/marks_migrate.py` writes the
sidecars for a whole tree in one go).
"""
import json, os, sys, threading, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import tags  # noqa: E402

IS_MAC = sys.platform == "darwin"
SIDECAR = "_marks.json"
KINDS = ("select", "ng", "pair")
_cache = {}; _lock = threading.Lock()


def mode():
    m = (config.load()["selects"].get("marks") or "auto").lower()
    if m == "auto":
        return "both" if IS_MAC else "sidecar"
    return m if m in ("sidecar", "tags", "both") else "both"


def use_sidecar():
    return mode() in ("sidecar", "both")


def use_tags():
    return IS_MAC and mode() in ("tags", "both")


def _who():
    c = config.load()["costs"]
    return c.get("person") or os.environ.get("USER") or os.environ.get("USERNAME") or "?"


def read_sidecar(folder):
    """The folder's sidecar as a dict (cached by mtime); {} when there is none."""
    p = os.path.join(folder, SIDECAR)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    with _lock:
        hit = _cache.get(p)
        if hit and hit[0] == mt:
            return hit[1]
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    with _lock:
        _cache[p] = (mt, data)
    return data


def write_sidecar(folder, data):
    p = os.path.join(folder, SIDECAR)
    data = {k: v for k, v in data.items() if any(v.get(x) for x in KINDS)}   # drop entries with nothing marked
    if not data:
        try:
            os.remove(p)
        except OSError:
            pass
        with _lock:
            _cache.pop(p, None)
        return
    tmp = p + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)
    with _lock:
        _cache.pop(p, None)


def read(path):
    """{"tagged": select?, "ng": NG?, "pair": final pair?} for a file in the local output tree; {} elsewhere."""
    root = config.load()["_paths"]["output_root"]
    if not (path and path.startswith(root + "/")):
        return {}
    out = None
    if use_sidecar():
        e = read_sidecar(os.path.dirname(path)).get(os.path.basename(path))
        if e is not None:
            out = {"tagged": bool(e.get("select")), "ng": bool(e.get("ng")), "pair": bool(e.get("pair"))}
    if out is None and use_tags():
        out = tags.marks(path)
    return out or {"tagged": False, "ng": False, "pair": False}


def set(path, kind, on, exclusive=True):
    """Set or clear one mark. select and NG are exclusive (setting one clears the other); pair sits beside them."""
    assert kind in KINDS
    if use_sidecar():
        folder, name = os.path.split(path)
        data = dict(read_sidecar(folder)); e = dict(data.get(name) or {})
        if e == {} and use_tags():   # first sidecar entry for a file that may carry tags already: start from them
            t = tags.marks(path); e = {"select": bool(t.get("tagged")), "ng": bool(t.get("ng")), "pair": bool(t.get("pair"))}
        e[kind] = bool(on)
        if on and exclusive and kind in ("select", "ng"):
            e["ng" if kind == "select" else "select"] = False
        e["by"] = _who(); e["when"] = datetime.now().isoformat(timespec="seconds")
        data[name] = e
        write_sidecar(folder, data)
    if use_tags():
        sel, ng = tags.colors(); pair = (config.load().get("pairs") or {}).get("tag") or "Green"
        tags.set_label(path, {"select": sel, "ng": ng, "pair": pair}[kind], on, exclusive=(kind != "pair"))


def folder_marks(folder):
    """{file name: {"tagged", "ng", "pair"}} for every marked file in a folder (sidecar, plus tags on a Mac for files without an entry)."""
    out = {}
    if use_sidecar():
        for name, e in read_sidecar(folder).items():
            out[name] = {"tagged": bool(e.get("select")), "ng": bool(e.get("ng")), "pair": bool(e.get("pair"))}
    return out
