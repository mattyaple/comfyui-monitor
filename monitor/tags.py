#!/usr/bin/env python3
"""Finder tags as the monitor's marks: one color for a select, one for NG (project.json selects.tags; Yellow / Red by default).

Read in-process through getxattr (a subprocess per file is too slow for a whole folder); written with xattr so no Finder
automation permission is needed. Finder shows the same tags, and tools/selects_index.py reads them back through mdls.
"""
import ctypes, os, plistlib, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

LABEL_IDX = {"Gray": 1, "Green": 2, "Purple": 3, "Blue": 4, "Yellow": 5, "Red": 6, "Orange": 7}
TAGS_ATTR, INFO_ATTR = "com.apple.metadata:_kMDItemUserTags", "com.apple.FinderInfo"
_libc = ctypes.CDLL(None)
_libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
_libc.getxattr.restype = ctypes.c_ssize_t


def colors():
    t = config.load()["selects"]["tags"]
    return t.get("select", "Yellow"), t.get("ng", "Red")


def _xattr(path, name):
    p, n = path.encode(), name.encode()
    size = _libc.getxattr(p, n, None, 0, 0, 0)
    if size <= 0:
        return b""
    buf = ctypes.create_string_buffer(size)
    size = _libc.getxattr(p, n, buf, size, 0, 0)
    return buf.raw[:size] if size > 0 else b""


def labels(path):
    """The Finder label colors on a file (from the tag list, or the legacy label bits in FinderInfo)."""
    out = set()
    try:
        raw = _xattr(path, TAGS_ATTR)
        for t in (plistlib.loads(raw) if raw else []):
            name, _, num = t.partition("\n")
            out.add(name if name in LABEL_IDX else next((c for c, i in LABEL_IDX.items() if str(i) == num), name))
        info = _xattr(path, INFO_ATTR)
        if len(info) > 9 and (info[9] >> 1) & 7:
            out.add(next(c for c, i in LABEL_IDX.items() if i == (info[9] >> 1) & 7))
    except Exception:
        pass
    return out


def set_label(path, color, on, exclusive=True):
    """Set or clear Finder's <color> label on a file, keeping unrelated tags. The select and NG marks are exclusive: setting
    one clears the other (exclusive=True); another label (the final-pair tag) is set beside them (exclusive=False)."""
    sel, ng = colors()
    read = lambda a: bytes.fromhex("".join(subprocess.run(["xattr", "-px", a, path], capture_output=True, text=True).stdout.split()))
    drop = ({sel, ng} if exclusive else set()) | {color} if on else {color}
    try:
        raw = read(TAGS_ATTR)
        tags = [t for t in (plistlib.loads(raw) if raw else []) if t.split("\n")[0] not in drop] + ([f"{color}\n{LABEL_IDX[color]}"] if on else [])
        if tags:
            subprocess.run(["xattr", "-wx", TAGS_ATTR, plistlib.dumps(tags, fmt=plistlib.FMT_BINARY).hex(), path], check=True)
        else:
            subprocess.run(["xattr", "-d", TAGS_ATTR, path], capture_output=True)
        info = bytearray(read(INFO_ATTR).ljust(32, b"\0")[:32])
        if on or (info[9] >> 1) & 7 == LABEL_IDX[color]:
            info[9] = (info[9] & ~0x0E) | ((LABEL_IDX[color] << 1) if on else 0)
        if any(info):
            subprocess.run(["xattr", "-wx", INFO_ATTR, info.hex(), path], check=True)
        else:
            subprocess.run(["xattr", "-d", INFO_ATTR, path], capture_output=True)
    except Exception as e:
        print(f"set_label({path}, {color}): {e}", file=sys.stderr)


def marks(path):
    """{"tagged": select tag?, "ng": NG tag?, "pair": final-pair tag?} for a file in the local output tree; {} elsewhere
    (remote mirrors cannot be marked)."""
    c = config.load(); root = c["_paths"]["output_root"]
    if not (path and path.startswith(root + "/")):
        return {}
    have = labels(path); sel, ng = colors(); pt = (c.get("pairs") or {}).get("tag") or "Green"
    return {"tagged": sel in have, "ng": ng in have, "pair": pt in have}
