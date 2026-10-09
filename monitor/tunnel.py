#!/usr/bin/env python3
"""SSH tunnels to the remote ComfyUI servers listed in monitor/servers.json.

  python3 monitor/tunnel.py up comfy4      # open (or reuse) the tunnel, print the local port
  python3 monitor/tunnel.py status         # every server: instance state and tunnel up/down
  python3 monitor/tunnel.py down comfy4    # close it

A remote server's ComfyUI port is not public; everything goes through `ssh -L <local port>:127.0.0.1:<remote port>`.
Local ports are fixed per server in servers.json, and the control socket name is shared with the comfyui skill's
comfy_tunnel.py, so a tunnel opened by either tool is reused by the other.

Never starts a stopped cloud instance (billing). If it is not running, `up` fails and says so; starting it is the user's call.
Server kinds: "local" (no tunnel), "aws" (instance looked up by Name tag through the AWS CLI), "ssh" (fixed host).
"""
import json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVERS = json.load(open(os.environ.get("MONITOR_SERVERS") or os.path.join(HERE, "servers.json")))


def sock(name):
    return f"/tmp/comfy_tunnel_{name}.sock"


def alive(port, timeout=3):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def lookup(cfg):
    """(state, host) for a server: an AWS instance by Name tag (read-only describe call), or a fixed ssh host."""
    if cfg.get("kind") == "ssh":
        return "running", cfg.get("host")
    out = subprocess.run(["aws", "ec2", "describe-instances", "--region", cfg["region"],
                          "--filters", f"Name=tag:Name,Values={cfg['instance_name']}",
                          "--query", "Reservations[].Instances[].[State.Name,PublicIpAddress]", "--output", "json"],
                         capture_output=True, text=True, timeout=30)
    rows = [r for r in json.loads(out.stdout or "[]") if r[0] != "terminated"]
    return (rows[0][0], rows[0][1]) if rows else ("missing", None)


def up(name):
    if name not in SERVERS:
        raise SystemExit(f"unknown server '{name}' (have: {', '.join(SERVERS)})")
    cfg = SERVERS[name]
    if cfg.get("kind", "local") == "local":
        return cfg["port"]
    if alive(cfg["port"]):
        return cfg["port"]
    subprocess.run(["ssh", "-S", sock(name), "-O", "exit", "x"], capture_output=True)  # clear a stale socket
    state, host = lookup(cfg)
    if state != "running" or not host:
        raise SystemExit(f"{name} ({cfg.get('instance_name') or cfg.get('host')}) is '{state}', not running. "
                         f"Starting it is the user's call (for AWS: aws-ssh start {name}).")
    key = ["-i", os.path.expanduser(cfg["key"])] if cfg.get("key") else []
    r = subprocess.run(["ssh", *key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-f", "-N", "-M", "-S", sock(name),
                        "-L", f"{cfg['port']}:127.0.0.1:{cfg.get('remote_port', 8188)}", f"{cfg.get('user', 'ubuntu')}@{host}"],
                       capture_output=True, text=True, timeout=40)
    for _ in range(20):
        if alive(cfg["port"]):
            return cfg["port"]
        time.sleep(0.5)
    raise SystemExit(f"tunnel to {name} failed (ComfyUI may still be starting on the server): {r.stderr.strip()[-300:]}")


def down(name):
    subprocess.run(["ssh", "-S", sock(name), "-O", "exit", "x"], capture_output=True)


def status():
    for n, c in SERVERS.items():
        tun = "UP" if alive(c["port"], 2) else "down"
        if c.get("kind", "local") == "local":
            print(f"{n:10s} local    port {c['port']}  ComfyUI {tun}")
        else:
            try:
                state, host = lookup(c)
            except Exception as e:
                state, host = f"lookup failed: {e}", None
            print(f"{n:10s} {state:8s} port {c['port']}  tunnel {tun}  ({c.get('instance_name') or c.get('host')}, {host or 'no host'})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up" and len(sys.argv) > 2:
        print(up(sys.argv[2]))
    elif cmd == "down" and len(sys.argv) > 2:
        down(sys.argv[2]); print("closed")
    else:
        status()
