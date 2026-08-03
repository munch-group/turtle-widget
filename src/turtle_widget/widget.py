"""
turtle_widget
=============

A small, dependency-light implementation of Python's `turtle` drawing API as a
Jupyter widget that renders an *animated* HTML/JS canvas below the cell.

* Works across VS Code notebooks, JupyterLab, Notebook 7 and Colab because it is
  built on `anywidget` (the standard ipywidgets comm protocol + plain ESM).
* Geometry is computed in Python and replayed as self-contained events in the
  browser, so the drawing matches the real turtle exactly and the frontend
  cannot drift.
* `show_code=True` displays the cell source in a second column and highlights the
  line responsible for each step *in sync* with the animation.

Usage
-----
    from turtle_widget import Turtle

    t = Turtle(show_code=True)        # show_code is optional
    t.speed(5)
    t.penup(); t.left(90); t.forward(200); t.right(90); t.pendown()
    for i in range(18):
        t.pencolor(["red","blue","yellow","brown","black","purple","green"][i % 7])
        t.right(20); t.forward(50)
    t.right(180)
    t.home()

The widget appears automatically at the end of the cell (no need to put `t` on
the last line). You can also display it explicitly with `t.show()` or by
evaluating `t`. Pass `autoshow=False` to disable the automatic display.
"""

from __future__ import annotations

import collections
import contextlib
import functools
import linecache
import math
import sys

import anywidget
import traitlets

try:  # IPython is present whenever a kernel is running, but guard anyway.
    from IPython import get_ipython
    from IPython.display import display as _ipy_display
except Exception:  # pragma: no cover
    def get_ipython():
        return None

    def _ipy_display(*a, **k):
        pass


__all__ = ["Turtle"]


# --------------------------------------------------------------------------- #
# Frontend (ESM + CSS) -- no external dependencies, offline-safe.             #
# --------------------------------------------------------------------------- #

_ESM = r"""
// --- turtle-widget diagnostics: set TW_DEBUG=true to log render/build timeline to the
// console and show a per-widget status panel (used to debug VS Code re-render issues) ---
const TW_DEBUG = false;
const _twDiag = (typeof window !== "undefined")
  ? (window.__twDiag = window.__twDiag || { renders: 0, builds: 0 })
  : { renders: 0, builds: 0 };
/** Debug logger gated by TW_DEBUG; a cheap no-op when debugging is off. */
function twlog(){ if (!TW_DEBUG) return; try { console.log.apply(console, ["[turtle-widget]"].concat([].slice.call(arguments))); } catch (e) {} }

// Vocabulary for the hand-rolled Python syntax highlighter below (no external
// tokenizer/CDN dependency — see CLAUDE.md "no external/CDN dependencies").
const KEYWORDS = new Set(["False","None","True","and","as","assert","async","await",
  "break","class","continue","def","del","elif","else","except","finally","for","from",
  "global","if","import","in","is","lambda","nonlocal","not","or","pass","raise","return",
  "try","while","with","yield"]);
const BUILTINS = new Set(["print","range","len","int","float","str","list","dict","set",
  "tuple","abs","min","max","sum","enumerate","zip","map","filter","sorted","round",
  "Turtle","reversed","bool"]);

/** HTML-escape a string so raw source text can be injected via innerHTML safely. */
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

/**
 * Tokenize and HTML-highlight one line of Python source for the code column.
 * A single-pass hand-rolled scanner (comment / quoted string / number /
 * keyword-or-builtin / other), not a real parser — so triple-quoted strings
 * spanning multiple lines aren't recognized, since each line is tokenized
 * independently (acceptable for turtle scripts; see CLAUDE.md's per-line
 * highlighter note). Returns an HTML string with `tw-*` span classes (styled
 * in `_CSS`).
 */
function highlight(line){
  let out = "", i = 0; const n = line.length;
  while (i < n){
    const c = line[i];
    if (c === "#"){ out += '<span class="tw-com">' + esc(line.slice(i)) + "</span>"; break; }
    if (c === '"' || c === "'"){
      const q = c; let j = i + 1, buf = c;
      // Stop at the next unescaped matching quote; doesn't special-case an
      // escaped backslash before the quote (e.g. "\\"), fine for typical
      // turtle scripts (see per-line highlighter caveat above).
      while (j < n){ buf += line[j]; if (line[j] === q && line[j-1] !== "\\"){ j++; break; } j++; }
      out += '<span class="tw-str">' + esc(buf) + "</span>"; i = j; continue;
    }
    if (c >= "0" && c <= "9"){
      let j = i, buf = "";
      // Permissive digit-run: also swallows ., _ and e/E so floats/exponents/
      // underscore-separated literals highlight as one token; not a strict grammar.
      while (j < n && /[0-9._eE]/.test(line[j])){ buf += line[j]; j++; }
      out += '<span class="tw-num">' + esc(buf) + "</span>"; i = j; continue;
    }
    if (/[A-Za-z_]/.test(c)){
      let j = i, buf = "";
      while (j < n && /[A-Za-z0-9_]/.test(line[j])){ buf += line[j]; j++; }
      if (KEYWORDS.has(buf)) out += '<span class="tw-kw">' + esc(buf) + "</span>";
      else if (BUILTINS.has(buf)) out += '<span class="tw-bi">' + esc(buf) + "</span>";
      else out += esc(buf);
      i = j; continue;
    }
    out += esc(c); i++;
  }
  return out;
}

/**
 * Mount the widget for one render pass: build the DOM (canvas + optional code
 * column + controls), prime the offscreen buffers, and start the animation.
 * Called fresh by `render()` on every relevant model change — see that
 * function's comment for why a one-shot build isn't enough.
 *
 * @param {object} args
 * @param {object} args.model - anywidget model; source of the synced
 *   traitlets (`width`, `height`, `show_code`, `bg`, `events`, `source_lines`).
 * @param {HTMLElement} args.el - the widget's output element; cleared and
 *   repopulated on every call.
 * @param {number} [args.rid] - the owning `render()` call's id, used only for
 *   TW_DEBUG log/status-panel labelling.
 * @returns {function(): void} cleanup — clears the pending frame timer so a
 *   torn-down/replaced build doesn't keep ticking in the background.
 */
function build({ model, el, rid }){
  el.innerHTML = "";
  _twDiag.builds++;
  const bid = _twDiag.builds;

  const width   = model.get("width");
  const height  = model.get("height");
  const showCode= model.get("show_code");
  let bgColor = model.get("bg") || "white";
  const events  = model.get("events") || [];
  const source  = model.get("source_lines") || [];
  twlog("build #" + bid + " (render #" + (rid || "?") + "):",
        "events=" + events.length, "source_lines=" + source.length,
        "show_code=" + showCode, "size=" + width + "x" + height);

  const dpr = window.devicePixelRatio || 1;
  const cx = width / 2, cy = height / 2;
  // Coordinate transform: turtle space has its origin at canvas centre with
  // +y up; canvas space has its origin top-left with +y down (see CLAUDE.md
  // "Coordinate system"). Every draw call below goes through TX/TY.
  const TX = x => cx + x;          // turtle coords -> canvas coords (y is up)
  const TY = y => cy - y;

  // ---- DOM scaffold ------------------------------------------------------ //
  const root = document.createElement("div");
  root.className = "tw-root";
  el.appendChild(root);

  const left = document.createElement("div");
  left.className = "tw-left";
  root.appendChild(left);

  const canvas = document.createElement("canvas");
  canvas.className = "tw-canvas";
  // HiDPI: the backing store is `dpr`x the CSS size, and the context below is
  // scaled by `dpr`, so every draw call afterwards can keep using logical
  // (CSS) pixel coordinates instead of device pixels.
  canvas.width = width * dpr; canvas.height = height * dpr;
  canvas.style.width = width + "px"; canvas.style.height = height + "px";
  left.appendChild(canvas);

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  // committed (already-drawn) art lives on an offscreen buffer, same HiDPI treatment
  const base = document.createElement("canvas");
  base.width = width * dpr; base.height = height * dpr;
  const bctx = base.getContext("2d");
  bctx.scale(dpr, dpr);

  // Obstacles live on their own offscreen layer, composited under the art each
  // frame by blitBase() (background -> obs -> base), so obstacles_visible can
  // toggle them all at once and clear() (which only clears `base`) leaves them
  // intact — see CLAUDE.md "In the frontend, obstacles live on their own
  // offscreen layer".
  const obs = document.createElement("canvas");
  obs.width = width * dpr; obs.height = height * dpr;
  const octx = obs.getContext("2d");
  octx.scale(dpr, dpr);

  let obstacles = [];   // world obstacles, drawn on the obs layer

  // controls
  const bar = document.createElement("div");
  bar.className = "tw-bar";
  const replayBtn = document.createElement("button");
  replayBtn.className = "tw-btn"; replayBtn.textContent = "\u21bb Replay";
  const pauseBtn = document.createElement("button");
  pauseBtn.className = "tw-btn"; pauseBtn.textContent = "\u275a\u275a Pause";
  bar.appendChild(replayBtn); bar.appendChild(pauseBtn);
  left.appendChild(bar);

  // TW_DEBUG status panel (see the flag's comment at the top of this file): a
  // small text readout of render/build ids, event count, and animation progress.
  let diagEl = null;
  if (TW_DEBUG){
    diagEl = document.createElement("div");
    diagEl.className = "tw-diag";
    diagEl.style.cssText = "font:11px/1.45 ui-monospace,monospace;color:#888;margin-top:6px;white-space:pre-wrap;";
    left.appendChild(diagEl);
  }
  /** Update the TW_DEBUG status line; no-op (diagEl is null) when TW_DEBUG is off. */
  function setDiag(s){ if (diagEl) diagEl.textContent = s; }
  setDiag("render #" + (rid || "?") + " · build #" + bid + " · events " + events.length + " · waiting…");

  // Code column: one .tw-codeline row per source line, syntax-highlighted up
  // front via highlight(); setActiveLine() below toggles which row is
  // highlighted as events play. Only built when show_code is on.
  let codeRows = [];
  if (showCode){
    const right = document.createElement("div");
    right.className = "tw-right";
    right.style.maxHeight = (height + 36) + "px";
    const codeEl = document.createElement("div");
    codeEl.className = "tw-code";
    source.forEach((line, idx) => {
      const row = document.createElement("div");
      row.className = "tw-codeline";
      const num = document.createElement("span");
      num.className = "tw-ln"; num.textContent = String(idx + 1);
      const src = document.createElement("span");
      src.className = "tw-src"; src.innerHTML = highlight(line) || "&nbsp;";
      row.appendChild(num); row.appendChild(src);
      codeEl.appendChild(row);
      codeRows.push(row);
    });
    right.appendChild(codeEl);
    root.appendChild(right);
  }

  /**
   * Highlight the code row for a 1-based source line number (from `ev.line`,
   * which `_emit()` injects into every event on the Python side) and
   * autoscroll it into view if it isn't already visible. No-op when
   * show_code is off or the event has no meaningful line.
   */
  function setActiveLine(lineNo){
    if (!showCode || !lineNo) return;
    const target = codeRows[lineNo - 1];
    if (!target || target.classList.contains("tw-active")) return;
    codeRows.forEach(r => r.classList.remove("tw-active"));
    target.classList.add("tw-active");
    const rTop = target.offsetTop, rBot = rTop + target.offsetHeight;
    const parent = target.parentElement.parentElement; // .tw-right
    if (rTop < parent.scrollTop || rBot > parent.scrollTop + parent.clientHeight){
      parent.scrollTop = rTop - parent.clientHeight / 2;
    }
  }

  // ---- drawing helpers --------------------------------------------------- //
  /** Fill the whole canvas with the current background colour (mutable via `bgcolor` events). */
  function paintBg(g){ g.save(); g.fillStyle = bgColor; g.fillRect(0,0,width,height); g.restore(); }

  /**
   * Draw the turtle marker (a small triangular arrowhead) at `(x,y)` pointing
   * `headingDeg` (turtle convention: 0 = east, increasing counter-clockwise),
   * onto context `g` — which may be the live `ctx` (idle/mid-animation
   * marker) or `bctx` (a `stamp` event, baked permanently into committed art).
   */
  function drawTurtle(g, x, y, headingDeg, color){
    g.save();
    g.translate(TX(x), TY(y));
    // Negate: canvas rotate() is clockwise-positive (y-down), but turtle
    // heading is counter-clockwise-positive, so a straight heading->rotate
    // mapping would spin the marker the wrong way (canvas y is flipped).
    g.rotate(-headingDeg * Math.PI / 180);
    g.beginPath();
    g.moveTo(11, 0); g.lineTo(-8, -7); g.lineTo(-4, 0); g.lineTo(-8, 7);
    g.closePath();
    g.fillStyle = color || "black";
    g.fill();
    g.lineWidth = 1; g.strokeStyle = "rgba(0,0,0,0.35)"; g.stroke();
    g.restore();
  }

  /** Permanently stroke a completed `line` event onto the committed `base` layer. */
  function commitLine(ev){
    bctx.save();
    bctx.strokeStyle = ev.color || "black";
    bctx.lineWidth = ev.width || 1;
    bctx.lineCap = "round"; bctx.lineJoin = "round";
    bctx.beginPath();
    bctx.moveTo(TX(ev.x1), TY(ev.y1));
    bctx.lineTo(TX(ev.x2), TY(ev.y2));
    bctx.stroke();
    bctx.restore();
  }

  /**
   * Draw one obstacle (`ob` is the raw `obstacle` event: shape fields keyed by
   * `ob.kind` — "segment" / "circle" / "polygon" — plus `color`) onto context
   * `g`. Circles/polygons get a translucent fill plus an outline; segments
   * are outline-only. Always draws — visibility is filtered by the caller.
   */
  function drawObstacle(g, ob){
    g.save();
    const col = ob.color || "#6c6c84";
    if (ob.kind === "segment"){
      g.strokeStyle = col; g.lineWidth = 3; g.lineCap = "round";
      g.beginPath(); g.moveTo(TX(ob.x1), TY(ob.y1)); g.lineTo(TX(ob.x2), TY(ob.y2)); g.stroke();
    } else if (ob.kind === "circle"){
      g.beginPath(); g.arc(TX(ob.x), TY(ob.y), ob.r, 0, 2*Math.PI);
      g.globalAlpha = 0.18; g.fillStyle = col; g.fill();
      g.globalAlpha = 1; g.strokeStyle = col; g.lineWidth = 1.5; g.stroke();
    } else if (ob.kind === "polygon" && ob.points && ob.points.length){
      g.beginPath(); g.moveTo(TX(ob.points[0][0]), TY(ob.points[0][1]));
      for (let k = 1; k < ob.points.length; k++) g.lineTo(TX(ob.points[k][0]), TY(ob.points[k][1]));
      g.closePath();
      g.globalAlpha = 0.18; g.fillStyle = col; g.fill();
      g.globalAlpha = 1; g.strokeStyle = col; g.lineWidth = 1.5; g.stroke();
    }
    g.restore();
  }

  /**
   * Repaint the entire obstacle layer from the `obstacles` array (skipping
   * any with `visible === false`). Called whenever the set or visibility of
   * obstacles changes (`obstacle` / `obstacles_visible` events) — cheap
   * enough to redo wholesale rather than patch incrementally.
   */
  function redrawObstacles(){
    octx.clearRect(0, 0, width, height);
    for (const o of obstacles) if (o.visible !== false) drawObstacle(octx, o);
  }

  /**
   * Draw a `sense` event's ray overlay onto `base`: each ray in `ev.rays` is
   * `{a, d, hit}` (angle in degrees, distance, whether it hit something),
   * cast from `(ev.x, ev.y)` — dashed line for the ray, solid dot where it hit.
   */
  function commitSense(ev){
    const col = ev.color || "rgba(220,50,50,0.9)";
    bctx.save();
    bctx.strokeStyle = col; bctx.fillStyle = col; bctx.lineWidth = 1; bctx.setLineDash([4,3]);
    (ev.rays || []).forEach(r => {
      const rad = r.a * Math.PI / 180;
      const ex = ev.x + r.d * Math.cos(rad), ey = ev.y + r.d * Math.sin(rad);
      bctx.beginPath(); bctx.moveTo(TX(ev.x), TY(ev.y)); bctx.lineTo(TX(ex), TY(ey)); bctx.stroke();
      if (r.hit){
        bctx.save(); bctx.setLineDash([]);
        bctx.beginPath(); bctx.arc(TX(ex), TY(ey), 3.5, 0, 2*Math.PI); bctx.fill(); bctx.restore();
      }
    });
    bctx.restore();
  }

  /**
   * Apply the permanent, non-animated effect of a completed event: bakes
   * drawing ops onto `base`/`obs`, or updates non-drawing state (`bgcolor`,
   * obstacle bookkeeping). Dispatches on `ev.op` — see the event-protocol
   * table in CLAUDE.md for the full op list. Ops not handled here (color/
   * width/pen/show/hide/teleport/…) are cosmetic state handled by
   * applyConfig()/endState() instead, or need no persistent drawing action.
   */
  function commit(ev){
    switch (ev.op){
      case "line": commitLine(ev); break;
      case "dot":
        bctx.save(); bctx.fillStyle = ev.color || "black";
        bctx.beginPath(); bctx.arc(TX(ev.x), TY(ev.y), (ev.size||4)/2, 0, 2*Math.PI);
        bctx.fill(); bctx.restore(); break;
      case "write":
        bctx.save(); bctx.fillStyle = ev.color || "black";
        bctx.font = ev.font || "14px sans-serif";
        bctx.textAlign = ev.align || "left"; bctx.textBaseline = "alphabetic";
        bctx.fillText(ev.text || "", TX(ev.x), TY(ev.y)); bctx.restore(); break;
      case "stamp":
        drawTurtle(bctx, ev.x, ev.y, ev.heading, ev.color || "black"); break;
      case "fill":
        if (ev.points && ev.points.length > 1){
          bctx.save();
          bctx.globalCompositeOperation = "destination-over"; // fill *behind* lines
          bctx.fillStyle = ev.color || "black";
          bctx.beginPath();
          bctx.moveTo(TX(ev.points[0][0]), TY(ev.points[0][1]));
          for (let k = 1; k < ev.points.length; k++)
            bctx.lineTo(TX(ev.points[k][0]), TY(ev.points[k][1]));
          bctx.closePath(); bctx.fill(); bctx.restore();
        }
        break;
      case "bgcolor": bgColor = ev.color || "white"; break;
      case "obstacle": obstacles.push(ev); redrawObstacles(); break;
      case "obstacles_visible":
        for (const o of obstacles) o.visible = ev.visible;
        redrawObstacles(); break;
      case "sense": commitSense(ev); break;
      case "clear": bctx.clearRect(0,0,width,height); break;   // art only; obstacles persist
      default: break; // pure config events (color/width/pen/visibility) handled in state
    }
  }

  // ---- animation engine -------------------------------------------------- //
  // `states` tracks each turtle's position/heading/visibility/pen colour as of
  // the last *completed* event, keyed by the event's `turtle` id (stamped by
  // Turtle._emit -- see CLAUDE.md "Event protocol"); advanced by endState() and
  // read by renderProgress()/drawFinal() to draw markers and interpolate motion.
  // A Map (not a plain object) so turtle ids stay their original type (numbers)
  // instead of being coerced to string keys.
  const states = new Map();
  function stateFor(tid){
    let s = states.get(tid);
    if (!s){ s = { x: 0, y: 0, heading: 0, visible: true, pencolor: "black" }; states.set(tid, s); }
    return s;
  }
  let idx = 0, evStart = null, timer = null, paused = false, frameCount = 0;
  const FRAME_MS = 16;   // ~60fps target for the setTimeout-driven frame loop
  /**
   * Queue the next frame. Uses `setTimeout`, not `requestAnimationFrame`:
   * notebook webviews (VS Code) stop ticking the compositor when an output
   * goes idle, so a freshly-scheduled rAF after one animation finishes never
   * fires again on re-runs (tell-tale symptom: clicking Pause, which keeps
   * this timer chain alive via step()'s `if (paused)` branch, "fixes" a
   * frozen re-run). Do not switch this back to rAF — see CLAUDE.md
   * "Animation engine".
   */
  function schedule(){ timer = setTimeout(() => step(performance.now()), FRAME_MS); }

  // Speed -> rate. Default speed is 6 when unset; speed 0 (or negative) means
  // "instant", encoded as Infinity so durationOf() below divides down to 0.
  // 150 px/s and 180 deg/s per speed unit — keep in sync with the identical
  // formula in Turtle.duration() (widget.py) if these ever change.
  function pxPerSec(s){ return (s == null ? 6 : s) <= 0 ? Infinity : s * 150; }
  function degPerSec(s){ return (s == null ? 6 : s) <= 0 ? Infinity : s * 180; }

  /**
   * Milliseconds an event should take to animate: distance/angle over the
   * speed-derived rate, or 0 for any op not explicitly handled here — i.e.
   * all the "instant" ops in the event-protocol table (dot/stamp/write/
   * teleport/color/…).
   */
  function durationOf(ev){
    if (ev.op === "line" || ev.op === "move"){
      const d = Math.hypot(ev.x2 - ev.x1, ev.y2 - ev.y1);
      const v = pxPerSec(ev.speed);
      return v === Infinity ? 0 : (d / v) * 1000;
    }
    if (ev.op === "turn"){
      const d = Math.abs(ev.to - ev.from);
      const v = degPerSec(ev.speed);
      return v === Infinity ? 0 : (d / v) * 1000;
    }
    return 0;
  }

  /**
   * Update tracked `state` for cosmetic/config events that have no visual
   * commit step of their own (contrast with `commit()`, which bakes pixels).
   * `pen` is intentionally a no-op here: pen up/down is Python-side state
   * that only decides whether a move is emitted as `line` vs `move` — there
   * is nothing for the frontend to track.
   */
  function applyConfig(ev){
    const state = stateFor(ev.turtle);
    switch (ev.op){
      case "color": state.pencolor = ev.color; break;
      case "pen": break;
      case "show": state.visible = true; break;
      case "hide": state.visible = false; break;
      default: break;
    }
  }

  /**
   * Advance tracked `state` to an event's end point once it has fully played
   * (called after commit()). For `line`/`move`, heading is *derived* from the
   * travel direction (atan2) rather than trusted from the event, since those
   * ops carry no heading field of their own; a zero-length move leaves
   * heading unchanged. `turn`/`teleport` set heading directly from the event.
   */
  function endState(ev){
    const state = stateFor(ev.turtle);
    if (ev.op === "line" || ev.op === "move"){
      state.x = ev.x2; state.y = ev.y2;
      if (ev.x2 !== ev.x1 || ev.y2 !== ev.y1)
        state.heading = Math.atan2(ev.y2 - ev.y1, ev.x2 - ev.x1) * 180 / Math.PI;
      if (ev.op === "line" && ev.color) state.pencolor = ev.color;
    } else if (ev.op === "turn"){
      state.x = ev.x; state.y = ev.y; state.heading = ev.to;
    } else if (ev.op === "teleport"){
      state.x = ev.x; state.y = ev.y; state.heading = ev.heading;
    } else if (ev.op === "dot" || ev.op === "write" || ev.op === "stamp"){
      if (ev.x != null) state.x = ev.x;
      if (ev.y != null) state.y = ev.y;
    }
  }

  /**
   * Redraw the visible canvas from scratch: background, then the obstacle
   * layer, then committed art — every frame, before the in-progress segment
   * and turtle marker are drawn on top (by renderProgress()/drawFinal()).
   * Cheap because `obs`/`base` are pre-rendered bitmaps being blitted, not
   * replayed stroke-by-stroke.
   */
  function blitBase(){
    ctx.clearRect(0,0,width,height);
    paintBg(ctx);                                   // background
    ctx.drawImage(obs, 0,0, width, height);         // obstacles, under the art
    ctx.drawImage(base, 0,0, width, height);        // committed art
  }

  /**
   * Draw every *other* known turtle's last-settled marker (skipping `activeTid`,
   * which the caller draws itself, possibly mid-interpolation). Only one turtle's
   * event animates at a time (see CLAUDE.md "Animation engine" / "No simultaneous
   * animation"), so without this, every other turtle's marker would vanish for the
   * duration of the active one's move.
   */
  function drawIdleTurtles(activeTid){
    for (const [tid, s] of states){
      if (tid === activeTid) continue;
      if (s.visible) drawTurtle(ctx, s.x, s.y, s.heading, s.pencolor);
    }
  }

  /**
   * Draw one in-progress animation frame for event `ev` at progress `p`
   * (0..1, elapsed/duration). Blits the settled canvas, draws every idle
   * turtle's settled marker, interpolates the moving turtle's (`ev.turtle`'s)
   * position/heading (and, for `line`, strokes the partial segment directly
   * onto the *visible* `ctx` — not `base`, since it isn't committed yet), draws
   * its marker at the interpolated pose, and syncs the code highlight to this
   * event's source line.
   */
  function renderProgress(ev, p){
    blitBase();
    drawIdleTurtles(ev.turtle);
    const state = stateFor(ev.turtle);
    let mx = state.x, my = state.y, mh = state.heading;
    if (ev.op === "line" || ev.op === "move"){
      mx = ev.x1 + (ev.x2 - ev.x1) * p;
      my = ev.y1 + (ev.y2 - ev.y1) * p;
      mh = (ev.x2 !== ev.x1 || ev.y2 !== ev.y1)
        ? Math.atan2(ev.y2 - ev.y1, ev.x2 - ev.x1) * 180 / Math.PI : state.heading;
      if (ev.op === "line"){
        ctx.save();
        ctx.strokeStyle = ev.color || "black";
        ctx.lineWidth = ev.width || 1; ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(TX(ev.x1), TY(ev.y1)); ctx.lineTo(TX(mx), TY(my));
        ctx.stroke(); ctx.restore();
      }
    } else if (ev.op === "turn"){
      mx = ev.x; my = ev.y; mh = ev.from + (ev.to - ev.from) * p;
    }
    if (state.visible) drawTurtle(ctx, mx, my, mh, state.pencolor);
    setActiveLine(ev.line);
  }

  /**
   * Draw the fully-settled frame (no in-progress segment): every known
   * turtle's last-settled marker. Used once the event queue is drained, and
   * by start() before the loop begins (when `states` is still empty, so this
   * just blits the background/obstacles/art with no markers on top).
   */
  function drawFinal(){
    blitBase();
    for (const [, s] of states) if (s.visible) drawTurtle(ctx, s.x, s.y, s.heading, s.pencolor);
  }

  /**
   * The frame loop, invoked via schedule()'s setTimeout callback with
   * `ts = performance.now()`. Drains all zero-duration ("instant") events
   * synchronously in the `while` loop below so a burst of e.g. `dot()`/
   * `color()` calls doesn't each wait a frame; the first event with real
   * duration is rendered at its current progress and, if unfinished, yields
   * by scheduling another frame and returning. Once the queue is empty, draws
   * the final frame and stops (`timer = null`) instead of scheduling forever.
   */
  function step(ts){
    if (paused){ schedule(); return; }
    if (evStart === null) evStart = ts;
    frameCount++;
    while (idx < events.length){
      const ev = events[idx];
      const dur = durationOf(ev);
      const elapsed = ts - evStart;
      if (dur <= 0){
        applyConfig(ev); commit(ev); endState(ev);
        setActiveLine(ev.line);
        idx++; evStart = ts; continue;
      }
      const p = Math.min(1, elapsed / dur);
      renderProgress(ev, p);
      if (p >= 1){
        commit(ev); endState(ev);
        // Carry the timing remainder into the next event's clock instead of
        // resetting to `ts`, so a slightly-late frame doesn't compound drift
        // across a chain of animated events.
        idx++; evStart = ts - (elapsed - dur); continue;
      }
      schedule();
      return;
    }
    drawFinal();
    timer = null;
    twlog("build #" + bid + " animation DONE:", "frames=" + frameCount, "events=" + events.length);
    setDiag("render #" + (rid || "?") + " · build #" + bid + " · events " + events.length +
            " · DONE in " + frameCount + " frames");
  }

  /**
   * (Re)initialize all animation/committed state from scratch and begin
   * playback from event 0. Used both for the initial mount and by the Replay
   * button — replaying re-derives everything from `events` rather than
   * snapshotting/restoring, since the events are the only source of truth
   * (the frontend never computes geometry — see CLAUDE.md architecture).
   */
  function start(){
    if (timer !== null){ clearTimeout(timer); timer = null; }
    idx = 0; evStart = null; paused = false;
    pauseBtn.textContent = "\u275a\u275a Pause";
    states.clear();
    obstacles = []; bgColor = model.get("bg") || "white";
    bctx.clearRect(0,0,width,height); octx.clearRect(0,0,width,height);
    drawFinal();   // nothing known yet (states is empty); step() below fills in reality
    twlog("build #" + bid + " animation START:", "events=" + events.length);
    setDiag("render #" + (rid || "?") + " · build #" + bid + " · events " + events.length + " · animating…");
    step(performance.now());
  }

  replayBtn.addEventListener("click", start);
  pauseBtn.addEventListener("click", () => {
    // Pausing only stops step() from advancing idx/committing (its `if
    // (paused)` branch below); schedule() keeps firing every frame, so the
    // setTimeout chain — and thus future re-runs, per schedule()'s comment
    // above — stays alive.
    paused = !paused;
    pauseBtn.textContent = paused ? "\u25b6 Resume" : "\u275a\u275a Pause";
  });

  start();

  // anywidget cleanup hook: called when this build is torn down (by
  // render()'s remount or on view destroy) so an old build's frame loop
  // doesn't keep running after a new one has taken over the canvas.
  return () => { if (timer !== null) clearTimeout(timer); };
}

/**
 * anywidget's entry point: called once per view. Re-mounts on any relevant
 * model change, not just once at view creation — notebook frontends (esp. VS
 * Code) may create a view before the model state has synced, or reuse a view
 * across cell re-executions; rebuilding on change makes the canvas
 * render/animate reliably every time. Do not move the animation back into a
 * one-shot render (see CLAUDE.md "Animation engine").
 */
function render({ model, el }){
  _twDiag.renders++;
  const rid = _twDiag.renders;
  twlog("render() call #" + rid + ":",
        "events=" + (model.get("events") || []).length,
        "source_lines=" + (model.get("source_lines") || []).length,
        "show_code=" + model.get("show_code"),
        "size=" + model.get("width") + "x" + model.get("height"));
  let inner = null, pending = false, destroyed = false;
  // Tear down the previous build (if any) and start a fresh one. `inner`
  // holds the last build()'s returned cleanup closure.
  const remount = () => { if (destroyed) return; if (inner) inner(); inner = build({ model, el, rid }); };
  // Debounce remounts to one per microtask: traitlets are flushed together
  // (see CLAUDE.md "flushed once, not streamed per-command"), so several
  // `change:*` events can fire synchronously for one Python-side update —
  // without this, that would tear down/rebuild the canvas multiple times.
  // (This is a distinct closure from the frame-loop `schedule()` inside
  // build() above — same name, unrelated purpose.)
  const schedule = () => {
    if (pending || destroyed) return;
    pending = true;
    Promise.resolve().then(() => { pending = false; remount(); });   // coalesce burst of changes
  };
  // The synced traitlets that should trigger a remount — see CLAUDE.md
  // "State crosses the boundary via synced traitlets".
  const evNames = ["change:events", "change:source_lines", "change:width",
                   "change:height", "change:show_code", "change:bg"];
  const handlers = {};
  remount();                                       // initial, synchronous
  evNames.forEach(e => {
    handlers[e] = () => { twlog("render #" + rid + ": " + e + " changed -> remount"); schedule(); };
    model.on(e, handlers[e]);
  });
  return () => {
    twlog("render #" + rid + ": cleanup (view torn down)");
    destroyed = true;
    evNames.forEach(e => model.off(e, handlers[e]));
    if (inner) inner();
  };
}

// anywidget module contract: default export with a `render` function.
export default { render };
"""

_CSS = r"""
/* Styles for the DOM built in _ESM's build(): .tw-root > .tw-left (canvas +
   controls, plus the TW_DEBUG panel when enabled) and, when show_code is on,
   .tw-right (the code column). Kept inline here rather than an external
   stylesheet, per the "no external/CDN dependencies" constraint (CLAUDE.md). */
.tw-root { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-start;
  margin-top: 8px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.tw-left { display: inline-block; }
.tw-canvas { background: #fff; border: 1px solid #d0d0d8; border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.08); display: block; }
.tw-bar { margin-top: 8px; display: flex; gap: 8px; }
.tw-btn { font: 500 12px/1 system-ui, sans-serif; padding: 6px 12px; cursor: pointer;
  border: 1px solid #c7c7d1; border-radius: 6px; background: #f6f6fa; color: #222; }
.tw-btn:hover { background: #ececf4; }
.tw-right { overflow: auto; border: 1px solid #d0d0d8; border-radius: 8px;
  background: #fbfbfd; min-width: 240px; }
.tw-code { font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, monospace;
  font-size: 12.5px; line-height: 1.55; padding: 8px 0; white-space: pre; }
.tw-codeline { display: flex; padding: 0 12px; }
/* Toggled by setActiveLine() in _ESM to track the animation's current source line. */
.tw-codeline.tw-active { background: #fff3b0; }
.tw-ln { display: inline-block; width: 2.2em; text-align: right; margin-right: 12px;
  color: #9a9aa6; user-select: none; }
.tw-src { color: #24292f; }
/* tw-kw/tw-bi/tw-str/tw-com/tw-num: token classes emitted by highlight() in _ESM. */
.tw-kw  { color: #cf222e; }
.tw-bi  { color: #8250df; }
.tw-str { color: #0a7d2c; }
.tw-com { color: #6e7781; font-style: italic; }
.tw-num { color: #0550ae; }
"""


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _as_color(c):
    """Accept a CSS color name/hex string or an (r,g,b) tuple (0-1 or 0-255)."""
    if isinstance(c, str):
        return c
    if isinstance(c, (tuple, list)) and len(c) == 3:
        r, g, b = c
        if all(isinstance(v, float) and 0 <= v <= 1 for v in (r, g, b)):
            r, g, b = (round(v * 255) for v in (r, g, b))
        return f"rgb({int(r)},{int(g)},{int(b)})"
    return str(c)


def _records(method):
    """Capture the caller's source line so animation can highlight it."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            frame = sys._getframe(1)
            self._cur_line = frame.f_lineno
            if self._cur_file is None:
                self._cur_file = frame.f_code.co_filename
        except Exception:
            pass
        return method(self, *args, **kwargs)
    return wrapper


_SPEEDS = {"fastest": 0, "fast": 10, "normal": 6, "slow": 3, "slowest": 1}

# --- obstacles & sensing --------------------------------------------------- #

_OBSTACLE_COLOR = "#6c6c84"
_SENSE_EPS = 1e-6

#: One detected obstacle: ``distance`` to its closest ``point`` (x, y); ``obstacle`` the shape
#: hit (an ``Obstacle`` — ``.kind``/``.label``/geometry), with ``kind``/``index`` mirroring it.
#: ``angle`` is the signed bearing (degrees, + = left) to that point for ``sense``/``nearest``,
#: or the signed angle of incidence (0 = head-on, ±90 = grazing) for ``distance_ahead``.
Detection = collections.namedtuple(
    "Detection", ["distance", "angle", "point", "kind", "index", "obstacle"])

#: One collision reported to an ``on_collision`` handler. ``obstacle`` is the shape hit (an
#: ``Obstacle`` — ``.kind``/``.label``/geometry); ``point`` the contact location; ``normal``
#: the unit surface normal there (facing the turtle); ``distance`` how far into the move it
#: occurred; ``angle`` the signed angle of incidence (degrees, 0 = head-on, ±90 = grazing);
#: ``speed`` the effective move speed at impact; the rest is the turtle's state.
Collision = collections.namedtuple(
    "Collision",
    ["point", "obstacle", "index", "normal", "distance", "pos",
     "heading", "angle", "speed", "isdown", "isvisible", "pencolor", "pensize"])


class Obstacle(dict):
    """A registered obstacle (or a synthetic ``wall``/``trail``/``turtle`` surface) as carried
    by ``Detection.obstacle`` / ``Collision.obstacle``. Behaves as a dict of its fields and also
    exposes them as attributes: ``kind`` (``"circle"``/``"segment"``/``"polygon"``/``"wall"``/
    ``"trail"``/``"turtle"``), ``label`` (or ``None``), ``color``, ``visible`` (drawn?), ``sense``
    (detectable by ``distance_ahead``/``sense``/``nearest``?), ``index`` (position among
    registered obstacles, else ``None``), ``turtle`` (the other ``Turtle`` this obstacle
    represents, for ``kind == "turtle"``; ``None`` otherwise), and geometry (``x``/``y``/``r``,
    ``x1``/``y1``/``x2``/``y2``, or ``points``)."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            if name in ("label", "index", "color", "visible", "turtle"):
                return None
            raise AttributeError(name)

    def __repr__(self):
        label = " " + repr(self["label"]) if self.get("label") else ""
        return "<Obstacle " + str(self.get("kind", "?")) + label + ">"


def _seg_closest_point(px, py, x1, y1, x2, y2):
    """Closest point on segment ``(x1,y1)-(x2,y2)`` to ``(px,py)``."""
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return x1, y1
    t = ((px - x1) * dx + (py - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    return x1 + t * dx, y1 + t * dy


def _circle_closest_point(px, py, cx, cy, r):
    """Closest point on the circle centred ``(cx,cy)`` radius ``r`` to ``(px,py)``."""
    dx, dy = px - cx, py - cy
    d = math.hypot(dx, dy)
    if d == 0.0:
        return cx + r, cy
    return cx + r * dx / d, cy + r * dy / d


def _ray_segment_t(ox, oy, dx, dy, x1, y1, x2, y2):
    """Distance ``t >= 0`` along unit ray ``(ox,oy)+t(dx,dy)`` to the segment, or None."""
    ex, ey = x2 - x1, y2 - y1
    det = ex * dy - dx * ey
    if abs(det) < 1e-12:
        return None
    cx, cy = x1 - ox, y1 - oy
    t = (ex * cy - cx * ey) / det
    u = (dx * cy - dy * cx) / det
    if t >= 0.0 and 0.0 <= u <= 1.0:
        return t
    return None


def _ray_circle_t(ox, oy, dx, dy, cx, cy, r, eps=_SENSE_EPS):
    """Smallest distance ``t > eps`` along the unit ray to the circle, or None."""
    fx, fy = ox - cx, oy - cy
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r
    disc = b * b - 4.0 * c
    if disc < 0.0:
        return None
    s = math.sqrt(disc)
    for t in ((-b - s) / 2.0, (-b + s) / 2.0):
        if t > eps:
            return t
    return None


def _polygon_edges(points):
    """Return the ``(x1,y1,x2,y2)`` edges of a closed polygon."""
    n = len(points)
    return [(points[i][0], points[i][1], points[(i + 1) % n][0], points[(i + 1) % n][1])
            for i in range(n)]


def _obstacle_closest_point(ob, px, py):
    """Closest point on an obstacle dict to ``(px,py)``."""
    kind = ob["kind"]
    if kind in ("circle", "turtle"):
        return _circle_closest_point(px, py, ob["x"], ob["y"], ob["r"])
    if kind in ("segment", "trail"):
        return _seg_closest_point(px, py, ob["x1"], ob["y1"], ob["x2"], ob["y2"])
    best, best_d = None, float("inf")
    for x1, y1, x2, y2 in _polygon_edges(ob["points"]):
        qx, qy = _seg_closest_point(px, py, x1, y1, x2, y2)
        d = math.hypot(qx - px, qy - py)
        if d < best_d:
            best_d, best = d, (qx, qy)
    return best


def _obstacle_ray_t(ob, ox, oy, dx, dy, eps):
    """Nearest forward intersection distance of a unit ray with an obstacle, or None."""
    kind = ob["kind"]
    if kind in ("circle", "turtle"):
        return _ray_circle_t(ox, oy, dx, dy, ob["x"], ob["y"], ob["r"], eps)
    if kind in ("segment", "trail"):
        t = _ray_segment_t(ox, oy, dx, dy, ob["x1"], ob["y1"], ob["x2"], ob["y2"])
        return t if (t is not None and t > eps) else None
    best = None
    for x1, y1, x2, y2 in _polygon_edges(ob["points"]):
        t = _ray_segment_t(ox, oy, dx, dy, x1, y1, x2, y2)
        if t is not None and t > eps and (best is None or t < best):
            best = t
    return best


def _rel_angle(qx, qy, ox, oy, heading):
    """Signed angle (degrees, + = counter-clockwise) of ``(qx,qy)`` relative to ``heading``."""
    a = math.degrees(math.atan2(qy - oy, qx - ox))
    return (a - heading + 180.0) % 360.0 - 180.0


def _incidence_angle(dx, dy, nx, ny):
    """Signed angle of incidence (degrees, -90..90) of unit travel ``(dx,dy)`` hitting a
    surface with unit normal ``(nx,ny)`` that faces the mover. 0 = head-on (perpendicular),
    ±90 = grazing; the sign distinguishes the two glancing sides."""
    return math.degrees(math.atan2(ny * dx - nx * dy, -(dx * nx + dy * ny)))


def _seg_hit(ax, ay, ux, uy, x1, y1, x2, y2, max_t):
    """``(t, normal)`` for the nearest crossing of unit ray ``(ax,ay)+t(ux,uy)`` with the
    segment within ``max_t`` (normal faces back toward the mover), else ``None``."""
    t = _ray_segment_t(ax, ay, ux, uy, x1, y1, x2, y2)
    if t is None or t <= _SENSE_EPS or t > max_t:
        return None
    ex, ey = x2 - x1, y2 - y1
    nx, ny = -ey, ex
    nlen = math.hypot(nx, ny) or 1.0
    nx, ny = nx / nlen, ny / nlen
    if ux * nx + uy * ny > 0:
        nx, ny = -nx, -ny
    return t, (nx, ny)


def _circle_hit(ax, ay, ux, uy, cx, cy, r, max_t):
    """``(t, normal)`` for the nearest contact of the ray with the circle within ``max_t``."""
    t = _ray_circle_t(ax, ay, ux, uy, cx, cy, r, _SENSE_EPS)
    if t is None or t > max_t:
        return None
    nx, ny = (ax + t * ux) - cx, (ay + t * uy) - cy
    nlen = math.hypot(nx, ny) or 1.0
    nx, ny = nx / nlen, ny / nlen
    if ux * nx + uy * ny > 0:
        nx, ny = -nx, -ny
    return t, (nx, ny)


def _obstacle_hit(ob, ax, ay, ux, uy, max_t):
    """``(t, normal)`` for the nearest contact of the ray with an obstacle dict, else ``None``."""
    kind = ob["kind"]
    if kind in ("circle", "turtle"):
        return _circle_hit(ax, ay, ux, uy, ob["x"], ob["y"], ob["r"], max_t)
    if kind in ("segment", "trail"):
        return _seg_hit(ax, ay, ux, uy, ob["x1"], ob["y1"], ob["x2"], ob["y2"], max_t)
    best = None
    for x1, y1, x2, y2 in _polygon_edges(ob["points"]):
        h = _seg_hit(ax, ay, ux, uy, x1, y1, x2, y2, max_t)
        if h is not None and (best is None or h[0] < best[0]):
            best = h
    return best


# --------------------------------------------------------------------------- #
# The canvas + the widget                                                     #
# --------------------------------------------------------------------------- #

class Canvas(anywidget.AnyWidget):
    """The widget half of a turtle: the canvas, its synced traits, and the display
    lifecycle (source capture, autoshow, ``show()``/``_repr_mimebundle_``).

    Not meant to be instantiated on its own. ``Turtle(Canvas)`` so a bare ``Turtle()``
    is simultaneously a turtle and the canvas it draws on (``self._canvas = self``);
    other turtles join that same canvas via :func:`Turtle.new_turtle` /
    :func:`Turtle.other_turtle`, which point their own ``_canvas`` at this one instead
    of constructing a canvas of their own.

    Parameters
    ----------
    width, height : int, optional
        Canvas size in pixels. Default 500.
    show_code : bool, optional
        If True, show the cell source beside the canvas and highlight the active line
        in sync with the animation. Default False.
    code : str, optional
        Source to display instead of the captured cell source.
    autoshow : bool, optional
        If True (default), display the widget automatically at the end of the cell.
    bg : str, optional
        Canvas background colour. Default ``"white"``.
    """

    _esm = _ESM
    _css = _CSS

    events = traitlets.List().tag(sync=True)
    source_lines = traitlets.List().tag(sync=True)
    show_code = traitlets.Bool(False).tag(sync=True)
    width = traitlets.Int(500).tag(sync=True)
    height = traitlets.Int(500).tag(sync=True)
    bg = traitlets.Unicode("white").tag(sync=True)

    def __init__(self, width=500, height=500, show_code=False,
                 code=None, autoshow=True, bg="white"):
        super().__init__()
        self.width = int(width)
        self.height = int(height)
        self.show_code = bool(show_code)
        self.bg = bg

        self._events = []
        self._obstacles = []       # shared "world" state: every turtle on this
        self._trail_segments = []  # canvas registers/draws into the same lists
        self._turtles = []         # every turtle sharing this canvas (see _join_turtle)
        self._cur_file = None
        self._explicit_code = code
        self._next_turtle_id = 0

        self._rendered = False
        self._hook = None
        if autoshow:
            self._register_autoshow()

    def _new_turtle_id(self):
        """Return a fresh id (0, 1, 2, ...) for a turtle joining this canvas.

        Stamped onto every event by ``Turtle._emit`` so the frontend can track each
        turtle's marker independently (see CLAUDE.md "Event protocol").
        """
        tid = self._next_turtle_id
        self._next_turtle_id += 1
        return tid

    def _join_turtle(self, turtle):
        """Assign ``turtle`` a fresh id and register it on this canvas.

        The registry (``self._turtles``) is what lets a turtle find every *other*
        turtle sharing its canvas -- see ``Turtle._other_turtle_obstacles``.
        """
        turtle._turtle_id = self._new_turtle_id()
        self._turtles.append(turtle)

    # -- display / source --------------------------------------------------- #
    def _get_source(self):
        if self._explicit_code is not None:
            return self._explicit_code.splitlines()
        if self._cur_file:
            lines = linecache.getlines(self._cur_file)
            if lines:
                return [ln.rstrip("\n") for ln in lines]
        return []

    def _flush(self):
        # set source_lines first so the frontend has it before events trigger render.
        # Only transmit the cell source when it is actually shown: the captured source
        # is the *only* content that varies with the cell's text, and sending it over
        # the comm on every (re-)display when show_code is off is both wasteful and a
        # source of frontend re-render flakiness on cell re-execution.
        self.source_lines = self._get_source() if self.show_code else []
        self.events = list(self._events)

    def show(self):
        """Flush the recorded commands and display the widget now.

        Returns
        -------
        Canvas
            This canvas (a plain ``Turtle()`` returns itself, to allow chaining).
        """
        self._unhook()
        self._flush()
        self._rendered = True
        _ipy_display(self)
        return self

    def _repr_mimebundle_(self, **kwargs):
        # called when the turtle is the last expression / passed to display()
        self._unhook()
        self._flush()
        self._rendered = True
        return super()._repr_mimebundle_(**kwargs)

    # automatic display at end-of-cell so the bare example "just works"
    def _register_autoshow(self):
        ip = get_ipython()
        if ip is None:
            return

        def _cb(*a, **k):
            self._unhook()
            if not self._rendered:
                self._flush()
                self._rendered = True
                _ipy_display(self)

        self._hook = _cb
        try:
            ip.events.register("post_run_cell", _cb)
        except Exception:
            self._hook = None

    def _unhook(self):
        if self._hook is not None:
            ip = get_ipython()
            try:
                ip.events.unregister("post_run_cell", self._hook)
            except Exception:
                pass
            self._hook = None


# --------------------------------------------------------------------------- #
# The turtle                                                                  #
# --------------------------------------------------------------------------- #

class Turtle(Canvas):
    """An animated, turtle-compatible drawing widget for Jupyter notebooks.

    Turtle commands are recorded in Python as a stream of events and replayed as a
    canvas animation below the cell. The widget displays itself automatically at the
    end of the cell; you can also call ``show()`` or evaluate the turtle to display it.

    A plain ``Turtle()`` is both a turtle and the canvas it draws on. Other turtles can
    join that same canvas with :func:`new_turtle` (preferred) or :func:`other_turtle`,
    so two or more turtles can draw on, and sense each other's trail on, one shared
    canvas — see those methods' docstrings.

    Parameters
    ----------
    width : int, optional
        Canvas width in pixels. Default 500.
    height : int, optional
        Canvas height in pixels. Default 500.
    show_code : bool, optional
        If True, show the cell source beside the canvas and highlight the active line
        in sync with the animation. Default False.
    code : str, optional
        Source to display instead of the captured cell source.
    autoshow : bool, optional
        If True (default), display the widget automatically at the end of the cell.
        Set False for headless use such as tests.
    bg : str, optional
        Canvas background colour. Default ``"white"``.
    home : tuple of float, optional
        The turtle's home, given as ``(x, y)`` or ``(x, y, heading)`` (heading defaults to
        0; 0 = east). Defaults to the origin ``(0, 0)`` facing east. The turtle both
        **starts** here and returns here on :func:`home`. (To re-target the home of an
        existing turtle *without* moving it, call :func:`sethome`.)

    Attributes
    ----------
    nr_collisions : int
        Running count of collisions detected since the turtle was created (reset to 0 by
        :func:`reset`). Incremented once per collision reported while an :func:`on_collision`
        handler is active — so with the default ``stop=True`` it counts the obstacles the
        turtle has run into, and with ``stop=False`` every crossing reported. Starts at 0.
    nr_left, nr_right : int
        Number of times :func:`left` / :func:`right` has been called. Starts at 0, reset by
        :func:`reset`. (The aliases ``lt``/``rt`` count too, as they are the same methods.)
    nr_sense, nr_distance_ahead : int
        Number of times :func:`sense` / :func:`distance_ahead` has been called. Starts at 0,
        reset by :func:`reset`. ``nearest`` is not counted (it does not call ``sense``).
    total_movement : float
        Total distance the turtle has travelled, in turtle units, summed over every move
        (``forward``/``backward``/``goto``/``circle``/``home``/…), whether the pen is up or
        down. A move stopped short by a collision contributes only the distance actually
        travelled; turns contribute nothing. Starts at 0.0, reset by :func:`reset`.
    """

    def __init__(self, width=500, height=500, show_code=False,
                 code=None, autoshow=True, bg="white", home=None):
        super().__init__(width=width, height=height, show_code=show_code,
                          code=code, autoshow=autoshow, bg=bg)
        self._canvas = self
        self._canvas._join_turtle(self)
        self._init_turtle_state(home=home)

    def _init_turtle_state(self, home=None):
        """Set up this turtle's own state (position/heading/pen/.../counters) --
        everything ``Canvas.__init__`` doesn't already cover. Split out from
        ``__init__`` so :func:`new_turtle` can build a turtle that joins another
        canvas without constructing (and immediately discarding) a ``Canvas`` of
        its own."""
        # turtle state (in turtle coordinates: origin at centre, +y is up)
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0
        self._home = (0.0, 0.0)   # target for home(); change with sethome() or home=
        self._home_heading = 0.0
        self._pendown = True
        self._pencolor = "black"
        self._fillcolor = "black"
        self._pensize = 1
        self._speed = 6
        self._speed_scale = 1.0   # transient per-move speed multiplier
        self._visible = True
        self._filling = False
        self._fillpath = []
        self._collision_cb = None
        self._collision_stop = True
        self._collision_walls = True
        self._collision_trail = False
        self._collision_turtles = False
        self._colliding = False
        self._hitbox = None       # circle radius other turtles can sense/collide with; None = off
        # public counters, all zeroed by reset()
        self.nr_collisions = 0       # collisions reported while on_collision is active
        self.nr_left = 0             # calls to left()
        self.nr_right = 0            # calls to right()
        self.nr_sense = 0            # calls to sense()
        self.nr_distance_ahead = 0   # calls to distance_ahead()
        self.total_movement = 0.0    # total distance travelled (pen up or down)
        self._cur_line = None
        if home is not None:
            self.sethome(*home)
            self._spawn_at_home()

    # -- canvas sharing ------------------------------------------------------ #
    def new_turtle(self, color=None, home=None):
        """Create a new turtle that shares this turtle's canvas.

        The new turtle draws on the same canvas as this one — the same committed
        art, obstacles, and (once drawn) sensed trail are shared — and animates by
        taking its turn in the shared event stream, in program order (there is no
        simultaneous animation of multiple turtles; see CLAUDE.md "Animation engine" /
        "No simultaneous animation"). Unlike plain ``Turtle()``, this never constructs
        a canvas of its own — no extra widget/comm is created and immediately discarded.

        Parameters
        ----------
        color : str or tuple, optional
            If given, sets the new turtle's pen colour (see :func:`pencolor`) — a
            convenient way to tell turtles on a shared canvas apart at a glance.
        home : tuple of float, optional
            The new turtle's home — see the ``home`` parameter of :class:`Turtle`.
            Defaults to the origin, independent of where this turtle currently is.

        Returns
        -------
        Turtle
            The new turtle, already sharing this canvas.

        See Also
        --------
        other_turtle : join an *already-constructed* Turtle to this canvas instead.
        """
        # cls.__new__(cls) (not cls(...)) deliberately skips Canvas/AnyWidget/Widget
        # __init__ entirely -- HasTraits.__new__ still runs (so e.g. t.width reads a
        # harmless class-default 500, per the dead-trait-value caveat on other_turtle
        # below), but Widget.__init__'s self.open() -- which is what actually creates
        # a comm and messages the frontend -- never runs, so no comm is ever opened.
        # Uses type(self), not the literal Turtle, so a subclass's new_turtle() still
        # returns an instance of that subclass rather than a plain Turtle.
        cls = type(self)
        t = cls.__new__(cls)
        t._canvas = self._canvas
        t._canvas._join_turtle(t)
        t._init_turtle_state(home=home)
        if color is not None:
            t.pencolor(color)
        return t

    def other_turtle(self, turtle):
        """Join an already-constructed turtle to this turtle's canvas.

        Use this when you already have a standalone ``Turtle()`` (e.g. one built by
        someone else's code) that should draw on this canvas instead of its own.
        Prefer :func:`new_turtle` when you are creating the turtle yourself — it
        never builds (and discards) a canvas of its own the way joining an
        already-constructed ``Turtle()`` does here.

        Parameters
        ----------
        turtle : Turtle
            The turtle to join to this canvas. Its autoshow hook is unregistered so
            it no longer tries to display its own (now unused) canvas. Its position,
            heading, and other turtle-level state are unchanged.

        Returns
        -------
        Turtle
            The joined turtle (the same object passed in), for convenience.

        Notes
        -----
        After joining, canvas-level trait access on ``turtle`` (``.width``,
        ``.height``, ``.show_code``, ``.bg``, ``.events``, ``.source_lines``)
        reflects its own now-unused canvas, not the one it actually draws on — use
        ``turtle._canvas.width`` etc. for the real values. This is the tradeoff of
        reusing an already-built turtle; :func:`new_turtle` avoids it by never
        building a canvas in the first place.
        """
        turtle._canvas = self._canvas
        turtle._canvas._join_turtle(turtle)
        turtle._unhook()
        return turtle

    # -- emission ----------------------------------------------------------- #
    def _emit(self, **ev):
        ev.setdefault("line", self._cur_line)
        ev.setdefault("speed", self._speed * self._speed_scale)
        ev.setdefault("turtle", self._turtle_id)
        self._canvas._events.append(ev)

    @contextlib.contextmanager
    def _scaled(self, mult):
        """Temporarily scale emitted-event speed by ``mult`` (relative to the global speed)."""
        prev = self._speed_scale
        self._speed_scale = float(mult)
        try:
            yield
        finally:
            self._speed_scale = prev

    def _goto(self, x, y):
        x1, y1 = self._x, self._y
        x, y = float(x), float(y)
        hits = []
        stopped = False
        ux = uy = 0.0
        if self._collision_cb is not None and not self._colliding:
            hits = self._collisions_along(x1, y1, x, y)
            if hits:
                dx, dy = x - x1, y - y1
                dist = math.hypot(dx, dy) or 1.0
                ux, uy = dx / dist, dy / dist          # travel direction (pre-truncation)
                if self._collision_stop:
                    x, y = hits[0][1]      # shorten the move to the first contact point
                    hits = hits[:1]
                    stopped = True
        if self._pendown:
            self._emit(op="line", x1=x1, y1=y1, x2=x, y2=y,
                       color=self._pencolor, width=self._pensize)
            self._canvas._trail_segments.append((x1, y1, x, y))
        else:
            self._emit(op="move", x1=x1, y1=y1, x2=x, y2=y)
        self._x, self._y = x, y
        self.total_movement += math.hypot(x - x1, y - y1)   # actual (truncated) distance moved
        if self._filling:
            self._fillpath.append((self._x, self._y))
        if hits:
            self._dispatch_collisions(hits, ux, uy)
        return not stopped                 # False if the turtle was stopped short

    # -- movement ----------------------------------------------------------- #
    @_records
    def forward(self, distance, speed=1.0):
        """Move the turtle forward by ``distance``, drawing if the pen is down.

        Parameters
        ----------
        distance : float
            Distance to move along the current heading.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            rad = math.radians(self._heading)
            self._goto(self._x + distance * math.cos(rad),
                       self._y + distance * math.sin(rad))
        return self
    fd = forward

    @_records
    def backward(self, distance, speed=1.0):
        """Move the turtle backward by ``distance`` (opposite its heading).

        Parameters
        ----------
        distance : float
            Distance to move opposite the current heading.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            rad = math.radians(self._heading)
            self._goto(self._x - distance * math.cos(rad),
                       self._y - distance * math.sin(rad))
        return self
    back = bk = backward

    @_records
    def right(self, angle, speed=1.0):
        """Turn the turtle clockwise by ``angle`` degrees.

        The animation spins exactly ``angle`` degrees, so multi-turn values such as
        ``right(720)`` are preserved as two full rotations. Increments ``nr_right``.

        Parameters
        ----------
        angle : float
            Degrees to turn clockwise.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self.nr_right += 1
        with self._scaled(speed):
            frm = self._heading
            to = frm - angle                  # spin exactly `angle` (sign matters)
            self._emit(op="turn", x=self._x, y=self._y, **{"from": frm, "to": to})
            self._heading = to % 360.0
        return self
    rt = right

    @_records
    def left(self, angle, speed=1.0):
        """Turn the turtle counter-clockwise by ``angle`` degrees.

        The animation spins exactly ``angle`` degrees, so multi-turn values such as
        ``left(720)`` are preserved as two full rotations. Increments ``nr_left``.

        Parameters
        ----------
        angle : float
            Degrees to turn counter-clockwise.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self.nr_left += 1
        with self._scaled(speed):
            frm = self._heading
            to = frm + angle
            self._emit(op="turn", x=self._x, y=self._y, **{"from": frm, "to": to})
            self._heading = to % 360.0
        return self
    lt = left

    @_records
    def setheading(self, to_angle, speed=1.0):
        """Turn the turtle to face an absolute heading.

        Rotates along the shortest path, so turning from 0 to 270 spins -90 degrees
        rather than +270. Headings are in degrees with 0 = east, counter-clockwise positive.

        Parameters
        ----------
        to_angle : float
            Absolute heading in degrees.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            self._turn_to(float(to_angle))
        return self
    seth = setheading

    def _turn_to(self, to_angle):
        """Rotate to an absolute heading via the shortest signed path (turtle semantics)."""
        frm = self._heading
        delta = ((to_angle - frm + 180.0) % 360.0) - 180.0
        self._emit(op="turn", x=self._x, y=self._y, **{"from": frm, "to": frm + delta})
        self._heading = to_angle % 360.0

    @_records
    def goto(self, x, y=None, speed=1.0):
        """Move the turtle to an absolute position, drawing if the pen is down.

        Parameters
        ----------
        x : float or tuple of float
            Target x-coordinate, or an ``(x, y)`` pair (then ``y`` must be omitted).
        y : float, optional
            Target y-coordinate.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            if y is None:
                x, y = x
            self._goto(float(x), float(y))
        return self
    setpos = setposition = goto

    @_records
    def setx(self, x, speed=1.0):
        """Set the x-coordinate, keeping y unchanged.

        Parameters
        ----------
        x : float
            New x-coordinate.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            self._goto(float(x), self._y)
        return self

    @_records
    def sety(self, y, speed=1.0):
        """Set the y-coordinate, keeping x unchanged.

        Parameters
        ----------
        y : float
            New y-coordinate.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            self._goto(self._x, float(y))
        return self

    def _spawn_at_home(self):
        """Place the turtle at its home position with no animation. Emits a leading
        ``teleport`` event if this actually changes the turtle's position/heading --
        at construction time that means "home isn't the origin" (the frontend marker
        otherwise initialises to (0,0,0)); at reset() time it means "home differs from
        wherever this turtle was a moment ago" -- so the frontend marker snaps there
        too either way."""
        old = (self._x, self._y, self._heading)
        self._x, self._y = self._home
        self._heading = self._home_heading
        if (self._x, self._y, self._heading) != old:
            self._emit(op="teleport", x=self._x, y=self._y, heading=self._heading)

    def sethome(self, x, y=None, heading=0):
        """Set the home position that :func:`home` returns to.

        This does **not** move the turtle — it only redefines where ``home()`` goes;
        the turtle stays where it is. (To set home when creating the turtle, pass
        ``Turtle(home=(x, y))``.)

        Parameters
        ----------
        x : float or tuple of float
            Home x-coordinate, or an ``(x, y)`` / ``(x, y, heading)`` tuple (then ``y``
            and ``heading`` must be omitted).
        y : float, optional
            Home y-coordinate.
        heading : float, optional
            The heading (degrees, 0 = east) the turtle faces after ``home()``. Default 0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        if y is None:
            seq = tuple(x)
            x, y = seq[0], seq[1]
            if len(seq) > 2:
                heading = seq[2]
        self._home = (float(x), float(y))
        self._home_heading = float(heading) % 360.0
        return self

    @_records
    def home(self, speed=1.0):
        """Move the turtle to its home position and turn to the home heading.

        Home is the origin ``(0, 0)`` facing east unless it was changed with
        :func:`sethome` or the ``home=`` constructor argument.

        Parameters
        ----------
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            self._goto(*self._home)
            self._turn_to(self._home_heading)
        return self

    @_records
    def circle(self, radius, extent=None, steps=None, speed=1.0):
        """Draw a circle or arc, approximated by a series of straight chords.

        A positive ``radius`` curves to the left of the turtle, a negative ``radius``
        to the right.

        Parameters
        ----------
        radius : float
            Radius of the circle. Positive curves left, negative curves right.
        extent : float, optional
            Angle of the arc to draw, in degrees. Defaults to 360 (a full circle).
        steps : int, optional
            Number of chords approximating the arc. Defaults to a value scaled to the
            radius and extent.
        speed : float, optional
            Per-move multiplier on the global ``speed()`` (2.0 = twice as fast,
            0.5 = half). Default 1.0.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        with self._scaled(speed):
            if extent is None:
                extent = 360.0
            if steps is None:
                frac = abs(extent) / 360.0
                steps = 1 + int(min(11.0 + abs(radius) / 6.0, 59.0) * frac)
            w = extent / steps
            w2 = 0.5 * w
            length = 2.0 * radius * math.sin(math.radians(w2))
            if radius < 0:
                length, w, w2 = -length, -w, -w2
            # turn into the arc, walk it, then straighten up
            self._heading += w2
            for _ in range(steps):
                rad = math.radians(self._heading)
                if not self._goto(self._x + length * math.cos(rad),
                                  self._y + length * math.sin(rad)):
                    break                  # stopped at an obstacle mid-arc
                self._heading += w
            self._heading -= w2
            self._heading %= 360.0
        return self

    # -- pen ---------------------------------------------------------------- #
    @_records
    def penup(self):
        """Lift the pen so subsequent moves do not draw.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._pendown = False
        self._emit(op="pen", down=False)
        return self
    pu = up = penup

    @_records
    def pendown(self):
        """Lower the pen so subsequent moves draw.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._pendown = True
        self._emit(op="pen", down=True)
        return self
    pd = down = pendown

    @_records
    def pensize(self, width=None):
        """Get or set the pen width.

        Parameters
        ----------
        width : float, optional
            New pen width in pixels. If omitted, the current width is returned.

        Returns
        -------
        Turtle or float
            This turtle when setting (to allow chaining), or the current pen width when
            called with no argument.

        Notes
        -----
        Named ``pensize`` (alias ``width_``) because ``width`` is the canvas-size trait.
        """
        if width is None:
            return self._pensize
        self._pensize = width
        self._emit(op="width", width=width)
        return self
    width_ = pensize  # 'width' name is taken by the trait; use .pensize()

    @_records
    def pencolor(self, *args):
        """Get or set the pen (outline) colour.

        Parameters
        ----------
        *args
            A CSS colour name or hex string (``"red"``, ``"#ff8800"``), or an ``(r, g, b)``
            triple given as three arguments or one tuple. Float components in ``[0, 1]``
            use the 0-1 scale, otherwise 0-255.

        Returns
        -------
        Turtle or str
            This turtle when setting (to allow chaining), or the current pen colour when
            called with no arguments.
        """
        if not args:
            return self._pencolor
        col = _as_color(args[0] if len(args) == 1 else tuple(args))
        self._pencolor = col
        self._emit(op="color", color=col)
        return self

    @_records
    def fillcolor(self, *args):
        """Get or set the fill colour used by ``begin_fill`` / ``end_fill``.

        Parameters
        ----------
        *args
            A CSS colour name or hex string, or an ``(r, g, b)`` triple; see ``pencolor``
            for the accepted forms.

        Returns
        -------
        Turtle or str
            This turtle when setting (to allow chaining), or the current fill colour when
            called with no arguments.
        """
        if not args:
            return self._fillcolor
        self._fillcolor = _as_color(args[0] if len(args) == 1 else tuple(args))
        return self

    @_records
    def color(self, *args):
        """Get or set the pen colour, the fill colour, or both at once.

        Parameters
        ----------
        *args
            With no arguments, returns ``(pencolor, fillcolor)``. With one argument, sets
            both pen and fill to that colour. With two, sets the pen and fill colours
            respectively. Each colour may be a CSS name/hex string or an ``(r, g, b)`` triple.

        Returns
        -------
        Turtle or tuple of str
            This turtle when setting (to allow chaining), or the ``(pencolor, fillcolor)``
            pair when called with no arguments.
        """
        if not args:
            return (self._pencolor, self._fillcolor)
        if len(args) == 1:
            c = _as_color(args[0])
            self._pencolor = self._fillcolor = c
            self._emit(op="color", color=c)
        else:
            self._pencolor = _as_color(args[0])
            self._fillcolor = _as_color(args[1])
            self._emit(op="color", color=self._pencolor)
        return self

    @_records
    def begin_fill(self):
        """Start recording a filled shape.

        Call before the commands that trace the outline, then call ``end_fill`` to fill it.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._filling = True
        self._fillpath = [(self._x, self._y)]
        return self

    @_records
    def end_fill(self):
        """Fill the shape traced since the last ``begin_fill``.

        The fill is drawn behind the outline in the current fill colour. Does nothing if
        fewer than two points were recorded.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        if self._filling and len(self._fillpath) > 1:
            self._emit(op="fill", points=[list(p) for p in self._fillpath],
                       color=self._fillcolor)
        self._filling = False
        self._fillpath = []
        return self

    # -- markers / text ----------------------------------------------------- #
    @_records
    def dot(self, size=None, *color):
        """Draw a filled dot at the current position.

        Parameters
        ----------
        size : float, optional
            Diameter of the dot in pixels. Defaults to a small multiple of the pen size.
        *color
            Optional dot colour as a CSS name/hex string or an ``(r, g, b)`` triple.
            Defaults to the current pen colour.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        if size is None:
            size = max(self._pensize + 4, 2 * self._pensize)
        col = _as_color(color[0] if len(color) == 1 else (tuple(color) if color else self._pencolor))
        self._emit(op="dot", x=self._x, y=self._y, size=size, color=col)
        return self

    @_records
    def stamp(self):
        """Stamp a copy of the turtle shape at the current position and heading.

        Returns
        -------
        int
            The number of recorded events after stamping (a stamp id).
        """
        self._emit(op="stamp", x=self._x, y=self._y,
                   heading=self._heading, color=self._pencolor)
        return len(self._canvas._events)

    @_records
    def write(self, text, align="left", font=("sans-serif", 14, "normal")):
        """Write text at the current position.

        Parameters
        ----------
        text : str
            The text to write.
        align : {"left", "center", "right"}, optional
            Horizontal alignment relative to the turtle. Default ``"left"``.
        font : tuple, optional
            A ``(family, size, style)`` triple; only family and size are used. Default
            ``("sans-serif", 14, "normal")``.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        family, size = font[0], font[1]
        self._emit(op="write", x=self._x, y=self._y, text=str(text),
                   color=self._pencolor, align=align, font=f"{size}px {family}")
        return self

    # -- visibility / screen ------------------------------------------------ #
    @_records
    def showturtle(self):
        """Make the turtle marker visible.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._visible = True
        self._emit(op="show")
        return self
    st = showturtle

    @_records
    def hideturtle(self):
        """Hide the turtle marker.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._visible = False
        self._emit(op="hide")
        return self
    ht = hideturtle

    @_records
    def bgcolor(self, *args):
        """Get or set the canvas background colour.

        Parameters
        ----------
        *args
            A CSS colour name/hex string or an ``(r, g, b)`` triple. With no arguments,
            the current background colour is returned.

        Returns
        -------
        Turtle or str
            This turtle when setting (to allow chaining), or the current background colour
            when called with no arguments.
        """
        if not args:
            return self._canvas.bg
        self._canvas.bg = _as_color(args[0] if len(args) == 1 else tuple(args))
        self._emit(op="bgcolor", color=self._canvas.bg)
        return self

    @_records
    def clear(self):
        """Clear the drawing and the sensed trail from the canvas.

        On a canvas shared with other turtles (see :func:`new_turtle`), this clears
        the *whole* canvas — every turtle's drawn art and sensed trail, not just this
        turtle's — since once art is drawn it is baked into one shared bitmap, with
        no way to erase only one turtle's pixels from it. Registered obstacles are
        kept (they are redrawn); every turtle's position, heading and pen are
        unchanged.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._canvas._trail_segments = []
        self._emit(op="clear")
        return self

    @_records
    def reset(self):
        """Reset this turtle to its initial state and return it to (its) home.

        Resets *this* turtle's position, heading, pen, colours, fill state,
        visibility and counters. On a canvas shared with other turtles (see
        :func:`new_turtle`), the shared drawing/obstacles/trail are left alone —
        unlike :func:`clear`, which wipes them for everyone — so resetting your
        turtle can't erase someone else's maze or trail. A solo turtle (the common
        case, and every turtle before this widget supported sharing a canvas) also
        gets a clean slate: its event history, registered obstacles and sensed
        trail are cleared too, exactly as before.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        if self._canvas is self:
            self._canvas._events = []
            self._canvas._obstacles = []
            self._canvas._trail_segments = []
        self._pendown = True
        self._pencolor = self._fillcolor = "black"
        self._pensize = 1
        self._visible = True
        self._filling = False
        self._fillpath = []
        self.nr_collisions = 0
        self.nr_left = self.nr_right = self.nr_sense = self.nr_distance_ahead = 0
        self.total_movement = 0.0
        self._spawn_at_home()    # returns to home, emitting a teleport if that moves it
        return self

    @_records
    def speed(self, s=None):
        """Get or set the drawing speed.

        Parameters
        ----------
        s : int or str, optional
            Speed from 0 to 10, or a name: ``"fastest"`` (0), ``"fast"`` (10),
            ``"normal"`` (6), ``"slow"`` (3), ``"slowest"`` (1). ``0`` disables animation
            (the drawing appears at once). If omitted, the current speed is returned.

        Returns
        -------
        Turtle or int
            This turtle when setting (to allow chaining), or the current speed when called
            with no argument.
        """
        if s is None:
            return self._speed
        if isinstance(s, str):
            s = _SPEEDS.get(s, 6)
        self._speed = max(0, min(10, int(s)))
        return self

    def duration(self):
        """Total time the animation will take to play, in seconds.

        Computed from the recorded events using the exact timing the browser uses: a
        ``line``/``move`` of length *d* at effective speed *s* takes ``d / (s * 150)``
        seconds, a ``turn`` of *a* degrees takes ``a / (s * 180)`` seconds, and every
        other event (dots, stamps, writes, fills, pen/colour changes, sensing, …) is
        instant. A move whose effective speed is ``0`` is instant too — it snaps into
        place. The effective speed already includes any per-move ``speed=`` multiplier.

        Returns
        -------
        float
            Seconds the full animation will take at the current events' speeds. ``0.0``
            if speed is ``0`` (everything is instant) or nothing has been drawn yet.

        Notes
        -----
        This is the *scheduled* play time derived from the event stream — it excludes the
        browser's frame-scheduling overhead and does not change as the animation plays.
        The *currently elapsed* time lives in the browser; the Python ``Turtle`` records
        events but does not receive playback progress back, so live elapsed/remaining time
        is not readable here. (It could be exposed by adding a synced trait the frontend
        updates each frame — ask if you want that.)
        """
        total = 0.0
        for ev in self._canvas._events:
            speed = ev.get("speed")
            v = 6 if speed is None else speed
            if v <= 0:
                continue                              # instant (speed 0 snaps into place)
            op = ev.get("op")
            if op in ("line", "move"):
                total += math.hypot(ev["x2"] - ev["x1"], ev["y2"] - ev["y1"]) / (v * 150.0)
            elif op == "turn":
                total += abs(ev["to"] - ev["from"]) / (v * 180.0)
        return total

    # -- getters (no events) ------------------------------------------------ #
    def position(self):
        """Return the current position.

        Returns
        -------
        tuple of float
            The ``(x, y)`` coordinates, with the origin at the canvas centre.
        """
        return (self._x, self._y)
    pos = position

    def xcor(self):
        """Return the current x-coordinate.

        Returns
        -------
        float
            The current x-coordinate.
        """
        return self._x

    def ycor(self):
        """Return the current y-coordinate.

        Returns
        -------
        float
            The current y-coordinate.
        """
        return self._y

    def heading(self):
        """Return the current heading in degrees.

        Returns
        -------
        float
            Heading in ``[0, 360)`` degrees, with 0 = east and counter-clockwise positive.
        """
        return self._heading

    def isdown(self):
        """Return whether the pen is down.

        Returns
        -------
        bool
            True if the pen is down (drawing), False otherwise.
        """
        return self._pendown

    def isvisible(self):
        """Return whether the turtle marker is visible.

        Returns
        -------
        bool
            True if the turtle marker is shown, False otherwise.
        """
        return self._visible

    # -- obstacles & sensing ------------------------------------------------ #
    def _add_obstacle(self, ob, color, visible=True, label=None, sense=True):
        ob["color"] = _as_color(color) if color is not None else _OBSTACLE_COLOR
        ob["visible"] = bool(visible)
        ob["sense"] = bool(sense)
        ob["label"] = label
        ob["index"] = len(self._canvas._obstacles)
        obstacle = Obstacle(ob)
        self._canvas._obstacles.append(obstacle)
        self._emit(op="obstacle", **obstacle)

    def _wall_obstacle(self):
        w, h = self._canvas.width / 2.0, self._canvas.height / 2.0
        return Obstacle(kind="wall", points=[[-w, -h], [w, -h], [w, h], [-w, h]],
                        color=None, visible=True, sense=True, label=None, index=None)

    @staticmethod
    def _trail_obstacle(x1, y1, x2, y2):
        return Obstacle(kind="trail", x1=x1, y1=y1, x2=x2, y2=y2,
                        color=None, visible=True, sense=True, label=None, index=None)

    @staticmethod
    def _turtle_obstacle(turtle):
        return Obstacle(kind="turtle", x=turtle._x, y=turtle._y, r=turtle._hitbox,
                        color=None, visible=False, sense=True, label=None, index=None,
                        turtle=turtle)

    def _other_turtle_obstacles(self):
        """Synthetic circle obstacles for every other turtle sharing this canvas that
        has a hitbox set (see :func:`hitbox`) -- computed fresh from each turtle's
        *current* position, unlike registered obstacles. Used by the ``turtles=``
        option of ``sense``/``distance_ahead``/``nearest``/``on_collision``."""
        return [self._turtle_obstacle(t) for t in self._canvas._turtles
                if t is not self and t._hitbox is not None]

    @_records
    def add_circle(self, x, y, radius, color=None, visible=True, label=None, sense=True):
        """Register a circular obstacle and draw it.

        Parameters
        ----------
        x, y : float
            Centre of the circle, in turtle coordinates.
        radius : float
            Radius of the circle.
        color : str or tuple, optional
            Outline/fill colour. Defaults to a neutral obstacle colour.
        visible : bool, optional
            Whether to draw it (default True). Invisible obstacles are still sensed
            and still collide.
        label : str, optional
            A name carried on the obstacle and surfaced as ``.label`` on the
            ``Detection``/``Collision`` objects that report it. Not drawn.
        sense : bool, optional
            Whether ``distance_ahead``/``sense``/``nearest`` can detect it (default True).
            A non-sensing obstacle is still drawn (unless ``visible=False``) and still
            collides — it is only hidden from the sensing queries.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._add_obstacle({"kind": "circle", "x": float(x), "y": float(y),
                            "r": float(radius)}, color, visible, label, sense)
        return self

    @_records
    def add_segment(self, x1, y1, x2, y2, color=None, visible=True, label=None, sense=True):
        """Register a line-segment (wall) obstacle and draw it.

        Parameters
        ----------
        x1, y1 : float
            One endpoint of the segment.
        x2, y2 : float
            The other endpoint.
        color : str or tuple, optional
            Segment colour. Defaults to a neutral obstacle colour.
        visible : bool, optional
            Whether to draw it (default True). Invisible obstacles are still sensed
            and still collide.
        label : str, optional
            A name carried on the obstacle and surfaced as ``.label`` on the
            ``Detection``/``Collision`` objects that report it. Not drawn.
        sense : bool, optional
            Whether ``distance_ahead``/``sense``/``nearest`` can detect it (default True).
            A non-sensing obstacle is still drawn (unless ``visible=False``) and still
            collides — it is only hidden from the sensing queries.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        self._add_obstacle({"kind": "segment", "x1": float(x1), "y1": float(y1),
                            "x2": float(x2), "y2": float(y2)}, color, visible, label, sense)
        return self

    @_records
    def add_polygon(self, points, color=None, visible=True, label=None, sense=True):
        """Register a filled polygon obstacle and draw it.

        Parameters
        ----------
        points : sequence of (float, float)
            Polygon vertices in turtle coordinates; the last vertex is joined
            back to the first.
        color : str or tuple, optional
            Outline/fill colour. Defaults to a neutral obstacle colour.
        visible : bool, optional
            Whether to draw it (default True). Invisible obstacles are still sensed
            and still collide.
        label : str, optional
            A name carried on the obstacle and surfaced as ``.label`` on the
            ``Detection``/``Collision`` objects that report it. Not drawn.
        sense : bool, optional
            Whether ``distance_ahead``/``sense``/``nearest`` can detect it (default True).
            A non-sensing obstacle is still drawn (unless ``visible=False``) and still
            collides — it is only hidden from the sensing queries.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        pts = [[float(px), float(py)] for px, py in points]
        self._add_obstacle({"kind": "polygon", "points": pts}, color, visible, label, sense)
        return self

    @_records
    def add_rectangle(self, x1, y1, x2, y2, color=None, visible=True, label=None, sense=True):
        """Register a rectangular obstacle (a 4-point polygon) and draw it.

        Parameters
        ----------
        x1, y1 : float
            One corner of the rectangle.
        x2, y2 : float
            The opposite corner.
        color : str or tuple, optional
            Outline/fill colour. Defaults to a neutral obstacle colour.
        visible : bool, optional
            Whether to draw it (default True). Invisible obstacles are still sensed
            and still collide.
        label : str, optional
            A name carried on the obstacle and surfaced as ``.label`` on the
            ``Detection``/``Collision`` objects that report it. Not drawn.
        sense : bool, optional
            Whether ``distance_ahead``/``sense``/``nearest`` can detect it (default True).
            A non-sensing obstacle is still drawn (unless ``visible=False``) and still
            collides — it is only hidden from the sensing queries.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        xa, xb = sorted((float(x1), float(x2)))
        ya, yb = sorted((float(y1), float(y2)))
        self._add_obstacle({"kind": "polygon",
                            "points": [[xa, ya], [xb, ya], [xb, yb], [xa, yb]]},
                           color, visible, label, sense)
        return self

    @_records
    def show_obstacles(self):
        """Make all registered obstacles visible.

        Visibility is purely cosmetic — hidden obstacles are still sensed and still
        collide. Affects every obstacle added so far (by any turtle on this canvas).

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        for ob in self._canvas._obstacles:
            ob["visible"] = True
        self._emit(op="obstacles_visible", visible=True)
        return self

    @_records
    def hide_obstacles(self):
        """Hide all registered obstacles from view.

        Visibility is purely cosmetic — hidden obstacles are still sensed and still
        collide (they become invisible walls). Affects every obstacle added so far
        (by any turtle on this canvas).

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.
        """
        for ob in self._canvas._obstacles:
            ob["visible"] = False
        self._emit(op="obstacles_visible", visible=False)
        return self

    def hitbox(self, *args):
        """Get or set this turtle's hitbox radius.

        A turtle with a hitbox behaves as an invisible circular obstacle *to other
        turtles* sharing its canvas: ``sense``/``distance_ahead``/``nearest``/
        ``on_collision``'s ``turtles=`` option detects/collides with it there. It has
        no effect on how *this* turtle senses or collides with ordinary obstacles,
        walls, or trails -- those are unaffected by a turtle's own hitbox.

        Parameters
        ----------
        *args
            A single radius (float or ``None``), centred on the turtle's current position
            (it moves with the turtle). ``None`` (the default, and the initial state)
            disables it -- this turtle is invisible to every other turtle's
            ``turtles=True`` queries. If omitted entirely, returns the current radius
            instead of setting it.

        Notes
        -----
        Like the collision configuration set by :func:`on_collision`, the hitbox is a
        standing policy rather than drawing state: :func:`reset` does not clear it.

        Returns
        -------
        Turtle or float or None
            This turtle when setting (to allow chaining), or the current hitbox radius
            when called with no arguments.
        """
        if not args:
            return self._hitbox
        radius = args[0]
        self._hitbox = None if radius is None else float(radius)
        return self

    def _scan(self, max_distance, walls, trail, turtles):
        px, py, hd = self._x, self._y, self._heading
        dets = []

        def consider(qx, qy, obstacle):
            d = math.hypot(qx - px, qy - py)
            if d > _SENSE_EPS and (max_distance is None or d <= max_distance):
                dets.append(Detection(d, _rel_angle(qx, qy, px, py, hd), (qx, qy),
                                       obstacle["kind"], obstacle["index"], obstacle))

        for ob in self._canvas._obstacles:
            if not ob.get("sense", True):
                continue
            qx, qy = _obstacle_closest_point(ob, px, py)
            consider(qx, qy, ob)

        if walls:
            wall = self._wall_obstacle()
            qx, qy = _obstacle_closest_point(wall, px, py)
            consider(qx, qy, wall)

        if trail and self._canvas._trail_segments:
            best = None
            for seg in self._canvas._trail_segments:
                qx, qy = _seg_closest_point(px, py, *seg)
                d = math.hypot(qx - px, qy - py)
                if d > _SENSE_EPS and (best is None or d < best[0]):
                    best = (d, qx, qy, seg)
            if best is not None:
                consider(best[1], best[2], self._trail_obstacle(*best[3]))

        if turtles:
            for tob in self._other_turtle_obstacles():
                qx, qy = _obstacle_closest_point(tob, px, py)
                consider(qx, qy, tob)

        dets.sort(key=lambda D: D.distance)
        return dets

    def _ray_ahead(self, max_distance, walls, trail, turtles):
        px, py = self._x, self._y
        rad = math.radians(self._heading)
        dx, dy = math.cos(rad), math.sin(rad)
        best = None                                    # (t, normal, obstacle)
        for ob in self._canvas._obstacles:
            if not ob.get("sense", True):
                continue
            h = _obstacle_hit(ob, px, py, dx, dy, math.inf)
            if h is not None and (best is None or h[0] < best[0]):
                best = (h[0], h[1], ob)
        if trail:
            for x1, y1, x2, y2 in self._canvas._trail_segments:
                h = _seg_hit(px, py, dx, dy, x1, y1, x2, y2, math.inf)
                if h is not None and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], self._trail_obstacle(x1, y1, x2, y2))
        if walls:
            wall = self._wall_obstacle()
            h = _obstacle_hit(wall, px, py, dx, dy, math.inf)
            if h is not None and (best is None or h[0] < best[0]):
                best = (h[0], h[1], wall)
        if turtles:
            for tob in self._other_turtle_obstacles():
                h = _obstacle_hit(tob, px, py, dx, dy, math.inf)
                if h is not None and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], tob)
        if best is None or (max_distance is not None and best[0] > max_distance):
            return None
        t, normal, obstacle = best
        return Detection(distance=t, point=(px + t * dx, py + t * dy),
                         kind=obstacle["kind"], index=obstacle["index"], obstacle=obstacle,
                         angle=_incidence_angle(dx, dy, normal[0], normal[1]))

    @_records
    def distance_ahead(self, max_distance=None, walls=True, trail=True, draw=False, color="red",
                       turtles=True):
        """Sense the nearest obstacle straight ahead.

        Casts a ray from the turtle along its current heading and returns the first
        obstacle it hits as a ``Detection`` — the same object :func:`sense` yields.
        Increments ``nr_distance_ahead``.

        Parameters
        ----------
        max_distance : float, optional
            Ignore hits farther than this. ``None`` (default) means unlimited.
        walls : bool, optional
            Include the canvas border as an obstacle. Default True.
        trail : bool, optional
            Include the turtle's own drawn path as obstacles. Default True.
        draw : bool, optional
            If True, also draw the sensor ray on the canvas. Default False.
        color : str or tuple, optional
            Colour of the drawn ray, used only when ``draw=True``. Default ``"red"``;
            accepts a CSS colour string or an ``(r, g, b)`` tuple.
        turtles : bool, optional
            Include other turtles on this canvas that have a :func:`hitbox` set.
            Default True (harmless if no other turtle has one).

        Returns
        -------
        Detection or None
            The nearest obstacle ahead, with the same fields as a ``sense`` result —
            ``distance``, ``angle`` (the signed angle of incidence — 0 = head-on,
            ±90 = grazing), ``point``, ``obstacle`` (the shape hit), ``kind`` and ``index`` —
            or ``None`` if nothing is within range.
        """
        self.nr_distance_ahead += 1
        det = self._ray_ahead(max_distance, walls, trail, turtles)
        if draw:
            length = det.distance if det is not None else (
                max_distance if max_distance is not None else max(self._canvas.width, self._canvas.height))
            self._emit(op="sense", x=self._x, y=self._y, color=_as_color(color),
                       rays=[{"a": self._heading, "d": length, "hit": det is not None}])
        return det

    @_records
    def sense(self, max_distance=None, walls=True, trail=True, draw=False, color="red",
             turtles=True):
        """Detect all obstacles within range, with the bearing to each.

        For every obstacle whose closest point lies within ``max_distance`` of
        the turtle, returns a ``Detection`` giving the distance and the signed
        angle (relative to the turtle's heading, positive = left / counter-
        clockwise) to that closest point. A point the turtle is touching is not
        reported. Increments ``nr_sense`` (``nearest`` does not, since it does not
        call this method).

        Parameters
        ----------
        max_distance : float, optional
            Only report obstacles whose closest point is within this distance.
            ``None`` (default) reports every obstacle.
        walls : bool, optional
            Include the canvas border. Default True.
        trail : bool, optional
            Include the turtle's own drawn path (its nearest point). Default True.
        draw : bool, optional
            If True, also draw a ray to each detected point. Default False.
        color : str or tuple, optional
            Colour of the drawn rays, used only when ``draw=True``. Default ``"red"``;
            accepts a CSS colour string or an ``(r, g, b)`` tuple.
        turtles : bool, optional
            Include other turtles on this canvas that have a :func:`hitbox` set.
            Default True (harmless if no other turtle has one).

        Returns
        -------
        list of Detection
            Detections sorted nearest-first. Each ``Detection`` has fields
            ``distance``, ``angle`` (degrees), ``point`` (x, y), ``obstacle`` (the shape hit —
            an ``Obstacle`` exposing ``.kind``/``.label``/geometry), ``kind`` (one of
            ``"circle"``, ``"segment"``, ``"polygon"``, ``"wall"``, ``"trail"``, ``"turtle"``)
            and ``index`` (position among registered obstacles, else ``None``).
        """
        self.nr_sense += 1
        dets = self._scan(max_distance, walls, trail, turtles)
        if draw:
            self._emit(op="sense", x=self._x, y=self._y, color=_as_color(color),
                       rays=[{"a": self._heading + D.angle, "d": D.distance, "hit": True}
                             for D in dets])
        return dets

    @_records
    def nearest(self, max_distance=None, walls=True, trail=True, draw=False, color="red",
               turtles=True):
        """The single nearest obstacle within range, or ``None``.

        A convenience wrapper around :func:`sense` returning only the closest
        detection.

        Parameters
        ----------
        max_distance : float, optional
            Only consider obstacles within this distance. ``None`` (default)
            considers every obstacle.
        walls : bool, optional
            Include the canvas border. Default True.
        trail : bool, optional
            Include the turtle's own drawn path. Default True.
        draw : bool, optional
            If True, draw a ray to the nearest detected point. Default False.
        color : str or tuple, optional
            Colour of the drawn ray, used only when ``draw=True``. Default ``"red"``;
            accepts a CSS colour string or an ``(r, g, b)`` tuple.
        turtles : bool, optional
            Include other turtles on this canvas that have a :func:`hitbox` set.
            Default True (harmless if no other turtle has one).

        Returns
        -------
        Detection or None
            The nearest detection (see ``sense``), or ``None`` if nothing is
            within range.
        """
        dets = self._scan(max_distance, walls, trail, turtles)
        result = dets[0] if dets else None
        if draw and result is not None:
            self._emit(op="sense", x=self._x, y=self._y, color=_as_color(color),
                       rays=[{"a": self._heading + result.angle,
                              "d": result.distance, "hit": True}])
        return result

    def on_collision(self, handler, stop=True, walls=True, trail=False, turtles=False):
        """Register a hook fired when a move runs into an obstacle.

        Collision detection is **opt-in** — calling this enables it; until then moves are
        unaffected. By default the turtle **stops at the first obstacle (or canvas edge) it
        hits**: the move is shortened to the contact point and the turtle ends there. After a
        move that hit something, ``handler(collision)`` is called with the contact details;
        the handler may then issue ordinary turtle commands (which animate next) to react.
        Each reported collision also increments the turtle's ``nr_collisions`` counter.

        Parameters
        ----------
        handler : callable or None
            Called as ``handler(collision)`` with a ``Collision`` (see Notes). Pass ``None``
            to turn collision detection off again.
        stop : bool, optional
            If True (default), the turtle stops at the first contact. If False, motion is
            unchanged (the move completes) and the handler is notified for *every* obstacle
            the path crossed, nearest first.
        walls : bool, optional
            Treat the canvas border as an obstacle. Default True.
        trail : bool, optional
            Also collide with the turtle's own drawn path (self-collision). Default False.
        turtles : bool, optional
            Also collide with other turtles on this canvas that have a :func:`hitbox` set
            (a turtle with no hitbox is never collided with). Default False — like ``trail``,
            this is a separate opt-in from ``walls``/regular obstacles, since reacting to
            another turtle is a different kind of behaviour than reacting to the scenery.

        Returns
        -------
        Turtle
            This turtle, to allow method chaining.

        Notes
        -----
        Each ``Collision`` carries the contact ``point`` (x, y); the ``obstacle`` that was hit
        (an ``Obstacle`` exposing ``.kind`` —
        ``"circle"``/``"segment"``/``"polygon"``/``"wall"``/``"trail"``/``"turtle"`` — plus
        ``.label`` and its geometry; for ``"turtle"``, ``.turtle`` is the actual other
        ``Turtle`` instance that was hit); its ``index`` among registered obstacles (else
        ``None``); the unit surface ``normal`` at the contact,
        facing back toward the turtle (reflect a direction ``d`` with ``d - 2*(d·n)*n`` to
        bounce); the ``distance`` travelled into the move; ``angle``, the signed angle of
        incidence in degrees (0 = a head-on, perpendicular hit; ±90 = a grazing hit parallel to
        the surface; the sign distinguishes the two glancing sides); ``speed``, the effective
        animation speed of the move (global ``speed()`` × the move's ``speed=`` multiplier);
        and the turtle's state — ``pos``, ``heading``, ``isdown``, ``isvisible``, ``pencolor``,
        ``pensize``. The handler's own moves do not re-trigger collisions, so it can move the
        turtle freely (e.g. to bounce or back away).
        """
        self._collision_cb = handler
        self._collision_stop = bool(stop)
        self._collision_walls = bool(walls)
        self._collision_trail = bool(trail)
        self._collision_turtles = bool(turtles)
        return self

    def _collisions_along(self, ax, ay, bx, by):
        """Sorted ``(t, point, normal, obstacle)`` for obstacles/edges the segment
        ``(ax,ay)->(bx,by)`` crosses, nearest first."""
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= _SENSE_EPS:
            return []
        ux, uy = dx / length, dy / length
        hits = []
        for ob in self._canvas._obstacles:
            h = _obstacle_hit(ob, ax, ay, ux, uy, length)
            if h is not None:
                hits.append((h[0], (ax + h[0] * ux, ay + h[0] * uy), h[1], ob))
        if self._collision_walls:
            wall = self._wall_obstacle()
            h = _obstacle_hit(wall, ax, ay, ux, uy, length)
            if h is not None:
                hits.append((h[0], (ax + h[0] * ux, ay + h[0] * uy), h[1], wall))
        if self._collision_trail:
            for x1, y1, x2, y2 in self._canvas._trail_segments:
                h = _seg_hit(ax, ay, ux, uy, x1, y1, x2, y2, length)
                if h is not None:
                    hits.append((h[0], (ax + h[0] * ux, ay + h[0] * uy), h[1],
                                 self._trail_obstacle(x1, y1, x2, y2)))
        if self._collision_turtles:
            for tob in self._other_turtle_obstacles():
                h = _obstacle_hit(tob, ax, ay, ux, uy, length)
                if h is not None:
                    hits.append((h[0], (ax + h[0] * ux, ay + h[0] * uy), h[1], tob))
        hits.sort(key=lambda r: r[0])
        return hits

    def _dispatch_collisions(self, hits, ux, uy):
        end = (self._x, self._y)
        snap = dict(heading=self._heading, speed=self._speed * self._speed_scale,
                    isdown=self._pendown, isvisible=self._visible,
                    pencolor=self._pencolor, pensize=self._pensize)
        self._colliding = True
        try:
            for t, point, normal, obstacle in hits:
                if self._collision_cb is not None:
                    self.nr_collisions += 1
                    # signed angle of incidence: 0 = head-on, ±90 = grazing
                    angle = _incidence_angle(ux, uy, normal[0], normal[1])
                    self._collision_cb(Collision(point=point, obstacle=obstacle,
                                                 index=obstacle["index"], normal=normal,
                                                 distance=t, pos=end, angle=angle, **snap))
        finally:
            self._colliding = False
