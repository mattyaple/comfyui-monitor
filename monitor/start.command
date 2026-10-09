#!/bin/zsh
# Double-click in Finder (or run from a terminal) to start the monitor for this project.
cd "$(dirname "$0")/.." && exec python3 monitor/start.py
