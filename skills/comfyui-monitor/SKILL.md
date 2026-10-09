---
name: comfyui-monitor
description: Set up, update or work with a project's ComfyUI run monitor from the comfyui-monitor template (a local page with Runs / Selects / Export / Costs / Settings tabs, a logged runner, Finder-tag selects and credit cost tracking). Use when the user asks for a run monitor, generation monitor, selects page, contact sheets / review PDFs or cost tracking for an AI image project, wants a project's monitor updated to the latest template, or when a project already has a `monitor/` folder from the template and the session needs to run generations through it.
---

# comfyui-monitor: a run monitor per project, from one template

The template lives in `~/cli-projects/comfyui-monitor` (or wherever it was cloned: look for a folder with
`monitor/VERSION` and `tools/new_monitor.py`; if none exists, `git clone https://github.com/mattyaple/comfyui-monitor ~/cli-projects/comfyui-monitor`).
Read its `docs/SETUP_GUIDE.md` before the first setup of a session; `docs/CONFIG.md` when a setting is in doubt.

## A. Setting up a monitor for a project

1. **Look before asking.** In the project folder read `CLAUDE.md`, list the folders, and check `~/ComfyUI/output/` for a
   folder that already belongs to the project. Derive defaults: name, code, client, output root and prefix, the unit
   term (shots, pages, scenes), the folder naming of units, whether rounds (V1, V2) exist, whether a Dropbox or review
   folder receives selects, which ports other monitors on the machine use (`grep -h '"port"' ~/cli-projects/*/monitor/project.json`).
2. **Ask the standard questions, two rounds of at most four**, with the derived defaults as the recommended options.
   Round 1: project name / code / client · output root and prefix · unit term and folder pattern (and sections:
   edits, plates, none) · versioned rounds. Round 2: where selects go (tag only / copy to a folder) · extra
   collections (references, tests) · servers (local only / remote) · costs (track? person, budget, shared folder) and
   exports (contact sheets, review PDFs). Skip a question when the answer is already known or the default is obvious;
   never ask about colors, thumbnail sizes, poll intervals or engine lists (Settings tab later).
3. **Write the answers file** (`docs/SETUP_GUIDE.md` section 3) to the scratchpad and run
   `python3 <template>/tools/new_monitor.py --target <project> --answers <file>`. Choose `--preset pages` when the
   project has versioned rounds with selects copied to a client folder, `simple` for one collection without sections,
   `shots` otherwise.
4. **Verify:** `python3 <project>/tools/check_monitor.py` must report no ✗. Fix the config for every ✗ (port taken,
   unresolved placeholder, missing comfy-cli) and re-run. Then `python3 <project>/monitor/start.py` and look at the
   page (browser tool) on the Runs, Selects and Settings tabs: the unit term reads naturally, the collections match the
   real folders (an empty Selects tab with folders on disk means `group_regex` or `output_prefix` is wrong).
5. **Wire the project in:** add the paragraph `new_monitor.py` printed to the project's `CLAUDE.md` (confirm with the
   user if the file is theirs and already long), keep `docs/MONITOR.md`. If the project has a graph builder, make it
   save under the output root with the prefix and, for units, into `<dir>/<unit>/` (`run.py --dry-run graph.json`
   prints where a save path classifies).
6. **Prove it with one real run** when the user agrees to spend the credits: build or pick a graph,
   `python3 monitor/run.py <graph> --title "monitor check" --<unit> <id>`, and confirm the card appears live, the
   images land in the expected collection folder, a star in the page sets the Finder tag, and the cost shows on the card.
7. **Report:** the URL, the answers you assumed, anything left as a note by the checker, and that the Settings tab
   changes the rest.

Do not copy files from another project's monitor, do not edit the template's code for one project's needs
(`docs/EXTENDING.md`: make it a config switch in the template, then `--update` every project).

## B. Updating a project to the latest template

`cd <template> && git pull`, then `python3 <template>/tools/new_monitor.py --target <project> --update`,
`python3 <project>/tools/check_monitor.py`, restart the server (`python3 monitor/start.py --stop && python3 monitor/start.py`).
Read `docs/CHANGELOG.md` for new settings; `project.json` keeps working (every new key has a default).

## C. Working in a project that has a monitor

- Every generation goes through `python3 monitor/run.py <graph.api.json> --title "what this run tests" [--<unit> <id>] [--server <alias>] [--tag <variant>]`.
  `--dry-run` checks the graph (engine, save path, collection, version) without queuing. A graph that saves into a
  frozen version needs `--retarget`. API graphs on a remote server need `--remote-ok` after the user agrees.
- Start of a session: `ls -t monitor/runs | head` shows runs the user started from the page (re-rolls); the monitor
  may need `python3 monitor/start.py` after a reboot.
- Selects are the user's: Finder tags set in the page. Read them with `python3 tools/selects_index.py [--group <id>] [--selects]`
  (writes `monitor/selects_index.json`); never assume a select, never put files into a select destination yourself.
- Remote outputs are collected into the local tree by `run.py`; backfill with `python3 monitor/collect.py <run_id>`.
- Costs: after each batch tell the user the billed cost and the balance (both on the run card and in the Costs tab).
  Several people on one Comfy account: each machine counts its own runs; the team total needs `costs.shared_dir` or
  the Costs tab's export / import (`docs/UI_GUIDE.md`, "Costs").
- Exports (contact sheets, review PDFs) come from the Export tab or `POST /api/export`; per-run sheets from the card.
- Settings: edit `monitor/project.json` through the Settings tab (validated) rather than by hand; `servers.json` for servers.

## Troubleshooting

| symptom | cause, fix |
|---|---|
| Selects tab empty, images exist | `collections[].dir` / `group_regex` / `paths.output_prefix` do not match the real folders; `run.py --dry-run` on a graph shows how its save path classifies |
| run card shows "other" category | the save path is outside every collection; fix the graph's save path or add a collection / `runs.extra_categories` |
| marks fail: "only files in the local output folder" | the image is a remote mirror; collect it first (`collect.py <run_id>`) |
| cost "?" on a card | comfy-cli not signed in (`comfy cloud whoami`), or the billing event came late: Costs → Backfill |
| exports fail | `uv` missing, or reportlab/Pillow could not be fetched; the error text is in the Export tab status |
| remote server red dot | tunnel down; `python3 monitor/tunnel.py up <alias>` (never start a stopped instance yourself) |
| port in use | another project's monitor; change `server.port` in Settings and restart |
