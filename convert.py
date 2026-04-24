#!/usr/bin/env python3
"""
miro2excalidraw — Convert Miro boards to Excalidraw format via the Miro REST API.

Usage:
    # Single board
    python3 convert.py --token <TOKEN> --board <BOARD_ID>
    python3 convert.py --token <TOKEN> --search "Domains" -o Domains.excalidraw

    # Batch: all boards owned by a user
    python3 convert.py --token <TOKEN> --owner "Jane Doe" --outdir ./output

    # Batch: list of board IDs from a file (one per line)
    python3 convert.py --token <TOKEN> --batch boards.txt --outdir ./output

    # Token can come from MIRO_ACCESS_TOKEN env var
    MIRO_ACCESS_TOKEN=xxx python3 convert.py --board uXjVK3fkjsQ=

Supports: shapes, text, sticky_notes, frames, images, embeds, connectors (arrows).
"""

import argparse
import base64
import html
import json
import hashlib
import os
import re
import struct
import sys
import time
import urllib.request
import urllib.parse
import urllib.error


# ---------------------------------------------------------------------------
# Miro named sticky-note colors → hex
# ---------------------------------------------------------------------------
STICKY_COLORS = {
    "light_yellow": "#FFF9B1",
    "yellow": "#F5D128",
    "orange": "#FF9D48",
    "light_green": "#D5F692",
    "green": "#93D275",
    "dark_green": "#67C6BF",
    "cyan": "#6CD8FA",
    "light_blue": "#A6CCF5",
    "blue": "#7B92FF",
    "light_pink": "#F2A9F2",
    "pink": "#FF9D8A",
    "violet": "#CDA1E0",
    "red": "#F16C7F",
    "gray": "#C9C9C9",
    "black": "#1A1A1A",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def stable_id(seed: str) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:20]


def _stable_int(seed: str) -> int:
    """Deterministic integer from a seed string (unlike hash() which is randomized per session)."""
    return int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)


def strip_html(html_str: str) -> str:
    text = html_str
    # Convert <br> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text)
    # Paragraph breaks
    text = re.sub(r"</p>\s*<p>", "\n", text)

    # Numbered lists: <ol>...<li>...</li>...</ol>
    def _replace_ol(m):
        ol_html = m.group(0)
        items = re.findall(r"<li[^>]*>(.*?)</li>", ol_html, re.DOTALL)
        lines = []
        for i, item_html in enumerate(items, 1):
            item_text = re.sub(r"<[^>]+>", "", item_html).strip()
            lines.append(f"{i}. {item_text}")
        return "\n".join(lines)

    text = re.sub(r"<ol[^>]*>.*?</ol>", _replace_ol, text, flags=re.DOTALL)

    # Bulleted lists: <ul>...<li>...</li>...</ul>
    def _replace_ul(m):
        ul_html = m.group(0)
        items = re.findall(r"<li[^>]*>(.*?)</li>", ul_html, re.DOTALL)
        lines = []
        for item_html in items:
            item_text = re.sub(r"<[^>]+>", "", item_html).strip()
            lines.append(f"- {item_text}")
        return "\n".join(lines)

    text = re.sub(r"<ul[^>]*>.*?</ul>", _replace_ul, text, flags=re.DOTALL)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = html.unescape(text)
    return text.strip()


def miro_color(color_str: str) -> str:
    if color_str in STICKY_COLORS:
        return STICKY_COLORS[color_str]
    if color_str and color_str.startswith("#"):
        return color_str
    return "#000000"


def safe_filename(name: str) -> str:
    return re.sub(r'[^\w\-. ]', '_', name).strip() or "untitled"


def get_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read pixel dimensions from PNG or JPEG header."""
    if data[:4] == b'\x89PNG' and len(data) > 24:
        w = struct.unpack('>I', data[16:20])[0]
        h = struct.unpack('>I', data[20:24])[0]
        return (w, h)
    if data[:2] == b'\xff\xd8':  # JPEG
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xff:
                break
            marker = data[i + 1]
            if marker in (0xc0, 0xc1, 0xc2):
                h = struct.unpack('>H', data[i + 5:i + 7])[0]
                w = struct.unpack('>H', data[i + 7:i + 9])[0]
                return (w, h)
            length = struct.unpack('>H', data[i + 2:i + 4])[0]
            i += 2 + length
    return None


def fit_font_size(text: str, container_w: float, container_h: float,
                   hint_size: float = 16) -> float:
    """Calculate a font size that fits text inside a container."""
    if not text or container_w <= 0 or container_h <= 0:
        return max(8, hint_size)
    lines = text.split("\n")
    max_line_len = max((len(line) for line in lines), default=1)
    nlines = len(lines)
    # Estimate: each char is ~0.6 * fontSize wide
    # Fit horizontally: fontSize <= container_w / (max_line_len * 0.6)
    fit_w = container_w / max(1, max_line_len * 0.55) if max_line_len > 0 else hint_size
    # Fit vertically: fontSize * 1.4 * nlines <= container_h * 0.85
    fit_h = (container_h * 0.85) / max(1, nlines * 1.4)
    # Use the smaller of horizontal/vertical fit, capped by hint
    size = min(fit_w, fit_h, hint_size)
    return max(6, min(size, 48))


def estimate_text_height(text: str, font_size: float, width: float) -> float:
    char_width = font_size * 0.6
    total_lines = 0
    for line in text.split("\n"):
        if not line.strip():
            total_lines += 1
        else:
            chars_per_line = max(1, int(width / char_width))
            total_lines += max(1, -(-len(line) // chars_per_line))
    return total_lines * font_size * 1.4


# ---------------------------------------------------------------------------
# Miro API
# ---------------------------------------------------------------------------
class MiroClient:
    BASE = "https://api.miro.com/v2"

    def __init__(self, token: str):
        self.token = token

    def _get(self, url: str, max_retries: int = 5) -> dict:
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    retry_after = int(e.headers.get("Retry-After", "5"))
                    print(f"    Rate limited, waiting {retry_after}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(retry_after)
                    continue
                raise
        raise RuntimeError(f"Max retries ({max_retries}) exceeded for {url}")

    def _board_path(self, board_id: str) -> str:
        return urllib.parse.quote(board_id, safe="")

    def search_boards(self, query: str) -> list:
        url = f"{self.BASE}/boards?query={urllib.parse.quote(query)}&limit=10"
        return self._get(url).get("data", [])

    def list_all_boards(self) -> list:
        boards = []
        offset = 0
        while True:
            url = f"{self.BASE}/boards?limit=50&offset={offset}"
            data = self._get(url)
            boards.extend(data.get("data", []))
            if offset + 50 >= data.get("total", 0):
                break
            offset += 50
        return boards

    def get_items(self, board_id: str) -> list:
        items = []
        url = f"{self.BASE}/boards/{self._board_path(board_id)}/items?limit=50"
        while url:
            data = self._get(url)
            items.extend(data.get("data", []))
            url = data.get("links", {}).get("next")
        return items

    def download_image(self, image_url: str, use_original: bool = True) -> tuple[bytes, str] | None:
        """Download an image from a Miro image resource URL.
        Returns (image_bytes, mime_type) or None on failure."""
        try:
            # Use original format instead of preview for full quality
            if use_original:
                image_url = image_url.replace("format=preview", "format=original")
            # Step 1: get the signed URL
            data = self._get(image_url)
            signed_url = data.get("url")
            if not signed_url:
                return None
            # Step 2: download the actual image
            req = urllib.request.Request(signed_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                img_bytes = resp.read()
                content_type = resp.headers.get("Content-Type", "image/png")
                # Normalize mime type
                if "jpeg" in content_type or "jpg" in content_type:
                    mime = "image/jpeg"
                elif "svg" in content_type:
                    mime = "image/svg+xml"
                elif "gif" in content_type:
                    mime = "image/gif"
                elif "webp" in content_type:
                    mime = "image/webp"
                else:
                    mime = "image/png"
                return (img_bytes, mime)
        except Exception as e:
            print(f"      Warning: image download failed: {e}")
            return None

    def get_connectors(self, board_id: str) -> list:
        conns = []
        url = f"{self.BASE}/boards/{self._board_path(board_id)}/connectors?limit=50"
        while url:
            data = self._get(url)
            conns.extend(data.get("data", []))
            url = data.get("links", {}).get("next")
        return conns


# ---------------------------------------------------------------------------
# Converters  (Miro item → list[ExcalidrawElement])
# ---------------------------------------------------------------------------
def _base_element(eid: str, etype: str, x: float, y: float, w: float, h: float, **kw):
    return {
        "type": etype,
        "version": 1,
        "versionNonce": _stable_int(eid),
        "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "angle": 0,
        "strokeColor": kw.get("strokeColor", "#1e1e1e"),
        "backgroundColor": kw.get("backgroundColor", "transparent"),
        "fillStyle": kw.get("fillStyle", "solid"),
        "strokeWidth": kw.get("strokeWidth", 1),
        "strokeStyle": kw.get("strokeStyle", "solid"),
        "roughness": 0,
        "opacity": kw.get("opacity", 100),
        "seed": _stable_int(eid + "seed"),
        "groupIds": [],
        "frameId": None,
        "index": kw.get("index", "a0"),
        "roundness": kw.get("roundness"),
        "boundElements": [],
        "updated": 1,
        "link": None,
        "locked": False,
        "isDeleted": False,
    }


def _text_element(eid: str, x: float, y: float, w: float, h: float,
                  text: str, font_size: float, color: str = "#1e1e1e",
                  text_align: str = "center", v_align: str = "middle",
                  container_id: str | None = None):
    el = _base_element(eid, "text", x, y, w, h, strokeColor=color, index="a1")
    # autoResize=False for bound text so text wraps inside the container
    # autoResize=True for standalone text so it sizes naturally
    el.update({
        "text": text,
        "fontSize": font_size,
        "fontFamily": 1,
        "textAlign": text_align,
        "verticalAlign": v_align,
        "containerId": container_id,
        "originalText": text,
        "autoResize": container_id is None,
        "lineHeight": 1.25,
    })
    return el


def convert_shape(item: dict, scale: float, id_map: dict) -> list:
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo, style = item["position"], item["geometry"], item.get("style", {})
    w = geo["width"] * scale
    h = geo.get("height", geo["width"]) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2

    fill_color = miro_color(style.get("fillColor", "#ffffff"))
    fill_opacity = float(style.get("fillOpacity", "1.0"))
    border_color = style.get("borderColor", "#1a1a1a")
    border_width = max(1, float(style.get("borderWidth", "2.0")) * scale)
    border_style = style.get("borderStyle", "normal")
    stroke_style = {"dashed": "dashed", "dotted": "dotted"}.get(border_style, "solid")

    opacity = int(fill_opacity * 100) if 0 < fill_opacity < 1 else 100
    bg = fill_color if fill_opacity > 0 else "transparent"

    shape_type = item.get("data", {}).get("shape", "rectangle")
    excalidraw_type = {"rectangle": "rectangle", "circle": "ellipse",
                       "triangle": "diamond", "rhombus": "diamond",
                       "round_rectangle": "rectangle"}.get(shape_type, "rectangle")

    rect = _base_element(eid, excalidraw_type, x, y, w, h,
                         strokeColor=border_color, backgroundColor=bg,
                         strokeWidth=border_width, strokeStyle=stroke_style,
                         opacity=opacity, roundness={"type": 3})

    content = strip_html(item.get("data", {}).get("content", ""))
    if not content:
        return [rect]

    text_id = stable_id(item["id"] + "_text")
    hint = min(int(style.get("fontSize", "28")), 72) * scale
    font_size = fit_font_size(content, w - 20, h - 10, hint)
    text_color = style.get("color", "#1a1a1a")
    nlines = max(1, len(content.split("\n")))
    th = font_size * 1.4 * nlines

    text_el = _text_element(text_id, x + 10, y + h / 2 - th / 2, w - 20, th,
                            content, font_size, text_color,
                            style.get("textAlign", "center"), "middle", eid)
    rect["boundElements"].append({"id": text_id, "type": "text"})
    return [rect, text_el]


def convert_text(item: dict, scale: float, id_map: dict) -> list:
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo, style = item["position"], item["geometry"], item.get("style", {})
    content = strip_html(item.get("data", {}).get("content", ""))
    if not content:
        return []

    font_size = max(12, min(int(style.get("fontSize", "28")), 144) * scale)
    w = geo.get("width", 200) * scale
    h = estimate_text_height(content, font_size, w)
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2
    text_color = style.get("color", "#1a1a1a")

    return [_text_element(eid, x, y, w, h, content, font_size, text_color,
                          style.get("textAlign", "left"), "top")]


def convert_sticky_note(item: dict, scale: float, id_map: dict) -> list:
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo, style = item["position"], item["geometry"], item.get("style", {})
    w = geo.get("width", 200) * scale
    h = geo.get("height", 200) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2
    fill_color = miro_color(style.get("fillColor", "light_yellow"))

    rect = _base_element(eid, "rectangle", x, y, w, h,
                         backgroundColor=fill_color, roundness={"type": 3})

    content = strip_html(item.get("data", {}).get("content", ""))
    if not content:
        return [rect]

    text_id = stable_id(item["id"] + "_text")
    font_size = fit_font_size(content, w - 20, h - 10, 16)
    nlines = max(1, len(content.split("\n")))
    th = font_size * 1.25 * nlines

    text_el = _text_element(text_id, x + 10, y + h / 2 - th / 2, w - 20, th,
                            content, font_size, container_id=eid)
    rect["boundElements"].append({"id": text_id, "type": "text"})
    return [rect, text_el]


def convert_frame(item: dict, scale: float, id_map: dict) -> list:
    """Convert a Miro frame to an Excalidraw rectangle with dashed border + title."""
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo = item["position"], item.get("geometry", {})
    w = geo.get("width", 500) * scale
    h = geo.get("height", 400) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2

    title = item.get("data", {}).get("title", "") or item.get("data", {}).get("content", "")
    title = strip_html(title)

    frame_rect = _base_element(eid, "rectangle", x, y, w, h,
                               strokeColor="#868e96", backgroundColor="#f8f9fa",
                               strokeWidth=2, strokeStyle="dashed",
                               opacity=30, roundness={"type": 3})

    if not title:
        return [frame_rect]

    text_id = stable_id(item["id"] + "_title")
    font_size = max(12, min(20, w * 0.03))
    text_el = _text_element(text_id, x + 8, y - font_size - 6, w - 16, font_size * 1.25,
                            title, font_size, "#868e96", "left", "top")
    return [frame_rect, text_el]


def convert_image(item: dict, scale: float, id_map: dict,
                   client: "MiroClient | None" = None, files: dict | None = None) -> list:
    """Convert a Miro image to a real Excalidraw image element with embedded data."""
    eid = stable_id(item["id"])
    file_id = stable_id(item["id"] + "_file")
    id_map[item["id"]] = eid

    pos, geo = item["position"], item.get("geometry", {})
    w = geo.get("width", 200) * scale
    h = geo.get("height", 200) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2

    # Try to download the actual image
    image_url = item.get("data", {}).get("imageUrl", "")
    downloaded = False
    if client and files is not None and image_url:
        result = client.download_image(image_url)
        if result:
            img_bytes, mime_type = result

            # Cap element size to actual pixel dimensions to avoid upscaling blur
            px = get_image_dimensions(img_bytes)
            if px:
                px_w, px_h = px
                if w > px_w or h > px_h:
                    aspect = w / h if h > 0 else 1
                    w = min(w, px_w)
                    h = w / aspect
                    if h > px_h:
                        h = min(h, px_h)
                        w = h * aspect
                    # Re-center at the same midpoint
                    mid_x = x + (geo.get("width", 200) * scale) / 2
                    mid_y = y + (geo.get("height", 200) * scale) / 2
                    x = mid_x - w / 2
                    y = mid_y - h / 2

            data_url = f"data:{mime_type};base64,{base64.b64encode(img_bytes).decode()}"
            files[file_id] = {
                "mimeType": mime_type,
                "id": file_id,
                "dataURL": data_url,
                "created": int(time.time() * 1000),
            }
            downloaded = True

    if downloaded:
        el = _base_element(eid, "image", x, y, w, h)
        el["fileId"] = file_id
        el["status"] = "saved"
        el["scale"] = [1, 1]
        el["crop"] = None
        return [el]
    else:
        # Fallback: placeholder rectangle
        title = item.get("data", {}).get("title", "")
        label = f"[Image: {title}]" if title else "[Image]"

        rect = _base_element(eid, "rectangle", x, y, w, h,
                             strokeColor="#adb5bd", backgroundColor="#e9ecef",
                             strokeStyle="dashed", roundness={"type": 3})
        text_id = stable_id(item["id"] + "_label")
        font_size = max(10, min(14, w * 0.06))
        th = font_size * 1.25
        text_el = _text_element(text_id, x + 4, y + h / 2 - th / 2, w - 8, th,
                                label, font_size, "#868e96", "center", "middle", eid)
        rect["boundElements"].append({"id": text_id, "type": "text"})
        return [rect, text_el]


def convert_embed(item: dict, scale: float, id_map: dict,
                   client: "MiroClient | None" = None, files: dict | None = None) -> list:
    """Convert a Miro embed to an image (with preview) or a labeled rectangle with link."""
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo = item["position"], item.get("geometry", {})
    w = geo.get("width", 300) * scale
    h = geo.get("height", 200) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2

    data = item.get("data", {})
    title = data.get("title", "")
    content_type = data.get("contentType", "")
    provider = data.get("providerName", "")
    preview_url = data.get("previewUrl", "")

    # Extract video URL from iframe html
    link_url = data.get("url", "")
    if not link_url:
        html = data.get("html", "")
        m = re.search(r'src="([^"]+)"', html)
        if m:
            link_url = m.group(1).replace("&amp;", "&")

    # Build a styled card for embeds (dark bg, white text, clickable)
    icon = "▶  " if content_type == "video" else ""
    label_parts = []
    if title:
        label_parts.append(f"{icon}{title}")
    elif content_type == "video":
        label_parts.append("▶  Video")
    if provider:
        label_parts.append(provider)
    if not label_parts:
        label_parts.append("[Embed]")
    label = "\n".join(label_parts)

    rect = _base_element(eid, "rectangle", x, y, w, h,
                         strokeColor="#1e1e1e", backgroundColor="#1e1e1e",
                         fillStyle="solid", strokeWidth=1, opacity=100,
                         roundness={"type": 3})
    if link_url:
        rect["link"] = {"type": "url", "url": link_url}

    text_id = stable_id(item["id"] + "_label")
    font_size = max(14, min(24, h * 0.08))
    nlines = max(1, len(label.split("\n")))
    th = font_size * 1.35 * nlines
    text_el = _text_element(text_id, x + 10, y + h / 2 - th / 2, w - 20, th,
                            label, font_size, "#ffffff", "center", "middle", eid)
    rect["boundElements"].append({"id": text_id, "type": "text"})
    return [rect, text_el]


def convert_card(item: dict, scale: float, id_map: dict) -> list:
    """Convert a Miro card to a styled rectangle with title."""
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid

    pos, geo, style = item["position"], item.get("geometry", {}), item.get("style", {})
    w = geo.get("width", 320) * scale
    h = geo.get("height", 200) * scale
    x = pos["x"] * scale - w / 2
    y = pos["y"] * scale - h / 2

    fill_color = style.get("cardTheme", "#2d9bf0")
    if fill_color in STICKY_COLORS:
        fill_color = STICKY_COLORS[fill_color]

    rect = _base_element(eid, "rectangle", x, y, w, h,
                         strokeColor="#1e1e1e", backgroundColor=fill_color,
                         roundness={"type": 3})

    title = strip_html(item.get("data", {}).get("title", ""))
    desc = strip_html(item.get("data", {}).get("description", ""))
    content = title
    if desc:
        content = f"{title}\n---\n{desc}" if title else desc

    if not content:
        return [rect]

    text_id = stable_id(item["id"] + "_text")
    font_size = fit_font_size(content, w - 16, h - 10, 14)
    nlines = max(1, len(content.split("\n")))
    th = font_size * 1.25 * nlines

    text_el = _text_element(text_id, x + 8, y + h / 2 - th / 2, w - 16, th,
                            content, font_size, "#1e1e1e", "left", "middle", eid)
    rect["boundElements"].append({"id": text_id, "type": "text"})
    return [rect, text_el]


def convert_connector(conn: dict, scale: float, id_map: dict, item_lookup: dict) -> list:
    eid = stable_id(conn["id"])
    style = conn.get("style", {})

    start_id = conn.get("startItem", {}).get("id")
    end_id = conn.get("endItem", {}).get("id")
    if not start_id or not end_id:
        return []

    s_eid = id_map.get(start_id)
    e_eid = id_map.get(end_id)
    if not s_eid or not e_eid:
        return []

    sp = item_lookup.get(start_id, {}).get("position", {})
    ep = item_lookup.get(end_id, {}).get("position", {})
    if not sp or not ep:
        return []

    sx, sy = sp["x"] * scale, sp["y"] * scale
    ex, ey = ep["x"] * scale, ep["y"] * scale

    stroke_color = style.get("strokeColor", "#333333")
    stroke_width = max(1, float(style.get("strokeWidth", "2.0")) * scale)
    end_cap = style.get("endStrokeCap", "rounded_stealth")
    start_cap = style.get("startStrokeCap", "none")

    arrow = _base_element(eid, "arrow", sx, sy, abs(ex - sx), abs(ey - sy),
                          strokeColor=stroke_color, strokeWidth=stroke_width,
                          roundness={"type": 2})
    arrow.update({
        "points": [[0, 0], [ex - sx, ey - sy]],
        "lastCommittedPoint": None,
        "startBinding": {"elementId": s_eid, "focus": 0, "gap": 5, "fixedPoint": None},
        "endBinding": {"elementId": e_eid, "focus": 0, "gap": 5, "fixedPoint": None},
        "startArrowhead": "arrow" if start_cap not in ("none", "") else None,
        "endArrowhead": "arrow" if end_cap != "none" else None,
        "elbowed": False,
    })

    elements = [arrow]
    captions = conn.get("captions", [])
    if captions:
        cap_text = strip_html(captions[0].get("content", ""))
        if cap_text:
            cap_id = stable_id(conn["id"] + "_caption")
            mid_x, mid_y = (sx + ex) / 2, (sy + ey) / 2
            elements.append(
                _text_element(cap_id, mid_x - 50, mid_y - 10, 200, 20,
                              cap_text, 14, "#333333", "center", "top"))
    return elements


# ---------------------------------------------------------------------------
# Converter registry
# ---------------------------------------------------------------------------
CONVERTERS = {
    "shape": convert_shape,
    "text": convert_text,
    "sticky_note": convert_sticky_note,
    "frame": convert_frame,
    "card": convert_card,
    # "image" and "embed" are handled separately in convert_board (need client + files)
}


# ---------------------------------------------------------------------------
# Position resolution
# ---------------------------------------------------------------------------
def _resolve_positions(items: list) -> None:
    """Resolve parent-relative positions to absolute canvas coordinates in-place.

    Miro items inside frames have relativeTo='parent_top_left' — their x/y
    is relative to the parent frame's top-left corner. Frames themselves use
    relativeTo='canvas_center'. This converts everything to canvas_center.
    """
    item_map = {item["id"]: item for item in items}

    # First pass: compute absolute top-left for each frame (they use canvas_center)
    frame_abs: dict[str, tuple[float, float]] = {}
    for item in items:
        if item["type"] == "frame":
            pos = item["position"]
            geo = item.get("geometry", {})
            w = geo.get("width", 0)
            h = geo.get("height", 0)
            # canvas_center origin=center → top-left
            frame_abs[item["id"]] = (pos["x"] - w / 2, pos["y"] - h / 2)

    # Second pass: convert parent_top_left items to canvas_center
    for item in items:
        pos = item.get("position", {})
        if pos.get("relativeTo") == "parent_top_left":
            parent_id = item.get("parent", {}).get("id")
            if parent_id and parent_id in frame_abs:
                ftl_x, ftl_y = frame_abs[parent_id]
                # Item's x/y is relative to parent top-left, origin=center
                pos["x"] = ftl_x + pos["x"]
                pos["y"] = ftl_y + pos["y"]
                pos["relativeTo"] = "canvas_center"


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------
def convert_board(client: MiroClient, board_id: str, scale: float,
                  compact: bool = False, quiet: bool = False) -> dict:
    """Fetch and convert a single board. Returns the Excalidraw document dict."""
    if not quiet:
        print(f"  Fetching items...")
    items = client.get_items(board_id)
    if not quiet:
        print(f"    {len(items)} items")
        print(f"  Fetching connectors...")
    connectors = client.get_connectors(board_id)
    if not quiet:
        print(f"    {len(connectors)} connectors")

    # Resolve all parent-relative positions to absolute canvas coordinates
    _resolve_positions(items)

    id_map: dict[str, str] = {}
    item_lookup = {item["id"]: item for item in items}
    elements: list[dict] = []
    files: dict[str, dict] = {}
    skipped: dict[str, int] = {}
    img_count = 0
    embed_count = 0

    for item in items:
        itype = item["type"]
        if itype == "image":
            elements.extend(convert_image(item, scale, id_map, client, files))
            img_count += 1
        elif itype == "embed":
            elements.extend(convert_embed(item, scale, id_map, client, files))
            embed_count += 1
        else:
            conv = CONVERTERS.get(itype)
            if conv:
                elements.extend(conv(item, scale, id_map))
            else:
                skipped[itype] = skipped.get(itype, 0) + 1

    if not quiet:
        if img_count:
            print(f"    {img_count} images ({len(files)} downloaded)")
        if embed_count:
            print(f"    {embed_count} embeds")
        if skipped:
            for t, c in skipped.items():
                print(f"    Skipped {c}x {t}")

    el_by_id = {el["id"]: el for el in elements}

    for conn in connectors:
        elems = convert_connector(conn, scale, id_map, item_lookup)
        if elems:
            arrow = elems[0]
            for binding_key in ("startBinding", "endBinding"):
                binding = arrow.get(binding_key)
                if binding:
                    target = el_by_id.get(binding["elementId"])
                    if target:
                        target["boundElements"].append({"id": arrow["id"], "type": "arrow"})
        elements.extend(elems)

    if not quiet:
        print(f"    → {len(elements)} excalidraw elements")

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "miro2excalidraw",
        "elements": elements,
        "appState": {
            "gridSize": 20,
            "gridStep": 5,
            "gridModeEnabled": False,
            "viewBackgroundColor": "#ffffff",
        },
        "files": files,
    }


def write_doc(doc: dict, output: str, compact: bool = False):
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    indent = None if compact else 2
    with open(output, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="miro2excalidraw",
        description="Convert Miro boards to Excalidraw (.excalidraw) files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single board
  %(prog)s --token $TOKEN --board uXjVK3fkjsQ=
  %(prog)s --token $TOKEN --search "Domains" -o Domains.excalidraw

  # Batch: all boards by owner
  %(prog)s --token $TOKEN --owner "Jane Doe" --outdir ./output

  # Batch: from file (one board ID per line)
  %(prog)s --token $TOKEN --batch boards.txt --outdir ./output
        """,
    )
    p.add_argument("--token", default=os.environ.get("MIRO_ACCESS_TOKEN", ""),
                   help="Miro API access token (default: $MIRO_ACCESS_TOKEN)")

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--board", help="Single board ID")
    mode.add_argument("--search", help="Search by name, convert first match")
    mode.add_argument("--owner", help="Batch: convert all boards owned by this user")
    mode.add_argument("--batch", metavar="FILE",
                      help="Batch: file with board IDs (one per line)")

    p.add_argument("-o", "--output", default=None,
                   help="Output file (single mode only, default: <name>.excalidraw)")
    p.add_argument("--outdir", default=".",
                   help="Output directory for batch mode (default: .)")
    p.add_argument("--scale", type=float, default=0.25,
                   help="Coordinate scale factor (default: 0.25)")
    p.add_argument("--compact", action="store_true",
                   help="Compact JSON output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.token:
        print("Error: --token required or set MIRO_ACCESS_TOKEN env var", file=sys.stderr)
        return 1

    client = MiroClient(args.token)

    # ---- Single board ----
    if args.board or args.search:
        board_id = args.board
        board_name = None

        if args.search:
            print(f"Searching for '{args.search}'...")
            boards = client.search_boards(args.search)
            if not boards:
                print(f"Error: no board found matching '{args.search}'", file=sys.stderr)
                return 1
            board_id = boards[0]["id"]
            board_name = boards[0].get("name", board_id)
            print(f"  Found: '{board_name}' (id={board_id})")

        print(f"Converting board {board_id}...")
        doc = convert_board(client, board_id, args.scale, args.compact)

        output = args.output
        if not output:
            name = board_name or board_id
            output = os.path.join(args.outdir, f"{safe_filename(name)}.excalidraw")

        write_doc(doc, output, args.compact)
        print(f"Output: {output}")
        return 0

    # ---- Batch: by owner ----
    if args.owner:
        print(f"Listing all boards...")
        all_boards = client.list_all_boards()
        boards = [b for b in all_boards
                  if args.owner.lower() in b.get("owner", {}).get("name", "").lower()
                  or args.owner.lower() in b.get("createdBy", {}).get("name", "").lower()]
        print(f"Found {len(boards)} boards owned by '{args.owner}' (of {len(all_boards)} total)")

    # ---- Batch: from file ----
    elif args.batch:
        with open(args.batch) as f:
            board_ids = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        boards = [{"id": bid, "name": bid} for bid in board_ids]
        print(f"Loaded {len(boards)} board IDs from {args.batch}")

    else:
        return 1

    # Process batch
    os.makedirs(args.outdir, exist_ok=True)
    success, failed = 0, 0

    for i, board in enumerate(boards, 1):
        bid = board["id"]
        bname = board.get("name", bid)
        fname = safe_filename(bname)
        # Append short board ID to avoid collisions (e.g. multiple "Untitled" boards)
        bid_suffix = bid[:8].replace("=", "").replace("/", "")
        output = os.path.join(args.outdir, f"{fname}_{bid_suffix}.excalidraw")

        # Skip if already converted
        if os.path.exists(output):
            print(f"[{i}/{len(boards)}] SKIP (exists) {bname}")
            success += 1
            continue

        print(f"[{i}/{len(boards)}] {bname} ({bid})")
        try:
            doc = convert_board(client, bid, args.scale, args.compact, quiet=True)
            write_doc(doc, output, args.compact)
            n_el = len(doc["elements"])
            print(f"    → {n_el} elements → {output}")
            success += 1
        except Exception as e:
            print(f"    ERROR: {e}")
            failed += 1

    print(f"\nDone: {success} converted, {failed} failed out of {len(boards)} boards")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
