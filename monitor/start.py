#!/usr/bin/env python3
"""Start the monitor server for this project (or tell you it is already running) and open it in the browser.

  python3 monitor/start.py [--no-open] [--foreground]

Without --foreground the server runs detached (log: monitor/server.log) so the terminal is free; stop it with
  python3 monitor/start.py --stop
"""
import json, os, subprocess, sys, time, urllib.request, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import config  # noqa: E402

cfg = config.load(); port = cfg["server"]["port"]; url = f"http://127.0.0.1:{port}"


def up():
    try:
        with urllib.request.urlopen(url + "/api/config", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


PID = os.path.join(HERE, "server.pid")
if "--stop" in sys.argv:
    try:
        pid = int(open(PID).read().strip())
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, 15)
        os.remove(PID); print("stopped")
    except Exception:
        if os.name != "nt":
            subprocess.run(["pkill", "-f", f"monitor/server.py.*{port}|monitor/server.py$"]); print("stopped")
        else:
            print("no server.pid; stop it from the Task Manager (python monitor/server.py)")
    sys.exit(0)
if up():
    print(f"already running: {url}")
elif "--foreground" in sys.argv:
    os.execv(sys.executable, [sys.executable, os.path.join(HERE, "server.py")])
else:
    log = open(os.path.join(HERE, "server.log"), "a")
    kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], cwd=ROOT, stdout=log, stderr=log, **kw)
    open(PID, "w").write(str(proc.pid))
    for _ in range(30):
        if up():
            break
        time.sleep(0.2)
    print(f"{'started' if up() else 'did not come up (see monitor/server.log)'}: {url}")
if "--no-open" not in sys.argv and up():
    webbrowser.open(url)
