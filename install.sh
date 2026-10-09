#!/bin/zsh
# Link the skills into ~/.claude/skills and register comfy-mcp with Claude Code (user scope). Safe to re-run.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.claude/skills
for s in comfyui-monitor comfyui; do
  if [ -e ~/.claude/skills/$s ] && [ ! -L ~/.claude/skills/$s ]; then
    echo "~/.claude/skills/$s exists and is not a link: left alone (compare with $HERE/skills/$s)"
  elif [ -L ~/.claude/skills/$s ]; then
    echo "~/.claude/skills/$s already linked -> $(readlink ~/.claude/skills/$s)"
  else
    ln -s "$HERE/skills/$s" ~/.claude/skills/$s && echo "linked ~/.claude/skills/$s"
  fi
done
BIN="$HOME/.local/share/uv/tools/comfy-mcp/bin"
if [ -x "$BIN/comfy-mcp" ]; then
  if command -v claude >/dev/null 2>&1; then
    if claude mcp list 2>/dev/null | grep -q "comfy-mcp"; then echo "comfy-mcp already registered"
    else claude mcp add comfy-mcp -s user -e COMFY_BIN="$BIN/comfy" -- "$BIN/comfy-mcp" && echo "registered comfy-mcp (user scope); start a new Claude Code session"; fi
  else echo "claude CLI not found: register later with: claude mcp add comfy-mcp -s user -e COMFY_BIN=$BIN/comfy -- $BIN/comfy-mcp"; fi
else
  echo "comfy-cli / comfy-mcp not installed yet: uv tool install comfy-mcp --with comfy-cli --python 3.12   (see INSTALL.md)"
fi
echo "done. New project: python3 $HERE/tools/new_monitor.py --target /path/to/project"
