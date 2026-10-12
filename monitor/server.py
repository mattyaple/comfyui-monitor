#!/usr/bin/env python3
"""Local monitor server: serves monitor/index.html, the run records, images, marks, exports, costs and settings.

  python3 monitor/server.py            # http://127.0.0.1:<server.port from project.json>

Everything project-specific comes from monitor/project.json (see config.py and docs/CONFIG.md); the file is re-read
when it changes, so the Settings tab's edits apply at once.

GET
  /                        the page
  /api/config              project.json plus derived fields (versions, paths, category colors)
  /api/servers             servers.json
  /api/runs                run records, newest first, with images resolved, marks, category / group / section / version
  /api/watch                 the UI-run watcher's status and log (runs queued from the ComfyUI interface)
  /api/queue               ComfyUI queue per server (never opens tunnels; a remote server reads as offline when its tunnel is down)
  /api/selects             version list + select counts per collection
  /api/collection?id=&v=   one collection's groups and files (the Selects tab), with marks and provenance
  /api/path?src=&srv=      local file path of an image
  /api/exports             files in the exports folder          /export/<name>  one of them
  /api/costs?period=       cost summary (runs, credit log, manual expenses, balance, budget)
  /api/costs/balance?refresh=1
  /thumb?src=&w=  /full?src=   cached JPEG thumbnail / the full file. src = out:<path under the server's output root> | in:<path under ComfyUI input> | exp:<export file>
POST
  /api/select?src=&srv=&col=&group=&sec=&v=&on=1|0   mark a select (tag mode: Finder's yellow tag; copy mode: also copied to the collection's dest)
  /api/ng?src=&srv=&on=1|0                           mark NG (Finder's red tag; exclusive with select)
  /api/pair?src=&srv=&on=1|0                         final pair: copy the image + its source image into pairs.dest, tag both (pairs in project.json)
  /api/reveal?src=&srv=                              show the file in Finder
  /api/reroll?id=&dry=0|1                            re-run a run's graph with a new seed (retargeted to the current version)
  /api/run/remove?id=                                move a run record into runs/_removed/
  /api/export (json body)  /api/export/delete?name=
  /api/costs/expense (json body)  /api/costs/expense/delete?id=  /api/costs/backfill
  /api/costs/export?format=json|csv  /api/costs/import (json body: a teammate's export)  /api/costs/import/delete?person=
  /api/config (json body)  /api/config/bump  /api/servers (json body)
"""
import hashlib, json, os, random, re, shutil, subprocess, sys, threading, time, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

MON = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MON)
import config  # noqa: E402

CACHE = os.path.join(MON, "cache")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
VID_EXT = (".mp4", ".mov", ".webm", ".mkv")
MEDIA_EXT = IMG_EXT + VID_EXT
def _imageio_ffmpeg():
    """The ffmpeg binary imageio-ffmpeg ships in a ComfyUI environment (Macs without Homebrew), or None."""
    import glob as _g
    hits = _g.glob(os.path.expanduser("~/venvs/*/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*")) + \
           _g.glob(os.path.expanduser("~/ComfyUI/.venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*")) + \
           _g.glob(os.path.expanduser("~/Documents/ComfyUI/.venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"))
    return next((h for h in hits if os.access(h, os.X_OK)), None)


FFMPEG = shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", os.path.expanduser("~/.local/bin/ffmpeg")) if os.path.exists(p)), None) or _imageio_ffmpeg()
FFPROBE = shutil.which("ffprobe") or next((p for p in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", os.path.expanduser("~/.local/bin/ffprobe")) if os.path.exists(p)), None)
is_video = lambda p: str(p).lower().endswith(VID_EXT)
LOCK = threading.Lock()


def cfg():
    return config.load()


def servers():
    try:
        return config.servers()
    except Exception:
        return {"local": {"kind": "local", "port": 8188, "output_root": cfg()["_paths"]["output_root"], "output_prefix": ""}}


def out_root(server):
    s = servers()
    return s.get(server or "local", s.get("local", {})).get("output_root") or cfg()["_paths"]["output_root"]


def local_root():
    return cfg()["_paths"]["output_root"]


def runs_dir():
    return cfg()["_paths"]["runs"]


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---- files ----------------------------------------------------------------------------------------------------------

_moved = {}


def find_moved(root, rel):
    """Files moved by hand into one of paths.moved_lookup (under a server's output root): found by name, index rebuilt every 30 s."""
    name = os.path.basename(rel)
    for base in cfg()["_paths"]["moved_lookup"]:
        if not base.startswith(root) or not os.path.isdir(base):
            continue
        t, idx = _moved.get(base, (0, {}))
        if name not in idx and time.time() - t > 30:
            idx = {}
            for d, _, fs in os.walk(base):
                for f in fs:
                    idx.setdefault(f, os.path.join(d, f))
            _moved[base] = (time.time(), idx)
        if name in idx:
            return idx[name]
    return None


def locate(root, rel):
    p = os.path.join(root, rel)
    return p if os.path.isfile(p) else find_moved(root, rel)


def resolve(src, server=None):
    """out:<rel> (a server's output root), in:<rel> (ComfyUI input), exp:<name> (exports) -> absolute path, or None."""
    kind, _, rel = (src or "").partition(":")
    base = {"out": out_root(server), "in": cfg()["_paths"]["comfy_input"], "exp": cfg()["_paths"]["exports"]}.get(kind)
    if not base:
        return None
    p = os.path.normpath(os.path.join(base, rel))
    if p.startswith(base) and os.path.isfile(p):
        return p
    if kind == "out":
        m = find_moved(base, rel)
        return m if m and os.path.normpath(m).startswith(base) else None
    return None


def thumb(path, w):
    key = hashlib.md5(f"{path}:{os.path.getmtime(path)}:{w}".encode()).hexdigest()
    out = os.path.join(CACHE, key + ".jpg")
    if not os.path.exists(out):
        if is_video(path):
            if FFMPEG:   # a frame from the first second, scaled to the requested width
                subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "0.3", "-i", path, "-frames:v", "1", "-vf", f"scale='min({w},iw)':-2", "-q:v", "4", out],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        else:
            subprocess.run(["sips", "-Z", str(w), "-s", "format", "jpeg", "-s", "formatOptions", "80", path, "--out", out],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out if os.path.exists(out) else None


_dims = {}


def dims(path):
    """{"w", "h"} of a PNG from its header, of a video through ffprobe (plus "dur" seconds), cached; {} for other formats
    (the page measures the loaded image)."""
    try:
        key = (path, os.path.getmtime(path))
        if key not in _dims:
            if is_video(path):
                d = {"video": True}
                if FFPROBE:
                    try:
                        j = json.loads(subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
                                                      capture_output=True, text=True, timeout=30).stdout or "{}")
                        st = (j.get("streams") or [{}])[0]
                        if st.get("width") and st.get("height"):
                            d.update(w=int(st["width"]), h=int(st["height"]))
                        if (j.get("format") or {}).get("duration"):
                            d["dur"] = round(float(j["format"]["duration"]), 1)
                    except Exception:
                        pass
                _dims[key] = d
            else:
                with open(path, "rb") as fh:
                    b = fh.read(24)
                _dims[key] = {"w": int.from_bytes(b[16:20], "big"), "h": int.from_bytes(b[20:24], "big")} if b[:8] == b"\x89PNG\r\n\x1a\n" else {}
        return _dims[key]
    except OSError:
        return {}


_hashes = {}


def file_hash(p):
    st = os.stat(p); k = (p, st.st_mtime, st.st_size)
    if k not in _hashes:
        with open(p, "rb") as fh:
            _hashes[k] = hashlib.sha256(fh.read()).hexdigest()[:16]
    return _hashes[k]


# ---- Finder tags (tags.py) --------------------------------------------------------------------------------------------

import tags  # noqa: E402

finder_label, finder_labels, marks = tags.set_label, tags.labels, tags.marks


def tag_colors():
    return tags.colors()


# ---- run records -------------------------------------------------------------------------------------------------------

def read_runs():
    out = []
    d = runs_dir()
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d), reverse=True):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f)) as fh:
                r = json.load(fh)
        except Exception:
            continue
        r.setdefault("id", f[:-5])
        if "group" not in r and r.get("page"):          # records from an older monitor
            r["group"] = r["page"]
        if r.get("version") is None and r.get("page_version"):
            r["version"] = r["page_version"]
        out.append(r)
    return out


def first_output(rec):
    coll = rec.get("collected") or {}
    return (list(coll.values()) or rec.get("outputs") or [None])[0]


def classify(rec):
    """(category, group, section id, version folder) for a run from its first output path; older records fall back on their fields."""
    c = cfg(); first = first_output(rec)
    if first:
        hit = config.classify_path(c, first)
        if hit:
            col, group, sid, ver = hit
            cat = col["id"] + (f":{sid}" if sid and config.section(col, sid).get("sub") else "")
            return cat, group.replace("/", " / ") if col.get("kind") == "folders" else group, sid, ver
        for ex in c["runs"].get("extra_categories") or []:
            m = re.search(ex["regex"], first)
            if m:
                return ex["id"], (m.group(1) if m.groups() else ex.get("label", ex["id"])), None, ""
    # no output path to read (a failed or running run, or an older record): fall back on the record's own fields
    kind, group = rec.get("kind"), rec.get("group") or ""
    col = next((x for x in c["collections"] if x["id"] == kind), None)
    if not col and group:
        for x in c["collections"]:
            config.matcher(c, x)
            top = config.fmt(x.get("dir") or "", **c["_vars"], version="", staging="").split("/")[0]
            if (x.get("kind") == "shots" and x["_grx"].match(group)) or (top and group.split("/")[0] == top):
                col = x; break
    if col:
        sid = rec.get("section") if rec.get("section") and config.section(col, rec["section"]) else None
        return col["id"] + (f":{sid}" if sid and config.section(col, sid).get("sub") else ""), group or "—", sid, rec.get("version") or ""
    for ex in c["runs"].get("extra_categories") or []:
        m = re.search(ex["regex"], group + "/")
        if m:
            return ex["id"], (m.group(1) if m.groups() else ex.get("label", ex["id"])), None, ""
    return "other", group or "—", None, ""


def find_group_file(col, group, name, prefer=None):
    """A group's image by name across versions (the preferred one first): (rel path under output_root, abs path) or None."""
    c = cfg()
    for ver in dict.fromkeys([prefer] + (c["_versions"] if col["versioned"] else [""])):
        if ver is None:
            continue
        for s in col.get("sections") or [{"sub": ""}]:
            p = os.path.join(config.group_dir(c, col, group, s.get("id") if s.get("sub") else None, ver), name)
            if os.path.isfile(p):
                return os.path.relpath(p, local_root()), p
    return None


def compare_for(rec, cat, group, sid, ver):
    """What the lightbox shows while the mouse is held on one of this run's images: the source image an edit was made
    from, the plate a render was made with (matched by content), or the first source image. None without inputs."""
    c = cfg(); refs = rec.get("refs") or []
    if not refs:
        return None
    col = next((x for x in c["collections"] if x["id"] == cat.split(":")[0]), None)
    first = os.path.basename(refs[0])
    sec = config.section(col, sid) if col and sid else None
    if col and col.get("kind") == "shots":
        # an edit: its first input is a group image (found by name, any section, preferred version first)
        wants_source = (sec and sec.get("compare") == "source") or first.startswith(group)  # an edit, or a run fed its own group's image
        hit = find_group_file(col, group, first, ver) if wants_source else None
        if hit:
            return {"src": "out:" + hit[0], "kind": "source", "name": first, "ref": 0}
        # a render made with a plate: an input whose content matches a file of a "hash" section of this group
        for s in col.get("sections") or []:
            if s.get("compare") != "hash":
                continue
            for v in dict.fromkeys([ver] + (c["_versions"] if col["versioned"] else [""])):
                d = config.group_dir(c, col, group, s["id"], v)
                if not os.path.isdir(d):
                    continue
                by_size = {}
                for f in os.listdir(d):
                    if f.lower().endswith(IMG_EXT) and not f.startswith("."):
                        by_size.setdefault(os.path.getsize(os.path.join(d, f)), []).append(f)
                for k, ref in enumerate(refs):
                    p = os.path.join(c["_paths"]["comfy_input"], ref)
                    if os.path.isfile(p) and (cands := by_size.get(os.path.getsize(p))):
                        for f in cands:
                            if file_hash(p) == file_hash(os.path.join(d, f)):
                                return {"src": "out:" + os.path.relpath(os.path.join(d, f), local_root()), "kind": s["label"].lower().rstrip("s"), "name": f, "ref": k}
    for k, ref in enumerate(refs):
        for rx, label in c["runs"].get("ref_roles") or []:
            if label in ("plate", "mask") and re.search(rx, os.path.basename(ref), re.I):
                return {"src": "in:" + ref, "kind": label, "name": os.path.basename(ref), "ref": k}
    return {"src": "in:" + refs[0], "kind": "image 1", "name": first, "ref": 0}


def ref_versions(r):
    """Per input, the file the run actually used: current / archived (a retired copy in Z_OLD/ next to it) / changed / unknown."""
    out = []; inp = cfg()["_paths"]["comfy_input"]
    for rel in r.get("refs") or []:
        want = (r.get("ref_hashes") or {}).get(rel)
        p = os.path.join(inp, rel)
        try:
            if not want or (os.path.isfile(p) and file_hash(p) == want):
                out.append({"src": "in:" + rel, "state": "current" if want else "unknown", "v": want or ""}); continue
            old = os.path.join(os.path.dirname(p), "Z_OLD")
            hit = next((f for f in sorted(os.listdir(old)) if os.path.isfile(os.path.join(old, f)) and file_hash(os.path.join(old, f)) == want), None) if os.path.isdir(old) else None
            out.append({"src": "in:" + os.path.relpath(os.path.join(old, hit), inp), "state": "archived", "v": want} if hit else {"src": "in:" + rel, "state": "changed", "v": want})
        except OSError:
            out.append({"src": "in:" + rel, "state": "unknown", "v": ""})
    return out


def image_entry(rec, o, p, srv, coll_rel=None):
    """One output image of a run, with its marks, size and its own place in the collections (col / grp / sec / ver)."""
    rel = coll_rel if coll_rel is not None else os.path.relpath(p, out_root(srv))
    e = {"src": "out:" + rel, "srv": srv, "name": os.path.basename(p), "from": o, **marks(p), **dims(p)}
    try:
        e["mtime"] = os.path.getmtime(p)   # the page adds it to image URLs, so a replaced file is never shown from the browser cache
    except OSError:
        pass
    hit = config.classify_path(cfg(), rel)
    if hit:
        col, group, sid, ver = hit
        e.update(col=col["id"], grp=group.replace("/", " / ") if col.get("kind") == "folders" else group, sec=sid, ver=ver)
        if (col.get("select") or {}).get("mode") == "copy" and not e.get("tagged"):
            e["sel"] = os.path.basename(p) in dest_names(config.select_dest(cfg(), col, group, sid if sid and config.section(col, sid).get("sub") else None, ver))
    e.setdefault("sel", bool(e.get("tagged")))
    return e


def load_runs():
    out = []; lr = local_root()
    for r in read_runs():
        root = out_root(r.get("server"))
        allo = r.get("outputs", []); coll = r.get("collected") or {}
        r["images"], r["syncing"] = [], []
        r["category"], r["group"], r["section"], ver = classify(r)
        r["version"] = ver or r.get("version") or ""
        try:
            r["compare"] = compare_for(r, r["category"], r["group"], r["section"], r["version"])
        except OSError:
            r["compare"] = None
        for o in allo:
            if o in coll and os.path.isfile(p := os.path.join(lr, coll[o])):
                r["images"].append(image_entry(r, o, p, "local", coll[o]))
            elif (p := locate(root, o)):
                r["images"].append(image_entry(r, o, p, r.get("server") or "local"))
            elif (r.get("server") or "local") != "local":
                r["syncing"].append(o)
        r["ref_versions"] = ref_versions(r)
        out.append(r)
    return out


def one_queue(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/queue", timeout=2.5) as r:
            q = json.load(r)
        return {"online": True, "running": [{"prompt_id": i[1], "number": i[0]} for i in q.get("queue_running", [])],
                "pending": [{"prompt_id": i[1], "number": i[0]} for i in q.get("queue_pending", [])]}
    except Exception as e:
        return {"online": False, "running": [], "pending": [], "error": str(e)}


def comfy_queue():
    per = {n: one_queue(c["port"]) for n, c in servers().items()}
    return {"servers": per, "online": any(v["online"] for v in per.values()),
            "running": [x for v in per.values() for x in v["running"]], "pending": [x for v in per.values() for x in v["pending"]]}


def reroll(run_id, dry=False):
    """Clone a run's graph with a fresh seed and launch it through run.py (retargeted to the current version when needed)."""
    c = cfg(); src = os.path.join(runs_dir(), run_id + ".json")
    if not os.path.isfile(src):
        return {"ok": False, "error": "unknown run id"}
    rec = json.load(open(src))
    wf = rec.get("workflow"); wf_abs = os.path.join(c["_root"], wf) if wf else None
    if not wf_abs or not os.path.isfile(wf_abs):
        return {"ok": False, "error": "this run has no workflow file to re-run"}
    g = json.load(open(wf_abs))
    import run as runner
    if rec.get("server"):
        sv = servers()
        if rec["server"] not in sv:
            return {"ok": False, "error": f"unknown server '{rec['server']}'"}
        reason = runner.reservation_reason(rec["server"], runner.engine_of(g), graph=g, servers=sv)
        if reason:
            return {"ok": False, "error": reason}
    seed = random.randint(1, 2**31 - 1); seeded = False
    for n in g.values():
        if n.get("class_type") == "Seed":
            n["inputs"]["seed"] = seed; seeded = True
    for n in g.values():
        if n.get("class_type") == "RandomNoise":
            n["inputs"]["noise_seed"] = random.randint(1, 2**48); seed = n["inputs"]["noise_seed"]; seeded = True
    for n in g.values():
        if (runner.is_api_node(n) or n.get("class_type") in ("KSampler", "SenseNovaU1LocalImageEdit")) and isinstance(n["inputs"].get("seed"), int):
            n["inputs"]["seed"] = seed; seeded = True
        elif n.get("class_type") == "KSamplerAdvanced" and isinstance(n["inputs"].get("noise_seed"), int):
            n["inputs"]["noise_seed"] = seed; seeded = True
    if not seeded:
        for n in g.values():
            if n.get("class_type") in ("OpenAIGPTImageNodeV2", "OpenAIGPTImage1"):
                n["inputs"]["seed"] = seed; seeded = True
    if not seeded:
        return {"ok": False, "error": "workflow has no seed to change"}
    moved = runner.retarget(g) if runner.frozen_saves(g) else []
    base = os.path.basename(wf_abs).replace(".api.json", "").split("_reroll")[0]
    os.makedirs(c["_paths"]["workflows"], exist_ok=True)
    new_wf = os.path.join(c["_paths"]["workflows"], f"{base}_reroll{seed}.api.json")
    title = rec.get("title", base).split(" (re-roll")[0] + f" (re-roll seed {seed})"
    note = f"saves into {c['_cur']} ({moved[0][0]} → {moved[0][1]})" if moved else ""
    if dry:
        return {"ok": True, "dry": True, "seed": seed, "workflow": os.path.relpath(new_wf, c["_root"]), "title": title, "note": note}
    json.dump(g, open(new_wf, "w"), indent=1)
    log = open(os.path.join(MON, "server.log"), "a")
    extra = ["--server", rec["server"]] if rec.get("server") else []
    subprocess.Popen([sys.executable, os.path.join(MON, "run.py"), new_wf, "--title", title, "--group", rec.get("group") or ""] + extra,
                     cwd=c["_root"], stdout=log, stderr=log, start_new_session=True)
    return {"ok": True, "seed": seed, "workflow": os.path.relpath(new_wf, c["_root"]), "title": title, "note": note}


def remove_run(run_id):
    src = os.path.join(runs_dir(), run_id + ".json")
    if not os.path.isfile(src):
        return {"ok": False, "error": "unknown run id"}
    d = os.path.join(runs_dir(), "_removed"); os.makedirs(d, exist_ok=True)
    shutil.move(src, os.path.join(d, run_id + ".json"))
    return {"ok": True, "moved": os.path.relpath(os.path.join(d, run_id + ".json"), cfg()["_root"])}


# ---- collections (the Selects tab) ---------------------------------------------------------------------------------------

_ref_runs = {"sig": None, "map": {}}


def run_index():
    """Local output path (relative to output_root) -> run provenance, from the run records (cached until the runs folder changes)."""
    d = runs_dir()
    files = sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    sig = (len(files), max((os.path.getmtime(os.path.join(d, f)) for f in files), default=0))
    if sig != _ref_runs["sig"]:
        m = {}; lr = local_root()
        for r in read_runs():
            prov = {"run": r.get("id"), "title": r.get("title"), "seed": r.get("seed"), "server": r.get("server"), "model": r.get("model"),
                    "cost": (r.get("cost") or {}).get("usd")}
            local = out_root(r.get("server")) == lr
            for o in list(r.get("outputs") or []) + list((r.get("collected") or {}).values()):
                if local or o in (r.get("collected") or {}).values():
                    m[o] = prov
        _ref_runs.update(sig=sig, map=m)
    return _ref_runs["map"]


def sources_meta(d):
    meta = {}
    try:
        for line in open(os.path.join(d, "_sources.jsonl")):
            m = json.loads(line); meta[m["file"]] = m
    except Exception:
        pass
    return meta


def dest_names(dest):
    """Files present in a copy-mode select destination (Z_OLD ignored)."""
    if not dest or not os.path.isdir(dest):
        return set()
    return {f for f in os.listdir(dest) if f.lower().endswith(MEDIA_EXT) and not f.startswith(".")}


def list_images(d):
    return [f for f in os.listdir(d) if f.lower().endswith(MEDIA_EXT) and not f.startswith((".", "_"))] if os.path.isdir(d) else []


def file_entry(c, col, group, sid, ver, d, f, meta, idx, dest_set):
    p = os.path.join(d, f); rel = os.path.relpath(p, c["_paths"]["output_root"])
    pv = meta.get(f) or idx.get(rel) or {}
    e = {"name": f, "src": "out:" + rel, "srv": "local", "path": p, "section": sid, **marks(p), **dims(p),
         **{k: pv.get(k) for k in ("run", "title", "seed", "server", "model")}, "mtime": os.path.getmtime(p)}
    e["sel"] = bool(e.get("tagged")) or (f in dest_set)
    return e


def collection_listing(col_id, ver=None):
    c = cfg(); col = next((x for x in c["collections"] if x["id"] == col_id), None)
    if not col:
        return {"error": f"unknown collection '{col_id}'"}
    if col["versioned"]:
        ver = ver if ver in c["_versions"] else c["_cur"]
    else:
        ver = ""
    root = config.col_dir(c, col, ver); idx = run_index(); groups = []
    if not os.path.isdir(root):
        return {"id": col_id, "version": ver, "groups": [], "root": root, "missing": True}
    if col.get("kind") == "shots":
        for g in sorted((x for x in os.listdir(root) if os.path.isdir(os.path.join(root, x)) and not x.startswith((".", "_")) and col["_grx"].match(x)), key=natural):
            files = []; dests = {}
            for s in col.get("sections") or []:
                sid = s["id"] if s.get("sub") else None
                d = config.group_dir(c, col, g, sid, ver)
                dest = config.select_dest(c, col, g, sid, ver); dests[s["id"]] = dest
                meta = sources_meta(d); ds = dest_names(dest)
                files += [file_entry(c, col, g, s["id"], ver, d, f, meta, idx, ds) for f in list_images(d) if config.section_accepts(col, s, f)]
            files.sort(key=lambda e: e["name"], reverse=True)
            groups.append({"group": g, "files": files, "dest": {k: (os.path.relpath(v, c["_root"]) if v and v.startswith(c["_root"]) else v) for k, v in dests.items() if v}})
    else:
        depth = col.get("depth", 1)
        subs = []
        if depth == 0:
            for d, dirs, fs in os.walk(root):
                dirs[:] = sorted(x for x in dirs if not x.startswith("."))
                if any(f.lower().endswith(MEDIA_EXT) and not f.startswith((".", "_")) for f in fs):
                    subs.append(os.path.relpath(d, root) if d != root else "(top level)")
        else:
            for x in sorted(os.listdir(root), key=natural):
                if not os.path.isdir(os.path.join(root, x)) or x.startswith((".", "_")):
                    continue
                subs.append(x)
                if depth >= 2:
                    subs += [f"{x}/{y}" for y in sorted(os.listdir(os.path.join(root, x)), key=natural)
                             if os.path.isdir(os.path.join(root, x, y)) and not y.startswith((".", "_", "Z_"))]
        rx = re.compile(col["section_from_name"]) if col.get("section_from_name") else None
        for g in subs:
            d = root if g == "(top level)" else os.path.join(root, g)
            dest = config.select_dest(c, col, g, None, ver); meta = sources_meta(d); ds = dest_names(dest)
            files = []
            for f in list_images(d):
                e = file_entry(c, col, g, None, ver, d, f, meta, idx, ds)
                if rx:
                    stem = re.sub(r"_\d+_?$", "", os.path.splitext(f)[0]); m = rx.search(stem)
                    e["section"] = m.group(1) if m and m.groups() else ("other" if not m else stem)
                files.append(e)
            files.sort(key=lambda e: (-int(e["mtime"] // 60), e["name"]))
            groups.append({"group": g.replace("/", " / "), "folder": g, "files": files, "latest": max((e["mtime"] for e in files), default=0),
                           "dest": {"": os.path.relpath(dest, c["_root"]) if dest and dest.startswith(c["_root"]) else dest} if dest else {}})
        if depth == 0:
            groups.sort(key=lambda g: -g["latest"])
    return {"id": col_id, "version": ver, "groups": groups, "root": root}


def select_counts():
    """{collection id: {version: n selects}} from Finder tags (one pass over the listings is too slow per poll; the page updates counts itself)."""
    return {}


# ---- marks ------------------------------------------------------------------------------------------------------------

def local_copy_of(p, col, group, sid, ver):
    """The local collected copy of a remote file (same name in the group's section folder), or p itself when local."""
    if p.startswith(local_root() + "/"):
        return p
    q = os.path.join(config.group_dir(cfg(), col, group, sid, ver), os.path.basename(p))
    return q if os.path.isfile(q) else None


def mark_select(src, srv, col_id, group, sid, ver, on):
    c = cfg(); col = next((x for x in c["collections"] if x["id"] == col_id), None)
    if not col:
        return {"ok": False, "error": f"unknown collection '{col_id}'"}
    sel_color, _ = tag_colors()
    p = resolve(src, srv)
    if not p:
        return {"ok": False, "error": "source image not found"}
    group = (group or "").replace(" / ", "/")
    sid = sid if sid and config.section(col, sid) and config.section(col, sid).get("sub") else None
    ver = ver if (col["versioned"] and ver in c["_versions"]) else (c["_cur"] if col["versioned"] else "")
    lp = local_copy_of(p, col, group, sid, ver)
    mode = (col.get("select") or {}).get("mode", "tag")
    dest = config.select_dest(c, col, group, sid, ver) if mode == "copy" else None
    if mode == "copy":
        if not dest:
            return {"ok": False, "error": "no select destination configured for this collection"}
        parent = os.path.dirname(dest.rstrip("/"))
        probe = parent
        while probe and not os.path.isdir(probe):
            probe = os.path.dirname(probe)
        if not probe or probe in ("/", os.path.expanduser("~")):
            return {"ok": False, "error": f"select destination not reachable: {dest} (is the volume mounted?)"}
        name = os.path.basename(p); target = os.path.join(dest, name)
        if on:
            os.makedirs(dest, exist_ok=True)
            if not os.path.exists(target):
                shutil.copy2(p, target)
        else:
            how = (col.get("select") or {}).get("unselect", "z_old")
            if os.path.isfile(target):
                if how == "z_old":
                    os.makedirs(os.path.join(dest, "Z_OLD"), exist_ok=True); shutil.move(target, os.path.join(dest, "Z_OLD", name))
                elif how == "delete":
                    os.remove(target)
    elif not lp:
        return {"ok": False, "error": "only files in the local output folder can be tagged (this one is on a remote mirror)"}
    for q in {x for x in (p, lp) if x and x.startswith(local_root() + "/")}:
        finder_label(q, sel_color, on)
    out = {"ok": True, "file": os.path.basename(p), "mode": mode, **marks(lp or p)}
    out["sel"] = on
    if dest:
        out["dest"] = os.path.relpath(dest, c["_root"]) if dest.startswith(c["_root"]) else dest
    return out


def pairs_index(dest):
    p = os.path.join(dest, "_pairs.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}


def write_pairs_index(dest, idx):
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, "_pairs.json.tmp"); json.dump(idx, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(dest, "_pairs.json"))


def pair_source_of(rel, col, group, ver):
    """The source image of an output (the first input of the run that made it): (rel path under output_root or None, abs path or None)."""
    prov = run_index().get(rel) or {}
    rec = next((r for r in read_runs() if r.get("id") == prov.get("run")), None) if prov.get("run") else None
    refs = (rec or {}).get("refs") or []
    if not refs:
        return None, None
    name = os.path.basename(refs[0])
    hit = find_group_file(col, group, name, ver or None)
    if hit:
        return hit
    q = os.path.join(cfg()["_paths"]["comfy_input"], refs[0])
    return (None, q) if os.path.isfile(q) else (None, None)


def mark_pair(src, srv, on):
    """Mark an image as a final pair with its source image: both are copied into pairs.dest under pairs.names and tagged."""
    c = cfg(); pr = c.get("pairs") or {}
    if not pr.get("enabled"):
        return {"ok": False, "error": "final pairs are not enabled (Settings → Final pairs)"}
    p = resolve(src, srv)
    if not p or not p.startswith(local_root() + "/"):
        return {"ok": False, "error": "only files in the local output folder can be paired"}
    rel = os.path.relpath(p, local_root()); hit = config.classify_path(c, rel)
    if not hit or hit[0].get("kind") != "shots":
        return {"ok": False, "error": "this image is not in a unit folder of the project"}
    col, group, sid, ver = hit
    dest = config.pair_dest(c, col, group, sid, ver)
    if not dest:
        return {"ok": False, "error": "pairs.dest is not set"}
    s = config.section(col, sid) if sid else None
    shot = os.path.basename((s or {}).get("sub") or "") or group
    names = pr.get("names") or ["{shot}_A", "{shot}_B"]
    a_name, b_name = (config.expand(n, {**c["_vars"], "group": group, "section": sid or "", "shot": shot}) for n in names[:2])
    src_rel, src_abs = pair_source_of(rel, col, group, ver)
    tag = pr.get("tag") or "Green"
    idx = pairs_index(dest); cur = idx.get(shot) or {}
    if on:
        if not src_abs:
            return {"ok": False, "error": "no source image is recorded for this run (its first input), so there is nothing to pair it with"}
        probe = dest
        while probe and not os.path.isdir(probe):
            probe = os.path.dirname(probe)
        if not probe or probe in ("/", os.path.expanduser("~")):
            return {"ok": False, "error": f"pair destination not reachable: {dest} (is the volume mounted?)"}
        os.makedirs(dest, exist_ok=True)
        # a new pair for this shot replaces the previous one: its copies go to Z_OLD, its originals lose the tag
        for key in ("image", "source"):
            old_rel = cur.get(key)
            if old_rel and old_rel != (rel if key == "image" else src_rel):
                op = os.path.join(local_root(), old_rel)
                if os.path.isfile(op):
                    finder_label(op, tag, False, exclusive=False)
        for f in os.listdir(dest) if os.path.isdir(dest) else []:
            stem = os.path.splitext(f)[0]
            if stem in (a_name, b_name) and f.lower().endswith(MEDIA_EXT):
                os.makedirs(os.path.join(dest, "Z_OLD"), exist_ok=True)
                k, target = 0, os.path.join(dest, "Z_OLD", f)
                while os.path.exists(target):
                    k += 1; target = os.path.join(dest, "Z_OLD", f"{stem}_{k}{os.path.splitext(f)[1]}")
                shutil.move(os.path.join(dest, f), target)
        a_file = a_name + os.path.splitext(src_abs)[1]; b_file = b_name + os.path.splitext(p)[1]
        shutil.copy2(src_abs, os.path.join(dest, a_file)); shutil.copy2(p, os.path.join(dest, b_file))
        idx[shot] = {"image": rel, "source": src_rel or src_abs, "copies": [a_file, b_file], "when": datetime.now().isoformat(timespec="seconds")}
        write_pairs_index(dest, idx)
        finder_label(p, tag, True, exclusive=False)
        if src_rel:
            finder_label(os.path.join(local_root(), src_rel), tag, True, exclusive=False)
    else:
        how = pr.get("unpair", "z_old")
        for f in cur.get("copies") or []:
            fp = os.path.join(dest, f)
            if os.path.isfile(fp):
                if how == "z_old":
                    os.makedirs(os.path.join(dest, "Z_OLD"), exist_ok=True); shutil.move(fp, os.path.join(dest, "Z_OLD", f))
                elif how == "delete":
                    os.remove(fp)
        idx.pop(shot, None); write_pairs_index(dest, idx)
        finder_label(p, tag, False, exclusive=False)
        for q in (os.path.join(local_root(), cur["source"]) if cur.get("source") and not os.path.isabs(cur["source"]) else cur.get("source"), os.path.join(local_root(), src_rel) if src_rel else None):
            if q and os.path.isfile(q) and q.startswith(local_root() + "/"):
                finder_label(q, tag, False, exclusive=False)
    out = {"ok": True, "pair": on, "shot": shot, "file": os.path.basename(p), "dest": os.path.relpath(dest, c["_root"]) if dest.startswith(c["_root"]) else dest,
           "source": os.path.basename(src_abs) if src_abs else None, "source_src": ("out:" + src_rel) if src_rel else None, "copies": [a_name, b_name] if on else []}
    return out


def mark_ng(src, srv, on):
    _, ng_color = tag_colors()
    p = resolve(src, srv)
    if not p:
        return {"ok": False, "error": "source image not found"}
    if not p.startswith(local_root() + "/"):
        return {"ok": False, "error": "only files in the local output folder can be marked (this one is on a remote mirror)"}
    finder_label(p, ng_color, on)
    return {"ok": True, "file": os.path.basename(p), **marks(p), "sel": False if on else marks(p).get("tagged", False)}


def reveal(src, srv):
    p = resolve(src, srv)
    if not p:
        return {"ok": False, "error": "file not found"}
    subprocess.run(["open", "-R", p])
    return {"ok": True, "path": p}


# ---- exports -----------------------------------------------------------------------------------------------------------

def exports_list():
    d = cfg()["_paths"]["exports"]
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if f.startswith(".") or not os.path.isfile(p) or not f.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
            continue
        out.append({"name": f, "size": os.path.getsize(p), "mtime": os.path.getmtime(p), "url": "/export/" + f, "src": "exp:" + f})
    out.sort(key=lambda x: -x["mtime"])
    return out


def export_request(body):
    """Build the export spec from a request and render it with export.py. Returns {ok, file, url} or {ok: False, error}."""
    c = cfg(); ex = c["export"]
    if not ex.get("enabled", True):
        return {"ok": False, "error": "exports are disabled in Settings"}
    tname = body.get("template") or ex.get("default_template") or "contact"
    tpl = (ex.get("templates") or {}).get(tname)
    if not tpl or not tpl.get("enabled", True):
        return {"ok": False, "error": f"template '{tname}' is not enabled"}
    opts = {**tpl, **(body.get("options") or {})}
    scope = body.get("scope") or {}; which = body.get("images") or "selects"
    groups = []
    if scope.get("run"):
        r = next((x for x in load_runs() if x["id"] == scope["run"]), None)
        if not r:
            return {"ok": False, "error": "unknown run"}
        items = [{"path": resolve(im["src"], im["srv"]), "name": os.path.splitext(im["name"])[0], "caption": f"{r['id']} · seed {r.get('seed')}", "selected": bool(im.get("sel"))}
                 for im in r["images"] if not (which != "all_ng" and im.get("ng"))]
        groups.append({"id": r["id"], "label": r.get("title") or r["id"], "prompt": r.get("prompt"), "note": f"{r.get('model') or ''} · seed {r.get('seed')}", "items": [i for i in items if i["path"]]})
        scope_name = r["id"]
    else:
        col_id = scope.get("collection") or (c["collections"][0]["id"] if c["collections"] else None)
        listing = collection_listing(col_id, scope.get("version"))
        if listing.get("error"):
            return {"ok": False, "error": listing["error"]}
        want = set(scope.get("groups") or [])
        secs = set(scope.get("sections") or [])
        runs_by_id = {r["id"]: r for r in read_runs()} if opts.get("prompt") else {}
        for g in listing["groups"]:
            if want and g["group"] not in want:
                continue
            files = [f for f in g["files"] if (not secs or (f.get("section") or "") in secs)]
            if which == "selects":
                files = [f for f in files if f.get("sel")]
            elif which == "all":
                files = [f for f in files if not f.get("ng")]
            if not files:
                continue
            if body.get("sort") == "asc":
                files = list(reversed(files))
            items = [{"path": f["path"], "name": os.path.splitext(f["name"])[0], "selected": bool(f.get("sel")),
                      "caption": " · ".join(str(x) for x in (f.get("run"), f.get("title"), f"seed {f['seed']}" if f.get("seed") is not None else None) if x)} for f in files]
            prompt = None
            if opts.get("prompt"):
                rid = next((f.get("run") for f in files if f.get("run")), None)
                prompt = (runs_by_id.get(rid) or {}).get("prompt")
            groups.append({"id": g["group"], "label": (listing["version"] + " · " if listing["version"] else "") + g["group"], "prompt": prompt, "items": items})
        scope_name = col_id + (("_" + listing["version"]) if listing["version"] else "") + ("_" + "-".join(sorted(want))[:40] if want else "")
    if not groups:
        return {"ok": False, "error": "nothing to export with these settings (no matching images)"}
    pr = c["project"]; today = datetime.now()
    fvars = {"client": pr.get("client") or "", "title": pr.get("title") or pr.get("name") or "", "code": pr.get("code") or "", "date": today.strftime("%Y-%m-%d")}
    spec = {"template": tname, "title": body.get("title") or f"{pr.get('name') or pr.get('code')} · {tpl.get('label', tname)}",
            "subtitle": body.get("subtitle") or f"{scope_name.replace('_', ' ')} · {sum(len(g['items']) for g in groups)} images · {which.replace('_', ' ')} · {today:%Y-%m-%d %H:%M}",
            "footer": config.fmt(ex.get("footer") or "", **fvars), "options": opts, "groups": groups, "cache": CACHE}
    fmt_ext = (body.get("format") or opts.get("format") or "pdf").lower().lstrip(".")
    fmt_ext = "jpg" if fmt_ext in ("jpg", "jpeg") else "pdf"
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", f"{pr.get('code') or 'export'}_{tname}_{scope_name}")[:90]
    name = f"{safe}_{today:%Y-%m-%d_%H%M%S}.{fmt_ext}"
    os.makedirs(c["_paths"]["exports"], exist_ok=True); os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(c["_paths"]["exports"], name)
    spec_path = os.path.join(CACHE, "export_spec.json"); json.dump(spec, open(spec_path, "w"))
    cmd = ["uv", "run", "--quiet", "--with", "reportlab", "--with", "pillow", "python3", os.path.join(MON, "export.py"), spec_path, out]
    if not shutil.which("uv"):
        cmd = [sys.executable, os.path.join(MON, "export.py"), spec_path, out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        return {"ok": False, "error": (r.stderr or r.stdout)[-900:] or "export failed"}
    return {"ok": True, "file": name, "path": out, "url": "/export/" + name, "groups": len(groups), "images": sum(len(g["items"]) for g in groups)}


def export_delete(name):
    d = cfg()["_paths"]["exports"]; p = os.path.normpath(os.path.join(d, os.path.basename(name or "")))
    if not p.startswith(d) or not os.path.isfile(p):
        return {"ok": False, "error": "not found"}
    os.remove(p)
    return {"ok": True}


# ---- costs (costs.py) -------------------------------------------------------------------------------------------------

import costs  # noqa: E402


# ---- HTTP ------------------------------------------------------------------------------------------------------------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def send_file(self, path, ctype, cache="no-store"):
        with open(path, "rb") as f:
            b = f.read()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query); g1 = lambda k, d="": q.get(k, [d])[0]
        try:
            if u.path == "/api/reroll":
                return self.send_json(reroll(g1("id"), dry=g1("dry", "0") == "1"))
            if u.path == "/api/run/remove":
                return self.send_json(remove_run(g1("id")))
            if u.path in ("/api/select", "/api/tagselect"):
                return self.send_json(mark_select(g1("src"), g1("srv") or None, g1("col"), g1("group"), g1("sec"), g1("v"), g1("on", "1") != "0"))
            if u.path == "/api/ng":
                return self.send_json(mark_ng(g1("src"), g1("srv") or None, g1("on", "1") != "0"))
            if u.path == "/api/pair":
                return self.send_json(mark_pair(g1("src"), g1("srv") or None, g1("on", "1") != "0"))
            if u.path == "/api/reveal":
                return self.send_json(reveal(g1("src"), g1("srv") or None))
            if u.path == "/api/export":
                return self.send_json(export_request(self.body()))
            if u.path == "/api/export/delete":
                return self.send_json(export_delete(g1("name")))
            if u.path == "/api/costs/expense":
                e = costs.add_expense(self.body()); costs.write_shared()
                return self.send_json({"ok": True, "expense": e})
            if u.path == "/api/costs/expense/delete":
                costs.write_expenses([e for e in costs.read_expenses() if e.get("id") != g1("id")]); costs.write_shared()
                return self.send_json({"ok": True})
            if u.path == "/api/costs/backfill":
                return self.send_json(costs.backfill())
            if u.path == "/api/costs/export":
                return self.send_json(costs.export_file(g1("format", "json")))
            if u.path == "/api/costs/import":
                return self.send_json(costs.import_ledger(self.body()))
            if u.path == "/api/costs/import/delete":
                return self.send_json(costs.forget_person(g1("person")))
            if u.path == "/api/config":
                errs = config.save(self.body())
                return self.send_json({"ok": not errs, "errors": errs, "config": config.public(config.load(fresh=True))})
            if u.path == "/api/config/bump":
                data = config.raw(); c = cfg(); v = data.setdefault("versions", {})
                if not c["versions"].get("enabled"):
                    return self.send_json({"ok": False, "error": "versioning is off"})
                n = (c["_cur_n"] or 1) + 1; v["current"] = c["versions"]["folder"].format(n=n); v["enabled"] = True
                errs = config.save(data)
                return self.send_json({"ok": not errs, "errors": errs, "current": v["current"]})
            if u.path == "/api/servers":
                b = self.body()
                if not isinstance(b, dict) or not b:
                    return self.send_json({"ok": False, "error": "servers.json must be an object with at least one server"})
                for name, s in b.items():
                    if not isinstance(s, dict) or not isinstance(s.get("port"), int):
                        return self.send_json({"ok": False, "error": f"server '{name}': needs an integer port"})
                tmp = config.SERVERS_PATH + ".tmp"; json.dump(b, open(tmp, "w"), indent=2); os.replace(tmp, config.SERVERS_PATH)
                return self.send_json({"ok": True})
        except Exception as e:
            return self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}, 500)
        self.send_response(404); self.end_headers()

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); g1 = lambda k, d="": q.get(k, [d])[0]
        try:
            if u.path in ("/", "/index.html"):
                return self.send_file(os.path.join(MON, "index.html"), "text/html; charset=utf-8")
            if u.path == "/api/config":
                c = cfg(); pub = config.public(c); pub["_errors"] = config.validate(c); pub["_servers"] = servers()
                return self.send_json(pub)
            if u.path == "/api/servers":
                return self.send_json(servers())
            if u.path == "/api/runs":
                return self.send_json({"runs": load_runs()})
            if u.path == "/api/queue":
                return self.send_json(comfy_queue())
            if u.path == "/api/watch":
                import watch
                return self.send_json(watch.status())
            if u.path == "/api/selects":
                c = cfg()
                return self.send_json({"version": c["_cur"], "versions": c["_versions"], "collections": [x["id"] for x in c["collections"]]})
            if u.path == "/api/collection":
                return self.send_json(collection_listing(g1("id"), g1("v") or None))
            if u.path == "/api/path":
                p = resolve(g1("src"), g1("srv") or None)
                return self.send_json({"ok": bool(p), "path": p})
            if u.path == "/api/exports":
                return self.send_json({"exports": exports_list(), "dir": cfg()["_paths"]["exports"]})
            if u.path == "/api/costs":
                return self.send_json(costs.summary(g1("period", "all")))
            if u.path == "/api/costs/balance":
                return self.send_json(costs.balance(refresh=g1("refresh", "0") == "1"))
            if u.path.startswith("/export/"):
                d = cfg()["_paths"]["exports"]; f = os.path.normpath(os.path.join(d, os.path.basename(u.path)))
                if f.startswith(d) and os.path.isfile(f):
                    return self.send_file(f, "application/pdf" if f.lower().endswith(".pdf") else "image/jpeg", "max-age=60")
                self.send_response(404); self.end_headers(); return
            if u.path in ("/thumb", "/full"):
                p = resolve(g1("src"), g1("srv") or None)
                if not p:
                    self.send_response(404); self.end_headers(); return
                fresh = "no-store" if g1("src").startswith("in:") else None  # inputs get replaced under the same name
                if u.path == "/full":
                    ext = os.path.splitext(p)[1].lower()
                    ctype = {".png": "image/png", ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(ext, "image/jpeg")
                    return self.send_file(p, ctype, fresh or "max-age=3600")
                t = thumb(p, int(g1("w", "640")))
                if not t:
                    self.send_response(500); self.end_headers(); return
                return self.send_file(t, "image/jpeg", fresh or "max-age=86400")
        except Exception as e:
            return self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}, 500)
        self.send_response(404); self.end_headers()


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    c = cfg()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else c["server"]["port"]
    errs = config.validate(c)
    if errs:
        print("project.json has problems (the Settings tab shows them):", *errs, sep="\n  ")
    print(f"{c['project'].get('title') or 'monitor'}: http://127.0.0.1:{port}")
    if c["runs"].get("watch_ui", True):
        import watch; watch.start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
