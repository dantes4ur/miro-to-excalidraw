# Excalidraw Primitives Reference

File format and element schema reference for `.excalidraw` files. Used by `convert.py` to generate valid Excalidraw documents.

## Document Structure

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "miro2excalidraw",
  "elements": [ /* ExcalidrawElement[] */ ],
  "appState": {
    "gridSize": 20,
    "gridStep": 5,
    "gridModeEnabled": false,
    "viewBackgroundColor": "#ffffff"
  },
  "files": {}
}
```

- `type` — Always `"excalidraw"`
- `version` — Format version (currently `2`)
- `source` — App identifier
- `elements` — Array of all scene elements
- `appState` — Canvas settings (grid, background)
- `files` — Binary data map for image elements (keyed by fileId)

MIME type: `application/vnd.excalidraw+json`

## Base Element Properties

Every element shares these properties:

```json
{
  "type": "rectangle",
  "id": "unique-string-id",
  "version": 1,
  "versionNonce": 123456789,
  "x": 100,
  "y": 200,
  "width": 300,
  "height": 150,
  "angle": 0,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "transparent",
  "fillStyle": "solid",
  "strokeWidth": 1,
  "strokeStyle": "solid",
  "roughness": 0,
  "opacity": 100,
  "seed": 987654321,
  "groupIds": [],
  "frameId": null,
  "index": "a0",
  "roundness": null,
  "boundElements": [],
  "updated": 1,
  "link": null,
  "locked": false,
  "isDeleted": false
}
```

### Property Details

| Property | Type | Description |
|----------|------|-------------|
| `type` | string | Element type (see types below) |
| `id` | string | Unique identifier |
| `version` | int | Incremented on each edit |
| `versionNonce` | int | Random nonce for conflict resolution |
| `x`, `y` | float | Top-left corner position (scene coordinates) |
| `width`, `height` | float | Dimensions |
| `angle` | float | Rotation in radians |
| `strokeColor` | string | Border/line color (hex or named) |
| `backgroundColor` | string | Fill color (`"transparent"` for none) |
| `fillStyle` | string | `"solid"`, `"hachure"`, `"cross-hatch"` |
| `strokeWidth` | float | Border thickness (1, 2, 4 are standard) |
| `strokeStyle` | string | `"solid"`, `"dashed"`, `"dotted"` |
| `roughness` | int | Hand-drawn effect: `0` (none), `1` (light), `2` (heavy) |
| `opacity` | int | 0–100 |
| `seed` | int | Random seed for roughness rendering |
| `groupIds` | string[] | Group membership |
| `frameId` | string\|null | Parent frame element ID |
| `index` | string | Z-order index (fractional indexing: `"a0"`, `"a1"`, etc.) |
| `roundness` | object\|null | `{"type": 3}` for rounded corners, `null` for sharp |
| `boundElements` | array | Elements bound to this one (text labels, arrows) |
| `locked` | bool | Whether element is locked |
| `isDeleted` | bool | Soft-delete flag |

## Element Types

### rectangle

Basic rectangle shape.

```json
{
  "type": "rectangle",
  "x": 0, "y": 0,
  "width": 200, "height": 100,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "#a5d8ff",
  "fillStyle": "solid",
  "roundness": { "type": 3 }
}
```

Used for: Miro shapes, sticky notes, frames, image/embed placeholders, cards.

### ellipse

Circle or oval.

```json
{
  "type": "ellipse",
  "x": 0, "y": 0,
  "width": 150, "height": 150,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "#b2f2bb"
}
```

Used for: Miro circle shapes.

### diamond

Diamond/rhombus shape.

```json
{
  "type": "diamond",
  "x": 0, "y": 0,
  "width": 150, "height": 150,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "#ffec99"
}
```

Used for: Miro triangle and rhombus shapes.

### text

Text element. Can be standalone or bound to a container.

```json
{
  "type": "text",
  "x": 10, "y": 30,
  "width": 180, "height": 25,
  "strokeColor": "#1e1e1e",
  "text": "Hello world",
  "fontSize": 16,
  "fontFamily": 1,
  "textAlign": "center",
  "verticalAlign": "middle",
  "containerId": null,
  "originalText": "Hello world",
  "autoResize": true,
  "lineHeight": 1.25
}
```

#### Text-specific properties

| Property | Type | Description |
|----------|------|-------------|
| `text` | string | Display text (newlines as `\n`) |
| `fontSize` | float | Font size in pixels |
| `fontFamily` | int | `1` = Virgil (hand-drawn), `2` = Helvetica, `3` = Cascadia (monospace) |
| `textAlign` | string | `"left"`, `"center"`, `"right"` |
| `verticalAlign` | string | `"top"`, `"middle"` |
| `containerId` | string\|null | ID of parent container (makes text "bound") |
| `originalText` | string | Text before wrapping |
| `autoResize` | bool | Auto-resize container to fit text |
| `lineHeight` | float | Line height multiplier |

#### Bound Text Pattern

To bind text inside a rectangle:

1. On the **container** (rectangle): add `{"id": "<text_id>", "type": "text"}` to `boundElements`
2. On the **text**: set `containerId` to the container's ID

```json
// Container
{
  "type": "rectangle",
  "id": "rect-1",
  "boundElements": [{ "id": "text-1", "type": "text" }]
}

// Bound text
{
  "type": "text",
  "id": "text-1",
  "containerId": "rect-1",
  "verticalAlign": "middle",
  "textAlign": "center"
}
```

### arrow

Arrow/line connecting two elements.

```json
{
  "type": "arrow",
  "x": 200, "y": 100,
  "width": 300, "height": 150,
  "strokeColor": "#333333",
  "strokeWidth": 2,
  "points": [
    [0, 0],
    [300, 150]
  ],
  "lastCommittedPoint": null,
  "startBinding": {
    "elementId": "source-element-id",
    "focus": 0,
    "gap": 5,
    "fixedPoint": null
  },
  "endBinding": {
    "elementId": "target-element-id",
    "focus": 0,
    "gap": 5,
    "fixedPoint": null
  },
  "startArrowhead": null,
  "endArrowhead": "arrow",
  "elbowed": false
}
```

#### Arrow-specific properties

| Property | Type | Description |
|----------|------|-------------|
| `points` | float[][] | Array of `[x, y]` relative to element origin. Min 2 points. |
| `startBinding` | object\|null | Connection to start element |
| `endBinding` | object\|null | Connection to end element |
| `startArrowhead` | string\|null | `null`, `"arrow"`, `"bar"`, `"dot"`, `"triangle"` |
| `endArrowhead` | string\|null | Same options as start |
| `elbowed` | bool | Whether arrow uses right-angle routing |
| `lastCommittedPoint` | array\|null | Last user-placed point |

#### Binding object

```json
{
  "elementId": "target-id",
  "focus": 0,
  "gap": 5,
  "fixedPoint": null
}
```

- `elementId` — ID of the element being connected to
- `focus` — Float between -1 and 1, controls which side of the element the arrow attaches to (0 = center)
- `gap` — Distance between arrow tip and element border
- `fixedPoint` — `[x, y]` normalized coordinates (0-1) for a fixed attachment point, or `null` for auto

#### Arrow ↔ Element binding

When an arrow connects to an element, **both** sides must be updated:

1. On the **arrow**: set `startBinding` / `endBinding`
2. On the **connected element**: add `{"id": "<arrow_id>", "type": "arrow"}` to `boundElements`

### line

Same as arrow but without arrowheads. Uses identical properties.

```json
{
  "type": "line",
  "points": [[0, 0], [200, 0]],
  "startArrowhead": null,
  "endArrowhead": null
}
```

### freedraw

Freehand drawing path.

```json
{
  "type": "freedraw",
  "points": [[0, 0], [5, 3], [10, 1], [15, 8]],
  "pressures": [0.5, 0.6, 0.7, 0.5],
  "simulatePressure": false
}
```

### frame

Groups elements visually. Children reference the frame via `frameId`.

```json
{
  "type": "frame",
  "x": 0, "y": 0,
  "width": 800, "height": 600,
  "name": "Frame Title"
}
```

Note: Our converter renders Miro frames as dashed rectangles (not native Excalidraw frames) because the native frame type requires setting `frameId` on all children, which complicates the conversion.

### image

Requires a corresponding entry in the `files` map.

```json
{
  "type": "image",
  "x": 0, "y": 0,
  "width": 400, "height": 300,
  "fileId": "abc123",
  "status": "saved",
  "scale": [1, 1]
}
```

The `files` map entry:
```json
{
  "abc123": {
    "mimeType": "image/png",
    "id": "abc123",
    "dataURL": "data:image/png;base64,..."
  }
}
```

Note: Our converter uses placeholder rectangles for images because the Miro API doesn't expose raw image data through the items endpoint.

## Color Values

Excalidraw accepts:
- Hex colors: `"#1e1e1e"`, `"#a5d8ff"`
- `"transparent"` for no fill/stroke

Common Excalidraw palette colors:

| Color | Hex |
|-------|-----|
| Black | `#1e1e1e` |
| Dark gray | `#868e96` |
| Red | `#e03131` |
| Pink | `#c2255c` |
| Violet | `#9c36b5` |
| Blue | `#1971c2` |
| Cyan | `#0c8599` |
| Green | `#2f9e44` |
| Yellow | `#f08c00` |
| Orange | `#e8590c` |

## ID Generation

The converter uses `hashlib.md5(seed)[:20]` for deterministic IDs based on Miro element IDs. This means re-converting the same board produces the same element IDs, making diffs meaningful.

## Coordinate Mapping

| Aspect | Miro | Excalidraw |
|--------|------|------------|
| Origin | Center of element | Top-left of element |
| Reference | Relative to canvas center | Absolute scene coordinates |
| Scale | Large (thousands) | Flexible (scaled by `--scale`) |

Conversion formula:
```
excalidraw_x = miro_x * scale - width / 2
excalidraw_y = miro_y * scale - height / 2
```
