# Local ComfyUI setup notes (per machine)

The skill assumes a ComfyUI on `127.0.0.1:8188`. Everything below describes **one machine (Matt Yaple's) as an example**: how its server is started, where its logs are, which venv its custom nodes use. Replace it with your own notes when you install the skill on another machine; the skill reads this file when it needs to start, restart or debug the local server.

## Example: "ComfyUI Local" wrapper app on an Apple Silicon Mac

- `/Applications/ComfyUI Local.app` (Matt's own wrapper app, `com.mattyaple.comfyui-local`) opens a
  window on `http://127.0.0.1:8188/` and starts the server with
  `nohup ~/ComfyUI/start.sh >> ~/ComfyUI/user/comfyui-server.log 2>&1 &` if it is not already up.
  The server keeps running after the app window closes ("Quit, Keep Server Running"), so check the
  port, not the app.
- `~/ComfyUI` is a git checkout of ComfyUI (0.27.x) using the venv `~/venvs/comfy-current`.
  `start.sh` activates that venv, sets `HF_DEACTIVATE_ASYNC_LOAD=1` (avoids an MPS segfault in
  transformers 5.x) and runs `main.py --listen 127.0.0.1 --port 8188 --max-upload-size 4096`.
  Extra `main.py` flags can be appended: `~/ComfyUI/start.sh --some-flag`.
- The old "Comfy Desktop" app is no longer used; do not open it or use its paths.
- Not running (`server_info` → `running: false`): start it the same way the app does, with
  `open -a "ComfyUI Local"` or
  `nohup ~/ComfyUI/start.sh >> ~/ComfyUI/user/comfyui-server.log 2>&1 &`, then poll
  `curl -s localhost:8188/system_stats` until it answers. Do not use `launch_comfyui`/`restart_comfyui`:
  they start comfy-cli's own server without `start.sh`'s venv and environment.
- Restart (after node installs or a hang): check `curl -s localhost:8188/queue` is empty (or ask),
  `pkill -f "$HOME/ComfyUI/main.py"`, then start as above.
- Logs: `~/ComfyUI/user/comfyui-server.log` (stdout of `start.sh`), `~/ComfyUI/user/comfyui_8188.log`
  (ComfyUI's own), `~/ComfyUI/user/comfyui-app.log` (the wrapper app). `get_logs` may read the wrong
  file for this setup; tail these directly.
- Python packages for custom nodes go into `~/venvs/comfy-current`
  (`~/venvs/comfy-current/bin/pip install -r custom_nodes/<pack>/requirements.txt`), never the uv tool env.
- Apple Silicon: `system_stats` reports no VRAM figure (unified memory). Large Flux 2 / video graphs
  are slow on MPS; that is a reason to suggest comfy4/5, not to route there without asking.

