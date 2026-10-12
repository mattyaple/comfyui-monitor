# Changelog

## 0.2.0 (2026-10-12)

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
