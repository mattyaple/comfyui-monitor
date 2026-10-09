# Changelog

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
