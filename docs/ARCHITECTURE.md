# How the monitor works

Plain files, one Python process, one HTML page. Nothing runs unless `run.py` or the server is started.

```
 graph.api.json ──run.py──▶ comfy-cli ──▶ ComfyUI (local or through tunnel.py) ──▶ images in the output tree
       │                      │                                                          │
       │  run record          │ NDJSON events (queued / executing / executed)             │ collect.py: remote files
       ▼                      ▼                                                          ▼  into the local tree,
 monitor/runs/<date>_<nnn>.json  ◀── updated live ──  credits.py (billed cost) ◀── costs.py (team ledger)  _sources.jsonl
       │
 server.py ──▶ index.html (Runs · Selects · Export · Costs · Settings)
       │          marks = Finder tags on the files (tags.py) · thumbnails via sips into monitor/cache/
       ▼
 export.py (contact sheets, review PDFs)      selects_index.py (tags → JSON for other sessions)
```

## Modules

| file | role |
|---|---|
| `config.py` | Loads `project.json` over `DEFAULTS`, expands paths, derives versions, builds the regex that recognizes each collection's files, resolves select destinations. Everything else imports it. Re-reads on change. |
| `run.py` | The logged runner. Reads the graph (`graph_details`: prompt, inputs, seed, model, batch, LoRAs, sampler, save path), classifies the save path, checks frozen versions, picks a server, stages inputs on a remote, submits through comfy-cli, streams events into the record, cleans file names, collects outputs, bills the run. |
| `watch.py` | Daemon thread in the server (`runs.watch_ui`): polls each local server's `/queue` and `/history`, and gives every prompt queued from the ComfyUI interface that saves into this project's tree a run record built with `run.graph_details` (same fields as run.py: prompt, refs, model, seed, batch), its outputs (reported files, else new files under the graph's save folders during the execution window), timing from the history's timestamps and the billed cost. Saves the API graph under `workflows/ui/` so re-roll works. |
| `collect.py` | Puts a run's outputs where the project tree wants them: remote staging → local collection folder (numbered base-section files, kept names for sections, mirrored folders for other collections), local files recorded in place. Writes `_sources.jsonl` next to the files and the `collected` map on the record. |
| `server.py` | HTTP server and API (below). Reads run records and the output tree on every request (cheap: xattr in-process, PNG headers for sizes, cached thumbnails and hashes). Marks, re-rolls, exports, costs, settings. |
| `index.html` | The page. Single file, no build step, no dependencies. Reads `/api/config` first and renders every label, segment and color from it. Remembers its filters per browser (localStorage). |
| `export.py` | Renders a spec (groups of images with captions) as a JPG contact sheet (Pillow) or a PDF (reportlab); run through `uv run --with reportlab --with pillow`. |
| `costs.py` | This machine's billed runs + credit-log rows + manual expenses → ledger; shared-folder and import/export merging across teammates; summaries by model / group / day / person; backfill. |
| `credits.py` | Comfy billing: balance, cost per prompt id (from `/api/billing/events`), the append-only shared credit log. Re-executes itself with comfy-cli's Python so the sign-in is reused. |
| `marks.py` | Marks (select / NG / final pair): the `_marks.json` sidecar beside the files (shared, every OS) and, on a Mac, Finder tags mirrored through `tags.py` (`selects.marks`). |
| `team.py` | Publishes this machine's finished run records (+ source images by content, + API graphs) into `team.dir` in the shared tree; indexes the teammates' records by output path for the Selects tab. |
| `tags.py` | Finder label colors (macOS): read through `getxattr` in-process, written with `xattr`; no-ops elsewhere. |
| `tunnel.py` | SSH tunnels to remote servers (`servers.json`); never starts a stopped cloud instance. Shares its socket name with the comfyui skill's tunnel script so tunnels are reused. |
| `start.py` / `start.command` | Start the server detached (or in the foreground), open the page, stop it. |

## The run record

`monitor/runs/<YYYY-MM-DD>_<nnn>.json`, written by `run.py` and updated while the run executes (ids are reserved with a
hidden marker so concurrent runs never collide and a deleted record's id is never reused):

| field | meaning |
|---|---|
| `id`, `title`, `group`, `tags`, `note` | what was run and for which unit; `--title` names the variable being tested |
| `started`, `finished`, `status` (`queued` / `running` / `done` / `error`), `current_node`, `events`, `error` | progress |
| `prompt`, `negative`, `refs`, `ref_hashes`, `seed`, `model`, `batch`, `aspect`, `resolution`, `quality`, `steps`, `cfg`, `sampler`, `loras` | the generation settings read from the graph; `ref_hashes` = sha256 of each input at submission |
| `workflow`, `prompt_id`, `server`, `engine` (`api` / `flux2` / `open_edit` / `other`) | where and how |
| `outputs` (server-relative, prefix stripped), `collected` (output → local path), `expected` | the files |
| `kind`, `section`, `version` | classification of the save path at submission (the server re-classifies from the real output paths) |
| `gen_seconds` (first generator node → first saved file), `total_seconds` | timing |
| `cost` (`credits`, `usd`, `api_calls`, `models`, `balance_usd`, `checked`, `error`) | billed by `credits.py` after the run |

Records from the origin monitor used `page` and `page_version`; the server reads those as `group` and `version`.

The server adds, per request: `category` (`<collection>` or `<collection>:<section>`, an extra category, or `other`),
`group`, `section`, `version`, `images` (one entry per output: `src`, `srv`, marks `sel` / `tagged` / `ng`, size, and
its own `col` / `grp` / `sec` / `ver`), `syncing` (remote files not mirrored yet), `compare` (what hold-to-compare
shows), `ref_versions` (per input: current / archived copy in `Z_OLD/` / changed / unknown).

## Where files go

| what | local server | remote server |
|---|---|---|
| a unit's render (base section) | saved straight into `<dir>/<group>/` under the output root; recorded | saved into the staging folder (`<staging>/<group>/`), downloaded through the tunnel (or copied from the mirror) into `<dir>/<group>/` as `<file_name>` numbered per group |
| a section file (`_edits/`, `_plates/`) | in place | same folder locally, name kept (`_<server>` suffix on a clash) |
| a folders-collection file (references, tests) | in place | mirrored to the same relative path |
| anything else | in place (category `other`) | left on the server (shown from the mirror when one exists) |

Every collected file gets a line in `<folder>/_sources.jsonl` (`file`, `run`, `server`, `source`, `seed`, `title`,
`model`, `workflow`, `via`). The Selects tab reads this for provenance; files with no line fall back on the run index.

## Marks

A mark lives in `_marks.json` next to the file (`{"name.png": {"select", "ng", "pair", "by", "when"}}`), so the marks of
a shared output tree are the same on every machine, and on a Mac also as a Finder label on the file (Yellow select,
Red NG, Green final pair) when `selects.marks` is `both` (the default there). Select and NG are exclusive.

**Windows / Linux:** the monitor runs the same way; paths are handled with forward slashes (config.py wraps the
os.path helpers on Windows), thumbnails come from ffmpeg instead of `sips`, "show in Finder" opens Explorer, and
`start.py` keeps a `server.pid` for `--stop`.
Copy-mode collections also copy a select into the destination (`select.dest`) and move it to `Z_OLD/` on unselect; the
local original keeps the tag. A file is shown as a select when it carries the tag or sits in the destination. Remote
mirrors cannot be tagged; marks go on the local copy. `tools/selects_index.py` reads the tags (through `mdls`, which
sees fresh Finder writes) into `monitor/selects_index.json` for sessions that cannot read tags.

## API

| method, path | returns / does |
|---|---|
| GET `/api/config` | project.json plus derived fields: `_versions`, `_cur`, `_paths`, `_categories` (id → label, color), `_servers`, `_errors` |
| GET `/api/runs` | `{runs: [...]}` newest first (see the record above) |
| GET `/api/queue` | queue state per server; a remote server reads as offline when its tunnel is down (polling never opens tunnels) |
| GET `/api/selects` | current version, versions, collection ids |
| GET `/api/collection?id=&v=` | `{id, version, groups: [{group, files: [...], dest}], team_runs}`; files carry marks, size, section, provenance (`team` = "person/run id" of a teammate's record in `team_runs`) |
| GET `/api/path?src=&srv=` | the local path of an image |
| GET `/thumb?src=&w=`, `/full?src=` | cached JPEG thumbnail, the file. `src` = `out:<path under the server's output root>`, `in:<path under ComfyUI input>`, `exp:<export file>`, `team:<path under team.dir>` |
| GET `/api/exports`, `/export/<name>` | exports folder listing, one file |
| GET `/api/costs?period=all|90|30|7`, `/api/costs/balance?refresh=1` | the summary (`costs.summary`), the balance |
| POST `/api/select?src=&srv=&col=&group=&sec=&v=&on=` | mark / unmark a select |
| POST `/api/ng?src=&srv=&on=` | mark / unmark NG |
| POST `/api/reveal?src=&srv=` | show in Finder |
| POST `/api/reroll?id=&dry=` | clone the run's graph with a new seed (retargeted to the current version) and launch `run.py` |
| POST `/api/run/remove?id=` | move the record into `runs/_removed/` |
| POST `/api/export` (JSON: template, format, images, sort, title, subtitle, options, scope `{collection, version, groups, sections}` or `{run}`) | render an export |
| POST `/api/export/delete?name=` | |
| POST `/api/costs/expense` (JSON), `/api/costs/expense/delete?id=`, `/api/costs/backfill`, `/api/costs/export?format=json|csv`, `/api/costs/import` (JSON ledger), `/api/costs/import/delete?person=` | |
| POST `/api/config` (JSON), `/api/config/bump`, `/api/servers` (JSON) | save settings (validated), start the next version, save servers.json |

The page polls `/api/runs` and `/api/queue` every `server.poll_ms`; the Selects tab reloads its collection when a run
starts, progresses or finishes.

## Performance notes

Everything is recomputed per request from the files, which is fast enough for a few thousand records: xattr reads are
in-process, PNG sizes come from the header, thumbnails are cached by path + mtime + width in `monitor/cache/`, content
hashes (plates, inputs) by path + mtime + size in memory. Remote mirrors are only touched when a run's file is not
collected locally. `moved_lookup` folders are indexed at most every 30 s.
