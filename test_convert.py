#!/usr/bin/env python3
"""Tests for miro2excalidraw converter. No Miro token needed — all data is mocked."""

import json
import struct
import unittest
import zlib

from convert import (
    strip_html,
    miro_color,
    safe_filename,
    stable_id,
    _stable_int,
    get_image_dimensions,
    estimate_text_height,
    _base_element,
    _text_element,
    _resolve_positions,
    convert_shape,
    convert_text,
    convert_sticky_note,
    convert_frame,
    convert_card,
    convert_connector,
    convert_image,
    convert_embed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_png(width: int, height: int) -> bytes:
    """Generate a minimal valid PNG file."""
    def chunk(ctype, data):
        c = ctype + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw = b'\x00' * (1 + width * 3) * height
    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')
    return header + ihdr + idat + iend


def _make_jpeg(width: int, height: int) -> bytes:
    """Generate a minimal JPEG-like header (not a real image, just enough for dimension reading)."""
    # SOI + APP0 + SOF0
    soi = b'\xff\xd8'
    # APP0 marker with minimal data
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    # SOF0 marker
    sof0 = b'\xff\xc0' + struct.pack('>H', 11) + b'\x08' + struct.pack('>HH', height, width) + b'\x03'
    return soi + app0 + sof0


def _miro_item(item_id, item_type, x=0, y=0, w=200, h=100, **kwargs):
    """Create a minimal Miro item dict for testing."""
    item = {
        "id": str(item_id),
        "type": item_type,
        "position": {"x": x, "y": y, "origin": "center", "relativeTo": "canvas_center"},
        "geometry": {"width": w, "height": h},
        "data": kwargs.get("data", {}),
        "style": kwargs.get("style", {}),
    }
    if "parent_id" in kwargs:
        item["parent"] = {"id": str(kwargs["parent_id"])}
        item["position"]["relativeTo"] = "parent_top_left"
    return item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestStripHtml(unittest.TestCase):
    def test_basic_tags(self):
        self.assertEqual(strip_html("<p>hello</p>"), "hello")

    def test_br_newline(self):
        self.assertEqual(strip_html("line1<br/>line2"), "line1\nline2")
        self.assertEqual(strip_html("line1<br>line2"), "line1\nline2")
        self.assertEqual(strip_html("line1<br />line2"), "line1\nline2")

    def test_paragraph_newline(self):
        self.assertEqual(strip_html("<p>a</p><p>b</p>"), "a\nb")

    def test_html_entities(self):
        self.assertEqual(strip_html("&amp; &lt; &gt; &quot;"), '& < > "')
        # &nbsp; decodes to \xa0 (non-breaking space), which strip() removes when alone
        self.assertIn(strip_html("hello&nbsp;world"), ["hello\xa0world", "hello world"])

    def test_unicode_entities(self):
        self.assertEqual(strip_html("&#x1f534;"), "\U0001f534")  # 🔴
        self.assertEqual(strip_html("&#x1f7e1;"), "\U0001f7e1")  # 🟡
        self.assertEqual(strip_html("&#x1f7e2;"), "\U0001f7e2")  # 🟢

    def test_numeric_entities(self):
        self.assertEqual(strip_html("&#65;"), "A")

    def test_nested_tags(self):
        self.assertEqual(strip_html("<p><strong>bold</strong> text</p>"), "bold text")

    def test_ordered_list(self):
        html = '<ol><li>First</li><li>Second</li><li>Third</li></ol>'
        self.assertEqual(strip_html(html), "1. First\n2. Second\n3. Third")

    def test_unordered_list(self):
        html = '<ul><li>A</li><li>B</li></ul>'
        self.assertEqual(strip_html(html), "- A\n- B")

    def test_ordered_list_with_formatting(self):
        html = '<ol><li data-list="ordered"><span class="ql-ui"></span><strong>Bold</strong>: desc</li><li data-list="ordered"><span class="ql-ui"></span>Plain item</li></ol>'
        result = strip_html(html)
        self.assertIn("1. Bold: desc", result)
        self.assertIn("2. Plain item", result)

    def test_mixed_content_with_list(self):
        html = '<p>Title</p><ol><li>Item 1</li><li>Item 2</li></ol>'
        result = strip_html(html)
        self.assertIn("Title", result)
        self.assertIn("1. Item 1", result)

    def test_empty(self):
        self.assertEqual(strip_html(""), "")
        self.assertEqual(strip_html("<p></p>"), "")
        self.assertEqual(strip_html("<p><br /></p>"), "")


class TestMiroColor(unittest.TestCase):
    def test_named_colors(self):
        self.assertEqual(miro_color("light_yellow"), "#FFF9B1")
        self.assertEqual(miro_color("gray"), "#C9C9C9")

    def test_hex_passthrough(self):
        self.assertEqual(miro_color("#ff0000"), "#ff0000")

    def test_unknown_fallback(self):
        self.assertEqual(miro_color("nonexistent"), "#000000")
        self.assertEqual(miro_color(""), "#000000")


class TestSafeFilename(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(safe_filename("My Board"), "My Board")

    def test_special_chars(self):
        self.assertEqual(safe_filename("a/b:c?d"), "a_b_c_d")

    def test_empty(self):
        self.assertEqual(safe_filename(""), "untitled")

    def test_unicode(self):
        result = safe_filename("Diseño técnico")
        self.assertIn("Dise", result)


class TestDeterministicIds(unittest.TestCase):
    def test_stable_id_deterministic(self):
        self.assertEqual(stable_id("test"), stable_id("test"))
        self.assertNotEqual(stable_id("a"), stable_id("b"))

    def test_stable_id_length(self):
        self.assertEqual(len(stable_id("anything")), 20)

    def test_stable_int_deterministic(self):
        self.assertEqual(_stable_int("test"), _stable_int("test"))
        self.assertIsInstance(_stable_int("test"), int)
        self.assertGreater(_stable_int("test"), 0)

    def test_stable_int_different_seeds(self):
        self.assertNotEqual(_stable_int("a"), _stable_int("b"))


class TestGetImageDimensions(unittest.TestCase):
    def test_png(self):
        png = _make_png(640, 480)
        self.assertEqual(get_image_dimensions(png), (640, 480))

    def test_png_large(self):
        png = _make_png(1920, 1080)
        self.assertEqual(get_image_dimensions(png), (1920, 1080))

    def test_jpeg(self):
        jpeg = _make_jpeg(800, 600)
        self.assertEqual(get_image_dimensions(jpeg), (800, 600))

    def test_svg_returns_none(self):
        svg = b'<svg width="100" height="100"></svg>'
        self.assertIsNone(get_image_dimensions(svg))

    def test_garbage_returns_none(self):
        self.assertIsNone(get_image_dimensions(b'garbage'))
        self.assertIsNone(get_image_dimensions(b''))


class TestEstimateTextHeight(unittest.TestCase):
    def test_single_line(self):
        h = estimate_text_height("hello", 16, 200)
        self.assertGreater(h, 0)

    def test_multiline(self):
        h1 = estimate_text_height("a", 16, 200)
        h2 = estimate_text_height("a\nb\nc", 16, 200)
        self.assertGreater(h2, h1)

    def test_wrapping(self):
        short = estimate_text_height("hi", 16, 200)
        long_text = estimate_text_height("a" * 500, 16, 200)
        self.assertGreater(long_text, short)


class TestBaseElement(unittest.TestCase):
    def test_required_fields(self):
        el = _base_element("id1", "rectangle", 10, 20, 100, 50)
        self.assertEqual(el["type"], "rectangle")
        self.assertEqual(el["id"], "id1")
        self.assertEqual(el["x"], 10)
        self.assertEqual(el["y"], 20)
        self.assertEqual(el["width"], 100)
        self.assertEqual(el["height"], 50)
        self.assertFalse(el["isDeleted"])
        self.assertFalse(el["locked"])
        self.assertEqual(el["boundElements"], [])

    def test_custom_style(self):
        el = _base_element("id2", "ellipse", 0, 0, 50, 50,
                           strokeColor="#ff0000", backgroundColor="#00ff00",
                           strokeWidth=3, opacity=50)
        self.assertEqual(el["strokeColor"], "#ff0000")
        self.assertEqual(el["backgroundColor"], "#00ff00")
        self.assertEqual(el["strokeWidth"], 3)
        self.assertEqual(el["opacity"], 50)

    def test_deterministic_nonce_and_seed(self):
        el1 = _base_element("same_id", "rectangle", 0, 0, 10, 10)
        el2 = _base_element("same_id", "rectangle", 0, 0, 10, 10)
        self.assertEqual(el1["versionNonce"], el2["versionNonce"])
        self.assertEqual(el1["seed"], el2["seed"])


class TestTextElement(unittest.TestCase):
    def test_text_fields(self):
        el = _text_element("t1", 0, 0, 100, 20, "hello", 16)
        self.assertEqual(el["type"], "text")
        self.assertEqual(el["text"], "hello")
        self.assertEqual(el["fontSize"], 16)
        self.assertEqual(el["originalText"], "hello")
        self.assertIsNone(el["containerId"])

    def test_bound_text(self):
        el = _text_element("t2", 0, 0, 100, 20, "bound", 14, container_id="rect1")
        self.assertEqual(el["containerId"], "rect1")


class TestResolvePositions(unittest.TestCase):
    def test_parent_relative_resolved(self):
        frame = _miro_item("frame1", "frame", x=1000, y=2000, w=800, h=600)
        child = _miro_item("child1", "text", x=100, y=50, w=200, h=30,
                           parent_id="frame1", data={"content": "hi"})

        items = [frame, child]
        _resolve_positions(items)

        # Frame top-left = (1000-400, 2000-300) = (600, 1700)
        # Child absolute = (600+100, 1700+50) = (700, 1750)
        self.assertEqual(child["position"]["x"], 700)
        self.assertEqual(child["position"]["y"], 1750)
        self.assertEqual(child["position"]["relativeTo"], "canvas_center")

    def test_canvas_center_unchanged(self):
        item = _miro_item("item1", "shape", x=500, y=300)
        _resolve_positions([item])
        self.assertEqual(item["position"]["x"], 500)
        self.assertEqual(item["position"]["y"], 300)

    def test_orphan_parent_unchanged(self):
        child = _miro_item("child1", "text", x=100, y=50, parent_id="nonexistent")
        orig_x = child["position"]["x"]
        _resolve_positions([child])
        self.assertEqual(child["position"]["x"], orig_x)


class TestConvertShape(unittest.TestCase):
    def test_basic_rectangle(self):
        item = _miro_item("s1", "shape", x=400, y=200, w=200, h=100,
                          data={"content": "", "shape": "rectangle"},
                          style={"fillColor": "#ff0000", "fillOpacity": "1.0",
                                 "borderColor": "#000000", "borderWidth": "2.0"})
        id_map = {}
        elements = convert_shape(item, 0.25, id_map)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0]["type"], "rectangle")
        self.assertIn("s1", id_map)

    def test_shape_with_text(self):
        item = _miro_item("s2", "shape", x=400, y=200, w=200, h=100,
                          data={"content": "<p>Hello</p>", "shape": "rectangle"},
                          style={"fillColor": "#ff0000", "fillOpacity": "1.0",
                                 "borderColor": "#000000", "borderWidth": "2.0",
                                 "fontSize": "28", "color": "#1a1a1a"})
        id_map = {}
        elements = convert_shape(item, 0.25, id_map)
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]["type"], "rectangle")
        self.assertEqual(elements[1]["type"], "text")
        self.assertEqual(elements[1]["text"], "Hello")
        self.assertEqual(elements[1]["containerId"], elements[0]["id"])

    def test_circle_maps_to_ellipse(self):
        item = _miro_item("s3", "shape", data={"shape": "circle"}, style={})
        elements = convert_shape(item, 0.25, {})
        self.assertEqual(elements[0]["type"], "ellipse")

    def test_rhombus_maps_to_diamond(self):
        item = _miro_item("s4", "shape", data={"shape": "rhombus"}, style={})
        elements = convert_shape(item, 0.25, {})
        self.assertEqual(elements[0]["type"], "diamond")


class TestConvertText(unittest.TestCase):
    def test_basic(self):
        item = _miro_item("t1", "text", x=100, y=200, w=300,
                          data={"content": "<p>Hello world</p>"},
                          style={"fontSize": "28", "color": "#1a1a1a", "textAlign": "left"})
        id_map = {}
        elements = convert_text(item, 0.25, id_map)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0]["text"], "Hello world")
        self.assertIn("t1", id_map)

    def test_empty_content_skipped(self):
        item = _miro_item("t2", "text", data={"content": "<p></p>"}, style={})
        elements = convert_text(item, 0.25, {})
        self.assertEqual(len(elements), 0)


class TestConvertStickyNote(unittest.TestCase):
    def test_with_content(self):
        item = _miro_item("sn1", "sticky_note", w=200, h=200,
                          data={"content": "<p>TODO</p>"},
                          style={"fillColor": "light_yellow"})
        id_map = {}
        elements = convert_sticky_note(item, 0.25, id_map)
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]["backgroundColor"], "#FFF9B1")
        self.assertEqual(elements[1]["text"], "TODO")

    def test_empty_content(self):
        item = _miro_item("sn2", "sticky_note", data={"content": ""},
                          style={"fillColor": "yellow"})
        elements = convert_sticky_note(item, 0.25, {})
        self.assertEqual(len(elements), 1)


class TestConvertFrame(unittest.TestCase):
    def test_with_title(self):
        item = _miro_item("f1", "frame", w=800, h=600,
                          data={"title": "My Frame"})
        id_map = {}
        elements = convert_frame(item, 0.25, id_map)
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]["strokeStyle"], "dashed")
        self.assertEqual(elements[1]["text"], "My Frame")

    def test_without_title(self):
        item = _miro_item("f2", "frame", w=800, h=600, data={})
        elements = convert_frame(item, 0.25, {})
        self.assertEqual(len(elements), 1)


class TestConvertCard(unittest.TestCase):
    def test_with_title_and_desc(self):
        item = _miro_item("c1", "card",
                          data={"title": "<p>Bug</p>", "description": "<p>Fix it</p>"},
                          style={"cardTheme": "#2d9bf0"})
        elements = convert_card(item, 0.25, {})
        self.assertEqual(len(elements), 2)
        self.assertIn("Bug", elements[1]["text"])
        self.assertIn("Fix it", elements[1]["text"])


class TestConvertConnector(unittest.TestCase):
    def test_basic_arrow(self):
        id_map = {"start_item": "exc_start", "end_item": "exc_end"}
        item_lookup = {
            "start_item": {"position": {"x": 100, "y": 100}},
            "end_item": {"position": {"x": 500, "y": 300}},
        }
        conn = {
            "id": "conn1",
            "startItem": {"id": "start_item"},
            "endItem": {"id": "end_item"},
            "style": {"strokeColor": "#333", "strokeWidth": "2.0",
                       "startStrokeCap": "none", "endStrokeCap": "rounded_stealth"},
        }
        elements = convert_connector(conn, 0.25, id_map, item_lookup)
        self.assertEqual(len(elements), 1)
        arrow = elements[0]
        self.assertEqual(arrow["type"], "arrow")
        self.assertEqual(arrow["startBinding"]["elementId"], "exc_start")
        self.assertEqual(arrow["endBinding"]["elementId"], "exc_end")
        self.assertIsNone(arrow["startArrowhead"])
        self.assertEqual(arrow["endArrowhead"], "arrow")

    def test_missing_endpoint_skipped(self):
        elements = convert_connector(
            {"id": "c2", "startItem": {"id": "a"}, "endItem": {},
             "style": {}}, 0.25, {"a": "ea"}, {})
        self.assertEqual(len(elements), 0)

    def test_caption(self):
        id_map = {"s": "es", "e": "ee"}
        item_lookup = {"s": {"position": {"x": 0, "y": 0}}, "e": {"position": {"x": 100, "y": 0}}}
        conn = {
            "id": "c3",
            "startItem": {"id": "s"}, "endItem": {"id": "e"},
            "style": {"strokeColor": "#333", "strokeWidth": "2.0",
                       "startStrokeCap": "none", "endStrokeCap": "none"},
            "captions": [{"content": "<p>label</p>"}],
        }
        elements = convert_connector(conn, 0.25, id_map, item_lookup)
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[1]["text"], "label")


class TestConvertImage(unittest.TestCase):
    def test_fallback_placeholder(self):
        """Without client, should produce placeholder rectangle."""
        item = _miro_item("img1", "image", w=400, h=300,
                          data={"imageUrl": "", "title": "Photo"})
        elements = convert_image(item, 0.25, {})
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]["type"], "rectangle")
        self.assertIn("Image", elements[1]["text"])


class TestConvertEmbed(unittest.TestCase):
    def test_video_card(self):
        item = _miro_item("emb1", "embed", w=800, h=450,
                          data={"contentType": "video", "title": "My Video",
                                "providerName": "Vimeo",
                                "html": '<iframe src="https://player.vimeo.com/video/123?h=abc"></iframe>',
                                "url": ""})
        elements = convert_embed(item, 0.25, {})
        self.assertEqual(len(elements), 2)
        rect = elements[0]
        self.assertEqual(rect["backgroundColor"], "#1e1e1e")
        self.assertEqual(rect["link"]["url"], "https://player.vimeo.com/video/123?h=abc")
        self.assertIn("My Video", elements[1]["text"])

    def test_non_video_embed(self):
        item = _miro_item("emb2", "embed", w=400, h=300,
                          data={"contentType": "link", "title": "Docs",
                                "providerName": "Google", "url": "https://docs.google.com"})
        elements = convert_embed(item, 0.25, {})
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]["link"]["url"], "https://docs.google.com")


class TestExcalidrawDocument(unittest.TestCase):
    """Integration test: build a mini board and validate the output document."""

    def test_full_document_structure(self):
        items = [
            _miro_item("f1", "frame", x=0, y=0, w=1000, h=800, data={"title": "Frame"}),
            _miro_item("s1", "shape", x=200, y=100, w=200, h=100,
                       parent_id="f1",
                       data={"content": "<p>Box</p>", "shape": "rectangle"},
                       style={"fillColor": "#ff0000", "fillOpacity": "1.0",
                              "borderColor": "#000", "borderWidth": "2", "fontSize": "28",
                              "color": "#000"}),
            _miro_item("sn1", "sticky_note", x=500, y=100, w=200, h=200,
                       parent_id="f1",
                       data={"content": "<p>Note</p>"},
                       style={"fillColor": "yellow"}),
        ]

        _resolve_positions(items)

        id_map = {}
        elements = []
        from convert import CONVERTERS
        for item in items:
            conv = CONVERTERS.get(item["type"])
            if conv:
                elements.extend(conv(item, 0.25, id_map))

        # Build document
        doc = {
            "type": "excalidraw",
            "version": 2,
            "source": "test",
            "elements": elements,
            "appState": {"viewBackgroundColor": "#ffffff"},
            "files": {},
        }

        # Validate structure
        self.assertEqual(doc["type"], "excalidraw")
        self.assertGreater(len(doc["elements"]), 0)

        # All elements have required fields
        for el in doc["elements"]:
            self.assertIn("id", el)
            self.assertIn("type", el)
            self.assertIn("x", el)
            self.assertIn("y", el)
            self.assertIn("width", el)
            self.assertIn("height", el)
            self.assertFalse(el["isDeleted"])

        # Bound text has containerId
        texts = [e for e in doc["elements"] if e["type"] == "text" and e.get("containerId")]
        self.assertGreater(len(texts), 0)
        for t in texts:
            container = next((e for e in doc["elements"] if e["id"] == t["containerId"]), None)
            self.assertIsNotNone(container, f"Text {t['id']} references missing container {t['containerId']}")
            bound_ids = [b["id"] for b in container.get("boundElements", [])]
            self.assertIn(t["id"], bound_ids)

        # Serializes to valid JSON
        serialized = json.dumps(doc)
        reparsed = json.loads(serialized)
        self.assertEqual(len(reparsed["elements"]), len(doc["elements"]))


if __name__ == "__main__":
    unittest.main()
