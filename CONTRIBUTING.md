# Contributing

## Project Philosophy

- **Single file**: `convert.py` is the entire tool. Don't split into a package.
- **No dependencies**: Python 3.10+ stdlib only. No pip packages.
- **CLI-first**: All configuration via CLI args or env vars. Never hardcode values.

## Adding a New Miro Element Type

1. Write a converter function:

```python
def convert_<type>(item: dict, scale: float, id_map: dict) -> list:
    eid = stable_id(item["id"])
    id_map[item["id"]] = eid  # Required for connector bindings

    pos, geo = item["position"], item.get("geometry", {})
    w = geo.get("width", 200) * scale
    h = geo.get("height", 200) * scale
    x = pos["x"] * scale - w / 2  # Miro center → Excalidraw top-left
    y = pos["y"] * scale - h / 2

    # Use _base_element() and _text_element() helpers
    el = _base_element(eid, "rectangle", x, y, w, h,
                       backgroundColor="#a5d8ff")
    return [el]
```

2. Register it in the `CONVERTERS` dict:

```python
CONVERTERS = {
    ...
    "<type>": convert_<type>,
}
```

If your converter needs `client` or `files` (for downloading images), handle it in `convert_board()` like `image` and `embed`.

3. Test with a board containing that element type.

4. Update `README.md` supported types table.

## Key Rules

### Positions
- Miro uses **center-based** coordinates. Excalidraw uses **top-left**.
- Items inside frames use `relativeTo: "parent_top_left"`. The `_resolve_positions()` function converts these to absolute canvas coordinates BEFORE conversion. Your converter doesn't need to handle this.

### Images
- Always use `format=original` (not `format=preview`).
- Always read actual pixel dimensions with `get_image_dimensions()`.
- Never display an image larger than its pixel dimensions — this causes blur.

### Text
- Strip HTML from all Miro content with `strip_html()`.
- Named colors (sticky notes) go through `miro_color()`.
- Bound text needs `containerId` on the text + `boundElements` on the container.

### IDs
- Use `stable_id(seed)` for deterministic element IDs.
- Always register: `id_map[item["id"]] = eid` — connectors need this to resolve bindings.

## Testing

```bash
# Single board
python3 convert.py --token $TOKEN --board <ID> -o test.excalidraw

# Open in excalidraw.com and verify:
# - Elements positioned correctly
# - Images sharp (not blurry/upscaled)
# - Text readable
# - Arrows connect to the right elements
```

## Reference

- `docs/excalidraw-primitives.md` — Full Excalidraw JSON schema
- [Miro REST API v2](https://developers.miro.com/reference/api-reference) — Board items, connectors, images
