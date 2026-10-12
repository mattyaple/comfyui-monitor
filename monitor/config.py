#!/usr/bin/env python3
"""project.json: the one place a monitor is configured.

Shared by server.py, run.py, collect.py, export.py and the tools. `load()` returns the project's config merged over
DEFAULTS, every path expanded, plus a few derived fields (prefixed with "_"):

  cfg["_root"], cfg["_monitor"]   project root and monitor folder
  cfg["_paths"]                   absolute paths: output_root, comfy_input, comfy_bin, runs, workflows, exports, selects_root, credit_log
  cfg["_versions"]                version folder names, newest first (["V3", "V2"]) or [] when versioning is off
  cfg["_cur"]                     the current version folder ("V3") or ""

Placeholders accepted in paths and patterns:
  {project} {monitor} {output_root} {selects_root} {code} {client} {title}   project constants
  {version} {staging} {n} {file_suffix}                                      the version being addressed ("" when off)
  {group} {section} {name}                                                   the group (shot) and section being addressed

`fmt(pattern, **vars)` substitutes them and drops the empty path segments that an unused {version} leaves behind.
The config file is re-read whenever its modification time changes, so the Settings tab's edits apply at once.
"""
import copy, json, os, re, sys

MON = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(MON)
def _pick(env, name):
    """The project's file, or the template's example next to it (so the template runs as a demo out of the box)."""
    p = os.environ.get(env) or os.path.join(MON, name)
    ex = os.path.join(MON, name.replace(".json", ".example.json"))
    return p if os.path.exists(p) or not os.path.exists(ex) else ex


PATH = _pick("MONITOR_CONFIG", "project.json")
SERVERS_PATH = _pick("MONITOR_SERVERS", "servers.json")
USING_EXAMPLE = PATH.endswith(".example.json")
try:
    TEMPLATE_VERSION = open(os.path.join(MON, "VERSION")).read().strip()
except OSError:
    TEMPLATE_VERSION = "0"

DEFAULTS = {
    "template_version": TEMPLATE_VERSION,
    "project": {"name": "My Project", "code": "PROJ", "title": "PROJ Run Monitor", "client": ""},
    "terms": {"unit": "shot", "units": "shots"},
    "server": {"port": 8765, "poll_ms": 2500},
    "paths": {
        "output_root": "~/ComfyUI/output/{code}",     # the local ComfyUI output subtree that belongs to this project
        "output_prefix": "{code}/",                   # what graphs put in front of their save path (relative to ComfyUI's output dir)
        "comfy_input": "~/ComfyUI/input",
        "comfy_bin": "~/.local/share/uv/tools/comfy-mcp/bin/comfy",
        "runs": "{monitor}/runs",
        "workflows": "{project}/workflows",
        "exports": "{project}/exports",
        "selects_root": "",                           # where copy-mode selects go (a client Dropbox, a review folder); "" = none
        "moved_lookup": [],                           # folders (under a server's output root) where files get moved by hand; found by name
    },
    "versions": {
        "enabled": False,
        "current": "V1",
        "first": 1,
        "folder": "V{n}",          # version folder name under output_root
        "staging": "V{n}_Staging", # where a remote server saves a version's files before collect.py moves them into {folder}
        "file_suffix": "_v{n}",    # goes into collected file names: {group}{file_suffix}_{n:03d}
        "refuse_frozen": True,     # run.py refuses a graph that saves into an older version (use --retarget)
    },
    "collections": [
        {"id": "shots", "label": "Shots", "kind": "shots", "dir": "{version}", "group_regex": "^[A-Za-z0-9][^/]*$", "color": "#ffcf33",
         "file_name": "{group}{file_suffix}_{n:03d}",
         "sections": [{"id": "option", "label": "Options", "sub": "", "color": "#ffcf33"},
                      {"id": "edit", "label": "Edits", "sub": "_edits", "color": "#c792ea", "compare": "source"},
                      {"id": "plate", "label": "Plates", "sub": "_plates", "color": "#7fd7c4", "compare": "hash"}],
         "select": {"mode": "tag", "dest": "{selects_root}/{version}/{group}", "section_dest": {}, "unselect": "z_old"}},
        {"id": "refs", "label": "References", "kind": "folders", "dir": "References", "depth": 2, "color": "#3ddc84",
         "section_from_name": "_ref_(.+)$", "section_order": ["design", "face", "body", "turn", "expr"],
         "select": {"mode": "copy", "dest": "{project}/references/{group}", "dest_map": {}, "unselect": "z_old"}},
        {"id": "tests", "label": "Testing", "kind": "folders", "dir": "__TESTING", "depth": 0, "color": "#8b95a3",
         "select": {"mode": "tag"}},
    ],
    "runs": {
        "extra_categories": [],   # [{"id": "dataset", "label": "LoRA dataset", "regex": "(?:^|/)(CharDataset_[^/]+)/", "color": "#3ddc84"}]
        "ref_roles": [            # what a source image is, from its file name (first match wins); shown on run cards and in the lightbox
            ["_mask$", "mask"], ["^plate_|lineart|sketch|matte|layout|graybox|schematic", "plate"],
            ["facegrid", "face grid"], ["(^|_)face", "face"], ["turnaround|_turn(_|$)", "turnaround"], ["expression|_expr", "expressions"],
            ["(^|_)body|_hero|lineup", "body"], ["sheet", "sheet"], ["angles?_grid", "angle grid"], ["^env_", "environment"],
            ["style", "style"], ["logo", "logo"], ["photo", "photo"], ["_SEL$", "select"], ["product|packshot|bottle", "product"],
        ],
        "collapse_after": 2,      # run cards after the n newest start collapsed
        "watch_ui": True,         # runs queued from the ComfyUI interface get records too (watch.py polls /queue and /history)
        "ui_watch_interval_s": 4, # seconds between polls
        "ui_history_items": 40,   # how many history entries each poll reads
        "ui_lookback_h": 24,      # ignore history entries that started longer ago (first start on a busy machine)
        "ui_title_node": "Run Title",  # a string node with this title in the UI graph names the run on its card
    },
    "selects": {"tags": {"select": "Yellow", "ng": "Red"}},
    "pairs": {                # "final pair": an image plus the source image it was made from (its run's first input), copied together
        "enabled": False,
        "dest": "{selects_root}/{group}/Final",   # folder pattern: {selects_root} {output_root} {group} {section} {shot} (the section folder's last part)
        "names": ["{shot}_A", "{shot}_B"],        # file names of the copies (source, image); the extension is kept
        "tag": "Green",                           # Finder label set on both originals (kept alongside the select tag)
        "unpair": "z_old",                        # what unmarking does with the copies: z_old | delete | keep
    },
    "export": {
        "enabled": True,
        "footer": "{client} · {title} · {date}",
        "default_template": "contact",
        "templates": {
            "contact": {"enabled": True, "label": "Contact sheet", "format": "jpg", "columns": 4, "thumb": 900, "captions": True,
                        "group_headers": True, "paper": "letter", "orientation": "landscape"},
            "review": {"enabled": True, "label": "Review PDF", "format": "pdf", "per_row": 2, "captions": True, "prompt": False,
                       "notes": True, "cover": True, "paper": "letter", "orientation": "landscape"},
        },
    },
    "costs": {
        "enabled": True,
        "client": "",                # client and project names for the shared credit log (defaults: project.client / project.name)
        "project": "",
        "credit_log": "~/AI_Credit_Log/ai_credit_log.csv",
        "credits_per_usd": 211,
        "budget_usd": None,
        "bill_runs": True,           # run.py looks up and logs the billed cost after every run with partner API nodes
        "balance_cache_s": 120,
        "person": "",                # who this machine's usage belongs to in the team ledger (default: the macOS account's full name)
        "shared_dir": "",            # a folder every teammate can reach (Dropbox, Drive): each monitor drops its ledger there and reads the others
    },
    "naming": {"strip_trailing_underscore": True},   # name_00001_.png -> name_00001.png (ComfyUI's built-in save nodes add the underscore)
    "engines": {
        "api_nodes_extra": [],        # class_type names that mean "partner API node" besides the built-in list
        "generator_nodes_extra": [],  # class_type names whose start marks the beginning of generation (timing)
    },
}

BUILTIN_API_NODES = {"NanoBananaBatch", "NanoBanana2Batch", "NanoBananaProBatch", "GeminiImage2Node", "GeminiNanoBanana2", "GeminiNanoBanana2V2",
                     "OpenAIGPTImageNodeV2", "OpenAIGPTImage1", "ByteDanceSeedreamNodeV2", "Flux2ImageNode", "Flux2ProImageNode",
                     "Flux2MaxImageNode", "ClaudeNode", "GeminiNode", "GeminiImageNode", "KlingTextToVideoNode", "KlingImage2VideoNode",
                     "VeoVideoGenerationNode", "ByteDanceSeedanceNode", "ByteDanceSeedance25Node", "ByteDance2FirstLastFrameNode", "ByteDance2ReferenceNodeV2",
                     "ByteDance2ReferenceNode", "ByteDance2TextToVideoNode", "ByteDanceSeedance2ReferenceBatch", "ByteDanceImageToVideoNode",
                     "ByteDanceFirstLastFrameNode", "ByteDanceTextToVideoNode", "ByteDanceSeedreamNode", "ByteDanceSeedreamNodeV3"}


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def fmt(pattern, **vars):
    """Substitute placeholders; empty segments (an unused {version}) are dropped, "a//b" never appears."""
    if not isinstance(pattern, str):
        return pattern
    s = pattern.format_map(_Safe({k: ("" if v is None else v) for k, v in vars.items()}))
    lead = s.startswith("/")
    parts = [p for p in s.split("/") if p != ""]
    return ("/" if lead else "") + "/".join(parts)


def deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def expand(p, vars):
    if not p:
        return ""
    return os.path.normpath(os.path.expanduser(fmt(p, **vars)))


def version_number(name):
    m = re.search(r"\d+", name or "")
    return int(m.group(0)) if m else None


def _derive(cfg):
    pr = cfg["project"]
    cfg["_root"], cfg["_monitor"] = ROOT, MON
    base = {"project": ROOT, "monitor": MON, "code": pr.get("code", ""), "client": pr.get("client", ""), "title": pr.get("title", "")}
    paths = cfg["paths"]
    base["output_root"] = expand(paths["output_root"], base)
    base["selects_root"] = expand(paths.get("selects_root") or "", base)
    cfg["_vars"] = base
    cfg["_paths"] = {
        "output_root": base["output_root"], "selects_root": base["selects_root"],
        "comfy_input": expand(paths["comfy_input"], base), "comfy_bin": expand(paths["comfy_bin"], base),
        "runs": expand(paths["runs"], base), "workflows": expand(paths["workflows"], base), "exports": expand(paths["exports"], base),
        "credit_log": expand(cfg["costs"].get("credit_log") or "", base),
        "moved_lookup": [expand(p, base) for p in paths.get("moved_lookup") or []],
    }
    v = cfg["versions"]
    if v.get("enabled"):
        n = version_number(v.get("current")) or 1
        first = int(v.get("first") or 1)
        cfg["_cur"] = v["folder"].format(n=n)
        cfg["_cur_n"] = n
        cfg["_versions"] = [v["folder"].format(n=k) for k in range(n, first - 1, -1)]
    else:
        cfg["_cur"], cfg["_cur_n"], cfg["_versions"] = "", None, []
    for col in cfg["collections"]:
        col.setdefault("kind", "shots" if col.get("sections") is not None else "folders")
        col.setdefault("sections", [{"id": "option", "label": "Options", "sub": "", "color": col.get("color", "#ffcf33")}] if col["kind"] == "shots" else [])
        col.setdefault("select", {"mode": "tag"})
        col["versioned"] = bool(v.get("enabled")) and ("{version}" in (col.get("dir") or "") or "{staging}" in (col.get("dir") or ""))
        col["_grx"] = re.compile(col.get("group_regex") or ".*")   # eager: listings need it before any run is indexed
    cfg["costs"]["client"] = cfg["costs"].get("client") or pr.get("client") or ""
    cfg["costs"]["project"] = cfg["costs"].get("project") or pr.get("name") or pr.get("code") or ""
    return cfg


_cache = {"mtime": None, "cfg": None}


def raw():
    """The file as written (no defaults), {} when it does not exist yet."""
    try:
        with open(PATH) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def load(fresh=False):
    mt = os.path.getmtime(PATH) if os.path.exists(PATH) else None
    if not fresh and _cache["cfg"] is not None and _cache["mtime"] == mt:
        return _cache["cfg"]
    cfg = _derive(deep_merge(copy.deepcopy(DEFAULTS), raw()))
    _cache.update(mtime=mt, cfg=cfg)
    return cfg


def save(data):
    """Write project.json (the public keys only; derived "_" keys are dropped). Returns the validation errors, [] when saved."""
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    merged = _derive(deep_merge(copy.deepcopy(DEFAULTS), copy.deepcopy(clean)))
    errs = validate(merged)
    if errs:
        return errs
    tmp = PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(clean, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, PATH)
    _cache["cfg"] = None
    return []


def validate(cfg):
    errs = []
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", cfg["project"].get("code") or ""):
        errs.append("project.code: letters, digits, - and _ only (it names folders and files)")
    if not isinstance(cfg["server"].get("port"), int) or not 1024 <= cfg["server"]["port"] <= 65535:
        errs.append("server.port: an integer between 1024 and 65535")
    v = cfg["versions"]
    if v.get("enabled"):
        if version_number(v.get("current")) is None:
            errs.append("versions.current: must contain the version number (V3)")
        if "{n}" not in (v.get("folder") or ""):
            errs.append("versions.folder: must contain {n}")
    ids = [c.get("id") for c in cfg["collections"]]
    if len(ids) != len(set(ids)) or not all(ids):
        errs.append("collections: every entry needs a unique id")
    for col in cfg["collections"]:
        if col.get("kind") not in ("shots", "folders"):
            errs.append(f"collections[{col.get('id')}].kind: shots or folders")
        try:
            re.compile(col.get("group_regex") or "")
        except re.error as e:
            errs.append(f"collections[{col.get('id')}].group_regex: {e}")
        sel = col.get("select") or {}
        if sel.get("mode") not in (None, "tag", "copy"):
            errs.append(f"collections[{col.get('id')}].select.mode: tag or copy")
        if sel.get("mode") == "copy" and not sel.get("dest"):
            errs.append(f"collections[{col.get('id')}].select.dest: required for copy mode")
        if sel.get("unselect") not in (None, "z_old", "delete", "keep"):
            errs.append(f"collections[{col.get('id')}].select.unselect: z_old, delete or keep")
        subs = [s.get("sub") for s in col.get("sections") or []]
        if col.get("kind") == "shots" and subs.count("") != 1:
            errs.append(f"collections[{col.get('id')}].sections: exactly one section with sub \"\" (the base section)")
        for sec in col.get("sections") or []:
            try:
                re.compile(sec.get("file_regex") or "")
            except re.error as e:
                errs.append(f"collections[{col.get('id')}].sections[{sec.get('id')}].file_regex: {e}")
        by_sub = {}
        for sec in col.get("sections") or []:
            by_sub.setdefault(sec.get("sub") or "", []).append(sec)
        for sub, secs in by_sub.items():
            if len(secs) > 1 and sum(1 for x in secs if not x.get("file_regex")) > 1:
                errs.append(f"collections[{col.get('id')}].sections: several sections share the folder '{sub}' without a file_regex to tell them apart")
    pr = cfg.get("pairs") or {}
    if pr.get("enabled"):
        if not pr.get("dest"):
            errs.append("pairs.dest: required when pairs are enabled")
        if not (isinstance(pr.get("names"), list) and len(pr["names"]) == 2):
            errs.append("pairs.names: two name patterns (source, image)")
        if pr.get("unpair") not in (None, "z_old", "delete", "keep"):
            errs.append("pairs.unpair: z_old, delete or keep")
    for rx, _ in cfg["runs"].get("ref_roles") or []:
        try:
            re.compile(rx)
        except re.error as e:
            errs.append(f"runs.ref_roles: bad regex {rx!r}: {e}")
    for ex in cfg["runs"].get("extra_categories") or []:
        try:
            re.compile(ex.get("regex") or "")
        except re.error as e:
            errs.append(f"runs.extra_categories[{ex.get('id')}]: {e}")
    return errs


# ---- path patterns -------------------------------------------------------------------------------------------------

def version_vars(cfg, ver=None):
    """Placeholder values for a version folder name ("V3"; None = the current one; "" = no version)."""
    v = cfg["versions"]
    if not v.get("enabled") or ver == "":
        return {"version": "", "staging": "", "n": "", "file_suffix": ""}
    name = ver or cfg["_cur"]
    n = version_number(name) or cfg["_cur_n"]
    return {"version": v["folder"].format(n=n), "staging": v["staging"].format(n=n), "n": n, "file_suffix": v["file_suffix"].format(n=n)}


def col_dir(cfg, col, ver=None):
    """Absolute folder of a collection (its version folder for a versioned one)."""
    return os.path.join(cfg["_paths"]["output_root"], fmt(col.get("dir") or "", **cfg["_vars"], **version_vars(cfg, ver)))


def col_rel(cfg, col, ver=None):
    """The collection folder relative to output_root ("V3", "References", "")."""
    return fmt(col.get("dir") or "", **cfg["_vars"], **version_vars(cfg, ver))


def section(col, sid):
    return next((s for s in col.get("sections") or [] if s["id"] == sid), None)


def section_for_file(col, sub, name):
    """The section id of a file in subfolder `sub`: among the sections sharing that folder, the first whose `file_regex`
    matches the name (without extension), else the first without a regex (the folder's default). None: no such section."""
    stem = os.path.splitext(name or "")[0]
    cands = [s for s in col.get("sections") or [] if (s.get("sub") or None) == sub]
    for s in cands:
        if s.get("file_regex") and re.search(s["file_regex"], stem):
            return s["id"]
    return next((s["id"] for s in cands if not s.get("file_regex")), None)


def section_accepts(col, s, name):
    """Does section `s` list file `name` of its folder? (file_regex sections take their matches; the default takes the rest.)"""
    return section_for_file(col, s.get("sub") or None, name) == s["id"]


def base_section(col):
    return next((s for s in col.get("sections") or [] if not s.get("sub")), None)


def _vpat(cfg):
    """Regex for a version folder or its staging folder in an output path: (?:V(\\d+)_Pages|V(\\d+))."""
    v = cfg["versions"]
    f = re.escape(v["folder"]).replace(r"\{n\}", r"(\d+)")
    s = re.escape(v["staging"]).replace(r"\{n\}", r"(\d+)")
    return f"(?:{s}|{f})" if s != f else f


def matcher(cfg, col):
    """Compiled regex that recognizes an output path (relative to a server's output root, prefix stripped or not) as a file
    of this collection: groups "group", "sub" (section subfolder or None), "file", and version number via vnum(m)."""
    if "_rx" in col:
        return col["_rx"]
    d = col.get("dir") or ""
    if cfg["versions"].get("enabled"):
        d = re.escape(fmt(d, **cfg["_vars"], version="\x00", staging="\x00")).replace("\x00", _vpat(cfg))
    else:
        d = re.escape(fmt(d, **cfg["_vars"], version="", staging=""))
    subs = list(dict.fromkeys(re.escape(s["sub"]) for s in col.get("sections") or [] if s.get("sub")))
    sub = f"(?:(?P<sub>{'|'.join(subs)})/)?" if subs else ""
    depth = col.get("depth", 1) if col.get("kind") == "folders" else 1
    group = r"[^/]+" if depth == 1 else r"[^/]+(?:/[^/]+)?" if depth == 2 else r".+?"
    code = re.escape(cfg["project"].get("code") or "")
    head = f"(?:^|/){d}/" if d else (f"^(?:{code}/)?" if code else "^")
    col["_rx"] = re.compile(f"{head}(?P<group>{group})/{sub}(?P<file>[^/]+)$")
    col["_grx"] = re.compile(col.get("group_regex") or ".*")
    return col["_rx"]


def vnum(m):
    nums = [g for g in m.groups() if g and g.isdigit()] if m else []
    return int(nums[0]) if nums else None


def classify_path(cfg, rel):
    """(collection, group, section id, version folder or "") for an output path, or None."""
    cols = sorted(cfg["collections"], key=lambda c: 0 if c.get("dir") else 1)  # explicit folders before the catch-all
    tops = {fmt(c.get("dir") or "", **cfg["_vars"], version="", staging="").split("/")[0] for c in cfg["collections"] if c.get("dir")}
    for col in cols:
        m = matcher(cfg, col).search(rel or "")
        if not m:
            continue
        group = m.group("group")
        if col.get("kind") == "shots" and not col["_grx"].match(group):
            continue
        if not col.get("dir") and group.split("/")[0] in tops:
            continue
        if col["versioned"]:
            n = vnum(m)
            if n is None:
                continue
            ver = cfg["versions"]["folder"].format(n=n)
        else:
            ver = ""
        sub = m.group("sub") if "sub" in m.groupdict() else None
        sid = section_for_file(col, sub, m.group("file"))
        if sid is None and col.get("kind") == "shots":
            continue
        return col, group, sid, ver
    return None


def group_dir(cfg, col, group, sid=None, ver=None):
    s = section(col, sid) if sid else None
    return os.path.join(col_dir(cfg, col, ver), group, (s or {}).get("sub") or "")


def staging_rel(cfg, col, group, sid=None, ver=None):
    """Where a remote server saves this collection's files (relative to its output root, after output_prefix)."""
    vv = version_vars(cfg, ver)
    d = fmt(col.get("dir") or "", **cfg["_vars"], **{**vv, "version": vv["staging"] or vv["version"]})
    s = section(col, sid) if sid else None
    return "/".join(p for p in (d, group, (s or {}).get("sub") or "") if p)


def select_dest(cfg, col, group, sid=None, ver=None):
    """Absolute destination folder for a copy-mode select, or None for tag mode."""
    sel = col.get("select") or {}
    if sel.get("mode") != "copy":
        return None
    pat = (sel.get("section_dest") or {}).get(sid) or sel.get("dest")
    g = (sel.get("dest_map") or {}).get(group, group)
    return expand(pat, {**cfg["_vars"], **version_vars(cfg, ver), "group": g, "section": sid or ""})


def pair_dest(cfg, col, group, sid=None, ver=None):
    """Absolute folder the final pairs of a group go to, or None when pairs are off."""
    pr = cfg.get("pairs") or {}
    if not pr.get("enabled") or not pr.get("dest"):
        return None
    s = section(col, sid) if sid else None
    shot = os.path.basename((s or {}).get("sub") or "") or group
    return expand(pr["dest"], {**cfg["_vars"], **version_vars(cfg, ver), "group": group, "section": sid or "", "shot": shot})


def category_style(cfg):
    """{category id: {label, color}} for the UI: one per collection and per section, plus extra run categories."""
    out = {}
    for col in cfg["collections"]:
        out[col["id"]] = {"label": col["label"].rstrip("s").lower() if col.get("kind") == "shots" else col["label"].lower(), "color": col.get("color", "#8b95a3")}
        for s in col.get("sections") or []:
            if s.get("sub"):
                out[f"{col['id']}:{s['id']}"] = {"label": f"{out[col['id']]['label']} {s['label'].lower().rstrip('s')}", "color": s.get("color", col.get("color", "#8b95a3"))}
    for ex in cfg["runs"].get("extra_categories") or []:
        out[ex["id"]] = {"label": ex.get("label", ex["id"]), "color": ex.get("color", "#8b95a3")}
    out["other"] = {"label": "other", "color": "#8b95a3"}
    return out


_servers = {"mtime": None, "data": None}


def servers():
    """servers.json, re-read when it changes."""
    mt = os.path.getmtime(SERVERS_PATH)
    if _servers["data"] is None or _servers["mtime"] != mt:
        with open(SERVERS_PATH) as fh:
            data = json.load(fh)
        for s in data.values():   # servers.json may use ~ like project.json does
            if isinstance(s, dict) and isinstance(s.get("output_root"), str):
                s["output_root"] = os.path.expanduser(s["output_root"])
        _servers.update(mtime=mt, data=data)
    return _servers["data"]


def public(cfg):
    """What /api/config returns: the config plus the derived fields the page needs."""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    out["_versions"], out["_cur"], out["_paths"] = cfg["_versions"], cfg["_cur"], cfg["_paths"]
    out["_root"], out["_monitor"], out["_categories"] = cfg["_root"], cfg["_monitor"], category_style(cfg)
    out["_config_path"], out["_servers_path"] = PATH, SERVERS_PATH
    out["collections"] = [{k: v for k, v in col.items() if not k.startswith("_")} for col in cfg["collections"]]
    return out


if __name__ == "__main__":
    if "--write-example" in sys.argv:  # keep monitor/project.example.json in step with DEFAULTS
        ex = copy.deepcopy(DEFAULTS); ex.pop("template_version", None)
        with open(os.path.join(MON, "project.example.json"), "w") as fh:
            json.dump(ex, fh, indent=1); fh.write("\n")
        print("wrote monitor/project.example.json"); sys.exit(0)
    c = load(fresh=True)
    errs = validate(c)
    print(json.dumps({"config": PATH, "errors": errs, "paths": c["_paths"], "versions": c["_versions"], "current": c["_cur"],
                      "collections": [(x["id"], x["kind"], col_rel(c, x), x["versioned"]) for x in c["collections"]]}, indent=1))
