# AGENTS.md — miro2excalidraw

Guidelines for AI agents working in this project.

## Core Rules

1. **CLI params only** — Never hardcode tokens, board IDs, file paths, or user-specific values in source code. Everything is a CLI argument or environment variable.
2. **No new dependencies** — This project uses Python stdlib only. Do not add pip packages.
3. **Single-file tool** — `convert.py` is the entire converter. Do not split into a package unless it exceeds ~800 lines.
4. **Test via CLI** — Always verify changes by running the tool with real args. Don't just read the code.

## How to Use the Tool

```bash
# Always pass --token or set MIRO_ACCESS_TOKEN
export MIRO_ACCESS_TOKEN="..."

# Convert one board
python3 convert.py --board <BOARD_ID>

# Convert by search
python3 convert.py --search "Board Name" -o output.excalidraw

# Batch convert by owner
python3 convert.py --owner "Owner Name" --outdir ./output

# Batch from file
python3 convert.py --batch boards.txt --outdir ./output
```

## Extending the Converter

### Adding a New Miro Element Type

The `CONVERTERS` dict in `convert.py` maps Miro type names to converter functions:

```python
CONVERTERS = {
    "shape": convert_shape,
    "text": convert_text,
    "sticky_note": convert_sticky_note,
    "frame": convert_frame,
    "image": convert_image,
    "embed": convert_embed,
    "card": convert_card,
}
```

To add a new type:

1. Write `convert_<type>(item: dict, scale: float, id_map: dict) -> list[dict]`
2. Use `_base_element()` and `_text_element()` helpers — never build raw element dicts
3. Register the Miro item ID: `id_map[item["id"]] = eid` (required for connector bindings)
4. Add it to `CONVERTERS`
5. Test with a board that contains that element type

### Adding a New Excalidraw Element Type

Refer to `docs/excalidraw-primitives.md` for the full schema. Key rules:
- All elements need the base properties (id, x, y, width, height, stroke*, fill*, etc.)
- Text bound to a container needs `containerId` on the text and `boundElements` on the container
- Arrows need `points`, `startBinding`, `endBinding`

## Miro API Reference

- Base URL: `https://api.miro.com/v2`
- Auth: `Authorization: Bearer <token>` header
- Board items: `GET /boards/{id}/items?limit=50` (paginated via `links.next`)
- Connectors: `GET /boards/{id}/connectors?limit=50` (paginated via `links.next`)
- Rate limits: 429 responses include `Retry-After` header. The client handles this automatically.

Positions are center-based relative to canvas center. The converter translates to top-left origin for Excalidraw.

## File Layout

```
convert.py              # The tool — single file, stdlib only
.env                    # MIRO_ACCESS_TOKEN (never commit)
output/                 # Generated .excalidraw files (gitignored)
docs/
  excalidraw-primitives.md  # Excalidraw format reference
CLAUDE.md               # Claude Code project instructions
AGENTS.md               # This file — agent guidelines
README.md               # User-facing documentation
```

## Known Pitfalls

These are mistakes that were made during development. Do NOT repeat them:

1. **Parent-relative positions**: Miro items inside frames have `relativeTo: "parent_top_left"`. You MUST resolve to absolute coords by adding the parent frame's top-left offset. See `_resolve_positions()`.

2. **Image format=preview is garbage**: The Miro API defaults to `format=preview` (tiny 5-7KB thumbnails). Always use `format=original` for real image data.

3. **Image upscaling causes blur**: Miro geometry can be 4443x2499 but the actual image is only 960x540 pixels. ALWAYS read PNG/JPEG headers with `get_image_dimensions()` and cap element size to pixel dimensions. Never display larger than actual resolution.

4. **Private Vimeo embeds break**: Excalidraw's native `embeddable` element strips URL parameters. Private Vimeo videos need `?h=<hash>`. Use styled rectangle cards with `link` property instead.

5. **SVGs work as image/svg+xml**: Don't convert SVGs to PNG. Excalidraw supports SVG in the files map natively.

## Quality Checklist

Before considering work done:

- [ ] `python3 convert.py --help` shows correct usage
- [ ] Single board conversion works: `python3 convert.py --board <ID> --outdir /tmp`
- [ ] Output opens in excalidraw.com without errors
- [ ] Images render sharp (no upscaling — check pixel dimensions vs element dimensions)
- [ ] Video embeds show as dark cards with clickable links
- [ ] Items inside frames are positioned correctly (not clustered at origin)
- [ ] No hardcoded values in source (tokens, IDs, paths, org-specific strings)
- [ ] README and CLAUDE.md updated if you changed CLI args or added element types
