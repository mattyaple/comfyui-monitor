#!/usr/bin/env python3
"""Contact sheets and review PDFs from a spec the server writes (the Export tab) or from the command line.

  uv run --quiet --with reportlab --with pillow python3 monitor/export.py spec.json out.<jpg|pdf>

spec = {
  "template": "contact" | "review",
  "title": "...", "subtitle": "...", "footer": "...",
  "options": {... the template's options from project.json, overridden by the request ...},
  "groups": [{"id": "SC010", "label": "SC010", "note": "", "items": [{"path": "/abs/file.png", "name": "file", "caption": "run · title · seed", "selected": true}]}]
}

contact: one grid, optional group headers (a new row per group); JPG by default, or a PDF with as many pages as needed.
review:  a PDF with a cover page and one page per group (its images in a grid, captions, an optional notes line and prompt).

Paper: letter / a4 / tabloid, landscape or portrait. Everything is drawn with reportlab; Pillow builds the JPG sheet and
reads image sizes. Fonts are the PDF base 14 (Helvetica), so no font files are needed.
"""
import datetime, json, os, sys

from PIL import Image, ImageDraw, ImageFont

spec = json.load(open(sys.argv[1])); OUT = sys.argv[2]
T = spec.get("template", "contact"); O = spec.get("options") or {}
TITLE = spec.get("title") or "Contact sheet"; SUB = spec.get("subtitle") or ""
FOOTER = spec.get("footer") or ""
GROUPS = [g for g in spec.get("groups", []) if g.get("items")]
TODAY = datetime.date.today().isoformat()
PAPER = {"letter": (11, 8.5), "a4": (11.69, 8.27), "tabloid": (17, 11)}.get(str(O.get("paper", "letter")).lower(), (11, 8.5))
if str(O.get("orientation", "landscape")).lower() == "portrait":
    PAPER = (PAPER[1], PAPER[0])


def size_of(p):
    try:
        with Image.open(p) as im:
            return im.size
    except Exception:
        return (16, 9)


CACHE = spec.get("cache") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
MAX_PX = int(O.get("max_px", 1800))   # longest side of an image embedded in a PDF (full-size PNGs make gigabyte files)


def prepped(p):
    """A JPEG copy of the image at most MAX_PX on its longest side, cached by path + mtime + size (PDFs embed this)."""
    import hashlib
    try:
        key = hashlib.md5(f"{p}:{os.path.getmtime(p)}:{MAX_PX}".encode()).hexdigest()
    except OSError:
        return p
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"export_{key}.jpg")
    if not os.path.exists(out):
        try:
            with Image.open(p) as im:
                im = im.convert("RGB"); im.thumbnail((MAX_PX, MAX_PX)); im.save(out, "JPEG", quality=86)
        except Exception:
            return p
    return out


# ---- JPG contact sheet (Pillow) --------------------------------------------------------------------------------------

def font(size, bold=False):
    for name in (["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]):
        try:
            return ImageFont.truetype(name, size, index=1 if bold and name.endswith(".ttc") else 0)
        except Exception:
            continue
    return ImageFont.load_default()


def contact_jpg():
    cols = max(1, int(O.get("columns", 4))); tw = int(O.get("thumb", 900)); gap = 24; pad = 40
    caps = bool(O.get("captions", True)); heads = bool(O.get("group_headers", True))
    f_title, f_sub, f_head, f_cap = font(34, True), font(20), font(26, True), font(17)
    cap_h = 48 if caps else 8; head_h = 56
    # lay rows out: each row holds up to `cols` tiles of one group (a new row per group when headers are on)
    rows = []
    for g in GROUPS:
        items = g["items"]
        if heads:
            rows.append(("head", g))
        for i in range(0, len(items), cols):
            rows.append(("tiles", items[i:i + cols]))
    if not heads:  # pack across groups
        flat = [it for g in GROUPS for it in g["items"]]
        rows = [("tiles", flat[i:i + cols]) for i in range(0, len(flat), cols)]
    heights = []
    for kind, payload in rows:
        if kind == "head":
            heights.append(head_h); continue
        th = max(int(tw * h / w) for w, h in (size_of(it["path"]) for it in payload))
        heights.append(min(th, tw * 2) + cap_h + gap)
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = pad + 100 + sum(heights) + pad
    sheet = Image.new("RGB", (W, H), (17, 20, 24)); d = ImageDraw.Draw(sheet)
    d.text((pad, pad - 6), TITLE, fill=(230, 233, 238), font=f_title)
    d.text((pad, pad + 42), SUB, fill=(139, 149, 163), font=f_sub)
    y = pad + 100
    for (kind, payload), h in zip(rows, heights):
        if kind == "head":
            d.text((pad, y + 12), payload.get("label") or payload["id"], fill=(255, 207, 51), font=f_head)
            y += h; continue
        x = pad
        for it in payload:
            try:
                im = Image.open(it["path"]).convert("RGB")
            except Exception:
                im = Image.new("RGB", (16, 9), (40, 40, 40))
            im.thumbnail((tw, tw * 2))
            sheet.paste(im, (x + (tw - im.width) // 2, y))
            if it.get("selected"):
                d.rectangle([x + (tw - im.width) // 2 - 3, y - 3, x + (tw - im.width) // 2 + im.width + 2, y + im.height + 2], outline=(255, 207, 51), width=5)
            if caps:
                d.text((x, y + h - cap_h - gap + 10), it.get("name", "")[:60], fill=(230, 233, 238), font=f_cap)
                if it.get("caption"):
                    d.text((x, y + h - cap_h - gap + 30), it["caption"][:90], fill=(139, 149, 163), font=font(14))
            x += tw + gap
        y += h
    d.text((pad, H - pad + 6), FOOTER, fill=(139, 149, 163), font=font(15))
    sheet.save(OUT, quality=88)


# ---- PDF (reportlab) -------------------------------------------------------------------------------------------------

def pdf():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    W, H = PAPER[0] * inch, PAPER[1] * inch
    M = 0.5 * inch
    BG, PANEL, INK, MUTED, GOLD = HexColor("#111418"), HexColor("#1a1f26"), HexColor("#e6e9ee"), HexColor("#8b95a3"), HexColor("#ffcf33")
    c = canvas.Canvas(OUT, pagesize=(W, H)); c.setTitle(TITLE)

    def page_bg():
        c.setFillColor(BG); c.rect(0, 0, W, H, stroke=0, fill=1)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8); c.drawRightString(W - M, M / 2, FOOTER)

    def header(text, sub=None):
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 16); c.drawString(M, H - M - 4, text)
        if sub:
            c.setFillColor(MUTED); c.setFont("Helvetica", 9.5); c.drawString(M, H - M - 18, sub)

    def grid(items, x0, y_top, y_bottom, per_row, caps, gap=0.14 * inch):
        """Tiles of equal width in rows of per_row, each tile keeping its image's shape; returns the y it stopped at."""
        tw = (W - 2 * M - gap * (per_row - 1)) / per_row
        y = y_top; i = 0
        while i < len(items):
            row = items[i:i + per_row]
            th = max(tw * sh / sw for sw, sh in (size_of(it["path"]) for it in row))
            avail = y - y_bottom - (22 if caps else 4)
            if th > avail:
                if y != y_top:  # new page for this row
                    return i
                th = max(avail, 1 * inch)
            for k, it in enumerate(row):
                x = x0 + k * (tw + gap); sw, sh = size_of(it["path"]); r = min(tw / sw, th / sh); w, h = sw * r, sh * r
                yy = y - th + (th - h) / 2
                try:
                    c.drawImage(ImageReader(prepped(it["path"])), x + (tw - w) / 2, yy, w, h)
                except Exception:
                    c.setFillColor(PANEL); c.rect(x, yy, tw, th, stroke=0, fill=1)
                if it.get("selected"):
                    c.setStrokeColor(GOLD); c.setLineWidth(2.2); c.rect(x + (tw - w) / 2 - 2, yy - 2, w + 4, h + 4, stroke=1, fill=0)
                if caps:
                    c.setFillColor(INK); c.setFont("Helvetica", 8); c.drawString(x, y - th - 11, it.get("name", "")[:70])
                    c.setFillColor(MUTED); c.setFont("Helvetica", 7); c.drawString(x, y - th - 20, (it.get("caption") or "")[:110])
            y -= th + (26 if caps else 6)
            i += per_row
        return y

    if T == "review" and O.get("cover", True):
        page_bg()
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 30); c.drawString(M, H / 2 + 20, TITLE)
        c.setFillColor(MUTED); c.setFont("Helvetica", 13); c.drawString(M, H / 2 - 8, SUB)
        c.setFont("Helvetica", 11); c.drawString(M, H / 2 - 30, f"{len(GROUPS)} groups · {sum(len(g['items']) for g in GROUPS)} images · {TODAY}")
        c.showPage()
    caps = bool(O.get("captions", True))
    if T == "review":
        per_row = max(1, int(O.get("per_row", 2)))
        for g in GROUPS:
            items = g["items"]; start = 0
            while start < len(items) or start == 0:
                page_bg(); header(g.get("label") or g["id"], g.get("note") or None)
                y_top = H - M - 30
                if O.get("prompt") and g.get("prompt") and start == 0:
                    style = ParagraphStyle("p", fontName="Helvetica", fontSize=8, leading=10, textColor=MUTED)
                    para = Paragraph(g["prompt"].replace("&", "&amp;").replace("<", "&lt;")[:1200], style)
                    _, ph = para.wrap(W - 2 * M, 1.6 * inch); para.drawOn(c, M, y_top - ph); y_top -= ph + 10
                nxt = grid(items[start:], M, y_top, M + 0.3 * inch, min(per_row, max(1, len(items[start:]))) if len(items[start:]) < per_row else per_row, caps)
                if isinstance(nxt, int) and nxt > 0:
                    start += nxt; c.showPage(); continue
                if O.get("notes", True):
                    c.setStrokeColor(HexColor("#2a313b")); c.setLineWidth(0.6); c.line(M, M + 0.55 * inch, W - M, M + 0.55 * inch)
                    c.setFillColor(MUTED); c.setFont("Helvetica", 7.5); c.drawString(M, M + 0.42 * inch, "Notes")
                c.showPage(); break
    else:  # contact as a PDF: as many pages as the grid needs, with group headers
        cols = max(1, int(O.get("columns", 4))); heads = bool(O.get("group_headers", True))
        page_bg(); header(TITLE, SUB); y = H - M - 34
        for g in GROUPS:
            items = g["items"]; start = 0
            if heads:
                if y < M + 1.6 * inch:
                    c.showPage(); page_bg(); header(TITLE, SUB); y = H - M - 34
                c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 11); c.drawString(M, y - 10, g.get("label") or g["id"]); y -= 20
            while start < len(items):
                nxt = grid(items[start:], M, y, M + 0.3 * inch, cols, caps)
                if isinstance(nxt, int):
                    start += nxt; c.showPage(); page_bg(); header(TITLE, SUB); y = H - M - 34
                else:
                    y = nxt; break
        c.showPage()
    c.save()


if OUT.lower().endswith((".jpg", ".jpeg", ".png")):
    contact_jpg()
else:
    pdf()
print(OUT)
