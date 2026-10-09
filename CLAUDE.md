# comfyui-monitor: rules for sessions working on the template

This repository is a **template**: the run monitor every AI image project gets a copy of. It must stay generic.

## Read first
- `README.md` (what it is), `docs/ARCHITECTURE.md` (how it works), `docs/CONFIG.md` (every setting), `docs/UI_GUIDE.md`
  (features and the design rules), `docs/EXTENDING.md` (how to add things), `docs/CHANGELOG.md`.
- To set up a monitor for a project, follow `docs/SETUP_GUIDE.md` (the `comfyui-monitor` skill is the same procedure).

## Hard rules
- **Nothing project-specific in the code.** Names, paths, folder layouts, id patterns, delivery folders and client
  names live only in a project's `monitor/project.json`, `monitor/servers.json` and its own docs. A need that looks
  project-specific becomes a config switch with a safe default (EXTENDING.md). `tools/check_monitor.py` greps the code
  for leftovers of the origin project; keep it green.
- **Every setting has a default** in `monitor/config.py` `DEFAULTS`, is documented in `docs/CONFIG.md`, and is editable
  in the Settings tab when a person would change it. Run `python3 monitor/config.py --write-example` after changing
  `DEFAULTS` so `project.example.json` stays in step.
- **Keep the UI vocabulary** (UI_GUIDE.md "Design rules"): segments, chips, pills, one hint line, sticky toolbars,
  consistent colors, per-browser state. Screenshot-check layout changes at a wide and a narrow width.
- **Verify against real data before calling a change done:** `MONITOR_CONFIG=… MONITOR_SERVERS=… python3 monitor/server.py 8799`
  on an existing project's records (read-only unless you mark or export), then `tools/check_monitor.py` on a scaffolded
  demo (`tools/new_monitor.py --target <scratch>/demo --answers …`).
- **Bump `monitor/VERSION` and add a CHANGELOG entry** for every change a project would want through `--update`.
- The repository never contains run records, images, exports, a `project.json` or a `servers.json` (gitignored).
- macOS only (Finder tags, sips); do not add dependencies beyond the Python standard library for the server, and
  reportlab + Pillow (through `uv run --with`) for exports.

## Layout
`monitor/` the code · `tools/` scaffold, checker, selects index · `docs/` the guides · `skills/` Claude Code skills
(`comfyui-monitor` for setups, `comfyui` for driving ComfyUI) · `install.sh` links the skills and registers comfy-mcp.
