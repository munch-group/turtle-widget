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
| `dot`     | `x,y,size,color`                         | no        | filled dot |
| `stamp`   | `x,y,heading,color`                      | no        | turtle-shaped mark |
| `write`   | `x,y,text,color,align,font`              | no        | `font` is a CSS font string |
| `fill`    | `points:[[x,y],...],color`               | no        | drawn with `destination-over` (behind outline) |
| `color`   | `color`                                  | no        | sets current pen colour |
| `width`   | `width`                                  | no        | sets pen width |
| `pen`     | `down:bool`                              | no        | pen up/down (state only) |
| `show`/`hide` | —                                    | no        | marker visibility |
| `bgcolor` | `color`                                  | no        | repaints background behind art |
| `clear`   | —                                        | no        | clears committed art |

Curves (`circle`) are decomposed in Python into many small `line` chords, so the frontend
needs no arc logic.

### Animation engine (`render` in `_ESM`)

- An offscreen `base` canvas holds committed art; the visible canvas is redrawn each frame
  as `drawImage(base)` + the in-progress segment + the turtle marker.
- `step(ts)` is a `requestAnimationFrame` loop. Per event: `durationOf()` gives a duration
  (0 ⇒ instant; `speed:0` ⇒ instant). Animated events interpolate via `renderProgress()`;
  on completion `commit()` writes to `base` and `endState()` advances the tracked
  `{x,y,heading,visible,pencolor}`. Instant events `commit()` immediately and the loop
  continues within the same frame.
- `setActiveLine(ev.line)` drives the synced code highlight + autoscroll.
- `render` returns a cleanup function that cancels the RAF (anywidget calls it on teardown).

### Heading semantics (subtle — matches CPython turtle)

`self._heading` is kept normalized to `[0, 360)`. `right`/`left` emit a `turn` whose
`to = from ∓ angle` (raw, so the animation spins exactly the requested amount, including
multi-turn spins like `right(720)`). `setheading`/`home` use `_turn_to()`, which rotates
through the **shortest signed path** (`((to-from+180) % 360) - 180`). Do not "simplify"
this to absolute from→to or the turtle will over-spin (e.g. `home()` would spin 540°).

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
