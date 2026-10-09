---
name: comfyui
description: Run and manage ComfyUI generations through the comfy-mcp server and comfy-cli. Defaults to the local ComfyUI install on 127.0.0.1:8188 (machine notes in references/local-setup.md); covers running workflow JSON, templates, models, custom nodes, jobs and outputs. Also covers running on remote GPU servers (scripts/servers.json: comfy4, comfy5 by default) over an SSH tunnel, but only when the user asks for AWS/remote/a server by name. Use whenever a task involves ComfyUI, a ComfyUI workflow (.json / .api.json), image or video generation through ComfyUI, ComfyUI models/LoRAs/custom nodes, or the comfy4/comfy5 servers. Also covers Comfy credit balance, per-run cost reporting and the cross-project AI credit log (for billing clients). Also sets how outputs are named: no trailing underscore on any generated image, video or audio file.
---

# ComfyUI (local by default, AWS on request)

## Precedence

If the current project has its own ComfyUI rules (a `CLAUDE.md`, a run wrapper such as a project's
`monitor/run.py`, fixed output folders), **those win**. Use this skill for the underlying mechanics
and for projects with no such rules.

## Targets

| Target | Where | How to drive it |
|---|---|---|
| **local** (default) | the ComfyUI on this machine, `http://127.0.0.1:8188`, outputs `~/ComfyUI/output/` (references/local-setup.md) | comfy-mcp tools (`mcp__comfy-mcp__*`) |
| **remote servers** (only when asked) | the entries of `scripts/servers.json` (comfy4 / comfy5: AWS us-east-2, RTX PRO 6000 96 GB), reached via SSH tunnel on a fixed local port each | comfy-cli with `--host 127.0.0.1 --port <tunnel port>`; see [references/aws.md](references/aws.md) |

Use AWS only when the user says so ("run on comfy4", "use AWS", "the remote server", "the big GPU").
Never pick it yourself because a job looks heavy; suggest it and ask instead.

## Setup check (once per session)

The comfy-mcp tools are deferred. Load them with ToolSearch (`select:mcp__comfy-mcp__server_info,...`
or a keyword search for `comfy`). If no `mcp__comfy-mcp__*` tools exist in this project, the server is
not registered here. Register it for all projects (user scope):

```bash
claude mcp add comfy-mcp -s user -e COMFY_BIN=$HOME/.local/share/uv/tools/comfy-mcp/bin/comfy -- $HOME/.local/share/uv/tools/comfy-mcp/bin/comfy-mcp
```

A new session is needed before the tools appear. Until then, use comfy-cli directly (below).

- comfy-mcp 0.10 + comfy-cli 1.20 live in a uv tool env: `~/.local/share/uv/tools/comfy-mcp/bin/`
  (`comfy-mcp`, `comfy`). `comfy` is not on PATH; use the full path. Do not pip-install comfy-cli into
  the ComfyUI venv.

### The local install

Machine specifics (how the server is started and restarted, log files, which venv custom nodes use) are in
[references/local-setup.md](references/local-setup.md): read it before starting, restarting or debugging the local
server, and keep it current for the machine you are on. Rules that hold everywhere:

- Not running (`server_info` → `running: false`): start it the way local-setup.md says, then poll
  `curl -s localhost:8188/system_stats` until it answers. Do not use `launch_comfyui` / `restart_comfyui` when the
  machine has its own launcher (they start comfy-cli's own server without that launcher's venv and environment).
- Restart (after node installs or a hang): check `curl -s localhost:8188/queue` is empty (or ask), stop the process,
  start it again as above.
- Python packages for custom nodes go into ComfyUI's own venv, never the uv tool env of comfy-cli.
- Apple Silicon: `system_stats` reports no VRAM figure (unified memory). Large Flux 2 / video graphs are slow on MPS;
  that is a reason to suggest a remote GPU server, not to route there without asking.

## Local workflow (MCP)

1. `server_info` first: confirms ComfyUI is up, shows hardware and whether a remote target is set.
2. Pick the path:
   - **Existing workflow JSON** (API or UI export): `validate_workflow` → fix gaps → `run_workflow`.
   - **Gallery template**: `search_templates` → `get_template`/`fetch_template` (must clear
     `local_check`; `checked:false` means run `validate_workflow` yourself) → `list_workflow_slots` →
     `set_workflow_slot` → `run_workflow`. One-shot: `run_template(name, params=...)`.
   - **Quick SD1.5 text-to-image test**: `generate_image(prompt)`.
   - **Variations** (seed/prompt sweeps): `vary_workflow`.
3. Before running a graph you built or edited: `comfy_clean_names.py graph` (see "Clean output
   filenames" below).
4. Long runs: `run_workflow(wait=False)` → `job(action="wait", timeout_seconds=...)` (repeat on
   `timed_out`) → `comfy_clean_names.py outputs <prompt_id> [--copy-to <project dir>]`. Files land in
   `~/ComfyUI/output/<save prefix>`. Use `--copy-to` rather than `fetch_outputs` to copy results into a
   project: after the rename, `fetch_outputs` and `comfy download` look for the old names and fail.
5. Inputs (images for LoadImage): `upload_file`, or copy into `~/ComfyUI/input/`.
6. Failures: `job(action="error")` for the node and traceback; `get_logs` for a crash; `server_died`
   usually means OOM, so check `system_stats` and `free_memory` before retrying.

Missing pieces:
- Unknown node class → `workflow_deps` → `install_node` (user confirms, runs third-party code;
  the workspace uses a legacy ComfyUI-Manager clone, so if it returns `unsupported`, `git clone` the pack
  into `~/ComfyUI/custom_nodes/` and pip-install its requirements into `~/venvs/comfy-current`) →
  restart the server as above → `validate_workflow` again.
- Missing model → `search_models` (check `loras`, `vae`, `diffusion_models` folders too before
  concluding it is absent) → `download_model(url, relative_path="models/<folder>")` → poll `download`.

## Clean output filenames: no trailing underscore (all projects)

ComfyUI's built-in save nodes hard-code `<prefix>_00001_.<ext>`. The convention in these projects: no trailing underscore on any generated file (image, video, audio, 3D). This applies in **every** project,
including ones with their own run wrapper, and to templates, `generate_image`, `run_template`
and `vary_workflow` runs. No run is finished while an output still ends in `_.<ext>`.

```bash
CN=~/.claude/skills/comfyui/scripts/comfy_clean_names.py
$CN graph graph.api.json [--server comfy4]       # before the run: SaveImage -> WAS Image Save
$CN outputs <prompt_id> [...] [--copy-to DIR]    # after the run: rename whatever is left
$CN path <dir-or-files> [--recursive] --dry-run  # files already on disk (wrapper runs, old batches)
```

1. **Images, when you write or edit the graph:** do not use the built-in `SaveImage`. Use WAS
   `Image Save` set up like the "Save Image" group node below, which writes `<prefix>_001.png`:
   `output_path` = folder under the output dir (`Project/Subfolder`), `filename_prefix` = the name
   only, `filename_delimiter` `_`, `filename_number_padding` 3, `filename_number_start` `false`,
   `overwrite_mode` `false`. `$CN graph` does this swap for every `SaveImage` in an API-format graph
   and prints the nodes it left alone. In a UI workflow that already has the
   `workflow>Save Image` group node, keep it and set its Project / Subfolder / Filename.
2. **Everything else** (`SaveVideo`, `SaveAudio*`, `SaveWEBM`, animated WEBP/PNG, 3D, templates,
   a wired or `%token%` prefix, a target without the WAS suite): no installed node saves these
   cleanly, so run `$CN outputs <prompt_id>` as soon as the job ends, before copying, reporting,
   or logging file names. It reads the job's files from `/history` and renames
   `name_00001_.mp4` → `name_00001.mp4`; if that name is taken it uses the next free number.
   `VHS_VideoCombine` already saves `name_00001.mp4` and needs nothing.
3. **Report and log the clean names** (credit log `--output`, project docs, messages to the user).
4. **comfy4 / comfy5:** `$CN outputs <prompt_id> --server comfy4 --copy-to <local dir>` downloads
   through the tunnel under the clean name. The copy on the server (and its NAS mirror) keeps the
   underscore unless the graph saved with `Image Save`, so use `$CN graph --server comfy4` first.
5. **Project wrappers** (for example a project's `monitor/run.py` from the comfyui-monitor template): the wrapper still launches the run,
   but the graph it launches follows rule 1, and if its outputs still carry the underscore, fix the
   graph builder rather than renaming files the wrapper has already indexed. Tell the user when a
   wrapper needs that change.

After an in-place rename the ComfyUI history panel shows a broken thumbnail for that job (it still
points at the old name); the file itself is fine.

## Money and consent

- `partner_generate`, and any `run_workflow`/`run_template` whose graph has partner API nodes
  (Gemini/Nano Banana, GPT Image, Flux Pro, Kling, Seedance, Veo...), **spend Comfy credits**. Ask the
  user before each spending run; pass `confirm_spend=True` only after a yes.
- Check `auth_status` before API-node runs. `signed_in` or `registration_env_key_present` true means OK.
- `update_comfyui(target="all")`, `switch_comfyui_version`, `install_node`: ask first.
- AWS instances bill while running. **Never start a stopped instance**; ask the user.

## Credit usage: report and log every paid run (all projects)

Credit costs are billed back to clients. This applies in **every** project, including ones with
their own ComfyUI rules or run wrapper.

After each run that used partner API nodes (and after a batch, once for the batch):

1. **Log it silently** (no narration, no asking) with the script below. One row per prompt_id.
   Take client / project / task from the project's `CLAUDE.md` or folder name; ask only if the
   client cannot be told from either.
2. **Tell the user the cost** of that run in credits and dollars, and the **remaining balance** in
   credits and dollars. Use the actual billed figures from the script, not the node's price badge.

```bash
CC=~/.claude/skills/comfyui/scripts/comfy_credits.py
$CC balance                               # {"usd":…, "credits":…}
$CC cost <prompt_id> [...]                # billed credits/usd per run + balance (no logging)
$CC log <prompt_id> [...] --client "Acme Beverages" --project "Spring Sizzle 2026" \
    --task "hero shot, pour variant" [--output file] [--job-folder dir] [--note "..."]
$CC report [--client X] [--project Y] [--since 2026-10-01]   # totals for invoicing
```

- The log is `~/AI_Credit_Log/ai_credit_log.csv` (project-agnostic; see its README). Append only;
  `log` skips prompt_ids already in it.
- Cost comes from `/api/billing/events` (`params.job_id` = prompt_id, `params.credits_used`); events
  land a few seconds after the job ends, and `log` waits for them. A run with no event was free,
  cached or failed; it is logged with 0 credits and a note.
- $1 = 211 credits. **Do not use `comfy cloud status` for the balance**: on this account (legacy
  billing rail) the API's `amount_micros` is in cents and comfy-cli 1.20 reads it as micro-dollars,
  showing about $0.006 when the real balance is about $64. The script handles this.
- Estimate before asking for a spend with the node's `price_badge` formula in `object_info`
  (Seedance 2.5: input reference video seconds are billed as well as output seconds).
- Runs made on a remote server with `--allow-spend` bill the same account; log them the same way.

## Direct comfy-cli (no MCP, scripting, or remote)

```bash
COMFY=~/.local/share/uv/tools/comfy-mcp/bin/comfy
$COMFY --skip-prompt --where local run --workflow graph.api.json --host 127.0.0.1 --port 8188 --wait --timeout 1200 --json
$COMFY --where local jobs status <prompt_id>
~/.claude/skills/comfyui/scripts/comfy_clean_names.py outputs <prompt_id> --copy-to ./out   # not `comfy download`
$COMFY --where local upload ref.png --host 127.0.0.1 --port 8188 --overwrite
```

Add `--allow-spend` to `run` only for an approved API-node graph. `--json-stream` gives one NDJSON
event per line (`queued`, `executing`, `executed` with output filenames, `execution_error`, final `envelope`).

## AWS servers (only when requested)

Read [references/aws.md](references/aws.md) before the first remote step. Short version:

```bash
python3 ~/.claude/skills/comfyui/scripts/comfy_tunnel.py status        # instance state + tunnels
python3 ~/.claude/skills/comfyui/scripts/comfy_tunnel.py up comfy4     # prints 8194
~/.local/share/uv/tools/comfy-mcp/bin/comfy --skip-prompt --where local run --workflow graph.api.json \
  --host 127.0.0.1 --port 8194 --wait --timeout 1200 --json
~/.claude/skills/comfyui/scripts/comfy_clean_names.py outputs <prompt_id> --server comfy4 --copy-to <local dir>
```

The comfy-mcp tools in this session stay pointed at local; do not use `run_workflow`, `system_stats`
or `download_model` for remote work.
