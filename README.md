# comfyui-monitor

A local web page that watches a project's ComfyUI generations, keeps every run's provenance (prompt, source images,
seed, model, cost), lets you mark selects and rejects as Finder tags, builds contact sheets and review PDFs, and tracks
what the project has spent in Comfy credits. One monitor per project, configured by one file, run by one Python script.

It started as the run monitor of a storybook production and was generalized into this template so every new AI image
project gets the same tool with the same polish, in minutes, without carrying the old project's specifics along.

## What you get

| Tab | What it does |
|---|---|
| **Runs** | Live progress of every run started through `monitor/run.py` (and a note about jobs queued elsewhere). One card per run: status, server, category, group, title, timing, cost; the generation settings, the source images it used (with the version it used, even after a file was replaced), the prompt, the result tiles. Re-roll with a new seed, contact sheet of the run, star / NG / Finder on every tile. |
| **Selects** | Every image of the project by collection (your units of work such as shots or pages, reference assets, tests), group and section, with the run that made it. Star = select (Finder tag, optionally copied to a delivery folder), ✕ = NG (rejected). Multi-select, filters, thumbnail size, collapsible groups, "generating…" tiles for runs in progress. |
| **Export** | Contact sheets (JPG or PDF) and review PDFs (one page per group) from any collection, version, group set and selection state, or from one run. Previous exports listed with open / Finder / delete. |
| **Costs** | Running total of billed Comfy credits per run, model, group, day and person; the shared account balance; a budget bar; manual expenses; a team ledger merged from every teammate's machine (shared folder or export/import) with a CSV for the client invoice. |
| **Settings** | Everything in `monitor/project.json`, as a form: names, terminology, paths, versioning, collections and sections, select rules, export templates, costs, servers. Changes apply at once. |

A lightbox on every image: ← → images, ↑ ↓ groups, **S** select, **X** NG, hold the mouse (or **C**) to compare with
the source image or plate, **I** details, **F** show in Finder, copy name / path buttons.

Everything is plain files: run records are JSON in `monitor/runs/`, marks are Finder tags on the images themselves,
provenance sits next to the images in `_sources.jsonl`, exports are files in `exports/`. No database, no accounts.

## Requirements

macOS (Finder tags, `sips` thumbnails), Python 3.10+, [`uv`](https://docs.astral.sh/uv/) (exports pull reportlab and
Pillow on demand), comfy-cli + comfy-mcp in a uv tool env (`INSTALL.md`), a ComfyUI you can reach (local, or remote
through an SSH tunnel).

## Set up a monitor for a project

```bash
git clone https://github.com/mattyaple/comfyui-monitor ~/cli-projects/comfyui-monitor
python3 ~/cli-projects/comfyui-monitor/tools/new_monitor.py --target /path/to/your/project
python3 /path/to/your/project/tools/check_monitor.py
python3 /path/to/your/project/monitor/start.py
```

`new_monitor.py` asks the ten setup questions (`docs/SETUP_GUIDE.md`), or takes them from `--answers answers.json`.
With Claude Code, install the skills (`./install.sh`) and say "set up a run monitor for this project": the
`comfyui-monitor` skill runs the same procedure.

Then, in the project, every generation goes through the runner so it is logged:

```bash
python3 monitor/run.py workflows/my_graph.api.json --title "what this run tests" --shot SC010
```

## Repository layout

| Path | Contents |
|---|---|
| `monitor/` | The monitor: `server.py` (HTTP + API), `index.html` (the page), `run.py` (logged runner), `collect.py` (outputs into the project tree), `export.py`, `costs.py`, `credits.py` (Comfy billing), `tags.py` (Finder tags), `tunnel.py` (SSH tunnels), `config.py` (project.json), `start.py`. `project.example.json` and `servers.example.json` are the defaults a project starts from. |
| `tools/` | `new_monitor.py` (scaffold / update a project), `check_monitor.py` (verify a setup), `selects_index.py` (read the Finder tags into JSON). |
| `docs/` | `SETUP_GUIDE.md` (the questions and the checklist), `CONFIG.md` (every key of project.json and servers.json), `ARCHITECTURE.md` (modules, data flow, run record, API), `UI_GUIDE.md` (features, keys, design rules), `EXTENDING.md` (adding collections, templates, engines, servers), `CHANGELOG.md`. |
| `skills/` | Claude Code skills: `comfyui-monitor` (set up and maintain a monitor from this template) and `comfyui` (drive ComfyUI through comfy-mcp / comfy-cli, local or remote, with the credit rules). `install.sh` links them into `~/.claude/skills/` and registers comfy-mcp. |

## Running the template itself

Without a `project.json` the server runs on the example config (empty project "PROJ"), which is enough to look at the
page: `python3 monitor/server.py` then http://127.0.0.1:8765. Point `MONITOR_CONFIG` / `MONITOR_SERVERS` at another
project's files to run this code against that project's data without touching its own monitor.

## License

MIT. The monitor writes only inside the project folder, the ComfyUI output tree it is configured for, the folders you
name as select destinations, and the shared credit log; nothing leaves the machine except calls to your own ComfyUI
servers and Comfy's billing API for the balance.
