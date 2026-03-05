# Physics Simulator (`physics`) Documentation

This document describes the Pebble OS interactive 2D text-based physics simulator command: `physics`.

## 1. Overview

`physics` is an interactive sandbox running inside Pebble OS shell.

Core features:

- 2D grid world rendered with ASCII
- configurable gravity and air drag
- simple rigid-body style object motion
- object-object collision response
- environment materials:
  - `air` (`.`)
  - `liquid` (`~`)
  - `solid` (`#`)

The simulator is intentionally lightweight and deterministic enough for quick experiments in terminal.

## 2. Start / Exit

From Pebble shell:

```text
physics
```

Exit simulator:

```text
quit
```

or:

```text
exit
```

## 3. Coordinate System

- Origin: top-left
- `x` increases to the right
- `y` increases downward
- Valid drawing area is:
  - `x in [0, width-1]`
  - `y in [0, height-1]`

## 4. Rendering Rules

- Empty air cell: `.`
- Liquid cell: `~`
- Solid cell: `#`
- Object glyph (for example `o`) overlays material cell at that position

If an object and material share a cell, object glyph is shown.

## 5. Command Reference

## 5.1 `help`

Show simulator command list.

```text
help
```

## 5.2 `show`

Render current world without advancing simulation.

```text
show
```

## 5.3 `step [N]` / `run N`

Advance simulation by `N` ticks (default `1`).

```text
step
step 10
run 30
```

Notes:

- min applied step count: `1`
- max applied step count: `500`

## 5.4 `add NAME X Y [VX VY MASS GLYPH]`

Create one object.

Grammar:

```text
add NAME X Y [VX VY MASS GLYPH]
```

Parameters:

- `NAME`: unique identifier
- `X Y`: initial position
- `VX VY` (optional): initial velocity (default `0 0`)
- `MASS` (optional): mass (default `1`)
- `GLYPH` (optional): displayed single character
  - if omitted, first character of `NAME` is used

Example:

```text
add ball 5 2 1 0 1 o
```

## 5.5 `rm NAME`

Remove object by name.

```text
rm ball
```

## 5.6 `list`

Print all objects with numeric state:

- position `(x, y)`
- velocity `(vx, vy)`
- `mass`
- `glyph`

```text
list
```

## 5.7 `gravity VALUE`

Set global gravity acceleration.

```text
gravity 0.18
gravity 0.25
gravity -0.05
```

## 5.8 `air VALUE`

Set global air drag factor.

```text
air 0.01
air 0.05
```

## 5.9 `fill X1 Y1 X2 Y2 MATERIAL`

Fill rectangular region with one material.

Grammar:

```text
fill X1 Y1 X2 Y2 air|liquid|solid
```

Behavior:

- rectangle is inclusive on both corners
- order is normalized, so `(X1, Y1)` can be greater than `(X2, Y2)`
- out-of-bounds positions are ignored

Example:

```text
fill 0 12 39 15 solid
fill 12 8 28 11 liquid
```

## 5.10 `size WIDTH HEIGHT`

Resize world.

```text
size 40 16
```

Limits:

- min: `8x6`
- max: `120x40`

Effects:

- world cells are reinitialized to all `air`
- world tick resets to `0`
- objects remain unless you run `reset` or `clearobjects`

## 5.11 `clearobjects`

Delete all objects, keep world/material layout.

```text
clearobjects
```

## 5.12 `reset`

Reset world and objects to simulator defaults.

```text
reset
```

## 6. Material Physics Model

Each cell has a material with predefined properties:

- `air`: low drag
- `liquid`: higher drag + buoyancy
- `solid`: collision surface (bounce response)

Object update per tick (simplified):

1. read material at current object cell
2. compute effective drag (`global_air_drag + material_drag`)
3. apply gravity and material buoyancy
4. integrate velocity to position
5. enforce world bounds with velocity reflection damping
6. if entering `solid`, roll back to previous position and bounce
7. process object-object collision exchange

This is a compact arcade-style model, not a full rigid-body solver.

## 7. Recommended Workflow

Example session:

```text
physics
size 40 16
add ball 5 2 1 0 1 o
fill 0 12 39 15 solid
fill 12 8 28 11 liquid
show
step 20
show
list
quit
```

## 7.1 Text Screenshots

Screenshot A: Fresh simulator start

```text
Physics simulator (2D text)
materials: air, liquid, solid
type 'help' for commands
+----------------------------------------+
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
+----------------------------------------+
tick=0 gravity=0.18 air=0.01 objects=0
```

Screenshot B: After adding one object

Command:

```text
add ball 5 2 1 0 1 o
```

Expected render excerpt:

```text
+----------------------------------------+
|........................................|
|........................................|
|.....o..................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
+----------------------------------------+
tick=0 gravity=0.18 air=0.01 objects=1
```

Screenshot C: After adding solid floor + liquid pool

Commands:

```text
fill 0 12 39 15 solid
fill 12 8 28 11 liquid
```

Expected render excerpt:

```text
+----------------------------------------+
|........................................|
|........................................|
|.....o..................................|
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|########################################|
|########################################|
|########################################|
|########################################|
+----------------------------------------+
tick=0 gravity=0.18 air=0.01 objects=1
```

Screenshot D: After simulation stepping

Command:

```text
step 6
```

Typical render excerpt:

```text
+----------------------------------------+
|........................................|
|........................................|
|........................................|
|........................................|
|........................................|
|..........o.............................|
|........................................|
|........................................|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|............~~~~~~~~~~~~~~~~~...........|
|########################################|
|########################################|
|########################################|
|########################################|
+----------------------------------------+
tick=6 gravity=0.18 air=0.01 objects=1
```

Note:

- exact object coordinates after stepping can vary with parameter changes (`gravity`, `air`, initial velocity), but `liquid` and `solid` regions should remain unless overwritten.

## 8. State Persistence Behavior

The simulator persists state between input cycles in:

```text
.__physics_state__.txt
```

Purpose:

- preserve world/object state even when host input path re-enters execution loops

On normal `quit`/`exit`, persisted simulator state is cleared.

If you suspect stale state:

1. run `reset` in simulator, or
2. remove `.__physics_state__.txt` from Pebble disk and restart simulator

## 9. Troubleshooting

## 9.1 "Only liquid remains after fill"

Symptom:

- after sequential `add` + `fill solid` + `fill liquid`, previously visible entities seem missing

Action:

1. run `show`
2. run `list` and confirm `objects=...` in footer
3. if needed, `reset` and retry
4. ensure you are running latest `physics.peb`

## 9.2 Object appears static after `step`

Possible reasons:

- very high drag
- object starts in/near solid with repeated bounce cancellation
- too few ticks stepped

Try:

```text
air 0.01
gravity 0.2
step 30
```

## 9.3 Fill region not visible

Check:

- world size and coordinates are within range
- use `show` immediately after `fill`

## 10. Design Notes and Limitations

- no rotation, angular momentum, or shape polygons
- object footprint is one point-cell for rendering
- collision model is intentionally simple and fast
- material definition is fixed in current implementation (`air`, `liquid`, `solid`)

This command is built for lightweight terminal experimentation and Pebble runtime demonstrations, not physically accurate simulation.
