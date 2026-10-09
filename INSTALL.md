# Install

What a machine needs before a project can run a monitor. macOS only (Finder tags, `sips`).

## 1. Python, uv, Homebrew tools

```bash
brew install python uv            # Python 3.12+ and uv (exports fetch reportlab + Pillow through uv on first use)
```

## 2. comfy-cli and comfy-mcp (one uv tool env)

The runner submits graphs through comfy-cli; the cost lookups reuse comfy-cli's Comfy.org sign-in; the `comfyui`
skill drives ComfyUI through the comfy-mcp server. Keep both in one isolated env, never inside ComfyUI's own venv:

```bash
uv tool install comfy-mcp --with comfy-cli --python 3.12
ls ~/.local/share/uv/tools/comfy-mcp/bin/        # comfy-mcp, comfy, python
```

`paths.comfy_bin` in a project's `project.json` points at that `comfy` binary (this is the default). Sign in once so
partner API nodes and billing work:

```bash
~/.local/share/uv/tools/comfy-mcp/bin/comfy cloud login
~/.local/share/uv/tools/comfy-mcp/bin/comfy cloud whoami
```

## 3. ComfyUI

A ComfyUI the runner can reach: local on `127.0.0.1:8188` (the default in `servers.json`), or remote through an SSH
tunnel (`docs/CONFIG.md`, "servers.json"). For clean file names install the WAS Node Suite (`Image Save`) or leave
`naming.strip_trailing_underscore` on, which renames the built-in save node's `name_00001_.png` after each run.

## 4. The template and the skills

```bash
git clone https://github.com/mattyaple/comfyui-monitor ~/cli-projects/comfyui-monitor
cd ~/cli-projects/comfyui-monitor && ./install.sh
```

`install.sh` links `skills/comfyui-monitor` and `skills/comfyui` into `~/.claude/skills/` (existing folders of the same
name are left alone) and registers comfy-mcp with Claude Code at user scope:

```bash
claude mcp add comfy-mcp -s user -e COMFY_BIN=$HOME/.local/share/uv/tools/comfy-mcp/bin/comfy -- $HOME/.local/share/uv/tools/comfy-mcp/bin/comfy-mcp
```

A new Claude Code session is needed before the `mcp__comfy-mcp__*` tools appear. The `comfyui` skill's
`references/local-setup.md` describes one machine's ComfyUI install; adjust it to yours (how the server is started,
where logs are, which venv custom nodes use).

## 5. A monitor for a project

`docs/SETUP_GUIDE.md`, or in short:

```bash
python3 ~/cli-projects/comfyui-monitor/tools/new_monitor.py --target /path/to/project
python3 /path/to/project/tools/check_monitor.py
python3 /path/to/project/monitor/start.py
```

## Shared credit log and team costs

`credits.py` appends one row per paid run to `~/AI_Credit_Log/ai_credit_log.csv` (created on first use) so credits
can be billed back per client and project across every project on the machine. With several people on one Comfy
account, give every machine a `costs.person` and either a `costs.shared_dir` (a folder all can reach) or use the Costs
tab's export / import (`docs/UI_GUIDE.md`, "Costs").

## Updating

```bash
cd ~/cli-projects/comfyui-monitor && git pull
python3 tools/new_monitor.py --target /path/to/project --update      # per project; keeps its config and runs
```
