# Changelog

## 0.4.0 (2026-10-14)

- New: teammates' generations in the Selects tab (`team`, on by default). Each monitor publishes its finished run records
  into `team.dir` (default `{output_root}/_monitor`, inside the shared output tree): `runs/<person>/<id>.json` with the
  files each run made, the source images it used in `refs/` (stored once by content) and its API graph in `workflows/`.
  The Selects tab finds a teammate's file there by its path, so the lightbox shows their run's settings, prompt and
  reference frames, and hold-to-compare and final pairs work with their source images. A pill names the person. The Runs
  tab still shows only this machine's runs. Publishing runs in the server every `team.publish_interval_s`, or
  `python3 monitor/team.py` once; every teammate needs 0.4.0 for their runs to appear.
- `_sources.jsonl` lines carry `by` (the person), so a teammate's run id is never matched against a local run of the same id; an older line without `by` whose run id belongs to a local run with a different title is treated as another machine's too.
- Fix: depth-0 folder collections no longer list files inside `_` folders.

## 0.3.4 (2026-10-14)

- Fix: files inside a folder whose name starts with `_` (the yaple Save Video (Path) node's `_10bit/` originals,
  `_removed/`) are never run outputs: skipped by the runner, the watcher and the run cards (`config.sidecar_output`).
  Before, a video run listed its 10-bit original as a second thumbnail.

## 0.3.3 (2026-10-14)

- New: `pairs.max_per_shot` (1 by default, the old behaviour: a new final pair replaces the shot's previous one). Set it
  to 2 or more and a shot keeps alternates: each end frame marked adds a pair until the limit, the first named by
  `pairs.names`, the others by `pairs.alt_names` (`{shot}_A_{n}`, `{shot}_B_{n}`); unmarking an end frame removes only
  its own pair and the start frame keeps its tag while another pair still uses it. `_pairs.json` keys alternates
  `SHOT#n`; `/api/pair` answers `slot`, `slots` and `max`, and the toast says "Final pair 2 of 3".

## 0.3.2 (2026-10-14)

- New: `selects.copy_tags` (off by default). Copies of selects in a copy-mode destination carry one Finder label: the
  select color, or the final-pair tag instead when the original is in a final pair, so a select folder reads at a
  glance which selects made it into a pair. Before, the copies carried no tags at all (macOS copies drop them).

## 0.3.1 (2026-10-13)

- New: `collections[].auto_sections`, section templates (`sub` ending in `/*`). A folder under such a parent that no
  configured section claims (a custom shot code written by a workflow) becomes a section by itself: listed in the
  Selects tab with its own shot chip, classified by run.py and the watcher, usable for selects and final pairs. Before,
  images saved into an unconfigured folder were on disk but invisible in the monitor.

## 0.3.0 (2026-10-12)

- Marks (select / NG / final pair) are stored in a `_marks.json` sidecar beside the files (`selects.marks`, default
  `auto`): shared across every machine that sees the output tree, Mac or Windows. On a Mac Finder tags are kept as a
  mirror (`both`); `tools/marks_migrate.py --write` converts existing tags. `tools/selects_index.py` reads both.
- Windows support: forward-slash paths throughout (os.path wrapped on Windows), ffmpeg thumbnails when `sips` is
  absent (winget / imageio-ffmpeg locations searched), Explorer for "show in Finder", `start.py --stop` through a pid
  file, comfy-cli defaults for uv on Windows, no `pwd` / `fcntl` imports at module load.

## 0.2.0 (2026-10-12)

- New: sections can carry a `stage` and a `shot`; the Selects tab then filters by two chip rows (stage, shot) instead
  of one chip per section, group headers count per stage and sections sort stage → shot (`collections[].stages`).
- New: videos (.mp4 .mov .webm .mkv) are outputs too: listed in the Selects tab and on run cards (ffmpeg frame as
  the thumbnail, ▶ badge with the duration from ffprobe), played in the lightbox (hold-compare swaps to the source
  image), found by run.py and the watcher. Seedance nodes (ByteDance2*) are read for model, prompt, resolution,
  duration and start / end frames (`mode` on the record). Exports still take images only. The yaple `SaveVideoToPath` node (output folder + prefix) is read like WAS Image Save.
- New: sections may share a folder and be told apart by `file_regex` (start and end frames of one shot in one folder).
- New: final pairs (`pairs` in project.json, ◆ / P in the lightbox): an image and the source image it was made from are
  copied together into a final folder as `{shot}_A` + `{shot}_B` and tagged (Green); `/api/pair`.
- New: runs queued from the ComfyUI interface are recorded too (`monitor/watch.py`, `runs.watch_ui`, on by default). The
  watcher polls `/queue` and `/history` of every local server, builds the same record run.py would (prompt, references,
  model, seed, batch, outputs, timing, billed cost) for prompts that save into the project's output tree, marks them
  `via: "ui"` (a `ui` pill) and keeps their API graph under `workflows/ui/` for re-rolls. A string node titled
  `Run Title` in the workflow names the card. `/api/watch` shows the watcher's status and log.
  `python3 monitor/watch.py import <prompt_id>… [--group G] [--title T]` records history entries the watcher skipped
  (runs before the monitor existed, graphs that saved outside the project tree) so their cost counts.
- Fix: the credit balance reads the billing API's `*_cents` fields (a "stripe" rail account showed $0.02 instead of $248).
- Fix: run.py resolves DF_Text (`Text`) and chained string nodes in save paths; a run-time string (LLM output) ends the
  static path instead of becoming "None".
- Fix: run.py finds the outputs of a graph whose save folder is only known at run time (an LLM-named shot folder):
  every new image below the static part of the path counts, so such runs no longer end as "error" with files on disk.
- Fix: image URLs carry the file's mtime, so a file re-rendered under an old name is never shown from the browser cache.
- New: a prompt produced at run time (an LLM-written prompt, a shot splitter) is read from ComfyUI's history after the run
  (the text of the linked node or of a preview node fed by it), for run.py and UI runs alike. A linked aspect / model /
  resolution setting is read off the node that feeds it instead of being recorded as a link.
- Fix: the watcher resolves DF_Text (`Text`) nodes in save paths and no longer drops a graph whose static path ends at
  the output root (UI end-frame runs were skipped as "another project's").
- Fix: the watcher treats removed records (`runs/_removed/`) as known, so a run removed from the page is not imported
  again from the history after a restart.
- Fix: video thumbnails also find the ffmpeg that imageio-ffmpeg ships in a ComfyUI environment and `~/.local/bin`
  (Intel Macs without Homebrew). Without ffprobe the size and duration of a video are simply not shown.
- Costs tab: videos are counted apart from images (images / videos columns, $ per image and $ per video, in the
  team table and the CSV too); wide tables scroll inside their panel instead of overflowing; By model and By <unit> are stacked full-width.
- Fix: run.py strips `paths.output_prefix` (or an absolute output root) from a graph's save path before classifying it, so
  `--dry-run` and the record's `kind`/`section` are right for graphs that save with an absolute path.
- Fix: `config.load()` now compiles every collection's `group_regex` eagerly. Before, a fresh project with no run
  records got `KeyError: '_grx'` (HTTP 500) on the Selects tab and `/api/collection` until the first run was indexed.
  Found setting up the Peter Millar AI Shoes monitor (2026-10-12).
- Fix: `~` in a `servers.json` `output_root` is now expanded (the scaffold writes `~/ComfyUI/output/...` there; thumbnails and
  `/full` returned 404 for every local file until the path was absolute).

## 0.1.0 (2026-10-09)

First release of the template, generalized from the UWNYC storybook run monitor (built 2026-09-09 to 2026-10-01).

Kept from the origin: the Runs tab (live progress, per-run cards with full provenance, tile layout, re-roll, source
image versions), the Selects tab (collections, groups, sections, batch rows, multi-select, pending tiles), Finder-tag
marks (select / NG), the lightbox with hold-to-compare, remote servers through tunnels with collection into the local
tree, reservations and API routing rules, atomic run ids, clean file names.

New: `project.json` as the one configuration (terminology, paths, versions, collections, select rules, export
templates, costs, engines) with a Settings tab; an Export tab (contact sheets JPG/PDF, review PDFs, per-run sheets)
replacing the storyboard; a Costs tab (billed runs, by model / group / day, balance, budget, expenses, team ledgers
through a shared folder or export/import, invoice CSV); timing per run; `tools/new_monitor.py` (scaffold / update),
`tools/check_monitor.py` (verification with a smoke test), generalized `tools/selects_index.py`; the docs; the
`comfyui-monitor` and `comfyui` skills.

Removed: the Storyboard tab and book-layout PDF, the script docx parser, prompt-guide tags, every hard-coded path.
