#!/usr/bin/env python3
"""Set up (or update) a project's run monitor from this template.

  python3 tools/new_monitor.py --target /path/to/project [--answers answers.json] [--preset shots|pages|simple] [--dry-run]
  python3 tools/new_monitor.py --target /path/to/project --update        # refresh the code files, keep config and runs
  python3 tools/new_monitor.py --target /path/to/project                 # no answers file + a terminal: asks the questions

What it does on a first setup
  1. copies the monitor code (monitor/*.py, index.html, start scripts) and the generic tools into <target>/monitor and <target>/tools
  2. writes <target>/monitor/project.json from the preset merged with the answers (docs/CONFIG.md explains every key)
  3. writes <target>/monitor/servers.json (local ComfyUI only, unless the answers list remote servers)
  4. creates <target>/monitor/runs, <target>/exports, <target>/workflows
  5. writes <target>/docs/MONITOR.md (how to use this project's monitor) and prints the CLAUDE.md paragraph to add
Existing project.json / servers.json / runs are never overwritten (use --force to replace the config).

Answers file: see docs/SETUP_GUIDE.md ("The answers file"). Every key is optional; missing ones take the preset's value.
"""
import argparse, json, os, re, shutil, sys

TEMPLATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = ["monitor/config.py", "monitor/server.py", "monitor/run.py", "monitor/watch.py", "monitor/marks.py", "monitor/collect.py", "monitor/export.py", "monitor/credits.py",
        "monitor/costs.py", "monitor/tags.py", "monitor/tunnel.py", "monitor/index.html", "monitor/start.py", "monitor/start.command", "monitor/VERSION",
        "tools/selects_index.py", "tools/check_monitor.py", "tools/marks_migrate.py"]
sys.path.insert(0, os.path.join(TEMPLATE, "monitor"))
import config as cfgmod  # noqa: E402


def preset(name, a):
    """A full project.json for a preset, before the answers are applied."""
    import copy
    c = copy.deepcopy(cfgmod.DEFAULTS); c.pop("template_version", None)
    unit, units = a.get("terms", {}).get("unit", "shot"), a.get("terms", {}).get("units", "shots")
    c["terms"] = {"unit": unit, "units": units}
    c["collections"][0]["label"] = units[:1].upper() + units[1:]
    if name == "pages":   # versioned rounds, selects copied to a client folder, edits + plates sections
        c["versions"].update(enabled=True, current="V1", first=1, folder="V{n}", staging="V{n}_Staging", file_suffix="_v{n}")
        c["collections"][0]["select"] = {"mode": "copy", "dest": "{selects_root}/{staging}/{group}", "section_dest": {"plate": "{selects_root}/{staging}/{group}/_plates"}, "unselect": "z_old"}
    elif name == "simple":  # one collection, no sections, tag selects
        c["collections"] = [{"id": "shots", "label": units[:1].upper() + units[1:], "kind": "shots", "dir": "", "group_regex": "^[A-Za-z0-9][^/]*$", "color": "#ffcf33",
                             "file_name": "{group}_{n:03d}", "sections": [{"id": "option", "label": "Options", "sub": "", "color": "#ffcf33"}], "select": {"mode": "tag"}}]
    return c


def apply_answers(c, a):
    pr = a.get("project") or {}
    for k in ("name", "code", "client", "title"):
        if pr.get(k):
            c["project"][k] = pr[k]
    c["project"]["code"] = re.sub(r"[^A-Za-z0-9_\-]+", "", c["project"]["code"] or re.sub(r"\W+", "", c["project"]["name"])[:12] or "PROJ")
    c["project"]["title"] = pr.get("title") or f"{c['project']['name']} Run Monitor"
    if a.get("port"):
        c["server"]["port"] = int(a["port"])
    for k in ("output_root", "output_prefix", "comfy_input", "comfy_bin", "selects_root", "workflows", "exports"):
        if a.get(k) is not None:
            c["paths"][k] = a[k]
    if a.get("moved_lookup"):
        c["paths"]["moved_lookup"] = a["moved_lookup"]
    v = a.get("versions") or {}
    if "enabled" in v:
        c["versions"]["enabled"] = bool(v["enabled"])
    for k in ("current", "first", "folder", "staging", "file_suffix", "refuse_frozen"):
        if v.get(k) is not None:
            c["versions"][k] = v[k]
    shots = c["collections"][0]
    shots["dir"] = "{version}" if c["versions"]["enabled"] else ""
    if a.get("group_regex"):
        shots["group_regex"] = a["group_regex"]
    if a.get("group_label"):
        shots["label"] = a["group_label"]
    secs = a.get("sections")
    if secs == "none":
        shots["sections"] = [{"id": "option", "label": "Options", "sub": "", "color": shots["color"]}]
    elif isinstance(secs, list) and secs:
        shots["sections"] = [{"id": "option", "label": "Options", "sub": "", "color": shots["color"]}] + [
            s if isinstance(s, dict) else {"id": re.sub(r"\W+", "", s.lower()), "label": s[:1].upper() + s[1:], "sub": "_" + re.sub(r"\W+", "", s.lower()), "color": "#c792ea"} for s in secs]
    sel = a.get("selects") or {}
    if sel.get("mode"):
        shots["select"]["mode"] = sel["mode"]
    if sel.get("dest"):
        shots["select"]["dest"] = sel["dest"]
    if sel.get("root"):
        c["paths"]["selects_root"] = sel["root"]
    if sel.get("unselect"):
        shots["select"]["unselect"] = sel["unselect"]
    if shots["select"]["mode"] == "copy" and not shots["select"].get("dest"):
        shots["select"]["dest"] = "{selects_root}/{staging}/{group}" if c["versions"]["enabled"] else "{selects_root}/{group}"
    extra = a.get("collections_extra")
    if extra is not None:
        keep = {"shots"} | set(extra)
        c["collections"] = [x for x in c["collections"] if x["id"] in keep or x["id"] == shots["id"]]
    for col in a.get("collections_add") or []:
        c["collections"].append(col)
    co = a.get("costs") or {}
    for k in ("enabled", "person", "client", "project", "budget_usd", "shared_dir", "bill_runs"):
        if co.get(k) is not None:
            c["costs"][k] = co[k]
    ex = a.get("export") or {}
    if "contact" in ex:
        c["export"]["templates"]["contact"]["enabled"] = bool(ex["contact"])
    if "review" in ex:
        c["export"]["templates"]["review"]["enabled"] = bool(ex["review"])
    for k in ("paper", "orientation"):
        if ex.get(k):
            for t in c["export"]["templates"].values():
                t[k] = ex[k]
    if ex.get("footer"):
        c["export"]["footer"] = ex["footer"]
    if "enabled" in ex:
        c["export"]["enabled"] = bool(ex["enabled"])
    return c


def ask(q, default=None, choices=None):
    tail = f" [{'/'.join(choices)}]" if choices else ""
    d = f" ({default})" if default not in (None, "") else ""
    while True:
        v = input(f"{q}{tail}{d}: ").strip()
        if not v and default is not None:
            return default
        if choices and v not in choices:
            print(f"  one of: {', '.join(choices)}"); continue
        if v or default is None:
            return v


def interview(target):
    """The standard setup questions (docs/SETUP_GUIDE.md), on a terminal."""
    a = {"project": {}, "terms": {}, "versions": {}, "selects": {}, "costs": {}, "export": {}}
    print("comfyui-monitor setup. Enter accepts the default in parentheses.\n")
    a["project"]["name"] = ask("1. Project name", os.path.basename(os.path.abspath(target)))
    a["project"]["code"] = ask("   Short code (folders, file names)", re.sub(r"\W+", "", a["project"]["name"])[:12].upper())
    a["project"]["client"] = ask("   Client (for the credit log and export footers)", "")
    a["output_root"] = ask("2. ComfyUI output root for this project", f"~/ComfyUI/output/{a['project']['code']}")
    a["output_prefix"] = ask("   Save-path prefix graphs use", f"{a['project']['code']}/")
    a["terms"]["unit"] = ask("3. What is one unit of work called", "shot")
    a["terms"]["units"] = ask("   Plural", a["terms"]["unit"] + "s")
    a["group_regex"] = ask("   Regex a folder name must match to be one (Enter = any)", "^[A-Za-z0-9][^/]*$")
    s = ask("   Sub-sections inside each one", "edits,plates", None)
    a["sections"] = "none" if s in ("none", "no", "") else [x.strip() for x in s.split(",") if x.strip()]
    v = ask("4. Versioned rounds (V1, V2, …)", "no", ["yes", "no"])
    a["versions"]["enabled"] = v == "yes"
    if a["versions"]["enabled"]:
        a["versions"]["current"] = ask("   Current version", "V1")
    m = ask("5. Selects: Finder tag only, or also copy to a folder", "tag", ["tag", "copy"])
    a["selects"]["mode"] = m
    if m == "copy":
        a["selects"]["root"] = ask("   Folder the selects are copied under (Dropbox, review folder)", "")
    extra = ask("6. Extra collections: references, tests, both or none", "both", ["references", "tests", "both", "none"])
    a["collections_extra"] = {"references": ["refs"], "tests": ["tests"], "both": ["refs", "tests"], "none": []}[extra]
    a["port"] = int(ask("7. Monitor port", "8765"))
    c = ask("8. Track credit costs", "yes", ["yes", "no"])
    a["costs"]["enabled"] = c == "yes"
    if a["costs"]["enabled"]:
        a["costs"]["person"] = ask("   Your name in the team ledger (Enter = macOS account name)", "")
        b = ask("   Budget in USD (Enter = none)", "")
        a["costs"]["budget_usd"] = float(b) if b else None
        a["costs"]["shared_dir"] = ask("   Shared ledger folder for teammates (Enter = none)", "")
    e = ask("9. Exports: contact sheets and review PDFs", "both", ["both", "contact", "review", "none"])
    a["export"] = {"contact": e in ("both", "contact"), "review": e in ("both", "review"), "enabled": e != "none"}
    return a


def monitor_doc(c):
    unit, units = c["terms"]["unit"], c["terms"]["units"]
    port = c["server"]["port"]; col = c["collections"][0]; sel = col.get("select", {}); vs = c["versions"]
    lines = [f"# {c['project']['name']}: run monitor", "",
             f"Local page: http://127.0.0.1:{port} (start with `python3 monitor/start.py`, or double-click `monitor/start.command`).",
             "Built from the comfyui-monitor template; settings live in `monitor/project.json` (Settings tab) and `monitor/servers.json`.", "",
             "## Rules", "",
             f"- Every generation goes through `python3 monitor/run.py <graph.api.json> --title \"what this run tests\" [--{unit} <id>]` so it is logged.",
             f"- Outputs save under `{c['paths']['output_root']}` (graph save paths start with `{c['paths']['output_prefix']}`).",
             f"- One {unit} = one folder `{col.get('dir') or '.'}/<{unit} id>/` under the output root" + (f" (its id must match `{col['group_regex']}`)" if col.get("group_regex") not in (None, "^[A-Za-z0-9][^/]*$") else "") + "." +
             (f" Sections inside it: " + ", ".join(f"`{s['sub']}/` ({s['label']})" for s in col.get("sections", []) if s.get("sub")) + "." if any(s.get("sub") for s in col.get("sections", [])) else ""),
             ]
    if vs.get("enabled"):
        lines.append(f"- Versioned rounds: the current version is **{vs['current']}** (`versions.current` in project.json; the Settings tab's \"Start next version\" bumps it). "
                     f"Older versions are frozen: run.py refuses a graph that saves into one unless `--retarget` is passed. Remote servers save into `{vs['staging']}` folders, collected into `{vs['folder']}`.")
    lines.append(f"- Selects are the user's call, marked in the monitor: Finder's {c['selects']['tags']['select']} tag = select, {c['selects']['tags']['ng']} tag = NG (rejected). "
                 + ("Selects are also copied to `" + sel.get("dest", "") + "`." if sel.get("mode") == "copy" else "Nothing is copied.") +
                 " Read them with `python3 tools/selects_index.py` (writes `monitor/selects_index.json`); never assume a select.")
    other = [x for x in c["collections"][1:]]
    if other:
        lines.append("- Other collections: " + "; ".join(f"**{x['label']}** in `{x.get('dir')}/` ({'copied to ' + x['select']['dest'] if (x.get('select') or {}).get('mode') == 'copy' else 'tag selects'})" for x in other) + ".")
    if c["costs"].get("enabled"):
        lines.append(f"- Costs: run.py logs the billed credits of every API run (client \"{c['costs'].get('client') or c['project'].get('client')}\", project \"{c['costs'].get('project') or c['project']['name']}\") and the Costs tab shows the running total" + (f" against a budget of ${c['costs']['budget_usd']}" if c["costs"].get("budget_usd") else "") + ". Tell the user the cost and balance after each batch.")
    if c["export"].get("enabled", True):
        lines.append("- Exports (contact sheets, review PDFs) are made in the Export tab and land in `exports/`.")
    lines += ["", "## Tabs", "", "Runs (live progress, one card per run, re-roll), Selects (every image per " + unit + ", star / NG, multi-select), Export, Costs, Settings.",
              "Lightbox: ← → images, ↑ ↓ groups, S select, X NG, hold-click compares with the source image, I info, F Finder, Esc.", "",
              "See the template's docs for everything else: ARCHITECTURE.md (data flow, API), CONFIG.md (every project.json key), UI_GUIDE.md."]
    return "\n".join(lines) + "\n"


def claude_paragraph(c):
    unit = c["terms"]["unit"]
    return (f"## Run monitor\n\n"
            f"- Every generation goes through `python3 monitor/run.py <graph.api.json> --title \"...\" [--{unit} <id>]` so it is logged; the monitor is at http://127.0.0.1:{c['server']['port']} (`python3 monitor/start.py`).\n"
            f"- Selects and rejects are Finder tags ({c['selects']['tags']['select']} = select, {c['selects']['tags']['ng']} = NG) set in the monitor; read them with `python3 tools/selects_index.py`, never assume.\n"
            f"- Outputs save under `{c['paths']['output_root']}`; one {unit} per folder. Settings: `monitor/project.json` (Settings tab). Details: `docs/MONITOR.md`.\n"
            + (f"- Report the billed cost and the balance after each batch (the Costs tab tracks the project total).\n" if c["costs"].get("enabled") else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="the project folder (created if missing)")
    ap.add_argument("--answers", help="JSON file with the setup answers (docs/SETUP_GUIDE.md)")
    ap.add_argument("--preset", default="shots", choices=["shots", "pages", "simple"])
    ap.add_argument("--update", action="store_true", help="refresh the code files only; keep project.json, servers.json and runs")
    ap.add_argument("--force", action="store_true", help="overwrite an existing project.json / servers.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    target = os.path.abspath(os.path.expanduser(args.target))
    if os.path.realpath(target) == os.path.realpath(TEMPLATE):
        sys.exit("refusing: the target is the template itself")
    print(f"template {TEMPLATE} (v{cfgmod.TEMPLATE_VERSION}) -> {target}")
    copied = []
    for rel in CODE:
        src, dst = os.path.join(TEMPLATE, rel), os.path.join(target, rel)
        if not os.path.isfile(src):
            print(f"  ! template file missing: {rel}"); continue
        if not args.dry_run:
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(src, dst)
        copied.append(rel)
    print(f"  copied {len(copied)} code files" + (" (dry run)" if args.dry_run else ""))
    for d in ("monitor/runs", "exports", "workflows", "docs"):
        if not args.dry_run:
            os.makedirs(os.path.join(target, d), exist_ok=True)
    if args.update:
        pj = os.path.join(target, "monitor", "project.json")
        if os.path.isfile(pj) and not args.dry_run:
            j = json.load(open(pj)); j["template_version"] = cfgmod.TEMPLATE_VERSION
            json.dump(j, open(pj, "w"), indent=1)
        print("  update done: config, servers and runs untouched. Restart the monitor server.")
        return
    pj = os.path.join(target, "monitor", "project.json"); sj = os.path.join(target, "monitor", "servers.json")
    if os.path.isfile(pj) and not args.force:
        sys.exit(f"  {pj} exists; use --update to refresh the code, or --force to replace the config")
    if args.answers:
        a = json.load(open(args.answers))
    elif sys.stdin.isatty():
        a = interview(target)
    else:
        a = {}
        print("  no answers file and no terminal: writing the preset's defaults; edit them in the Settings tab")
    c = apply_answers(preset(args.preset, a), a)
    c["template_version"] = cfgmod.TEMPLATE_VERSION
    errs = cfgmod.validate(cfgmod.deep_merge(json.loads(json.dumps(cfgmod.DEFAULTS)), json.loads(json.dumps(c))))
    if errs:
        sys.exit("  the resulting config is not valid:\n   " + "\n   ".join(errs))
    servers = {"local": {"kind": "local", "port": 8188, "output_root": c["paths"]["output_root"], "output_prefix": c["paths"]["output_prefix"],
                         "engines": ["api", "flux2", "open_edit", "other"]}}
    for name, s in (a.get("servers") or {}).items():
        servers[name] = s
    if args.dry_run:
        print(json.dumps(c, indent=1)); return
    json.dump(c, open(pj, "w"), indent=1)
    if not os.path.isfile(sj) or args.force:
        json.dump(servers, open(sj, "w"), indent=2)
    with open(os.path.join(target, "docs", "MONITOR.md"), "w") as fh:
        fh.write(monitor_doc(c))
    gi = os.path.join(target, ".gitignore"); lines = open(gi).read() if os.path.isfile(gi) else ""
    add = [l for l in ("monitor/cache/", "monitor/server.log", "monitor/costs_imports/", "monitor/expenses.json", "exports/", "__pycache__/", ".DS_Store") if l not in lines]
    if add:
        with open(gi, "a") as fh:
            fh.write(("\n" if lines and not lines.endswith("\n") else "") + "# run monitor runtime state\n" + "\n".join(add) + "\n")
    print(f"  wrote monitor/project.json, monitor/servers.json, docs/MONITOR.md")
    print("\nAdd this to the project's CLAUDE.md:\n")
    print(claude_paragraph(c))
    print(f"Next: python3 {os.path.join(target, 'tools', 'check_monitor.py')}   then   python3 {os.path.join(target, 'monitor', 'start.py')}")


if __name__ == "__main__":
    main()
