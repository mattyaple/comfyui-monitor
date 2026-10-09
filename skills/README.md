# Skills for Claude Code

| skill | what it does |
|---|---|
| `comfyui-monitor` | Set up, update and work with a project's run monitor from this template: the setup questions, the scaffold and check tools, the rules a session follows in a project that has a monitor. |
| `comfyui` | Drive ComfyUI through the comfy-mcp server and comfy-cli: local by default, remote servers over SSH tunnels when asked, the no-trailing-underscore file naming, the credit log and cost reporting rules. Its `scripts/servers.json` lists the remote servers (edit for your team); `references/local-setup.md` describes one machine's install (replace with yours). |

`../install.sh` links both into `~/.claude/skills/` (an existing folder of the same name is left alone) and registers
comfy-mcp with Claude Code. The `comfyui` skill here is a snapshot of its author's working copy with project recipes
and client material removed; keep your own copy's recipes in your `~/.claude/skills/comfyui` if you maintain one.
