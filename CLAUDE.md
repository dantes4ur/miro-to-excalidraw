# CLAUDE.md — miro2excalidraw

## What This Project Is

A single-file Python CLI (`convert.py`) that converts Miro boards to Excalidraw `.excalidraw` files via the Miro REST API v2. Zero external dependencies — stdlib only.

## Key Principles

- **CLI-first**: All configuration via CLI args or env vars. Never hardcode tokens, board IDs, paths, or defaults in source code.
- **Single file**: The converter is one file (`convert.py`). Don't split into modules unless it exceeds ~800 lines.
- **No dependencies**: stdlib only (argparse, json, urllib, hashlib, re, os, sys). Don't add pip packages.
- **Reusable**: The tool works for any Miro account. Don't bake in org-specific logic.

## Running the Tool

```bash
# Token from env
export MIRO_ACCESS_TOKEN="..."

# Single board
python3 convert.py --board <ID>

# Batch by owner
python3 convert.py --owner "Name" --outdir ./output

# The .env file has the token for local dev but is NOT read by the script.
# Pass --token explicitly or set the env var.
```

## Code Structure

`convert.py` has these sections (top to bottom):

1. **Constants** — `STICKY_COLORS` mapping
2. **Helpers** — `stable_id`, `strip_html`, `miro_color`, `safe_filename`, `estimate_text_height`
3. **MiroClient** — API wrapper with pagination and rate-limit retry
4. **Converters** — One function per Miro type: `convert_shape`, `convert_text`, `convert_sticky_note`, `convert_frame`, `convert_image`, `convert_embed`, `convert_card`, `convert_connector`
5. **CONVERTERS registry** — Dict mapping Miro type strings to converter functions
6. **convert_board()** — Orchestrates fetching + converting a single board
7. **CLI** — `build_parser()` + `main()` with single/batch modes

## Adding a New Miro Element Type

1. Write a `convert_<type>(item, scale, id_map) -> list[dict]` function
2. Add it to the `CONVERTERS` dict
3. Update `docs/excalidraw-primitives.md` if you use a new Excalidraw element type
4. Update the README supported types table

## Excalidraw Format

See `docs/excalidraw-primitives.md` for the full element schema reference. Key points:

- Coordinates are top-left origin (Miro uses center — the converter translates)
- Bound text uses `containerId` on the text + `boundElements` on the container
- Arrows use `startBinding`/`endBinding` to connect to elements
- The `--scale` flag (default 0.25) maps Miro's large coordinates to Excalidraw

## Critical Lessons (Don't Repeat These Mistakes)

### 1. Parent-relative positioning
Miro items inside frames use `position.relativeTo: "parent_top_left"` — their x/y is relative to the parent frame's top-left, NOT the canvas center. The converter MUST resolve these to absolute canvas coordinates before converting. See `_resolve_positions()`.

### 2. Image quality: use `format=original`
The Miro API image resource URL defaults to `format=preview` which returns tiny thumbnails (5-7KB). Always replace with `format=original` to get full-resolution images.

### 3. Never upscale images
Miro geometry dimensions can be larger than the actual image pixel dimensions. For example, Miro may report an image as 4443x2499 in geometry but the actual file is only 960x540 pixels. Always read the PNG/JPEG header to get real pixel dimensions and cap the Excalidraw element size to avoid upscaling blur. See `get_image_dimensions()`.

### 4. Private Vimeo videos can't use Excalidraw embeddable
Excalidraw's `embeddable` element runs URLs through its own validator and constructs its own player URL, stripping privacy hashes. Private/unlisted Vimeo videos need the hash parameter (`?h=xxx`). Use dark-styled rectangles with clickable links instead.

### 5. SVG images are supported
Excalidraw supports `image/svg+xml` mimeType in the files map. SVGs need `xmlns`, `width`, `height`, and `viewBox` attributes. Miro's SVG icons (star/arrow decorations) are 767-byte vector files that render correctly.

## Conventions

- Use `stable_id(seed)` to generate deterministic element IDs from Miro IDs
- Use `_base_element()` and `_text_element()` helpers — don't construct raw dicts
- Strip HTML from all Miro content with `strip_html()`
- Named colors (sticky notes) go through `miro_color()`
- Every converter returns `list[dict]` (even for single elements) for consistency

## Files to Ignore

- `.env` — contains the Miro API token, never commit
- `output/` — generated `.excalidraw` files
- `*.excalidraw` — generated output files
