# CLAUDE.md

Project context for `turtle-widget` — an animated Python-`turtle`-compatible
drawing widget for Jupyter notebooks, built on [anywidget](https://anywidget.dev).

## What this is

A small widget library that implements a subset of the
[`turtle`](https://docs.python.org/3/library/turtle.html) drawing API as a Jupyter
widget. Turtle commands are recorded in Python as a stream of absolute-coordinate
*events*; a plain-ESM frontend replays them as a canvas animation. Optionally the cell
source is shown beside the canvas with the active line highlighted in sync with the
animation.

The design priority is **cross-platform robustness**: it must behave identically in VS
Code notebooks, JupyterLab, Notebook 7, and Colab, and must work offline.

The repo was scaffolded from the `munch-group` Python-library template (pixi environment,
quartodoc docs, conda/PyPI release automation), now fully converted to the turtle widget —
see *Template conversion* below.

## Package layout

The widget is the `turtle_widget` package under `src/`:

- `src/turtle_widget/widget.py` — the whole widget: the Python `Turtle` class + the
  embedded `_ESM`/`_CSS` frontend strings. (`_esm`/`_css` on the class just alias those
  module-level strings, which is why the JS check below reads `m._ESM` off the module.)
- `src/turtle_widget/__init__.py` — re-exports the public API (`from .widget import Turtle`).
- `docs/pages/demo.ipynb` — showcase + tests (formerly `turtle_demo.ipynb`); each cell
  draws something, the last cell is a headless self-test.
- `test/` — pytest suite (`test/test_*.py`).
- `docs/` — Quarto + quartodoc site (`docs/pages/*.ipynb` prose, `docs/api/*.qmd` API ref).
- `pyproject.toml` — packaging metadata **and** the pixi workspace (deps + task runner).
- `conda-build/`, `.github/workflows/` — conda/PyPI release on tag push.
- `scripts/` — version-bump / docs-build / release helpers invoked by the pixi tasks.
- `CLAUDE.md` — this file.

### Template conversion: complete

The `munch-group` template has been fully converted to the turtle widget:

- The widget lives in `src/turtle_widget/widget.py`; `__init__.py` re-exports `Turtle`.
- `pyproject.toml` has real metadata, runtime deps (`anywidget`, `traitlets`, `ipython`),
  no stray console-script, and `[tool.pytest.ini_options]` with `testpaths = ["test"]`
  (this anchor matters — without it pytest walks above the repo and can hang on synced
  home dirs).
- Tests are in `test/` (`test_widget.py` + a `conftest.py` that puts `src/` on `sys.path`,
  so `pytest test/` runs without an install). `pixi run test` runs `pytest test/`.
- The docs document `Turtle`: `docs/pages/overview.ipynb` (intro + runnable example),
  `docs/pages/demo.ipynb` (showcase, wired into `docs/_quarto.yml`), and the
  quartodoc-generated `docs/api/Turtle.qmd`.

Run `pixi run install-dev` once after a fresh clone so `import turtle_widget` resolves for
the notebooks and the docs build (the editable `[tool.pixi.pypi-dependencies]` entry in
`pyproject.toml` is left commented out; tests don't need it thanks to the conftest).

The public `Turtle` methods carry numpy-style docstrings, so `pixi run api` (quartodoc)
renders a full method reference at `docs/api/Turtle.qmd`. Keep this up: give any new command
a numpy-style docstring (summary + Parameters/Returns) so it appears there too.

## Environment & commands

The repo is **pixi-managed** (config in `pyproject.toml` under `[tool.pixi.*]`; channels
`conda-forge` + `munch-group`; platforms `osx-arm64`, `linux-64`). Python 3.9–3.13. Key
deps: `anywidget` (0.11.x), `nodejs` (20–22), `jupyter`/`ipython`, `quarto`, `quartodoc`,
`pytest`. `pixi run init` is the one-time template bootstrap (already run — don't re-run).

- Dev install: `pixi run install-dev` (editable, no build isolation).
- Run the showcase: `pixi run install-dev` once, then open `docs/pages/demo.ipynb` in VS
  Code / Jupyter and run all; or `pixi run notebook docs/pages/demo.ipynb` to execute it
  headless, in place.
- Run tests: `pixi run pytest test/` (the named `pixi run test` task is mis-pointed — see
  the migration note); or run the demo's final self-test cell.
- Build docs: `pixi run api` (quartodoc API pages), then `pixi run docs` (execute the doc
  notebooks in place).
- Release: `pixi run bump` / `release` / `version` drive `scripts/bump_version.py` + a tag
  push, which triggers the conda/PyPI workflows.
- JS syntax check after editing the embedded frontend string:
  ```bash
  python -c "from turtle_widget import widget as m; open('/tmp/e.mjs','w').write(m._ESM)"
  node --check /tmp/e.mjs
  ```

## Architecture (read before editing)

Two layers with a thin, explicit contract between them:

1. **Python (`Turtle`)** owns all geometry and turtle state. Each public method updates
   internal state and appends one or more JSON-serializable *events* to `self._events`.
   No drawing logic lives in Python.
2. **Frontend (`_ESM`)** is a dumb playback engine. It never computes turtle geometry; it
   only interpolates and renders the events it receives. This is why the animation can
   never drift from real `turtle` behaviour.

State crosses the boundary via synced traitlets: `events`, `source_lines`, `show_code`,
`width`, `height`, `bg`. They are flushed once (see *Display lifecycle*), not streamed
per-command.

### Coordinate system

Turtle coordinates: origin at canvas centre, `+x` right, `+y` **up**, heading in degrees
with `0` = east and counter-clockwise positive. The frontend converts to canvas pixels
with `TX(x) = cx + x` and `TY(y) = cy - y` (canvas `y` is flipped). HiDPI is handled by
scaling both the visible and offscreen canvases by `devicePixelRatio`.

### Event protocol

Every event is a dict with an `op` plus op-specific fields. `_emit()` injects `line`
(1-based source line for highlighting) and `speed` (0–10) into *every* event.

| `op`      | fields                                   | animated? | notes |
|-----------|------------------------------------------|-----------|-------|
| `line`    | `x1,y1,x2,y2,color,width`                | yes       | pen-down move; animated stroke |
| `move`    | `x1,y1,x2,y2`                            | yes       | pen-up travel; marker only |
| `turn`    | `x,y,from,to`                            | yes       | rotation; spins exactly `to-from` |
| `teleport`| `x,y,heading`                            | no        | instant marker placement, no drawing; emitted as the first event when the turtle spawns away from the origin (`home=`) |
| `dot`     | `x,y,size,color`                         | no        | filled dot |
| `stamp`   | `x,y,heading,color`                      | no        | turtle-shaped mark |
| `write`   | `x,y,text,color,align,font`              | no        | `font` is a CSS font string |
| `fill`    | `points:[[x,y],...],color`               | no        | drawn with `destination-over` (behind outline) |
| `color`   | `color`                                  | no        | sets current pen colour |
| `width`   | `width`                                  | no        | sets pen width |
| `pen`     | `down:bool`                              | no        | pen up/down (state only) |
| `show`/`hide` | —                                    | no        | marker visibility |
| `bgcolor` | `color`                                  | no        | repaints background behind art |
| `obstacle`| `kind` + shape fields + `color`,`visible`,`sense`,`label`,`index`| no | registered obstacle (circle/segment/polygon); `label` is metadata (not drawn); drawn on the obstacle layer if `visible`; `sense=False` hides it from the sensing queries only |
| `obstacles_visible` | `visible:bool`                 | no        | show/hide *all* obstacles at once |
| `sense`   | `x,y,rays:[{a,d,hit}],color`             | no        | sensor-ray overlay (dashed rays + hit dots) |
| `clear`   | —                                        | no        | clears committed art only (obstacle layer untouched) |

Curves (`circle`) are decomposed in Python into many small `line` chords, so the frontend
needs no arc logic.

### Animation engine (`build` in `_ESM`)

- An offscreen `base` canvas holds committed art; the visible canvas is redrawn each frame
  as `drawImage(base)` + the in-progress segment + the turtle marker.
- `step(ts)` is the frame loop, driven by **`setTimeout`** (~16 ms, via `schedule()`) — *not*
  `requestAnimationFrame`. Notebook webviews (VS Code) stop ticking the compositor when an
  output goes idle, so a freshly-scheduled rAF after one animation finished never fires and
  re-runs freeze (tell-tale: clicking *Pause*, which keeps a loop alive, "fixes" re-runs).
  Don't switch this back to rAF. Per event: `durationOf()` gives a duration
  (0 ⇒ instant; `speed:0` ⇒ instant). Animated events interpolate via `renderProgress()`;
  on completion `commit()` writes to `base` and `endState()` advances the tracked
  `{x,y,heading,visible,pencolor}`. Instant events `commit()` immediately and the loop
  continues within the same frame.
- `setActiveLine(ev.line)` drives the synced code highlight + autoscroll.
- `build({model, el})` does the work above and returns a cleanup that clears the pending timer.
- `render` (the anywidget entry point) is a thin wrapper: it `build`s once, then **re-mounts on
  every model change** (`change:events`, `change:source_lines`, `change:width`, …), tearing
  down the previous mount each time (changes are coalesced via a microtask). This is essential
  for re-execution robustness — notebook frontends (esp. VS Code) may create the view *before*
  the model state has synced, or reuse a view across cell re-runs; reading `events` only once in
  `render` left the canvas blank/un-animated on the 2nd+ run. Do not move the animation back into
  a one-shot `render`.

### Heading semantics (subtle — matches CPython turtle)

`self._heading` is kept normalized to `[0, 360)`. `right`/`left` emit a `turn` whose
`to = from ∓ angle` (raw, so the animation spins exactly the requested amount, including
multi-turn spins like `right(720)`). `setheading`/`home` use `_turn_to()`, which rotates
through the **shortest signed path** (`((to-from+180) % 360) - 180`). Do not "simplify"
this to absolute from→to or the turtle will over-spin (e.g. `home()` would spin 540°).

### Obstacles & sensing

Obstacles live in Python: registered shapes in `self._obstacles`, the drawn
`self._trail_segments`, and the canvas border. `add_circle`/`add_segment`/`add_polygon`/
`add_rectangle` store a shape (wrapped as an `Obstacle`, carrying an optional `label=` and its
`index`) and emit an `obstacle` event. Sensing is **pure-Python geometry** (module-level
`_ray_*` / `_*_closest_point` / `_rel_angle` / `_incidence_angle`; geometry helpers map
`kind` `"trail"`→segment and `"wall"`→polygon). All three queries return `Detection`s —
`(distance, angle, point, kind, index, obstacle)`, where `obstacle` is the shape hit as an
`Obstacle` (a `dict` subclass that also exposes its keys as attributes: `.kind`, `.label`,
`.color`, `.visible`, `.sense`, `.index`, geometry). For a registered shape it **is** the object in
`self._obstacles`; walls/trails get a synthetic `Obstacle` (`_wall_obstacle`/`_trail_obstacle`,
`index`/`label` `None`). The flat `kind`/`index` fields are kept as convenience mirrors of
`obstacle.kind`/`obstacle.index`. `sense` returns a nearest-first list and `nearest` the single
closest (or `None`), with `angle` the signed **bearing** (+ = left/CCW) to the closest point.
`distance_ahead` casts a ray along
the heading and returns the obstacle hit there (or `None`), with `angle` the signed **angle
of incidence** (0 = head-on, ±90 = grazing — from the travel direction and the contact
`normal`). A small epsilon means the turtle never senses the point it is standing on. Pass
`draw=True` to a query to
*also* emit a `sense` event (the ray overlay, coloured by the `color=` kwarg — default `"red"`,
run through `_as_color`) — otherwise the queries are side-effect-free.

Each obstacle has a `visible` flag (`add_*(..., visible=False)` for an invisible wall);
`show_obstacles()`/`hide_obstacles()` toggle them all (emitting `obstacles_visible`).
Visibility is **rendering-only** — invisible obstacles are still sensed and still collide.
Independently, each obstacle has a `sense` flag (`add_*(..., sense=False)`): `_scan`/`_ray_ahead`
skip non-sensing shapes (so they're invisible to `sense`/`nearest`/`distance_ahead`), but they
are **still drawn and still collide**. The three concerns are orthogonal: `visible` = drawn,
`sense` = detectable by the queries, collision = always (when `on_collision` is enabled). The
`sense` filter applies only to registered shapes — walls/trail detectability is governed by each
query's `walls=`/`trail=` args.

In the frontend, obstacles live on their **own offscreen layer** (`obs`/`octx`), composited
under the art each frame: `blitBase` draws background → `obs` → `base` (art). `redrawObstacles`
repaints the layer (only `visible` ones) on `obstacle`/`obstacles_visible`. So `clear` (art
only) leaves obstacles intact, and `bgcolor` just sets the `bgColor` state repainted each
frame. In Python, `clear()` keeps `_obstacles` but resets `_trail_segments`; `reset()` clears
both.

### Collisions (`on_collision`)

`on_collision(handler, stop=True, walls=True, trail=False)` opts into collision detection
(off until called). By default the turtle **stops at the first obstacle/edge it hits**:
`_goto` calls `_collisions_along(start, requested)` to find the crossings (module-level
`_obstacle_hit`/`_seg_hit`/`_circle_hit`, each returning a contact distance `t` + the surface
`normal` facing the mover), and if any, shortens the move to the nearest contact point before
emitting it (and returns `False`). `circle` walks chords through `_goto` and breaks its loop
when `_goto` returns `False`. With `stop=False` the move completes unchanged and the handler
is notified for *every* crossing, nearest first. `_dispatch_collisions` builds a `Collision`
(contact `point`/`normal`/`distance`; `angle` = signed angle of incidence (0°=head-on,
±90°=grazing, via `_incidence_angle` from the travel direction and the contact `normal`); `speed` =
effective move speed (global × the per-move `speed=` multiplier); the `obstacle` hit (the
`Obstacle` shape object — `.kind`/`.label`/geometry) and its `index`;
and the turtle's state — `pos`, `heading`, `isdown`, …) and calls the handler; a `_colliding`
re-entrancy
guard lets the handler move the turtle (e.g. to bounce) without re-triggering. **Pure Python,
no new frontend op.** `_dispatch_collisions` also bumps the public `nr_collisions` counter once
per dispatched `Collision` (so `stop=True` ⇒ one per colliding move, `stop=False` ⇒ one per
crossing); `reset()` zeroes it.

### Source capture for code-sync

The `@_records` decorator on every public method grabs `sys._getframe(1).f_lineno` (and
the caller's filename once). Full cell source comes from `linecache.getlines(filename)` —
ipykernel registers each cell there, so line numbers are **cell-relative** and highlight
the correct line. `code="..."` overrides the captured source.

### Display lifecycle

Three paths, all idempotent via the `_rendered` flag and `_unhook()`:

- **Auto** (default): `_register_autoshow()` installs a one-shot `post_run_cell` IPython
  hook that flushes traitlets and `display()`s the widget at end of cell — so the bare
  example works without putting `t` on the last line.
- **Explicit expression**: `_repr_mimebundle_` flushes, marks rendered, unhooks, then
  defers to `anywidget`.
- **`t.show()`**: same flush + `display`, also unhooks.

`autoshow=False` skips the hook (use for headless inspection/tests).

`_flush` only sends `source_lines` when `show_code` is True — the cell source is the only
synced content that varies with the cell's *text*, and transmitting it on every (re-)display
when it's unused was a source of re-render flakiness in VS Code.

## Conventions & gotchas

- **`width` is the canvas-size trait**, so the pen-width method is `pensize()` (alias
  `width_`), *not* `width()`. Don't rename the trait.
- Canvas is **fixed size** (default 500×500, origin centred). Out-of-bounds drawing is
  clipped, like real turtle — there is no autoscale.
- The frontend has **no external/CDN dependencies** (offline-safe). Keep it that way; the
  Python syntax highlighter and everything else is hand-rolled in `_ESM`.
- Colours pass straight through to CSS. Tuples are converted by `_as_color` (floats in
  `[0,1]` are treated as the 0–1 RGB scale, otherwise 0–255).
- The per-line highlighter tokenizes one line at a time, so triple-quoted strings spanning
  lines won't be perfectly coloured — acceptable for turtle scripts.
- **Editing the frontend needs a kernel restart.** `_ESM`/`_CSS` are baked into the `Turtle`
  class at import and the editable-installed module is cached, so re-running a cell keeps the
  *old* frontend. Restart the kernel to load `_ESM` edits — not doing so caused a long detour
  where dormant fixes appeared to "do nothing".
- `_ESM` has a `TW_DEBUG` flag (default `false`): set it `true` to log the render/build
  timeline to the browser console and show a per-widget status panel (events count, frames,
  re-mounts), for diagnosing frontend re-render issues.
- `Turtle.duration()` returns the total *scheduled* animation time in seconds, summed in Python
  with the **same** formula the frontend uses (`line`/`move`: `dist/(speed·150)`; `turn`:
  `|Δ|/(speed·180)`; `speed:0` and all other ops instant). If you ever change `pxPerSec`/
  `degPerSec` in `_ESM`, update `duration()` to match. *Live* elapsed/remaining time is
  browser-side only and is **not** synced back to Python — there is no trait for it.
- `home()` returns to a **configurable** home (default origin, heading 0), stored in
  `self._home`/`self._home_heading`. Set it with `sethome(x, y, heading=0)` (also accepts an
  `(x,y[,heading])` tuple) or the `Turtle(home=(x, y[, heading]))` constructor arg.
  `Turtle(home=...)` also **spawns** the turtle there: `_spawn_at_home()` sets the initial
  position/heading and, when it isn't the origin, emits a leading `teleport` event so the
  frontend marker starts there too (the marker `state` otherwise inits to `(0,0)` in `_ESM`,
  which would strand the marker at the origin for e.g. a lone `dot()`). `reset()` re-spawns at
  home the same way. `sethome()` by contrast only re-targets `home()` — it does **not** move
  the turtle.
- **Public counters** (plain instance attrs, not traits — not synced to the frontend; all start
  at 0 and are zeroed by `reset()`): `nr_collisions` (see Collisions above) plus `nr_left`,
  `nr_right`, `nr_sense`, `nr_distance_ahead`, each bumped at the top of the matching method.
  These count *direct* calls only — there are no internal `self.left/right/sense/distance_ahead`
  calls (movement/heading helpers go through `_turn_to`/`_goto`, and `nearest` calls `_scan`
  directly), so nothing double-counts; the `lt`/`rt` aliases count because they *are* `left`/`right`.
  `total_movement` (float) is the total distance travelled; it accumulates in `_goto` (the single
  translation funnel) using the *post-truncation* end point, so a collision-shortened move counts
  only the distance actually travelled, pen-up moves count, and turns add nothing. The
  construction-time `teleport` (spawn) bypasses `_goto`, so spawning at a custom home is not
  counted as movement.

## Adding a new turtle command (recipe)

1. Add a method decorated with `@_records` and give it a numpy-style docstring; update
   internal turtle state.
2. `self._emit(op="...", ...)` with **absolute** coordinates. Do not duplicate `line`/
   `speed` — `_emit` adds them.
3. If the command should call another recorded method internally, factor the logic into a
   private helper (like `_turn_to` / `_goto`) and call that — calling the decorated method
   would overwrite `_cur_line` with the wrong frame and break highlighting.
4. Handle the new `op` in `_ESM`: add to `durationOf`/`renderProgress` if animated, and to
   `commit`/`endState`/`applyConfig` as needed.
5. Update the event-protocol table above.

## Testing approach

- Geometry is deterministic and headless-testable: `Turtle(autoshow=False)`, run commands,
  assert `pos()/heading()`, and `json.dumps(t._events)` to confirm serializability. These
  assertions belong in `test/` (port the demo's final self-test cell there).
- Validate the frontend parses with `node --check` on the extracted `_ESM`.
- Visual smoke test: run `docs/pages/demo.ipynb`.
