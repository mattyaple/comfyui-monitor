# Running on the remote ComfyUI servers (comfy4, comfy5 as configured in scripts/servers.json)

Use only when the user asks for AWS / comfy4 / comfy5 / "the remote server".

## Servers

| Alias | Instance | Region | Local tunnel port | NAS output mirror |
|---|---|---|---|---|
| comfy4 | ComfyUI-004 | us-east-2 | 8194 | `/Volumes/COT-ComfyUI/Outputs/comfy004/` |
| comfy5 | ComfyUI-005 | us-east-2 | 8195 | `/Volumes/COT-ComfyUI/Outputs/comfy005/` |

Both: g7e.2xlarge, RTX PRO 6000 Blackwell 96 GB, ComfyUI 0.37.x at `/home/ubuntu/ComfyUI`
(systemd `comfyui.service`, venv `~/venvs/comfy-current`, port 8188), identical models
(Flux 2 dev/klein + LoRAs, Qwen Image, yaple-nodes, GPT Image API nodes). Config lives in
`~/.claude/skills/comfyui/scripts/servers.json`; `~/.aws-ssh/aliases` also lists comfy1/comfy3
(not configured here; add a servers.json entry with a free port if one is needed).

## Billing rule

Instances bill while running. **Never run `aws-ssh start`** or otherwise start a stopped
instance, and never stop one the user may be using. If a server is stopped, tell the user and let
them start it. Read-only checks are fine: `aws-ssh status comfy4`, `comfy_tunnel.py status`.

## Access

- Port 8188 is not public (security groups open 22/80/443 only). Always tunnel.
- SSH: `ssh -i ~/.ssh/ComfyUI_KEY_us-east-2.pem ubuntu@<ip>`. The public IP changes on every
  restart; `aws-ssh status <alias>` or `comfy_tunnel.py status` looks it up.
- Tunnel: `python3 ~/.claude/skills/comfyui/scripts/comfy_tunnel.py up comfy4` prints the local
  port and reuses a live tunnel on that port (including one opened by a project's own tooling).
  Close with `... down comfy4`.

## Running a workflow

```bash
COMFY=~/.local/share/uv/tools/comfy-mcp/bin/comfy
PORT=$(python3 ~/.claude/skills/comfyui/scripts/comfy_tunnel.py up comfy4)
# inputs referenced by LoadImage nodes must exist on the server first
$COMFY --where local upload ref.png --host 127.0.0.1 --port $PORT --overwrite
$COMFY --skip-prompt --where local run --workflow graph.api.json --host 127.0.0.1 --port $PORT \
  --wait --no-watch --timeout 1200 --json
```

- `--timeout` is seconds of silence between events, not wall clock. Flux 2 dev first loads can be slow.
- API-node graphs (GPT Image, Gemini) need `--allow-spend` after the user agrees. The Comfy.org
  credential is this Mac's comfy-cli sign-in, sent with the job; the servers need no key of their own.
- Server stats / free VRAM through the tunnel: `curl -s localhost:$PORT/system_stats`,
  `curl -s -X POST localhost:$PORT/free -d '{"unload_models":true,"free_memory":true}'`.
- Queue: `curl -s localhost:$PORT/queue`. Interrupt: `curl -s -X POST localhost:$PORT/interrupt`.

## Getting outputs back

Outputs are written on the server under `/home/ubuntu/ComfyUI/output/<save prefix>`.
Ways back to the Mac, preferred first:

1. `~/.claude/skills/comfyui/scripts/comfy_clean_names.py outputs <prompt_id> --server comfy4 --copy-to <local dir>`:
   downloads every output of the job through the tunnel and drops the trailing underscore from
   the name (`name_00001_.mp4` → `name_00001.mp4`). This is the default; see "Clean output
   filenames" in SKILL.md. Run `comfy_clean_names.py graph <graph> --server comfy4` before the run
   so images are saved clean on the server too.
2. `$COMFY --where local download <prompt_id> -o <local dir>` or
   `curl -o out.png "http://127.0.0.1:$PORT/view?filename=<name>&subfolder=<sub>&type=output"`:
   keep the server's file name, so follow with `comfy_clean_names.py path <local dir>`.
3. The NAS mirror (names as saved on the server): `s3sync.service` pushes each new file to S3 and the NAS syncs it to
   `/Volumes/COT-ComfyUI/Outputs/comfy00N/<same path>` after a short lag (SMB share must be mounted).

Always copy remote results into the project's local output folder next to the local results;
do not leave them only on the NAS.

## Models and nodes on the server

comfy-mcp's `download_model` / `install_node` act on the local Mac only. For the servers, SSH in:

```bash
ssh -i ~/.ssh/ComfyUI_KEY_us-east-2.pem ubuntu@<ip>
ls ~/ComfyUI/models/loras/Flux2/
cd ~/ComfyUI/models/<folder> && wget -O <name>.safetensors '<url>'
~/venvs/comfy-3129-blackwell/bin/comfy --workspace ~/ComfyUI node install <pack>   # ask first
sudo systemctl restart comfyui    # ask first; interrupts any running job
```

Keep comfy4 and comfy5 in sync when adding models. Each server also has comfy-mcp at
`~/venvs/comfy-mcp/bin/comfy-mcp` for a Claude session run on the server itself.

## Troubleshooting

- Tunnel up but `/system_stats` fails: ComfyUI still starting or crashed.
  `sudo systemctl status comfyui` and `journalctl -u comfyui -n 100` on the server.
- Crash loop after a ComfyUI update (seen on comfy4, SQLAlchemy mismatch):
  `~/venvs/comfy-current/bin/pip install -r ~/ComfyUI/requirements.txt`, then
  `sudo systemctl reset-failed comfyui && sudo systemctl start comfyui`.
- Instance hangs (SSH times out at banner exchange, 8188 dead, EC2 checks still "ok", CPU flat
  around 16%): seen on comfy5 (64 GB RAM, no swap, ComfyUI 0.37.2) on 2026-10-01 running
  `VACE Clip Joiner v2.4`, right after the log line that starts loading the second 34 GB
  Wan 2.2 Fun VACE model while the first was already loaded. Cause not confirmed; RAM
  exhaustion is the likely one. It did not recover in 25 minutes and needs a reboot (ask the owner of the server).
  Before rerunning a graph with two 14B models, split it into two passes (save the latent,
  `POST /free`, then the second model) or check `free -g` while the first model loads.
- `Connection refused` on the local port after an instance restart: the IP changed; run
  `comfy_tunnel.py down <alias>` then `up <alias>`.

## Optional: comfy-mcp against a remote

comfy-mcp reads `COMFYUI_URL` once at startup, so it cannot switch targets mid-session. For a
long remote-only session, a second MCP entry can point at the tunnel port (tunnel must be up before
use): `claude mcp add comfy4-mcp -s local -e COMFY_BIN=... -e COMFYUI_URL=http://127.0.0.1:8194 -- .../comfy-mcp`.
Even then `system_stats`, `free_memory` and `download_model` still act on the local Mac.
