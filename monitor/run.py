#!/usr/bin/env python3
"""Run a ComfyUI API-format workflow via comfy-cli and log it for the monitor.

  python3 monitor/run.py <graph.api.json> --title "what this run tests" [--group <shot id>] [--server local|<remote>]
                         [--tag variant ...] [--note "..."] [--retarget] [--dry-run] [--remote-ok] [--no-bill]

Writes <runs>/<YYYY-MM-DD>_<nnn>.json (paths.runs in project.json) and keeps it updated while the run executes (status,
current node, outputs, timing), so the monitor shows live progress. When the run ends its outputs are collected into the
project's local output tree (collect.py), and, for a graph with partner API nodes, the billed cost is looked up and
logged (credits.py; costs.bill_runs in project.json).

Every generation of a project goes through this script so it is logged. The title names the variable being tested.

Server choice: --server names one from servers.json; without it, the engine of the graph (api / flux2 / open_edit /
other) picks among the servers whose "engines" list allows it, shortest queue first. API-node graphs stay on local
servers unless --remote-ok is given or the server's "api_models" allow the graph's model. A server with an active
"reservation" refuses what the reservation blocks, flags or not.

Versions: when versions.enabled, a graph whose save path points at an older version is refused; --retarget rewrites the
save path (and the base-section file suffix) to the current version and runs that copy (<name>_retarget_<V>.api.json).
--dry-run checks the graph (engine, save path, version, server) and stops before anything is queued or logged.
"""
import argparse, fnmatch, glob, hashlib, json, os, re, subprocess, sys, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

CFG = config.load()
ROOT = CFG["_root"]
RUNS = CFG["_paths"]["runs"]
COMFY = CFG["_paths"]["comfy_bin"]
INPUT_ROOT = CFG["_paths"]["comfy_input"]
SERVERS = config.servers()
API_NODES = set(config.BUILTIN_API_NODES) | set(CFG["engines"].get("api_nodes_extra") or [])
GEN_NODES = API_NODES | {"KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced", "SamplerCustom"} | set(CFG["engines"].get("generator_nodes_extra") or [])


def is_api_node(node):
    """Partner API nodes (Nano Banana / Gemini / GPT Image / Seedance / Kling ...), by class name or a gemini model input."""
    name = str(node.get("class_type", ""))
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if name in API_NODES or "nanobanana" in normalized or "gemini" in normalized:
        return True
    inputs = node.get("inputs", {})
    return any(isinstance(inputs.get(key), str) and "gemini" in inputs[key].lower() for key in ("model", "model_name", "model_id"))


def engine_of(g):
    """api (partner API nodes), flux2 (Flux 2 UNET), open_edit (Qwen / SenseNova editors) or other."""
    if any(is_api_node(n) for n in g.values()):
        return "api"
    if any(n.get("class_type") == "UNETLoader" and "flux" in str(n["inputs"].get("unet_name", "")).lower() for n in g.values()):
        return "flux2"
    for n in g.values():
        name = re.sub(r"[^a-z0-9]", "", str(n.get("class_type", "")).lower())
        model = re.sub(r"[^a-z0-9]", "", str(n.get("inputs", {}).get("unet_name", "")).lower())
        if ("qwen" in name and "edit" in name) or name == "textencodeqwenimage21" or "sensenova" in name or "qwenimageedit" in model:
            return "open_edit"
    return "other"


def allowed_api_models(graph, allowed_models):
    """True when every generator in the graph is a GPT Image node whose model the server lists in api_models."""
    if not graph or not allowed_models:
        return False
    passive = {"LoadImage", "LoadImageMask", "SaveImage", "PreviewImage", "Image Save", "ImageScale", "ImageScaleBy", "ImageBatch",
               "ImageToMask", "MaskToImage", "ImageCompositeMasked", "InvertMask", "SolidMask", "MultiImageLoader", "ImageBatchSplitter"}
    found = False
    for node in graph.values():
        if node.get("class_type") in passive:
            continue
        if node.get("class_type") != "OpenAIGPTImageNodeV2":
            return False
        model = node.get("inputs", {}).get("model")
        if isinstance(model, dict):
            model = model.get("model")
        if not isinstance(model, str) or model not in allowed_models:
            return False
        found = True
    return found


def reservation_reason(server, engine, graph=None, servers=None):
    """A reservation on a server is an unconditional exclusion (no flag overrides it)."""
    reservation = (SERVERS if servers is None else servers)[server].get("reservation", {})
    if not reservation.get("active"):
        return None
    allowed = reservation.get("allowed_engines")
    blocked = engine in reservation.get("blocked_engines", []) or (allowed is not None and engine not in allowed)
    if graph is not None:
        actual = engine_of(graph)
        blocked = blocked or actual in reservation.get("blocked_engines", []) or (allowed is not None and actual not in allowed)
        blocked = blocked or any(fnmatch.fnmatchcase(str(n.get("class_type", "")).lower(), pat.lower())
                                 for n in graph.values() for pat in reservation.get("blocked_node_patterns", []))
    if blocked:
        return f"{server} is reserved: {reservation.get('reason', 'queue reservation active')}."
    return None


def pick_server(engine, requested, remote_ok=False, graph=None):
    import urllib.request
    if requested and requested not in SERVERS:
        sys.exit(f"unknown server '{requested}' (have: {', '.join(SERVERS)})")
    ok = [n for n, c in SERVERS.items() if engine in c.get("engines", ["api", "flux2", "open_edit", "other"])] if engine in ("api", "flux2", "open_edit") else list(SERVERS)
    if engine == "api":  # API jobs stay local unless asked (or the model is on the server's approved list)
        ok = [n for n in ok if SERVERS[n].get("kind", "local") == "local"]
        if requested in SERVERS and SERVERS[requested].get("kind", "local") != "local":
            if remote_ok or allowed_api_models(graph, SERVERS[requested].get("api_models", [])):
                ok.append(requested)
    if not requested:
        ok = [n for n in ok if SERVERS[n].get("auto_select", True)]
    blocked = {n: reservation_reason(n, engine, graph) for n in SERVERS}
    if requested and blocked[requested]:
        sys.exit(f"refusing: {blocked[requested]}")
    ok = [n for n in ok if not blocked[n]]
    if not ok:
        sys.exit(f"refusing: no server allows {engine} graphs (check servers.json engines / reservations, or pass --remote-ok)")
    if requested:
        if requested not in ok:
            sys.exit(f"refusing: {engine} graphs may only run on {', '.join(ok)}; '{requested}' is not allowed (API graphs on a remote server need --remote-ok)")
        return requested
    if len(ok) == 1:
        return ok[0]
    best, depth = ok[0], None
    import tunnel
    for n in ok:  # shortest queue wins
        try:
            port = tunnel.up(n)
            q = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/queue", timeout=4))
            d = len(q["queue_running"]) + len(q["queue_pending"])
            if depth is None or d < depth:
                best, depth = n, d
        except SystemExit as e:
            print(f"[run] {n} unavailable: {e}")
    return best


def graph_inputs(g):
    files = []
    for n in g.values():
        i = n.get("inputs", {})
        if n.get("class_type") == "MultiImageLoader":
            try:
                files += json.loads(i.get("images", "[]"))
            except Exception:
                pass
        elif n.get("class_type") in ("LoadImage", "LoadImageMask", "LoadVideo", "LoadAudio") and isinstance(i.get("image", i.get("file", i.get("audio"))), str):
            files.append(i.get("image") or i.get("file") or i.get("audio"))
    return list(dict.fromkeys(files))


def upload_inputs(g, port):
    """Stage the graph's input files on a remote server (an input already on the server and missing locally is accepted)."""
    import urllib.parse, urllib.request
    for rel in graph_inputs(g):
        src = os.path.join(INPUT_ROOT, rel)
        sub, fn = os.path.split(rel)
        if not os.path.isfile(src):
            query = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": "input"})
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/view?{query}", timeout=10) as response:
                    response.read(1)
            except Exception as e:
                sys.exit(f"refusing: input {rel!r} is missing locally and unavailable on the destination: {e}")
            print(f"[run]   using staged remote input {rel}")
            continue
        r = subprocess.run(["curl", "-fsS", "--connect-timeout", "10", "--max-time", "300", "-F", f"image=@{src};filename={fn}",
                            "-F", f"subfolder={sub}", "-F", "type=input", "-F", "overwrite=true", f"http://127.0.0.1:{port}/upload/image"],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"refusing: could not upload input {rel!r}: {r.stderr.strip()[:300]}")
        print(f"[run]   uploaded input {rel}")


def ref_hashes(refs):
    """sha256 (16 hex) of each local input at submission, so the monitor can show the version a run used after a file is replaced."""
    out = {}
    for rel in refs or []:
        p = os.path.join(INPUT_ROOT, rel)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()[:16]
    return out


def new_id():
    """A monotonic daily id (YYYY-MM-DD_nnn) reserved with a hidden marker, so concurrent submissions and deleted records never reuse one."""
    day = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(RUNS, exist_ok=True)
    pat = re.compile(rf"^\.?{re.escape(day)}_(\d+)\.(?:json|reserved)$")
    number = max([int(m.group(1)) for f in os.listdir(RUNS) if (m := pat.fullmatch(f))], default=0) + 1
    while True:
        rid = f"{day}_{number:03d}"
        try:
            with open(os.path.join(RUNS, f".{rid}.reserved"), "x") as fh:
                fh.write(f"pid={os.getpid()}\n")
        except FileExistsError:
            number += 1; continue
        if not os.path.exists(os.path.join(RUNS, rid + ".json")):
            return rid
        number += 1


def sval(g, v):
    """A string input, following a link into the string node that feeds it (StringConcatenate, primitives)."""
    if not isinstance(v, list):
        return v
    src = g.get(v[0], {}); si = src.get("inputs", {})
    if src.get("class_type") == "StringConcatenate":
        return f"{sval(g, si.get('string_a'))}{si.get('delimiter', '')}{sval(g, si.get('string_b'))}"
    for k in ("text", "Text", "value", "string"):   # Text / DF_Text / primitives, possibly fed by another string node
        if isinstance(si.get(k), str):
            return si[k]
        if isinstance(si.get(k), list):
            return sval(g, si[k])
    return ""   # a node whose text is only known at run time (an LLM, a splitter): the static part of the path ends here


def graph_details(g):
    """What the monitor shows about a graph: prompt, inputs, seed, model, batch, aspect, resolution, LoRAs, sampler, save path."""
    d = {"refs": [], "seed": None, "model": None, "batch": None, "aspect": None, "resolution": None, "prompt": "", "save_path": None, "quality": None}

    def cond_text(link):
        if not isinstance(link, list) or len(link) < 2:
            return None
        node = g.get(link[0], {}); inputs = node.get("inputs", {})
        if node.get("class_type") == "TextEncodeQwenImage21":
            field = {0: "prompt", 1: "negative_prompt"}.get(link[1])
            return inputs.get(field) if field else None
        return inputs.get("prompt", inputs.get("text"))

    def ar_of(w, h):
        try:
            r = w / h
        except Exception:
            return None
        for lab, val in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1), ("4:3", 4 / 3), ("3:4", 3 / 4), ("3:2", 1.5), ("2:3", 2 / 3), ("21:9", 21 / 9)):
            if abs(r - val) < 0.02:
                return lab
        return f"{w}x{h}"

    for n in g.values():
        ct, inp = n.get("class_type"), n.get("inputs", {})
        if ct == "MultiImageLoader":
            try:
                d["refs"] = json.loads(inp.get("images", "[]"))
            except Exception:
                pass
        elif ct == "Seed":
            d["seed"] = inp.get("seed")
        elif ct in ("NanoBanana2Batch", "NanoBananaProBatch", "NanoBananaBatch"):
            d["model"] = inp.get("model"); d["batch"] = inp.get("batch_size")
            d["aspect"] = inp.get("aspect_ratio"); d["resolution"] = inp.get("resolution")
            if isinstance(inp.get("prompt"), str) and not d["prompt"]:
                d["prompt"] = inp["prompt"]
            if isinstance(inp.get("seed"), int) and d["seed"] is None:
                d["seed"] = inp["seed"]
            for key, val in inp.items():
                if key.startswith("reference_images.") and isinstance(val, list):
                    src = g.get(val[0], {})
                    if src.get("class_type") == "LoadImage":
                        d["refs"].append(src["inputs"].get("image"))
        elif ct in ("GeminiImage2Node", "GeminiNanoBanana2", "GeminiImageNode"):
            d["model"] = inp.get("model", ct); d["prompt"] = d["prompt"] or inp.get("prompt", ""); d["seed"] = d["seed"] if d["seed"] is not None else inp.get("seed")
            d["aspect"] = inp.get("aspect_ratio"); d["resolution"] = inp.get("resolution"); d["batch"] = (d["batch"] or 0) + 1
        elif ct in ("OpenAIGPTImageNodeV2", "OpenAIGPTImage1"):
            d["model"] = inp.get("model") if isinstance(inp.get("model"), str) else ct; d["batch"] = inp.get("n")
            size = inp.get("model.size", inp.get("size"))
            if size == "Custom":
                size = f"{inp.get('model.custom_width')}x{inp.get('model.custom_height')}"
            d["resolution"] = size; d["quality"] = inp.get("model.quality", inp.get("quality"))
            try:
                w, h = map(int, str(size).split("x")); d["aspect"] = ar_of(w, h)
            except Exception:
                d["aspect"] = "mask edit" if inp.get("model.mask") else None
            if d["seed"] is None:
                d["seed"] = inp.get("seed")
            if not d["prompt"]:
                d["prompt"] = inp.get("prompt", "")
            refs = []
            for key, val in inp.items():
                if (key.startswith("model.images.") or key == "image") and isinstance(val, list):
                    src = g.get(val[0], {})
                    if src.get("class_type") == "LoadImage":
                        refs.append(src["inputs"].get("image"))
            mask = inp.get("model.mask")
            if isinstance(mask, list):
                m2 = g.get(mask[0], {})
                src = g.get(m2["inputs"]["image"][0], {}) if m2.get("class_type") == "ImageToMask" else m2
                if src.get("class_type") == "LoadImage":
                    refs.append(src["inputs"].get("image"))
            if refs and not d["refs"]:
                d["refs"] = refs
        elif ct == "LLMPromptBuilder":
            d["prompt"] = inp.get("prompt", "")
        elif ct in ("ByteDanceSeedreamNodeV2", "Flux2ImageNode", "Flux2ProImageNode", "Flux2MaxImageNode", "GeminiNanoBanana2V2"):
            d["model"] = inp.get("model", ct); d["prompt"] = inp.get("prompt", ""); d["seed"] = inp.get("seed")
            w, h = inp.get("model.width", inp.get("width")), inp.get("model.height", inp.get("height"))
            d["resolution"] = f"{w}x{h}" if w and h else inp.get("model.resolution")
            d["aspect"] = inp.get("model.aspect_ratio", ar_of(w, h) if w and h else None)
        elif ct == "CFGGuider":
            d["cfg"] = inp.get("cfg")
            pos = g.get(inp["positive"][0], {}) if isinstance(inp.get("positive"), list) else {}
            neg = g.get(inp["negative"][0], {}) if isinstance(inp.get("negative"), list) else {}
            ptxt = pos.get("inputs", {}).get("text")
            if isinstance(ptxt, list):
                ptxt = g.get(ptxt[0], {}).get("inputs", {}).get("value")
            if isinstance(ptxt, str):
                d["prompt"] = ptxt
            if isinstance(neg.get("inputs", {}).get("text"), str):
                d["negative"] = neg["inputs"]["text"]
        elif ct == "BasicGuider":  # Flux 2 dev: the prompt sits behind guidance / regional conditioning nodes
            src, seen = inp.get("conditioning"), set()
            while isinstance(src, list) and src[0] not in seen:
                seen.add(src[0]); n2 = g.get(src[0], {}); i2 = n2.get("inputs", {})
                if n2.get("class_type") == "CLIPTextEncode":
                    if isinstance(i2.get("text"), str):
                        d["prompt"] = i2["text"]
                    break
                src = i2.get("conditioning") or i2.get("cond_NEW") or i2.get("conditioning_1")
        elif ct == "FluxGuidance":
            d["cfg"] = inp.get("guidance")
        elif ct == "CreateHookLora":
            d.setdefault("loras", []).append({"name": os.path.basename(str(inp.get("lora_name"))).replace(".safetensors", "") + "  [regional hook]", "strength": inp.get("strength_model")})
        elif ct == "RandomNoise":
            d["seed"] = inp.get("noise_seed")
        elif ct in ("KSampler", "KSamplerAdvanced"):
            d["seed"] = inp.get("seed", inp.get("noise_seed")); d["steps"] = inp.get("steps"); d["cfg"] = inp.get("cfg"); d["sampler"] = inp.get("sampler_name")
            positive = cond_text(inp.get("positive"))
            if isinstance(positive, str):
                d["prompt"] = positive
            d["negative"] = cond_text(inp.get("negative"))
        elif ct == "UNETLoader":
            d["model"] = os.path.basename(str(inp.get("unet_name", ""))).replace(".safetensors", "")
        elif ct == "CheckpointLoaderSimple":
            d["model"] = os.path.basename(str(inp.get("ckpt_name", ""))).replace(".safetensors", "")
        elif ct == "SenseNovaU1ModelLoader":
            d["model"] = inp.get("model_weights")
            if isinstance(inp.get("lora_name"), str) and inp["lora_name"].strip():
                d.setdefault("loras", []).append({"name": inp["lora_name"], "strength": inp.get("lora_strength", 1.0)})
        elif ct == "SenseNovaU1LocalImageEdit":
            d["prompt"] = inp.get("prompt", ""); d["seed"] = inp.get("seed"); d["batch"] = inp.get("batch_size", 1)
            d["steps"] = inp.get("num_steps"); d["cfg"] = inp.get("cfg_scale")
        elif ct == "RepeatLatentBatch":
            d["batch"] = inp.get("amount", 1)
        elif ct in ("ImageScale", "EmptyLatentImage", "EmptySD3LatentImage") or (ct == "SenseNovaU1EditOutputSize" and inp.get("preset") == "Custom"):
            w, h = inp.get("width"), inp.get("height")
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                d["resolution"] = f"{w}x{h}"; d["aspect"] = ar_of(w, h)
            if ct != "ImageScale" and isinstance(inp.get("batch_size"), int):
                d["batch"] = inp["batch_size"]
        elif ct == "Flux2Scheduler":
            d["steps"] = inp.get("steps")
        elif ct == "KSamplerSelect":
            d["sampler"] = inp.get("sampler_name")
        elif ct == "EmptyFlux2LatentImage":
            d["batch"] = inp.get("batch_size")
            val = lambda v: g.get(v[0], {}).get("inputs", {}).get("value") if isinstance(v, list) else v
            w, h = val(inp.get("width")), val(inp.get("height"))
            d["resolution"] = f"{w}x{h}"; d["aspect"] = ar_of(w, h) if w and h else d.get("aspect")
        elif ct == "Power Lora Loader (rgthree)":
            for k2, v2 in inp.items():
                if k2.startswith("lora_") and isinstance(v2, dict) and v2.get("on") and v2.get("lora") not in (None, "None"):
                    d.setdefault("loras", []).append({"name": os.path.basename(v2["lora"]).replace(".safetensors", ""), "strength": v2.get("strength")})
        elif ct == "FreeFuseLoRALoader":
            d.setdefault("loras", []).append({"name": os.path.basename(str(inp.get("lora_name"))).replace(".safetensors", "") + f"  [FreeFuse adapter: {inp.get('adapter_name')}]", "strength": inp.get("strength_model")})
        elif ct in ("LoraLoaderModelOnly", "LoraLoader"):
            d.setdefault("loras", []).append({"name": os.path.basename(str(inp.get("lora_name"))).replace(".safetensors", ""), "strength": inp.get("strength_model")})
        elif ct == "SaveImage" and isinstance(inp.get("filename_prefix"), str):
            d["save_path"] = inp.get("filename_prefix")
        elif ct in ("SaveVideo", "SaveAudio", "SaveAudioMP3", "SaveWEBM", "SaveAnimatedWEBP") and isinstance(inp.get("filename_prefix"), str):
            d["save_path"] = d["save_path"] or inp.get("filename_prefix")
        elif ct == "Image Save":
            d["save_path"] = f"{sval(g, inp.get('output_path'))}/{sval(g, inp.get('filename_prefix'))}"
    if isinstance(d.get("prompt"), list):   # the prompt is produced at run time (an LLM, a splitter): resolve it from the history afterwards
        d["prompt_link"] = d["prompt"]; d["prompt"] = ""
    if not d.get("prompt"):
        texts = [n["inputs"]["text"] for n in g.values() if n.get("class_type") == "CLIPTextEncode" and isinstance(n["inputs"].get("text"), str) and n["inputs"]["text"] != d.get("negative")]
        if texts:
            d["prompt"] = max(texts, key=len)
    engine = engine_of(g)
    if engine == "api":
        gens = [n for n in g.values() if is_api_node(n) and n.get("class_type") not in ("ClaudeNode", "GeminiNode")]
        if gens:
            d["batch"] = sum(n["inputs"].get("n", n["inputs"].get("batch_size", n["inputs"].get("model.max_images", 1))) or 1 for n in gens)
    if engine in ("api", "open_edit"):
        d["refs"] = list(dict.fromkeys(d["refs"] + [n["inputs"]["image"] for n in g.values() if n.get("class_type") == "LoadImage" and isinstance(n.get("inputs", {}).get("image"), str)]))
    if engine == "open_edit" and not d["prompt"]:
        prompts = [n.get("inputs", {}).get("prompt") for n in g.values() if "qwen" in str(n.get("class_type", "")).lower()]
        prompts = [p for p in prompts if isinstance(p, str) and p != d.get("negative")]
        if prompts:
            d["prompt"] = max(prompts, key=len)
    d["batch"] = d["batch"] or 1
    return d


# ---- save paths and versions -------------------------------------------------------------------------------------------

def project_rel_path(path):
    """A graph's save path relative to the project's output tree: the server's output_prefix (or the absolute output root) stripped."""
    p = os.path.expanduser(path or "")
    root = os.path.expanduser(CFG["_paths"]["output_root"]).rstrip("/")
    if os.path.isabs(p):
        return p[len(root) + 1:] if p.startswith(root + "/") else p
    pre = os.path.expanduser(CFG["_paths"].get("output_prefix") or "")
    return p[len(pre):] if pre and p.startswith(pre) else p


def history_prompt(g, link, prompt_id, port=8188):
    """The text a run-time prompt link carried, read from ComfyUI's history: the text output of the linked node itself or of a
    preview node fed by the same link. Several texts (one per shot) are joined. None when the history has nothing."""
    if not (isinstance(link, list) and prompt_id):
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{prompt_id}", timeout=8) as r:
            entry = (json.load(r) or {}).get(prompt_id) or {}
    except Exception:
        return None
    outs = entry.get("outputs") or {}
    want = [str(link[0])] + [nid for nid, n in g.items() if any(v == link for v in (n.get("inputs") or {}).values())]
    for nid in want:
        texts = (outs.get(nid) or {}).get("text") or (outs.get(nid) or {}).get("string") or []
        texts = [t for t in (texts if isinstance(texts, list) else [texts]) if isinstance(t, str) and t.strip()]
        if texts:
            return "\n\n".join(t.strip() for t in texts)
    return None


def save_nodes(g):
    """[(node id, class, save path)] for every save node, with linked string inputs resolved."""
    out = []
    for nid, n in g.items():
        ct, inp = n.get("class_type"), n.get("inputs", {})
        if ct in ("SaveImage", "SaveVideo", "SaveAudio", "SaveAudioMP3", "SaveWEBM", "SaveAnimatedWEBP"):
            out.append((nid, ct, sval(g, inp.get("filename_prefix"))))
        elif ct == "Image Save":
            out.append((nid, ct, f"{sval(g, inp.get('output_path'))}/{sval(g, inp.get('filename_prefix'))}"))
    return out


def path_version(path):
    """The version folder a save path targets ("V2"), or None (not a versioned collection, or versions off)."""
    c = config.classify_path(CFG, path + ".png") if isinstance(path, str) else None
    return c[3] if c and c[0]["versioned"] else None


def frozen_saves(g):
    """Save nodes that target a version other than the current one: [(node id, class, path, version)]."""
    if not CFG["versions"].get("enabled") or not CFG["versions"].get("refuse_frozen", True):
        return []
    return [(nid, ct, p, v) for nid, ct, p in save_nodes(g) if isinstance(p, str) and (v := path_version(p)) and v != CFG["_cur"]]


SAVE_KEYS = ("filename_prefix", "output_path", "string_a", "string_b", "text", "value", "string")


def retarget(g):
    """Point every versioned save path at the current version, in place. Returns [(old, new)]."""
    if not CFG["versions"].get("enabled"):
        return []
    v = CFG["versions"]; cur = CFG["_cur_n"]; changes = []
    fld = re.escape(v["folder"]).replace(r"\{n\}", r"\d+"); stg = re.escape(v["staging"]).replace(r"\{n\}", r"\d+")
    suf = re.escape(v["file_suffix"]).replace(r"\{n\}", r"\d+")

    def fix(node, key):
        val = node.get("inputs", {}).get(key)
        if isinstance(val, list):
            for k in SAVE_KEYS:
                fix(g.get(val[0], {}), k)
            return
        if not isinstance(val, str):
            return
        new = re.sub(rf"(^|/)({stg})(?=/|$)", lambda m: m.group(1) + v["staging"].format(n=cur), val)
        new = re.sub(rf"(^|/)({fld})(?=/|$)", lambda m: m.group(1) + v["folder"].format(n=cur), new)
        new = re.sub(rf"({suf})$", v["file_suffix"].format(n=cur), new)  # base-section file prefix: <group>_v2 -> <group>_v3
        if new != val:
            node["inputs"][key] = new; changes.append((val, new))

    for n in g.values():
        if n.get("class_type") in ("SaveImage", "SaveVideo", "SaveAudio", "SaveAudioMP3", "SaveWEBM", "SaveAnimatedWEBP"):
            fix(n, "filename_prefix")
        elif n.get("class_type") == "Image Save":
            fix(n, "output_path"); fix(n, "filename_prefix")
    return changes


def clean_names(rec, scfg):
    """Drop the trailing underscore ComfyUI's built-in save nodes add (name_00001_.png -> name_00001.png) on a local server's files."""
    if not CFG.get("naming", {}).get("strip_trailing_underscore", True) or scfg.get("kind", "local") != "local":
        return
    root = scfg["output_root"]; out = []
    for rel in rec["outputs"]:
        d, fn = os.path.split(rel); base, ext = os.path.splitext(fn)
        m = re.match(r"^(.*_\d+)_$", base)
        src = os.path.join(root, rel)
        if not m or not os.path.isfile(src):
            out.append(rel); continue
        new = m.group(1) + ext; k = 1
        while os.path.exists(os.path.join(root, d, new)):
            new = f"{m.group(1)}_{k}{ext}"; k += 1
        os.rename(src, os.path.join(root, d, new)); out.append(os.path.join(d, new) if d else new)
    rec["outputs"] = out


def bill(rec):
    """Look the billed cost up and log it in the shared credit log (append-only, skips ids already logged)."""
    cr = os.path.join(HERE, "credits.py"); c = CFG["costs"]
    try:
        subprocess.run([sys.executable, cr, "log", rec["prompt_id"], "--client", c["client"], "--project", c["project"],
                        "--task", f"{rec.get('group') or ''}: {rec['title']}".strip(": "), "--output", ", ".join(os.path.basename(o) for o in rec["outputs"][:4])],
                       capture_output=True, text=True, timeout=180)
        out = subprocess.run([sys.executable, cr, "cost", rec["prompt_id"], "--wait", "5"], capture_output=True, text=True, timeout=120).stdout
        j = json.loads(out)
        row = (j.get("runs") or {}).get(rec["prompt_id"]) or {}; bal = j.get("balance") or {}
        return {"credits": row.get("credits"), "usd": row.get("usd"), "api_calls": row.get("api_calls"), "models": row.get("models"),
                "balance_usd": bal.get("usd"), "balance_credits": bal.get("credits"), "checked": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        return {"error": str(e)[:300]}


def main():
    unit = CFG["terms"].get("unit", "shot")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow")
    ap.add_argument("--title", default=None, help="what this run tests (shown on the run card)")
    ap.add_argument("--group", f"--{unit}", dest="group", default=None, help=f"the {unit} this run belongs to (default: read from the save path)")
    ap.add_argument("--server", default=None, help="a server from servers.json (default: by engine, shortest queue)")
    ap.add_argument("--remote-ok", "--aws-ok", dest="remote_ok", action="store_true", help="allow an API-node graph on a remote server (ask the user first)")
    ap.add_argument("--timeout", type=int, default=1200, help="maximum seconds of silence between Comfy events")
    ap.add_argument("--retarget", action="store_true", help="a graph that saves into an older version: save into the current one instead")
    ap.add_argument("--dry-run", action="store_true", help="check the graph and stop before anything is queued or logged")
    ap.add_argument("--tag", action="append", default=[], help="variant tag(s) recorded on the run (repeatable)")
    ap.add_argument("--note", default=None, help="a free note recorded on the run")
    ap.add_argument("--no-bill", action="store_true", help="skip the credit lookup for this run")
    args = ap.parse_args()

    wf = os.path.abspath(args.workflow)
    g = json.load(open(wf))
    frozen = frozen_saves(g)
    if frozen:
        if not args.retarget:
            sys.exit("\n".join(f"refusing: this graph saves into version {v} (node {nid} {ct}: {p}); current version is {CFG['_cur']}: "
                               f"change the save path or pass --retarget" for nid, ct, p, v in frozen))
        for old, new in retarget(g):
            print(f"[run] retarget: {old} -> {new}")
        if left := frozen_saves(g):
            sys.exit(f"refusing: --retarget could not move node {left[0][0]} {left[0][1]} ({left[0][2]}) to {CFG['_cur']}; change the save path in the graph")
        wf = re.sub(r"(_retarget_[^.]+)?\.api\.json$|\.json$", f"_retarget_{CFG['_cur']}.api.json", wf, count=1)
        if not args.dry_run:
            json.dump(g, open(wf, "w"), indent=1)
    det = graph_details(g); engine = engine_of(g)
    rel = re.sub(r"/{2,}", "/", project_rel_path(det["save_path"] or ""))
    cls = config.classify_path(CFG, rel + ("x.png" if rel.endswith("/") else ".png"))   # a path that ends at a folder: classify the folder
    if args.dry_run:
        where = f"{cls[0]['id']} / {cls[1]}" + (f" / {cls[2]}" if cls[2] else "") + (f" ({cls[3]})" if cls[3] else "") if cls else "unclassified (shown under 'other')"
        print(f"[run] dry run: {os.path.relpath(wf, ROOT)} engine={engine} saves to {det['save_path']} -> {where}; nothing queued")
        return
    server = pick_server(engine, args.server, args.remote_ok, graph=g)
    scfg = SERVERS[server]
    import tunnel
    port = tunnel.up(server)
    if engine in ("api", "open_edit") and scfg.get("kind", "local") != "local":
        upload_inputs(g, port)
    group = args.group or (cls[1] if cls else (det["save_path"] or os.path.basename(wf)).split("/")[-1].rsplit("_", 1)[0])
    rec = {
        "id": new_id(), "group": group, "title": args.title or os.path.basename(wf).replace(".api.json", ""),
        "started": datetime.now().isoformat(timespec="seconds"), "finished": None, "status": "queued",
        "prompt": det["prompt"], "refs": det["refs"], "ref_hashes": ref_hashes(det["refs"]), "seed": det["seed"], "model": det["model"],
        "batch": det["batch"], "aspect": det["aspect"], "resolution": det["resolution"], "quality": det["quality"],
        "workflow": os.path.relpath(wf, ROOT), "prompt_id": None, "outputs": [], "events": [], "current_node": None, "error": None,
        "server": server, "engine": engine, "loras": det.get("loras"), "steps": det.get("steps"), "cfg": det.get("cfg"),
        "sampler": det.get("sampler"), "negative": det.get("negative"), "expected": det.get("batch") or 0,
        "kind": cls[0]["id"] if cls else None, "section": cls[2] if cls else None, "version": cls[3] if cls else None,
        "tags": args.tag, "note": args.note, "gen_seconds": None, "total_seconds": None, "cost": None, "prompt_link": det.get("prompt_link"),
    }
    path = os.path.join(RUNS, rec["id"] + ".json")

    def save():
        tmp = path + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, path)

    save()
    print(f"[run] {rec['id']} {rec['title']} -> {server}:{det['save_path']}")
    cmd = [COMFY, "--skip-prompt", "--where", "local", "run", "--workflow", wf, "--host", "127.0.0.1", "--port", str(port),
           "--wait", "--allow-spend", "--no-watch", "--timeout", str(args.timeout), "--json"]
    t_submit = time.time(); t_gen0 = None; ok = False
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        t = m.get("type")
        if t == "queued":
            rec["prompt_id"] = m.get("prompt_id"); rec["status"] = "running"
        elif t == "executing":
            rec["current_node"] = m.get("title") or m.get("class_type")
            if t_gen0 is None and (m.get("class_type") in GEN_NODES or rec["current_node"] in GEN_NODES):
                t_gen0 = time.time()
            rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": rec["current_node"]})
        elif t == "executed":
            for o in m.get("outputs") or []:
                if o.get("type") == "output" and o.get("filename"):
                    rel = "/".join(x for x in (o.get("subfolder"), o["filename"]) if x)
                    pre = scfg.get("output_prefix", "")
                    if pre and rel.startswith(pre):
                        rel = rel[len(pre):]
                    if rel not in rec["outputs"]:
                        rec["outputs"].append(rel); print(f"[run]   saved {rel}")
                        if t_gen0 and rec["gen_seconds"] is None:
                            rec["gen_seconds"] = round(time.time() - t_gen0, 1)
        elif t == "execution_error":
            rec["error"] = (m.get("exception_message") or json.dumps(m))[:800]
        elif t == "envelope":
            ok = bool(m.get("ok"))
            if not ok and m.get("error"):
                rec["error"] = json.dumps(m["error"])[:800]
        save()
    p.wait()
    rec["total_seconds"] = round(time.time() - t_submit, 1)
    if ok and not rec["outputs"] and det.get("save_path"):
        # the save node reported no file names (WAS Image Save without previews): find the new files on disk (waits for a NAS sync).
        # A static path names the file prefix; a path whose tail is only known at run time (an LLM-named shot folder) ends at a
        # folder, and every new image below it counts.
        sp = re.sub(r"/{2,}", "/", det["save_path"]); folder_only = sp.endswith("/")
        sub, prefix = (sp.rstrip("/"), "") if folder_only else os.path.split(sp)
        rel_sub = project_rel_path(sub)
        base = os.path.join(scfg["output_root"], rel_sub) if not os.path.isabs(rel_sub) else rel_sub
        t0 = datetime.fromisoformat(rec["started"]).timestamp() - 5
        hits = []
        for _ in range(45):
            if folder_only:
                hits = sorted(os.path.join(d, f) for d, dirs, files in os.walk(base) if d[len(base):].count("/") < 3
                              for f in files if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and not f.startswith(".") and os.path.getmtime(os.path.join(d, f)) >= t0)
            else:
                hits = sorted(f for f in glob.glob(os.path.join(base, prefix + "_*")) if os.path.getmtime(f) >= t0)
            if len(hits) >= (det.get("batch") or 1):
                break
            time.sleep(2)
        rec["outputs"] = [os.path.relpath(f, scfg["output_root"]) for f in hits]
        rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": f"outputs found on disk: {len(hits)}"})
    if not rec.get("prompt") and det.get("prompt_link") and rec.get("prompt_id"):
        rec["prompt"] = history_prompt(g, det["prompt_link"], rec["prompt_id"], port) or ""
    clean_names(rec, scfg)
    import collect
    if collect.is_collectable(rec):
        rec["current_node"] = "collecting"; save()
        try:
            n = collect.collect_run(rec)
            rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": f"collected {n} file(s) into the local output tree"})
        except Exception as e:
            rec["events"].append({"t": time.strftime("%H:%M:%S"), "node": f"collect failed: {e}"})
    rec["status"] = "done" if ok and rec["outputs"] else "error"
    if rec["status"] == "error" and not rec["error"]:
        rec["error"] = "run ended without saved outputs"
    rec["finished"] = datetime.now().isoformat(timespec="seconds"); rec["current_node"] = None
    save()
    if rec["prompt_id"] and engine == "api" and CFG["costs"].get("enabled") and CFG["costs"].get("bill_runs", True) and not args.no_bill:
        rec["current_node"] = "billing"; save()
        rec["cost"] = bill(rec); rec["current_node"] = None; save()
        try:
            import costs; costs.write_shared()   # the team ledger in costs.shared_dir, when one is configured
        except Exception as e:
            print(f"[run] shared ledger not written: {e}")
        c = rec["cost"] or {}
        if c.get("usd") is not None:
            print(f"[run] cost ${c['usd']:.3f} ({c.get('credits')} credits) · balance ${c.get('balance_usd', 0):.2f}")
        elif c.get("error"):
            print(f"[run] cost lookup failed: {c['error']}")
    print(f"[run] {rec['status']}: {len(rec['outputs'])} files in {rec['total_seconds']}s (gen {rec['gen_seconds']}s)")
    sys.exit(0 if rec["status"] == "done" else 1)


if __name__ == "__main__":
    main()
