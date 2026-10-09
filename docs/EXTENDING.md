# Extending the template

Rule one: a project-specific need becomes a **config switch in the template**, not an edit in one project's copy.
Every project then gets the feature through `new_monitor.py --update`, and the template never drifts into a fork per
project (which is what this template exists to end). Add the key to `DEFAULTS` in `monitor/config.py` with a safe
default, document it in `docs/CONFIG.md`, surface it in the Settings tab (`FIELDS` in `index.html`) when a person
would change it, and note it in `docs/CHANGELOG.md`. Then bump `monitor/VERSION`.

## Things that need no code

- **A new kind of folder to review** (storyboards, plates, animatics…): add a collection in Settings. `kind: shots`
  for groups with sections and numbered files, `kind: folders` for plain groups. Colors, select rule and destination
  are per collection.
- **A different unit** (scenes instead of shots): `terms`.
- **A project with rounds**: `versions.enabled` and the folder patterns.
- **Selects delivered somewhere** (client Dropbox): `select.mode: copy` and `select.dest` on the collection.
- **An output path the collections do not cover but that deserves a category** (LoRA datasets): `runs.extra_categories`.
- **A source-image role the lightbox should name** (turnaround, packshot…): `runs.ref_roles`.
- **A partner node class the engine detection misses**: `engines.api_nodes_extra`.
- **Another server**: `servers.json` (kinds local / aws / ssh).

## Adding an export template

1. `monitor/export.py`: add a branch for the template name (`T == "mytemplate"`), building pages from `GROUPS`
   (each `{"id", "label", "note", "prompt", "items": [{"path", "name", "caption", "selected"}]}`) and `O` (options).
   Use `prepped(path)` for anything embedded in a PDF (keeps files small).
2. `monitor/config.py` `DEFAULTS["export"]["templates"]["mytemplate"] = {"enabled": True, "label": "…", "format": "pdf", …}`.
3. `index.html`, Export tab: the template appears in the segment on its own; add its options to the `#exOpts` builder
   in `loadExport()` if they differ from the two existing ones. Add its options to `FIELDS` (Settings → Export).
4. CONFIG.md, CHANGELOG.md.

## Supporting another generator node

`run.py`: `graph_details` reads prompt, seed, model, batch, aspect, resolution, LoRAs and sampler from known node
classes; add an `elif ct == "MyNode":` branch that fills `d[...]`. `is_api_node` decides whether the run spends credits
(billing) and goes through API routing; add the class to `BUILTIN_API_NODES` in `config.py` when it is a partner node.
`GEN_NODES` (timing) picks up API nodes and samplers automatically. `reroll()` in `server.py` sets the seed on
`Seed`, `RandomNoise`, API nodes, KSamplers and `OpenAIGPTImageNodeV2`; add the node if its seed input is named
differently.

## Another server kind

`tunnel.py`: `lookup()` returns `(state, host)`; add a branch for the new kind (a cloud provider's CLI, a fixed host
with a different user). The rest (tunnel, queue, upload, collect) works on any ComfyUI behind `127.0.0.1:<port>`.

## A feature from the origin project you may want back

The storybook monitor had a Storyboard tab (page spreads built from the selects, script text split across the A and B
pages, a book-layout PDF) and a per-version client-feedback PDF link. Neither fits a generic template as is; the review
PDF covers the first use (one page per group, selects, notes line), and a "project links" panel driven by a
`links: [{"label", "path"}]` key would cover the second. If you add either, make it a config switch (off by default).

## Keeping the checker honest

`tools/check_monitor.py` greps the monitor code for words of the origin project. When you add code, keep examples in
comments and hints neutral (`SC010`, `shot`, `Acme`) so the check stays green for every project.

## Testing a change against real data without touching a project

```bash
MONITOR_CONFIG=/path/to/some/project.json MONITOR_SERVERS=/path/to/its/servers.json python3 monitor/server.py 8799
```

runs this repository's code on that project's records, images and tags (it writes nothing unless you mark, re-roll
or export). `tools/check_monitor.py --target /path/to/project` runs the smoke test on a spare port.
