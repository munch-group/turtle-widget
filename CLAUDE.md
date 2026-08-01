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

- `src/turtle_widget/widget.py` — the whole widget: `Canvas` (the widget/display half)
  and `Turtle(Canvas)` (the drawing half — see "Canvas vs Turtle") + the embedded
  `_ESM`/`_CSS` frontend strings. (`_esm`/`_css` on `Canvas` just alias those
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
   internal state and appends one or more JSON-serializable *events* to
   `self._canvas._events`. No drawing logic lives in Python.
2. **Frontend (`_ESM`)** is a dumb playback engine. It never computes turtle geometry; it
   only interpolates and renders the events it receives. This is why the animation can
   never drift from real `turtle` behaviour.

State crosses the boundary via synced traitlets: `events`, `source_lines`, `show_code`,
`width`, `height`, `bg`. They are flushed once (see *Display lifecycle*), not streamed
per-command.

### Canvas vs Turtle

`Canvas(anywidget.AnyWidget)` owns the widget half: `_esm`/`_css`, the six synced traits
above, the `_events` list (plus `_obstacles`/`_trail_segments` — shared "world" state,
see "Obstacles & sensing"), and the display lifecycle (`_flush`, `_get_source`, `show`,
`_repr_mimebundle_`, autoshow). `Turtle(Canvas)` owns the turtle half: position, heading,
home, pen, colour, speed, visibility, fill state, the collision *configuration* flags
(`_collision_stop`/`_walls`/`_trail`/`_cb`), and the public counters. A bare `t =
Turtle()` sets `self._canvas = self` in `__init__`, so it is simultaneously a turtle and
the canvas it draws on — the zero-ceremony single-turtle path (`t = Turtle()`) is
unchanged; there is no separate "make a screen, then populate it" step.

Multiple turtles share a canvas by pointing `_canvas` at the same object instead of at
themselves:

    t1 = Turtle()
    t2 = t1.new_turtle(color="red")     # preferred: never builds a canvas of its own
    t3 = t1.other_turtle(Turtle())      # joins an already-constructed Turtle instead

`new_turtle()` builds the new turtle via `cls.__new__(cls)` rather than `Turtle(...)`,
skipping `Canvas`/`AnyWidget`/`Widget.__init__` entirely — `HasTraits.__new__` still runs
(so e.g. `t2.width` reads a harmless class-default `500`), but `Widget.__init__`'s
`self.open()` — which is what actually creates a comm and messages the frontend — never
runs, so no comm is ever opened for a turtle that will never display itself.
`other_turtle(existing_turtle)` instead joins a `Turtle()` that was already fully
constructed (so it *did* build, and now discards, a real widget/comm of its own) and
unhooks its autoshow. Both leave the joined turtle's own canvas-level trait access dead
(`t2.width`/`.height`/`.show_code`/`.bg`/`.events`/`.source_lines` reflect its own unused
canvas, not the one it actually draws on — use `t2._canvas.width` etc. for the real
values); this is deliberately left undocumented-away rather than proxied, since
traitlets' descriptor machinery makes per-instance proxying fragile for little benefit.
Prefer `new_turtle()` in docs/examples; `other_turtle()` exists for joining a turtle
someone else's code already constructed.

Each turtle gets a small per-canvas sequential id (`self._turtle_id`, assigned by
`Canvas._new_turtle_id()` when it is constructed *or joined* — joining always assigns a
fresh id, since a turtle keeps whatever id it had on its previous canvas otherwise,
which could collide with another turtle already on the new one). `_emit()` stamps this
onto every event (see "Event protocol").

Only one turtle's event animates at a time, by design — see "No simultaneous animation"
under "Animation engine" below.

### Coordinate system

Turtle coordinates: origin at canvas centre, `+x` right, `+y` **up**, heading in degrees
with `0` = east and counter-clockwise positive. The frontend converts to canvas pixels
with `TX(x) = cx + x` and `TY(y) = cy - y` (canvas `y` is flipped). HiDPI is handled by
scaling both the visible and offscreen canvases by `devicePixelRatio`.

### Event protocol

Every event is a dict with an `op` plus op-specific fields. `_emit()` injects `line`
(1-based source line for highlighting), `speed` (0–10), and `turtle` (the emitting
turtle's per-canvas id, from `Canvas._new_turtle_id()` — see "Canvas vs Turtle" below)
into *every* event. The frontend keys its per-turtle tracked state (`states`, a `Map`)
by this id, so each turtle's marker/pen-colour/visibility is tracked independently
even though every turtle's events share one flat stream, replayed in program order.

| `op`      | fields                                   | animated? | notes |
|-----------|------------------------------------------|-----------|-------|
| `line`    | `x1,y1,x2,y2,color,width`                | yes       | pen-down move; animated stroke |
| `move`    | `x1,y1,x2,y2`                            | yes       | pen-up travel; marker only |
| `turn`    | `x,y,from,to`                            | yes       | rotation; spins exactly `to-from` |
| `teleport`| `x,y,heading`                            | no        | instant marker placement, no drawing; emitted when a turtle spawns away from the origin (`home=`) or when `reset()` moves it back to a home that differs from where it just was |
| `dot`     | `x,y,size,color`                         | no        | filled dot |
| `stamp`   | `x,y,heading,color`                      | no        | turtle-shaped mark |
| `write`   | `x,y,text,color,align,font`              | no        | `font` is a CSS font string |
| `fill`    | `points:[[x,y],...],color`               | no        | drawn with `destination-over` (behind outline) |
| `color`   | `color`                                  | no        | sets current pen colour |
| `width`   | `width`                                  | no        | sets pen width |
| `pen`     | `down:bool`                              | no        | pen up/down (state only) |
| `show`/`hide` | —                                    | no        | marker visibility |
| `bgcolor` | `color`                                  | no        | repaints background behind art |
| `obstacle`| `kind` + shape fields + `color`,`visible`,`sense`,`label`,`index`| no | registered obstacle (circle/segment/polygon), shared canvas-wide (any turtle can register one, every turtle senses/collides with it); `label` is metadata (not drawn); drawn on the obstacle layer if `visible`; `sense=False` hides it from the sensing queries only |
| `obstacles_visible` | `visible:bool`                 | no        | show/hide *all* obstacles at once |
| `sense`   | `x,y,rays:[{a,d,hit}],color`             | no        | sensor-ray overlay (dashed rays + hit dots) |
| `clear`   | —                                        | no        | clears committed art **for the whole canvas** — every turtle's drawing, not just the caller's (obstacle layer untouched); see `Turtle.clear`'s docstring |

Curves (`circle`) are decomposed in Python into many small `line` chords, so the frontend
needs no arc logic.

### Animation engine (`build` in `_ESM`)

- An offscreen `base` canvas holds committed art; the visible canvas is redrawn each frame
  as `drawImage(base)` + the in-progress segment + every turtle's marker.
- `step(ts)` is the frame loop, driven by **`setTimeout`** (~16 ms, via `schedule()`) — *not*
  `requestAnimationFrame`. Notebook webviews (VS Code) stop ticking the compositor when an
  output goes idle, so a freshly-scheduled rAF after one animation finished never fires and
  re-runs freeze (tell-tale: clicking *Pause*, which keeps a loop alive, "fixes" re-runs).
  Don't switch this back to rAF. Per event: `durationOf()` gives a duration
  (0 ⇒ instant; `speed:0` ⇒ instant). Animated events interpolate via `renderProgress()`;
  on completion `commit()` writes to `base` and `endState()` advances the tracked per-turtle
  `{x,y,heading,visible,pencolor}` (`states`, a `Map` keyed by `ev.turtle` — see `stateFor()`).
  Instant events `commit()` immediately and the loop continues within the same frame.
- **No simultaneous animation.** `step()` walks one flat event array with one cursor and one
  clock; each event's duration fully elapses before the next begins. With a shared stream
  (multiple turtles on one canvas), events land in the order the statements ran, so turtles
  animate by taking turns in program order — cheap, and matches this widget's pedagogical
  goal that code runs top to bottom. `renderProgress()`/`drawFinal()` draw every *other* known
  turtle's last-settled marker each frame (`drawIdleTurtles()`) so an idle turtle doesn't
  vanish while another one's move animates. Concurrent timelines would need a rewrite of
  `step`/`durationOf` to track one clock per turtle — not planned.
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

Obstacles live in Python, at the **canvas** level (shared "world" state — every turtle
drawing on a canvas senses/collides with every other turtle's obstacles and trail, the
one genuinely new capability from supporting multiple turtles): registered shapes in
`self._canvas._obstacles`, the drawn `self._canvas._trail_segments`, and the canvas
border (`_wall_obstacle()`, sized from `self._canvas.width`/`height`, not `self.width`/
`height` — those are dead on a joined turtle, see "Canvas vs Turtle"). `add_circle`/
`add_segment`/`add_polygon`/`add_rectangle` store a shape (wrapped as an `Obstacle`,
carrying an optional `label=` and its `index`) and emit an `obstacle` event. Sensing is
**pure-Python geometry** (module-level
`_ray_*` / `_*_closest_point` / `_rel_angle` / `_incidence_angle`; geometry helpers map
`kind` `"trail"`→segment and `"wall"`→polygon). All three queries return `Detection`s —
`(distance, angle, point, kind, index, obstacle)`, where `obstacle` is the shape hit as an
`Obstacle` (a `dict` subclass that also exposes its keys as attributes: `.kind`, `.label`,
`.color`, `.visible`, `.sense`, `.index`, geometry). For a registered shape it **is** the object in
`self._canvas._obstacles`; walls/trails get a synthetic `Obstacle` (`_wall_obstacle`/`_trail_obstacle`,
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
frame.

In Python, `clear()` always resets `self._canvas._trail_segments` (keeping
`_obstacles`) — deliberately whole-canvas, since once art is committed to the shared
`base` bitmap there is no way to erase only one turtle's pixels from it (see the `clear`
row in "Event protocol"). `reset()`, by contrast, wipes canvas-level state
(`_events`/`_obstacles`/`_trail_segments`) only when `self._canvas is self` — i.e. only
for a turtle that *is* its own canvas (the common, single-turtle case, unchanged from
before this widget supported sharing one). A turtle joined to another's canvas resets
only its own position/heading/pen/colours/fill/counters and re-spawns at home (emitting
a `teleport` if that actually moves it — see `_spawn_at_home`), leaving the shared
drawing, obstacles and trail alone: resetting your turtle shouldn't erase someone else's
maze or trail.

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

All of this lives on `Canvas` (see "Canvas vs Turtle"), not `Turtle` — a joined turtle
(`new_turtle`/`other_turtle`) is unhooked and never goes through this itself; only the
canvas-owning turtle displays. Three paths, all idempotent via the `_rendered` flag and
`_unhook()`:

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
  `Turtle(home=...)` also **spawns** the turtle there via `_spawn_at_home()`, and `reset()`
  re-spawns at home the same way. `_spawn_at_home()` sets the position/heading and emits a
  `teleport` event whenever that actually *changes* them (comparing against wherever the
  turtle was a moment ago, captured before the overwrite) — at construction that means "home
  isn't the origin" (a turtle's marker otherwise inits lazily to `(0,0,0)` the first time
  `_ESM`'s `states` Map sees its id, which would strand it at the origin for e.g. a lone
  `dot()`); at `reset()` it means "home differs from wherever this turtle just was", so the
  marker visibly snaps back rather than silently updating Python-side state the frontend
  never hears about. `sethome()` by contrast only re-targets `home()` — it does **not** move
  the turtle.
- **Public counters** (plain instance attrs, not traits — not synced to the frontend; per
  turtle, so a joined turtle's counters are independent of the canvas-owning turtle's; all
  start at 0 and are zeroed by `reset()`): `nr_collisions` (see Collisions above) plus `nr_left`,
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
   `speed`/`turtle` — `_emit` adds them.
3. If the command should call another recorded method internally, factor the logic into a
   private helper (like `_turn_to` / `_goto`) and call that — calling the decorated method
   would overwrite `_cur_line` with the wrong frame and break highlighting.
4. If the command reads or mutates "world" state shared by every turtle on a canvas
   (obstacles, the drawn trail) or canvas-level traits (`width`/`height`/`bg`/...), go
   through `self._canvas` rather than `self` — see "Canvas vs Turtle". Bare `self.width`
   etc. is silently wrong for a joined turtle (it reads that turtle's own dead trait, not
   the canvas it draws on).
5. Handle the new `op` in `_ESM`: add to `durationOf`/`renderProgress` if animated, and to
   `commit`/`endState`/`applyConfig` as needed (use `stateFor(ev.turtle)` for any per-turtle
   tracked state, not a bare local).
6. Update the event-protocol table above.

## Testing approach

- Geometry is deterministic and headless-testable: `Turtle(autoshow=False)`, run commands,
  assert `pos()/heading()`, and `json.dumps(t._events)` to confirm serializability. These
  assertions belong in `test/` (port the demo's final self-test cell there).
- `test/test_multi_turtle.py` is the regression net for two-or-more turtles sharing a
  canvas (`new_turtle()`/`other_turtle()`, cross-turtle sensing/collision, `reset()`/`clear()`
  scoping) — same headless style as the other `test/test_*.py` files.
- Validate the frontend parses with `node --check` on the extracted `_ESM`.
- Visual smoke test: run `docs/pages/demo.ipynb`.
