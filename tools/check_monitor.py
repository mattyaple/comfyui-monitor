#!/usr/bin/env python3
"""Check a project's monitor: config, paths, tools, leftovers from the template, and a smoke test of the server.

  python3 tools/check_monitor.py [--target /path/to/project] [--no-smoke] [--strict]

Run it after setting a monitor up (new_monitor.py) and after editing project.json by hand. Lines:
  ✓ fine   ! worth a look (the monitor works)   ✗ broken (fix before using the monitor)
Exit code 1 when anything is ✗ (or, with --strict, when anything is !).
"""
import argparse, json, os, re, shutil, socket, subprocess, sys, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--target", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap.add_argument("--no-smoke", action="store_true")
ap.add_argument("--strict", action="store_true")
args = ap.parse_args()
ROOT = os.path.abspath(args.target); MON = os.path.join(ROOT, "monitor")
sys.path.insert(0, MON)
os.environ.pop("MONITOR_CONFIG", None); os.environ.pop("MONITOR_SERVERS", None)
bad = warn = 0


def ok(msg):
    print("  ✓", msg)


def hm(msg):
    global warn; warn += 1; print("  !", msg)


def no(msg):
    global bad; bad += 1; print("  ✗", msg)


print(f"monitor check: {ROOT}")
missing = [f for f in ("config.py", "server.py", "run.py", "collect.py", "export.py", "credits.py", "costs.py", "tags.py", "marks.py", "tunnel.py", "index.html") if not os.path.isfile(os.path.join(MON, f))]
if missing:
    no(f"monitor code missing: {', '.join(missing)} (run tools/new_monitor.py --update from the template)")
    sys.exit(1)
import config  # noqa: E402

# ---- config
if config.USING_EXAMPLE:
    hm(f"no monitor/project.json: running on the example config {config.PATH} (new_monitor.py writes a real one)")
try:
    cfg = config.load(fresh=True)
except Exception as e:
    no(f"project.json does not load: {e}"); sys.exit(1)
errs = config.validate(cfg)
for e in errs:
    no("project.json: " + e)
if not errs:
    ok(f"project.json valid · {cfg['project']['name']} ({cfg['project']['code']}) · template {cfg.get('template_version')} · unit \"{cfg['terms']['unit']}\"")
tv = config.TEMPLATE_VERSION
if cfg.get("template_version") and cfg["template_version"] != tv:
    hm(f"project.json was written by template {cfg['template_version']}, the code is {tv} (new_monitor.py --update stamps it)")
raw = json.dumps(config.raw())
for ph in ("My Project", '"PROJ"', "PROJ/"):
    if ph in raw:
        hm(f"placeholder still in project.json: {ph}")
if not cfg["project"].get("client"):
    hm("project.client is empty (used in export footers and the credit log)")

# ---- leftovers from the project the template came from: none of these belong in generic code
words = ["UWNYC", "uwnyc", "mattyaple", "United Way", "Javier", "storybook"]
for rel in ("monitor/server.py", "monitor/run.py", "monitor/collect.py", "monitor/config.py", "monitor/index.html", "monitor/export.py", "monitor/costs.py"):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        continue
    txt = open(p, errors="ignore").read()
    hits = [w for w in words if w in txt]
    if hits:
        no(f"{rel} contains project-specific words: {', '.join(hits)}")
ok("monitor code is free of project-specific leftovers") if not bad else None

# ---- paths and tools
P = cfg["_paths"]
for k in ("output_root", "comfy_input"):
    (ok if os.path.isdir(P[k]) else hm)(f"paths.{k}: {P[k]}" + ("" if os.path.isdir(P[k]) else " (does not exist yet)"))
(ok if os.access(P["comfy_bin"], os.X_OK) else no)(f"comfy-cli: {P['comfy_bin']}" + ("" if os.access(P["comfy_bin"], os.X_OK) else " not found or not executable (INSTALL.md)"))
_d = os.path.dirname(P["comfy_bin"])
py = next((c for c in (os.path.join(_d, "python"), os.path.join(_d, "python.exe"), os.path.join(_d, "Scripts", "python.exe"), os.path.expanduser("~/AppData/Roaming/uv/tools/comfy-mcp/Scripts/python.exe")) if os.path.isfile(c)), os.path.join(_d, "python"))
(ok if os.path.isfile(py) else hm)(f"comfy-cli python (credit lookups): {py}" + ("" if os.path.isfile(py) else " missing: cost tracking will not work"))
(ok if shutil.which("uv") else hm)("uv: " + (shutil.which("uv") or "not on PATH (exports need `uv` for reportlab / pillow, or install both for python3)"))
if sys.platform == "darwin":
    (ok if shutil.which("sips") and shutil.which("xattr") else no)("macOS tools sips + xattr (thumbnails, Finder tags)")
else:
    sys.path.insert(0, MON); import server as _srv  # noqa: E402
    (ok if _srv.FFMPEG else no)("ffmpeg (thumbnails on this platform): " + (_srv.FFMPEG or "not found — install ffmpeg (winget install Gyan.FFmpeg)"))
    hm("marks live in _marks.json sidecars next to the files (no Finder tags on this platform)")
for k in ("runs", "exports", "workflows"):
    d = P[k]
    try:
        os.makedirs(d, exist_ok=True); ok(f"paths.{k}: {d}")
    except OSError as e:
        no(f"paths.{k}: {d}: {e}")
if P.get("selects_root"):
    (ok if os.path.isdir(P["selects_root"]) else hm)(f"paths.selects_root: {P['selects_root']}" + ("" if os.path.isdir(P["selects_root"]) else " (not reachable now)"))
if cfg["costs"].get("enabled"):
    (ok if os.path.isfile(P["credit_log"]) else hm)(f"credit log: {P['credit_log']}" + ("" if os.path.isfile(P["credit_log"]) else " (created on the first billed run)"))
    sd = cfg["costs"].get("shared_dir")
    if sd:
        d = config.expand(sd, cfg["_vars"]); (ok if os.path.isdir(d) else hm)(f"costs.shared_dir: {d}" + ("" if os.path.isdir(d) else " (not reachable now)"))

# ---- collections
for col in cfg["collections"]:
    d = config.col_dir(cfg, col)
    (ok if os.path.isdir(d) else hm)(f"collection {col['id']} ({col['kind']}, select {col.get('select', {}).get('mode', 'tag')}): {d}" + ("" if os.path.isdir(d) else " (not created yet)"))
    if col.get("select", {}).get("mode") == "copy":
        dest = config.select_dest(cfg, col, "EXAMPLE", None)
        if "{" in (dest or ""):
            no(f"collection {col['id']}: select.dest has an unresolved placeholder: {dest}")

# ---- servers
try:
    sv = config.servers()
    (hm if config.SERVERS_PATH.endswith(".example.json") else ok)(f"servers: {config.SERVERS_PATH} · {', '.join(sv)}")
    for n, s in sv.items():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{s['port']}/system_stats", timeout=2) as r:
                ok(f"server {n}: ComfyUI answers on port {s['port']}")
        except Exception:
            hm(f"server {n}: nothing on port {s['port']} now ({'start ComfyUI' if s.get('kind', 'local') == 'local' else 'tunnel down or instance stopped'})")
except Exception as e:
    no(f"servers.json: {e}")

# ---- port
port = cfg["server"]["port"]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
        other = json.load(r)
    if other.get("project", {}).get("code") == cfg["project"]["code"]:
        ok(f"monitor already running on port {port} (this project)")
    else:
        no(f"port {port} is used by another monitor ({other.get('project', {}).get('code')}); change server.port")
except Exception:
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            no(f"port {port} is taken by something that is not a monitor; change server.port")
        else:
            ok(f"port {port} is free")

# ---- smoke test: start the server on a spare port and hit the main endpoints
if not args.no_smoke:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); spare = s.getsockname()[1]
    log = open(os.path.join(MON, "check_smoke.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(MON, "server.py"), str(spare)], cwd=ROOT, stdout=log, stderr=log)
    try:
        up = False
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{spare}/api/config", timeout=1); up = True; break
            except Exception:
                time.sleep(0.25)
        if not up:
            no("smoke: the server did not start (monitor/check_smoke.log)")
        else:
            for path in ["/", "/api/config", "/api/runs", "/api/queue", "/api/selects", "/api/exports", "/api/costs"] + [f"/api/collection?id={c['id']}" for c in cfg["collections"]]:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{spare}{path}", timeout=20) as r:
                        body = r.read()
                    j = json.loads(body) if path != "/" else {}
                    if isinstance(j, dict) and j.get("error") and "ok" in j and not j["ok"]:
                        no(f"smoke {path}: {j['error']}")
                    else:
                        extra = f" · {len(j.get('runs', []))} runs" if path == "/api/runs" else f" · {len(j.get('groups', []))} groups" if path.startswith("/api/collection") else ""
                        ok(f"smoke {path}{extra}")
                except Exception as e:
                    no(f"smoke {path}: {e}")
    finally:
        p.terminate(); log.close()
        try:
            os.remove(os.path.join(MON, "check_smoke.log"))
        except OSError:
            pass

print(f"\n{bad} problem(s), {warn} note(s)")
sys.exit(1 if bad or (args.strict and warn) else 0)
