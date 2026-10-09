#!/usr/bin/env python3
"""SSH tunnels from this Mac to the AWS ComfyUI servers in servers.json.

  comfy_tunnel.py status            # every server: instance state + tunnel up/down
  comfy_tunnel.py up comfy4         # open (or reuse) the tunnel, print the local port
  comfy_tunnel.py down comfy4       # close it

ComfyUI's port 8188 is not public on the AWS servers, so everything goes through
`ssh -L <local port>:127.0.0.1:8188`. Local ports are fixed per server (comfy4 ->
8194, comfy5 -> 8195) so a tunnel opened by another project's tooling is reused.

Never starts a stopped instance (billing). If the instance is not running, `up`
fails and says so; starting it is the user's call (`aws-ssh start <alias>`).
"""
import json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVERS = json.load(open(os.path.join(HERE, "servers.json")))


def sock(name):
    return f"/tmp/comfy_tunnel_{name}.sock"


def alive(port, timeout=3):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def lookup(cfg):
    """(state, public_ip) of the instance via the AWS CLI. Read-only."""
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
    if cfg["kind"] == "local":
        return cfg["port"]
    if alive(cfg["port"]):
        return cfg["port"]
    subprocess.run(["ssh", "-S", sock(name), "-O", "exit", "x"], capture_output=True)  # clear a stale socket
    state, ip = lookup(cfg)
    if state != "running" or not ip:
        raise SystemExit(f"{name} ({cfg['instance_name']}) is '{state}', not running. "
                         f"Ask the user; they can start it with: aws-ssh start {name}")
    r = subprocess.run(["ssh", "-i", os.path.expanduser(cfg["key"]), "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
                        "-o", "StrictHostKeyChecking=accept-new", "-o", "ExitOnForwardFailure=yes",
                        "-o", "ServerAliveInterval=30", "-f", "-N", "-M", "-S", sock(name),
                        "-L", f"{cfg['port']}:127.0.0.1:{cfg['remote_port']}", f"{cfg['user']}@{ip}"],
                       capture_output=True, text=True, timeout=40)
    for _ in range(20):
        if alive(cfg["port"]):
            return cfg["port"]
        time.sleep(0.5)
    raise SystemExit(f"tunnel to {name} failed (ComfyUI may still be starting; "
                     f"check `sudo systemctl status comfyui` on the server): {r.stderr.strip()[-300:]}")


def down(name):
    subprocess.run(["ssh", "-S", sock(name), "-O", "exit", "x"], capture_output=True)


def status():
    for n, c in SERVERS.items():
        tun = "UP" if alive(c["port"], 2) else "down"
        if c["kind"] == "local":
            print(f"{n:8s} local   port {c['port']}  ComfyUI {tun}")
        else:
            state, ip = lookup(c)
            print(f"{n:8s} {state:8s} port {c['port']}  tunnel {tun}  ({c['instance_name']}, {ip or 'no ip'})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up" and len(sys.argv) > 2:
        print(up(sys.argv[2]))
    elif cmd == "down" and len(sys.argv) > 2:
        down(sys.argv[2]); print("closed")
    elif cmd == "status":
        status()
    else:
        raise SystemExit(__doc__)
