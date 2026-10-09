#!/usr/bin/env python3
"""Keep ComfyUI output filenames free of the trailing underscore.

ComfyUI's built-in save nodes (SaveImage, SaveVideo, SaveAudio*, SaveWEBM, SaveAnimated*,
SaveGLB...) hard-code `<prefix>_00001_.<ext>`. The convention is: no trailing underscore.

  comfy_clean_names.py graph  in.api.json [-o out.api.json] [--server local|comfy4|comfy5]
      Before a run. Rewrites every built-in SaveImage node in an API-format graph into
      WAS "Image Save" set up like the "Save Image" group node (-> <prefix>_001.png).
      Lists the save nodes it could not convert (video, audio, tokens, wired prefixes);
      those need `outputs` after the run. Without -o the file is rewritten in place.

  comfy_clean_names.py outputs <prompt_id> [...] [--server local|comfy4|comfy5]
                               [--copy-to DIR] [--dry-run]
      After a run. Looks the job up in /history and removes the trailing underscore from
      every output file (`name_00001_.mp4` -> `name_00001.mp4`; if that name is taken, the
      next free number). local: renames in ~/ComfyUI/output, and copies to --copy-to if given.
      comfy4/comfy5: needs --copy-to; downloads through the tunnel under the clean name
      (the copy on the server keeps its name).

  comfy_clean_names.py path <file-or-dir> [...] [--recursive] [--dry-run]
      Same rename for files already on disk (wrapper runs, older batches).

Prints one JSON object: what was converted / renamed / left alone.
"""
import argparse, json, os, re, shutil, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVERS = json.load(open(os.path.join(HERE, "servers.json")))

# <anything>_<digits>_  (the built-in nodes' counter followed by the stray underscore)
TRAILING = re.compile(r"^(?P<base>.*)_(?P<num>\d+)_$")

# Built-in save nodes that write `<prefix>_<counter>_.<ext>` and have no clean-name option.
CORE_SAVERS = {"SaveImage", "SaveImageAdvanced", "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveSVGNode",
               "SaveVideo", "SaveWEBM", "SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced",
               "SaveGLB", "Save3DAdvanced", "SaveImageWithAlpha", "SaveImageKJ"}

# The "Save Image" group node = WAS Image Save with these settings.
WAS_DEFAULTS = {"filename_delimiter": "_", "filename_number_padding": 3, "filename_number_start": "false",
                "extension": "png", "dpi": 300, "quality": 100, "optimize_image": "true",
                "lossless_webp": "false", "overwrite_mode": "false", "show_history": "false",
                "show_history_by_prefix": "true", "embed_workflow": "true", "show_previews": "true"}


def port_of(server):
    if server not in SERVERS:
        sys.exit(f"unknown server '{server}' (have: {', '.join(SERVERS)})")
    return SERVERS[server]["port"]


def get_json(port, path, timeout=20):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.load(r)


def clean_name(filename):
    """'a_00001_.png' -> 'a_00001.png'; None when the name has no trailing underscore."""
    stem, ext = os.path.splitext(filename)
    m = TRAILING.match(stem)
    return f"{m['base']}_{m['num']}{ext}" if m else None


def free_name(directory, wanted, taken=()):
    """`wanted`, or the same name with the next free counter if it already exists."""
    if not os.path.exists(os.path.join(directory, wanted)) and wanted not in taken:
        return wanted
    stem, ext = os.path.splitext(wanted)
    base, num = stem.rsplit("_", 1)
    pat = re.compile(rf"^{re.escape(base)}_(\d+){re.escape(ext)}$")
    used = [int(m[1]) for f in list(os.listdir(directory)) + list(taken) if (m := pat.match(f))]
    n = max(used + [int(num)]) + 1
    while True:
        cand = f"{base}_{n:0{len(num)}}{ext}"
        if not os.path.exists(os.path.join(directory, cand)) and cand not in taken:
            return cand
        n += 1


# ---------------------------------------------------------------- graph

def cmd_graph(a):
    g = json.load(open(a.workflow))
    if not isinstance(g, dict) or "nodes" in g or not all(isinstance(v, dict) and "class_type" in v for v in g.values()):
        sys.exit("not an API-format graph (UI exports are converted by comfy-cli at run time); "
                 "run it as is and use `outputs <prompt_id>` afterwards")
    try:
        have_was = "Image Save" in get_json(port_of(a.server), "/object_info/Image%20Save")
    except Exception as e:
        sys.exit(f"cannot reach {a.server} to check for the WAS Image Save node: {e}")
    converted, left = [], []
    for nid, node in g.items():
        ct = node["class_type"]
        if ct not in CORE_SAVERS:
            continue
        prefix = node.get("inputs", {}).get("filename_prefix", "ComfyUI")
        why = None
        if ct != "SaveImage":
            why = "no clean-name node for this output type"
        elif not have_was:
            why = f"WAS 'Image Save' is not installed on {a.server}"
        elif not isinstance(prefix, str):
            why = "filename_prefix is wired from another node"
        elif "%" in prefix:
            why = "filename_prefix uses %tokens%"
        if why:
            left.append({"node": nid, "class_type": ct, "filename_prefix": prefix if isinstance(prefix, str) else "(wired)", "why": why})
            continue
        folder, name = os.path.split(prefix.strip("/"))
        node["class_type"] = "Image Save"
        node["inputs"] = {"images": node["inputs"]["images"], "output_path": folder, "filename_prefix": name or "ComfyUI", **WAS_DEFAULTS}
        node.setdefault("_meta", {})["title"] = "Save Image (clean name)"
        converted.append({"node": nid, "output_path": folder, "filename_prefix": name, "saves_as": f"{name}_001.png"})
    out = a.out or a.workflow
    if converted:
        json.dump(g, open(out, "w"), indent=2)
    print(json.dumps({"workflow": out if converted else a.workflow, "converted": converted, "left": left,
                      "next": "run `outputs <prompt_id>` after the run" if left else "all saves are clean"}, indent=1))


# ---------------------------------------------------------------- outputs

def history_files(port, pid):
    h = get_json(port, f"/history/{pid}")
    if pid not in h:
        sys.exit(f"prompt {pid} is not in this server's history (still running, or the wrong server)")
    seen, files = set(), []
    for node_out in h[pid].get("outputs", {}).values():
        for items in node_out.values():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and it.get("filename") and it.get("type", "output") == "output":
                    key = (it.get("subfolder", ""), it["filename"])
                    if key not in seen:
                        seen.add(key); files.append(key)
    return sorted(files)


def cmd_outputs(a):
    port = port_of(a.server)
    cfg = SERVERS[a.server]
    local = cfg["kind"] == "local"
    if not local and not a.copy_to:
        sys.exit(f"{a.server} is remote: give --copy-to DIR (files are downloaded under the clean name)")
    if a.copy_to and not a.dry_run:
        os.makedirs(a.copy_to, exist_ok=True)
    root = os.path.expanduser(cfg.get("output_dir", "")) if local else None
    renamed, unchanged, missing, copied, taken, taken_copy = [], [], [], [], set(), set()
    for pid in a.prompt_ids:
        for sub, fn in history_files(port, pid):
            want = clean_name(fn)
            final = fn
            if local:
                d = os.path.join(root, sub)
                if not os.path.exists(os.path.join(d, fn)):
                    missing.append(os.path.join(sub, fn)); continue
                if want:
                    final = free_name(d, want, taken)
                    taken.add(final)
                    if not a.dry_run:
                        os.rename(os.path.join(d, fn), os.path.join(d, final))
                    renamed.append({"from": os.path.join(sub, fn), "to": os.path.join(sub, final)})
                else:
                    unchanged.append(os.path.join(sub, fn))
                if a.copy_to:
                    dest = free_name(a.copy_to, final, taken_copy) if os.path.isdir(a.copy_to) else final
                    taken_copy.add(dest)
                    if not a.dry_run:
                        shutil.copy2(os.path.join(d, final), os.path.join(a.copy_to, dest))
                    copied.append(os.path.join(a.copy_to, dest))
            else:
                dest = free_name(a.copy_to, want or fn, taken_copy) if os.path.isdir(a.copy_to) else (want or fn)
                taken_copy.add(dest)
                if not a.dry_run:
                    q = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": "output"})
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/view?{q}", timeout=600) as r, \
                            open(os.path.join(a.copy_to, dest), "wb") as f:
                        shutil.copyfileobj(r, f)
                copied.append(os.path.join(a.copy_to, dest))
                (renamed if want else unchanged).append({"from": os.path.join(sub, fn), "to": dest} if want else os.path.join(sub, fn))
    print(json.dumps({"server": a.server, "dry_run": a.dry_run, "renamed": renamed, "unchanged": unchanged,
                      "missing": missing, "copied": copied}, indent=1))


# ---------------------------------------------------------------- path

def cmd_path(a):
    todo = []
    for p in a.paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            walk = os.walk(p) if a.recursive else [(p, [], os.listdir(p))]
            for d, _, fs in walk:
                todo += [(d, f) for f in sorted(fs) if os.path.isfile(os.path.join(d, f))]
        elif os.path.isfile(p):
            todo.append(os.path.split(os.path.abspath(p)))
        else:
            sys.exit(f"no such file or folder: {p}")
    renamed, taken = [], set()
    for d, f in todo:
        want = clean_name(f)
        if not want:
            continue
        final = free_name(d, want, taken); taken.add(final)
        if not a.dry_run:
            os.rename(os.path.join(d, f), os.path.join(d, final))
        renamed.append({"from": os.path.join(d, f), "to": os.path.join(d, final)})
    print(json.dumps({"dry_run": a.dry_run, "renamed": renamed, "count": len(renamed)}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("graph"); g.add_argument("workflow"); g.add_argument("-o", "--out"); g.add_argument("--server", default="local")
    o = sp.add_parser("outputs"); o.add_argument("prompt_ids", nargs="+"); o.add_argument("--server", default="local")
    o.add_argument("--copy-to"); o.add_argument("--dry-run", action="store_true")
    p = sp.add_parser("path"); p.add_argument("paths", nargs="+"); p.add_argument("--recursive", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    {"graph": cmd_graph, "outputs": cmd_outputs, "path": cmd_path}[a.cmd](a)


if __name__ == "__main__":
    main()
