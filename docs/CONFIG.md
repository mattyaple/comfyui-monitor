# project.json and servers.json

`monitor/project.json` is the one place a monitor is configured. Every key has a default (`monitor/config.py`,
`DEFAULTS`; `project.example.json` is the same as JSON), so a project file only needs what differs. The Settings tab
edits it; the server re-reads it when the file changes; `run.py`, `collect.py` and the tools read it on each start.

Placeholders in paths and patterns: `{project}` (the project folder), `{monitor}`, `{output_root}`, `{selects_root}`,
`{code}`, `{client}`, `{title}`, and, where a version is being addressed, `{version}` (folder name, `V3`), `{staging}`
(`V3_Staging`), `{n}` (3), `{file_suffix}` (`_v3`); where a group is addressed, `{group}` and `{section}`. `~` expands.
An unused `{version}` leaves no empty path segment behind.

## project

| key | default | meaning |
|---|---|---|
| `name` | My Project | the project's name (credit log, export titles) |
| `code` | PROJ | short id: letters, digits, `-`, `_`; names export files and the default output folder |
| `title` | PROJ Run Monitor | the page title |
| `client` | "" | client name (export footers, credit log) |

## terms

`unit` / `units`: what one unit of work is called (`shot`/`shots`, `page`/`pages`, `scene`…). Used in labels, hints and
as the `--<unit>` alias of `run.py --group`.

## server

`port` (8765; one per project on a machine), `poll_ms` (2500; how often the page re-reads the runs).

## paths

| key | default | meaning |
|---|---|---|
| `output_root` | `~/ComfyUI/output/{code}` | the project's subtree of ComfyUI's output folder; every collection lives under it |
| `output_prefix` | `{code}/` | what graphs put in front of their save path (relative to ComfyUI's output dir); stripped from run outputs so they classify |
| `comfy_input` | `~/ComfyUI/input` | where graphs read their input images (source images are shown from here) |
| `comfy_bin` | `~/.local/share/uv/tools/comfy-mcp/bin/comfy` | comfy-cli; the Python next to it runs the credit lookups |
| `runs` | `{monitor}/runs` | run records (point an old monitor's folder here to migrate) |
| `workflows` | `{project}/workflows` | where re-rolls write their graph copies |
| `exports` | `{project}/exports` | contact sheets, review PDFs, cost exports |
| `selects_root` | "" | the folder copy-mode selects go under (a client Dropbox); `{selects_root}` in collection destinations |
| `moved_lookup` | [] | folders under a server's output root where files get moved by hand; the monitor finds a run's files there by name |

## versions

| key | default | meaning |
|---|---|---|
| `enabled` | false | rounds of work (V1, V2 …): each version is a folder, older ones are frozen |
| `current` | V1 | the version new renders belong to; the Settings tab's "Start next version" bumps it |
| `first` | 1 | the first version that has this folder layout (older ones are not listed) |
| `folder` | `V{n}` | version folder name under `output_root` |
| `staging` | `V{n}_Staging` | where a remote server saves a version's files; `collect.py` moves them into `folder` and numbers them |
| `file_suffix` | `_v{n}` | part of collected base-section file names (`{group}{file_suffix}_{n:03d}`) |
| `refuse_frozen` | true | `run.py` refuses a graph whose save path targets an older version (pass `--retarget` to move it) |

## collections

The list that drives the Selects tab (one segment each), the classification of run outputs (category pill, group), where
`collect.py` puts remote outputs, and what a select does. The first collection is the default and holds the units of work.

```json
{"id": "shots", "label": "Shots", "kind": "shots", "dir": "{version}", "group_regex": "^[A-Za-z0-9][^/]*$", "color": "#ffcf33",
 "file_name": "{group}{file_suffix}_{n:03d}",
 "sections": [{"id": "option", "label": "Options", "sub": "", "color": "#ffcf33"},
              {"id": "edit", "label": "Edits", "sub": "_edits", "color": "#c792ea", "compare": "source"},
              {"id": "plate", "label": "Plates", "sub": "_plates", "color": "#7fd7c4", "compare": "hash"}],
 "select": {"mode": "tag", "dest": "{selects_root}/{version}/{group}", "section_dest": {}, "unselect": "z_old"}}
```

| key | meaning |
|---|---|
| `id`, `label`, `color` | unique id; the segment label; the category color (pills, section squares) |
| `kind` | `shots`: group folders with sections, numbered base-section files. `folders`: plain folder groups (references, tests) |
| `dir` | the collection folder under `output_root`; `{version}` makes it versioned; `""` = the root itself (groups are its subfolders, other collections' folders excluded) |
| `group_regex` | (shots) a subfolder must match it to count as a group (`^(Page-\d+[ab]|Back-Cover)$`) |
| `file_name` | (shots) name pattern for collected base-section files, numbered per group and version |
| `stages` | (shots, optional) `[{"id", "label", "color"}]`: when sections carry a `stage` (and a `shot`), the Selects tab filters by two chip rows, stage (start frames / end frames / videos / other) and shot, instead of one chip per section; group headers count per stage and sections are ordered stage → shot |
| `sections` | (shots) exactly one with `sub: ""` (the base section) plus any sub-folders. Optional per section: `stage` (a `stages` id) and `shot` (the shot code the section belongs to), see `stages`. Several sections may share one `sub` when all but one carry a `file_regex` (matched against the file name without extension): the matching section takes the file, the one without a pattern takes the rest (start frames `HERO-PAIR_001` and end frames `HERO-PAIR_B_001` in one folder). `compare`: `source` = the first input of a run in this section is the group image it was made from (hold-to-compare shows it); `hash` = a run whose input matches a file in this section by content shows that file (plates) |
| `auto_sections` | (shots, optional) section templates for folders that are not known in advance, such as a custom shot code a workflow invents. Each entry is a section whose `sub` ends in `/*` and whose `id` contains `{name}`: every subfolder of that parent, in any group, that no entry of `sections` claims becomes a section of its own, with `{name}` replaced by the folder name in `id` (slugged, prefixed `auto_`), `label` and `shot`. Give one template per kind of file in the folder, exactly as for a configured shot: `{"id": "{name}", "label": "{name}", "sub": "Outputs/*", "stage": "start", "shot": "{name}"}`, a second one with a `file_regex` for end frames, a third on the video parent. The folders are looked up again every few seconds, so a new one shows in the Selects tab (with its own shot chip, selects, compare and final pairs) without a restart or a config edit. Auto sections carry `"auto": true` in `/api/config` and are never written to `project.json`; hidden folders and those starting with `_` or `Z_` are skipped |
| `depth` | (folders) 1 = subfolders are groups; 2 = one nested level too (`env_Classroom/front`); 0 = every folder with images, newest first (tests) |
| `section_from_name` | (folders) regex with one group, applied to a file name without its counter; the capture is the file's section (`_ref_(.+)$` → `face`, `turn_3q`); no match = `other`. `section_order`: prefixes in display order |
| `select.mode` | `tag`: Finder's select tag on the file, nothing copied. `copy`: also copied into `dest` |
| `select.dest` | (copy) destination folder pattern; `section_dest` overrides it per section id; `dest_map` renames a group's destination folder (`{"Kit": "Props"}`) |
| `select.unselect` | (copy) what unmarking does with the copy: `z_old` (move into `Z_OLD/` beside it), `delete`, `keep` |

How an output path is read: `(?:^|/)<dir>/<group>/[<section sub>/]<file>` with `{version}` matching either the version
folder or its staging folder. Collections with a `dir` are tried first, in order; the catch-all (`dir: ""`) last, after
an optional leading `{code}/`. A path no collection matches is category `other` (or an `extra_categories` match).

## runs

| key | meaning |
|---|---|
| `extra_categories` | `[{"id", "label", "regex", "color"}]`: output paths outside the collections that still deserve a category (LoRA datasets); the first regex group is the group name |
| `ref_roles` | `[["regex", "label"], …]`: what a source image is, from its file name (mask, plate, face, body…), shown on run cards and in the lightbox; `plate` and `mask` roles are also used for hold-to-compare when no section match exists |
| `collapse_after` | the n newest run cards start open |
| `watch_ui` | true: the server's watcher (`watch.py`) polls every local server's `/queue` and `/history` and writes a record for every prompt queued from the ComfyUI interface that saves into this project's output tree (`via: "ui"`, a `ui` pill on the card). Prompts saving elsewhere are ignored, so several monitors can watch one ComfyUI |
| `ui_watch_interval_s`, `ui_history_items`, `ui_lookback_h` | poll interval (4 s), history entries read per poll (40), and how old a history entry may be to still get a record (24 h) |
| `ui_title_node` | the title of a string node (DF_Text, Text, a primitive) in the UI graph whose value names the run on its card (`Run Title`); without one the card is named after the save folder |

## selects

`tags.select` (Yellow), `tags.ng` (Red): the Finder label colors used as marks on a Mac. The two are exclusive.

`marks`: where marks (select, NG, final pair) are stored. `sidecar` = a `_marks.json` beside the files, which travels
with them, so on a shared volume every machine (Mac or Windows) sees the same marks; `tags` = Finder tags only (macOS);
`both` = the sidecar is the source of truth and Finder tags mirror it; `auto` (default) = `both` on a Mac, `sidecar`
elsewhere. A file that has a tag but no sidecar entry still reads from the tag; `tools/marks_migrate.py --write`
converts a whole tree. `tools/selects_index.py` reads both stores.

`copy_tags` (false): for collections whose selects are copied (`select.mode: copy`), give each copy in the destination
one Finder label (macOS): the select color, or the final-pair tag instead when its original is part of a final pair. The
originals keep both labels. Selecting, pairing, unpairing and replacing a pair update the copies; the server also
re-checks every select recorded in a sidecar when it starts (a backfill for copies made before the switch was on).

## pairs

A "final pair" is an image together with the source image it was made from (the first input of the run that produced it:
an end frame and its start frame). Marking one (the lightbox's ◆ button or P) copies both into `dest` under `names` and
sets the `tag` Finder label on both originals, beside the select tag; `_pairs.json` in the destination remembers which
files form the pair. With `max_per_shot` at 1 a new pair for the same `{shot}` replaces the previous one (its copies go
to `Z_OLD/`); above 1 a shot keeps that many pairs: the first is named by `names`, the others by `alt_names` with `{n}`
the pair's number (2, 3…; the lowest free number is taken), `_pairs.json` keys them `SHOT` and `SHOT#n`, and unmarking
an end frame removes only the pair it is in (its start frame keeps the tag while another pair still uses it). The
video workflow's shot code comes from the start frame's name, and a trailing `_n` is stripped like a counter, so an
alternate pair's `DET-HEEL_A_2.png` still saves its video under `DET-HEEL`.

| key | default | meaning |
|---|---|---|
| `enabled` | false | the ◆ button and the P key |
| `dest` | `{selects_root}/{group}/Final` | folder pattern; `{shot}` is the last part of the section's folder (`DET-HEEL`), `{section}` its id |
| `names` | `["{shot}_A", "{shot}_B"]` | file names of the copies (source, image); the extension is kept |
| `tag` | Green | Finder label on both originals |
| `unpair` | z_old | what unmarking does with the copies: `z_old`, `delete`, `keep` |
| `max_per_shot` | 1 | pairs a shot may have: 1 replaces, 2 or more keeps alternates |
| `alt_names` | `["{shot}_A_{n}", "{shot}_B_{n}"]` | file names of the copies of the 2nd, 3rd… pair (`{n}` = its number) |

## naming

`strip_trailing_underscore` (true): ComfyUI's built-in save nodes write `name_00001_.png`; the runner renames a local
server's files to `name_00001.png` and the collector names remote copies that way.

## export

`enabled`, `default_template`, `footer` (placeholders `{client} {title} {code} {date}`), and `templates`:

| template | options |
|---|---|
| `contact` | `label`, `format` (jpg / pdf), `columns`, `thumb` (tile width for JPG), `captions`, `group_headers`, `paper`, `orientation` |
| `review` | `label`, `per_row`, `captions`, `prompt` (excerpt of the first run's prompt per group), `notes` (a notes line), `cover`, `paper`, `orientation` |

Each has `enabled`. The Export tab offers the enabled ones and lets the request override any option.

## costs

| key | default | meaning |
|---|---|---|
| `enabled` | true | the Costs tab and billing |
| `bill_runs` | true | `run.py` looks the charged credits up after every partner-API run (`credits.py log` + `cost`) and stores them on the record |
| `person` | "" | whose usage this machine's runs are in the team ledger (default: the macOS account's full name) |
| `client`, `project` | project.client / project.name | names written to the shared credit log; rows with this project name count toward the project |
| `credit_log` | `~/AI_Credit_Log/ai_credit_log.csv` | the shared, append-only log across projects on this machine |
| `credits_per_usd` | 211 | Comfy's rate |
| `budget_usd` | null | shows a budget bar when set |
| `shared_dir` | "" | a folder every teammate can reach; each monitor writes `<code>_costs_<person>.json` there after each billed run and reads the others' |
| `balance_cache_s` | 120 | how long a balance lookup is reused |

Manual expenses live in `monitor/expenses.json`; imported teammate ledgers in `monitor/costs_imports/`.

## engines

`api_nodes_extra`: class names that spend credits besides the built-in list (Nano Banana, Gemini, GPT Image, Seedream,
Flux Pro/Max, Kling, Veo, Seedance). `generator_nodes_extra`: class names whose start marks the beginning of generation
for the timing (gen seconds).

## servers.json

One entry per ComfyUI, keyed by alias. `local` is required.

```json
{
  "local":  {"kind": "local", "port": 8188, "output_root": "~/ComfyUI/output/PROJ", "output_prefix": "PROJ/", "engines": ["api", "flux2", "open_edit", "other"]},
  "comfy4": {"kind": "aws", "instance_name": "ComfyUI-004", "region": "us-east-2", "key": "~/.ssh/key.pem", "user": "ubuntu",
             "port": 8194, "remote_port": 8188, "output_root": "/Volumes/NAS/Outputs/comfy004", "output_prefix": "",
             "engines": ["flux2", "open_edit"], "api_models": ["gpt-image-2.5-flare"], "auto_select": true,
             "reservation": {"active": false, "reason": ""}}
}
```

| key | meaning |
|---|---|
| `kind` | `local` (no tunnel), `aws` (instance found by its EC2 Name tag, read-only `describe-instances`; never started by the monitor), `ssh` (fixed `host`) |
| `port` | local port: ComfyUI's own for `local`, the tunnel's local end otherwise (fixed per server so tunnels are shared between tools) |
| `remote_port`, `user`, `key`, `region`, `instance_name`, `host` | how to reach a remote server |
| `output_root` | where this machine can read that server's output files without the tunnel (a NAS mirror); `output_prefix` as in paths |
| `engines` | which graph engines may run there: `api` (partner API nodes), `flux2`, `open_edit` (Qwen / SenseNova editors), `other` |
| `api_models` | GPT Image models an API graph may use on a remote server without `--remote-ok` |
| `auto_select` | false keeps the server out of automatic routing (explicit `--server` only) |
| `reservation` | `{"active", "reason", "allowed_engines", "blocked_engines", "blocked_node_patterns"}`: an unconditional exclusion no flag overrides |

Routing: `--server` picks one; otherwise the engine of the graph picks among the servers whose `engines` allow it,
shortest queue first. API graphs stay on local servers unless `--remote-ok` or `api_models` allow the graph's model.
