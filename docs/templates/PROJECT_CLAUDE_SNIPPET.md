# Paragraph for a project's CLAUDE.md

`tools/new_monitor.py` prints this with the project's own values filled in. The generic form:

```markdown
## Run monitor

- Every generation goes through `python3 monitor/run.py <graph.api.json> --title "what this run tests" [--<unit> <id>]`
  so it is logged; the monitor is at http://127.0.0.1:<port> (`python3 monitor/start.py`). The title names the variable
  being tested. Check `ls -t monitor/runs | head` at the start of a session for runs the user started from the monitor.
- Selects and rejects are the user's call, marked in the monitor as Finder tags (<select color> = select,
  <ng color> = NG). Read them with `python3 tools/selects_index.py` (writes `monitor/selects_index.json`); never assume
  a select, never copy files into the select destination yourself.
- Outputs save under `<output root>`; one <unit> per folder (`<unit folder pattern>`), sections `<_edits/, _plates/>`.
  Remote servers' outputs are collected into the same tree by `run.py` (`python3 monitor/collect.py <run_id>` to backfill).
- <If versioned:> The current version is **<V>** (`versions.current` in `monitor/project.json`); older versions are
  frozen and `run.py` refuses graphs that save into them unless `--retarget` is passed.
- <If costs:> Report the billed cost and the balance after each batch; the Costs tab keeps the project total
  (client "<client>", project "<project>" in the shared credit log). API runs spend shared credits: run only what was asked.
- Settings live in `monitor/project.json` (Settings tab) and `monitor/servers.json`; details in `docs/MONITOR.md`.
```
