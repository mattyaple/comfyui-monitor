# Using the monitor, and the design rules behind it

## Header

Title, the tabs (the Runs tab carries a count of runs running or queued now, visible from every tab), one dot per
server (green = ComfyUI answers; a remote server shows red while its tunnel is down), the runs filter (category segment,
group dropdown, refresh), "Collapse older", "Hide NG" (Runs and Selects), "Selects only" (Selects), the last update time.

## Runs

- **In progress**: one line per active run with its current node, a progress bar and files saved / expected; plus a
  note when ComfyUI's queue holds jobs the monitor did not start.
- **One card per run**, newest first. Head: status, server, category pill (color per collection / section), the
  group with its version, the title (full title on hover and inside), timing, image count, cost pill, re-roll, sheet,
  remove (failed runs). Click the head to collapse or open; the n newest start open (`runs.collapse_after`), the
  choice is remembered per run.
- **Details** (left): seed, model, batch / aspect / resolution, steps / cfg / sampler, LoRAs, timing, cost (and the
  balance after the run), tags, note, workflow, prompt id; the source images with their role (face, plate, mask…) and
  an "old version" badge when the file was replaced since the run (the retired copy from `Z_OLD/` is shown); the
  prompt (expandable); the last events. Click a source image to show it in Finder, ⌘-click opens it.
- **Results** (right): tiles that fill the area at the images' own shapes; hover for ★ select, ✕ NG, folder (Finder).
  A select has a gold frame, an NG tile a red frame and is dimmed.
- **Re-roll seed** clones the graph with a new seed and launches `run.py` (asks first; an API graph spends credits).
  A graph that saved into a frozen version is retargeted to the current one.
- **⊞ Sheet** builds a contact sheet of the run's images into `exports/`.

## Selects

Toolbar row 1 (where and how): collection segment, version segment (versioned collections), group dropdown, order,
refresh, collapse / expand all, image count, thumbnail size. Row 2 (what): section chips (⌥-click solos one) and a
one-line hint saying what a select does in this collection.

Groups are collapsible boxes with a sticky heading: name, per-section counts, ★ selects, ✕ NG, batches running,
where selects are copied, a Finder button. Inside, one labeled row per batch (the run that made the images, its title,
seed, server, cost), or a plain grid when no run is known; runs in progress appear as "generating…" tiles in their
group. Reference collections get a sub-heading per asset kind (from the file name) with its pick status.

Pick several with ⇧-click (range) and ⌘-click (toggle); the floating bar stars or NGs the whole pick; Esc clears it.

## Lightbox

← → images, ↑ ↓ groups, **S** (or space) select, **X** NG, hold the mouse or **C** to compare with the source image
(an edit shows the image it was made from, a render made with a plate shows the plate, anything else its first input;
click another source in the panel to compare with that one instead, ⇧-click shows it in Finder), **I** the details
panel (open by default, remembered), **F** show in Finder, Esc close. Copy buttons for the file name and the local path.

## Export

Pick a template (contact sheet as JPG or PDF, review PDF), a source (a collection with its version, groups and
sections, or one run), which images (selects only, all but NG, everything), the order, layout options, a title and a
subtitle. The line under the form says how many groups and images the export will hold. Exports land in `exports/`
and are listed on the right with open, Finder and delete.

## Costs

Period segment (all, 90, 30, 7 days), refresh, balance (asks Comfy; otherwise cached for two minutes), backfill
(looks up the cost of finished API runs that have none), export my usage (JSON ledger), import a teammate's ledger,
team ledger CSV.

Cards: project total (runs + expenses), my usage (when there is a team), billed runs / images / selects, cost per
image, the shared account balance, the budget bar. Then: the team table (per person, with where their ledger came
from), by model (runs, images, average generation time, cost per image, selects, select rate), by unit, by day, other
expenses (add one with date, service, amount, note), billed runs newest first.

**One Comfy account, several machines.** Comfy's billing events carry only a prompt id, so each monitor can attribute
only its own runs; the balance it shows is the shared pool. Set `costs.person` on every machine. With a
`costs.shared_dir` (a Dropbox or Drive folder everyone can reach) each monitor drops its ledger there after every
billed run and reads the others', so every machine shows the whole project. Without one, each teammate presses
"Export my usage" and sends the JSON; "Import a ledger" stores it (the newest export per person wins; a prompt id is
never counted twice). The CSV merges everyone's runs and expenses for the invoice.

## Settings

A form over `project.json`, grouped as the config reference is (`docs/CONFIG.md`): project, terminology, server,
paths, versions (with "Start next version"), selects and naming, export templates, costs, runs, engines; then the
collections editor (cards with sections, select rules, order), the `servers.json` editor, and the raw JSON as a last
resort. Save validates on the server; problems are listed and nothing is written until they are fixed. Saved settings
apply at once (the server re-reads the file); the port needs a restart.

## Design rules (keep them when extending)

These came out of reviewing hundreds of images per session; the page must stay scannable as projects grow.

- Group controls by purpose: row 1 = where and how (view, group, order, refresh), row 2 = what (filters, sections).
- Use the page's own vocabulary: segmented `.vtoggle` for exclusive choices, toggle `.chip` for filters, `.pill` for
  labels and states, `.btn` for actions, `.ico` for per-tile buttons. No raw checkboxes or bare links in toolbars.
- One short muted hint line, never paragraphs of help; the hint says what an action does *here*.
- Remember filter, order, collapse and size state per browser; versions are remembered until the current one changes.
- Pin toolbars and group headers (sticky), so the context stays visible while scrolling.
- Colors mean the same thing everywhere: a collection's color on its category pill, section chip and section square;
  gold = select, red = NG, accent = picked / compare / running, amber = queued / warning, green = ok / remote-mirror
  categories kept muted.
- Tiles keep the image's own shape; nothing is cropped. Captions sit on the tile, not under it.
- Every destructive or paid action asks once (re-roll, remove, delete export); marks never ask.
- Check a layout change with a screenshot at a wide and a narrow width before calling it done.
