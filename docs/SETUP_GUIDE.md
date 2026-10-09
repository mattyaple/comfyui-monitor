# Setting up a monitor for a new project

This is the checklist an agent (or a person) follows to give a project its own run monitor from this template. It is
written so that nothing from the template's origin project leaks into the new one and no feature is lost on the way.
The `comfyui-monitor` skill (`skills/comfyui-monitor/SKILL.md`) is the same procedure packaged for Claude Code.

Time: about ten minutes. Needs: macOS, Python 3.10+, `uv`, comfy-cli (see `INSTALL.md`), a ComfyUI that runs.

## 1. Collect the answers (ask only these)

Keep the interview short. Infer what you can from the project folder first (an existing `CLAUDE.md`, folder names,
existing ComfyUI output folders) and present those as defaults; ask the rest in two rounds of up to four questions.
Every answer maps to a key of the answers file (section 3) and to `project.json` (`docs/CONFIG.md`).

| # | Question | Why it matters | Default |
|---|---|---|---|
| 1 | **Project name, short code, client.** | Titles, folder and file names (`code`), export footers and the credit log (`client`). | folder name; code = name without spaces, upper case |
| 2 | **Where does ComfyUI save this project's images?** Output root and the save-path prefix the graphs use. | Everything the monitor shows comes from this tree; the prefix is stripped from run outputs so paths classify. | `~/ComfyUI/output/<CODE>`, prefix `<CODE>/` |
| 3 | **What is one unit of work called, and how are its folders named?** The singular/plural term (shot, page, scene, asset…), an id pattern if folders follow one, and whether each unit has sub-sections (edits, plates, variants…). | The term appears in every label and as the `run.py --<unit>` flag; the pattern decides which folders count as units; sections become sub-folders with their own color and select rule. | `shot`/`shots`, any folder name, sections `edits` + `plates` |
| 4 | **Versioned rounds?** (V1, V2 …) If yes: the current one. | Versioning freezes earlier rounds (run.py refuses graphs that save into them), gives the Selects tab a version switch and the collector its numbering. Most short projects say no. | no |
| 5 | **Where do selects go?** Finder tag only, or also copied into a folder (client Dropbox, review folder)? If copied: the root folder. | Tag mode changes nothing on disk; copy mode mirrors the picks into a delivery folder and moves unpicked ones to `Z_OLD/`. | tag only |
| 6 | **Other collections besides the units?** Reference assets (`References/`), tests (`__TESTING/`), both, none. | Each is its own segment in the Selects tab with its own select rule; test images are never copied anywhere. | both |
| 7 | **Servers.** Local ComfyUI only, or remote ones too (alias, how it is reached, tunnel port, local mirror of its output)? | Remote servers need tunnels and collection of their outputs into the local tree. | local only |
| 8 | **Costs.** Track Comfy credits? Who is this machine's user in the team ledger, a budget, and a shared folder for teammates' ledgers? | run.py bills every API run; the Costs tab needs the client/project names for the shared credit log, the person for team totals. | yes, macOS account name, no budget, no shared folder |
| 9 | **Exports.** Contact sheets, review PDFs, both? Paper size and orientation. | Enables the templates in the Export tab. | both, letter landscape |
| 10 | **Monitor port.** | One port per project on the machine. | 8765, or the next free one if another monitor uses it |

Do not ask about: thumbnail sizes, colors, poll intervals, naming details, engine lists, prompt rules, where workflows
live. Those have good defaults and are changed later in the Settings tab.

When the project already has a monitor (an older clone of the origin project): ask whether to migrate its run records
(`paths.runs` can point at the old folder) and read its config for the answers instead of asking.

## 2. Run the scaffold

```bash
python3 /path/to/comfyui-monitor/tools/new_monitor.py --target /path/to/project --answers answers.json
```

(or without `--answers` on a terminal: it asks the questions above itself). It copies the code, writes
`monitor/project.json` and `monitor/servers.json`, creates `monitor/runs`, `exports`, `workflows`, writes
`docs/MONITOR.md` for the project and prints the paragraph to add to the project's `CLAUDE.md`.

Then:

```bash
python3 /path/to/project/tools/check_monitor.py      # config, paths, tools, leftovers, smoke test of the server
python3 /path/to/project/monitor/start.py             # start it and open the page
```

Fix every ✗ the check reports. Notes (!) are fine to leave.

## 3. The answers file

Every key is optional. Unknown keys are ignored. Paths may use `~`, `{code}`, `{project}`.

```json
{
  "project": {"name": "Demo Sizzle", "code": "DEMO", "client": "Acme Beverages", "title": "Demo Sizzle Run Monitor"},
  "terms": {"unit": "shot", "units": "shots"},
  "port": 8765,
  "output_root": "~/ComfyUI/output/{code}",
  "output_prefix": "{code}/",
  "group_regex": "^[A-Z]\\d+[A-Za-z_]*$",
  "group_label": "Shots",
  "sections": ["edits", "plates"],
  "versions": {"enabled": false, "current": "V1"},
  "selects": {"mode": "tag", "root": "", "dest": "", "unselect": "z_old"},
  "collections_extra": ["refs", "tests"],
  "collections_add": [],
  "servers": {},
  "costs": {"enabled": true, "person": "", "client": "", "project": "", "budget_usd": null, "shared_dir": ""},
  "export": {"enabled": true, "contact": true, "review": true, "paper": "letter", "orientation": "landscape"}
}
```

- `sections`: `"none"`, or a list of names (each becomes a `_<name>/` sub-folder inside every unit) or of full section
  objects (`{"id", "label", "sub", "color", "compare"}`; see CONFIG.md).
- `selects.mode` `copy` needs `selects.root` (or `dest` with placeholders); the default destination is
  `{selects_root}/{staging}/{group}` with versions, `{selects_root}/{group}` without.
- `collections_extra`: which of the preset collections to keep besides the units: `refs` (reference assets,
  copied into `references/<subject>/` on select), `tests` (tag-only).
- `collections_add`: full collection objects for anything else (CONFIG.md "collections").
- `servers`: extra entries for `servers.json` keyed by alias (CONFIG.md "servers.json"); the local server is always written.
- Presets (`--preset`): `shots` (default: units with edits/plates, refs + tests, tag selects, no versions),
  `pages` (versioned rounds, selects copied to `{selects_root}`), `simple` (one collection, no sections).

## 4. Tailor, then verify

After the scaffold, read `docs/MONITOR.md` in the project and the Settings tab once, and make sure:

- the unit term reads naturally everywhere (tabs, chips, hints, the `--<unit>` flag of run.py);
- the first collection's `group_regex` matches the real folder names (the Selects tab lists them; an empty tab with
  folders on disk means the pattern does not match);
- `output_prefix` is what the graphs really put in front of their save path (run a graph with `--dry-run`: the line
  says which collection and group the save path classifies into; "unclassified" means the prefix or the folder layout is off);
- copy-mode destinations resolve (check_monitor reports an unresolved `{placeholder}`);
- costs: `costs.client` / `costs.project` are the names the invoice will use; if teammates share the Comfy account,
  set `costs.person` on every machine and either a `shared_dir` or the export/import routine (UI_GUIDE.md "Costs");
- the project's `CLAUDE.md` has the monitor paragraph (every generation through `run.py`, selects are Finder tags,
  where files go, report costs).

Then run one real generation through `run.py` and confirm: the card appears live, the images land in the expected
collection folder, the Selects tab lists them under the right group, a star sets the Finder tag (and the copy, in copy
mode), and the cost shows on the card.

## 5. What must never leak from the origin project

The template was generalized from the UWNYC storybook monitor. The code carries no UWNYC specifics (the checker greps
for them); the places where a new project's specifics belong are only `project.json`, `servers.json`, `docs/MONITOR.md`
and the project's own `CLAUDE.md`. If a setup needs a feature the template lacks (a client-feedback link per version, a
script-text panel, a storyboard), add it to the template with a config switch (docs/EXTENDING.md) rather than editing
one project's copy; every project then gets it through `new_monitor.py --update`.

## 6. Updating a project to a newer template

```bash
python3 /path/to/comfyui-monitor/tools/new_monitor.py --target /path/to/project --update
python3 /path/to/project/tools/check_monitor.py
```

Only the code files are replaced; `project.json`, `servers.json`, run records, exports and expenses stay. Read
`docs/CHANGELOG.md` for new config keys (they all have defaults, so an old `project.json` keeps working).
