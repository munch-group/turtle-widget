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
const KEYWORDS = new Set(["False","None","True","and","as","assert","async","await",
  "break","class","continue","def","del","elif","else","except","finally","for","from",
  "global","if","import","in","is","lambda","nonlocal","not","or","pass","raise","return",
  "try","while","with","yield"]);
const BUILTINS = new Set(["print","range","len","int","float","str","list","dict","set",
  "tuple","abs","min","max","sum","enumerate","zip","map","filter","sorted","round",
  "Turtle","reversed","bool"]);

function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

function highlight(line){
  let out = "", i = 0; const n = line.length;
  while (i < n){
    const c = line[i];
    if (c === "#"){ out += '<span class="tw-com">' + esc(line.slice(i)) + "</span>"; break; }
    if (c === '"' || c === "'"){
      const q = c; let j = i + 1, buf = c;
      while (j < n){ buf += line[j]; if (line[j] === q && line[j-1] !== "\\"){ j++; break; } j++; }
      out += '<span class="tw-str">' + esc(buf) + "</span>"; i = j; continue;
    }
    if (c >= "0" && c <= "9"){
      let j = i, buf = "";
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

function render({ model, el }){
  el.innerHTML = "";

  const width   = model.get("width");
  const height  = model.get("height");
  const showCode= model.get("show_code");
  const bgColor = model.get("bg") || "white";
  const events  = model.get("events") || [];
  const source  = model.get("source_lines") || [];

  const dpr = window.devicePixelRatio || 1;
  const cx = width / 2, cy = height / 2;
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
  canvas.width = width * dpr; canvas.height = height * dpr;
  canvas.style.width = width + "px"; canvas.style.height = height + "px";
  left.appendChild(canvas);

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  // committed (already-drawn) art lives on an offscreen buffer
  const base = document.createElement("canvas");
  base.width = width * dpr; base.height = height * dpr;
  const bctx = base.getContext("2d");
  bctx.scale(dpr, dpr);

  // controls
  const bar = document.createElement("div");
  bar.className = "tw-bar";
  const replayBtn = document.createElement("button");
  replayBtn.className = "tw-btn"; replayBtn.textContent = "\u21bb Replay";
  const pauseBtn = document.createElement("button");
  pauseBtn.className = "tw-btn"; pauseBtn.textContent = "\u275a\u275a Pause";
  bar.appendChild(replayBtn); bar.appendChild(pauseBtn);
  left.appendChild(bar);

  // code column
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
  function paintBg(g){ g.save(); g.fillStyle = bgColor; g.fillRect(0,0,width,height); g.restore(); }

  function drawTurtle(g, x, y, headingDeg, color){
    g.save();
    g.translate(TX(x), TY(y));
    g.rotate(-headingDeg * Math.PI / 180);   // canvas y is flipped
    g.beginPath();
    g.moveTo(11, 0); g.lineTo(-8, -7); g.lineTo(-4, 0); g.lineTo(-8, 7);
    g.closePath();
    g.fillStyle = color || "black";
    g.fill();
    g.lineWidth = 1; g.strokeStyle = "rgba(0,0,0,0.35)"; g.stroke();
    g.restore();
  }

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
      case "bgcolor":
        bctx.save(); bctx.globalCompositeOperation = "destination-over";
        bctx.fillStyle = ev.color || "white"; bctx.fillRect(0,0,width,height);
        bctx.restore(); break;
      case "clear":
        bctx.clearRect(0,0,width,height); paintBg(bctx); break;
      default: break; // pure config events (color/width/pen/visibility) handled in state
    }
  }

  // ---- animation engine -------------------------------------------------- //
  const state = { x: 0, y: 0, heading: 0, visible: true, pencolor: "black" };
  let idx = 0, evStart = null, raf = null, paused = false;

  function pxPerSec(s){ return (s == null ? 6 : s) <= 0 ? Infinity : s * 150; }
  function degPerSec(s){ return (s == null ? 6 : s) <= 0 ? Infinity : s * 180; }

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

  function applyConfig(ev){
    switch (ev.op){
      case "color": state.pencolor = ev.color; break;
      case "pen": break;
      case "show": state.visible = true; break;
      case "hide": state.visible = false; break;
      default: break;
    }
  }

  function endState(ev){
    if (ev.op === "line" || ev.op === "move"){
      state.x = ev.x2; state.y = ev.y2;
      if (ev.x2 !== ev.x1 || ev.y2 !== ev.y1)
        state.heading = Math.atan2(ev.y2 - ev.y1, ev.x2 - ev.x1) * 180 / Math.PI;
      if (ev.op === "line" && ev.color) state.pencolor = ev.color;
    } else if (ev.op === "turn"){
      state.x = ev.x; state.y = ev.y; state.heading = ev.to;
    } else if (ev.op === "dot" || ev.op === "write" || ev.op === "stamp"){
      if (ev.x != null) state.x = ev.x;
      if (ev.y != null) state.y = ev.y;
    }
  }

  function blitBase(){ ctx.clearRect(0,0,width,height); ctx.drawImage(base, 0,0, width, height); }

  function renderProgress(ev, p){
    blitBase();
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

  function drawFinal(){
    blitBase();
    if (state.visible) drawTurtle(ctx, state.x, state.y, state.heading, state.pencolor);
  }

  function step(ts){
    if (paused){ raf = requestAnimationFrame(step); return; }
    if (evStart === null) evStart = ts;
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
        idx++; evStart = ts - (elapsed - dur); continue;
      }
      raf = requestAnimationFrame(step);
      return;
    }
    drawFinal();
    raf = null;
  }

  function start(){
    if (raf !== null){ cancelAnimationFrame(raf); raf = null; }
    idx = 0; evStart = null; paused = false;
    pauseBtn.textContent = "\u275a\u275a Pause";
    state.x = 0; state.y = 0; state.heading = 0; state.visible = true; state.pencolor = "black";
    paintBg(bctx); blitBase();
    if (state.visible) drawTurtle(ctx, 0, 0, 0, "black");
    raf = requestAnimationFrame(step);
  }

  replayBtn.addEventListener("click", start);
  pauseBtn.addEventListener("click", () => {
    paused = !paused;
    pauseBtn.textContent = paused ? "\u25b6 Resume" : "\u275a\u275a Pause";
  });

  start();

  // anywidget cleanup hook
  return () => { if (raf !== null) cancelAnimationFrame(raf); };
}

export default { render };
"""

_CSS = r"""
.tw-root { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-start;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
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
.tw-codeline.tw-active { background: #fff3b0; }
.tw-ln { display: inline-block; width: 2.2em; text-align: right; margin-right: 12px;
  color: #9a9aa6; user-select: none; }
.tw-src { color: #24292f; }
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


# --------------------------------------------------------------------------- #
# The widget                                                                  #
# --------------------------------------------------------------------------- #

class Turtle(anywidget.AnyWidget):
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

        # turtle state (in turtle coordinates: origin at centre, +y is up)
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0
        self._pendown = True
        self._pencolor = "black"
        self._fillcolor = "black"
        self._pensize = 1
        self._speed = 6
        self._visible = True
        self._filling = False
        self._fillpath = []

        self._events = []
        self._cur_line = None
        self._cur_file = None
        self._explicit_code = code

        self._rendered = False
        self._hook = None
        if autoshow:
            self._register_autoshow()

    # -- emission ----------------------------------------------------------- #
    def _emit(self, **ev):
        ev.setdefault("line", self._cur_line)
        ev.setdefault("speed", self._speed)
        self._events.append(ev)

    def _goto(self, x, y):
        x1, y1 = self._x, self._y
        if self._pendown:
            self._emit(op="line", x1=x1, y1=y1, x2=x, y2=y,
                       color=self._pencolor, width=self._pensize)
        else:
            self._emit(op="move", x1=x1, y1=y1, x2=x, y2=y)
        self._x, self._y = float(x), float(y)
        if self._filling:
            self._fillpath.append((self._x, self._y))

    # -- movement ----------------------------------------------------------- #
    @_records
    def forward(self, distance):
        rad = math.radians(self._heading)
        self._goto(self._x + distance * math.cos(rad),
                   self._y + distance * math.sin(rad))
        return self
    fd = forward

    @_records
    def backward(self, distance):
        rad = math.radians(self._heading)
        self._goto(self._x - distance * math.cos(rad),
                   self._y - distance * math.sin(rad))
        return self
    back = bk = backward

    @_records
    def right(self, angle):
        frm = self._heading
        to = frm - angle                      # spin exactly `angle` (sign matters)
        self._emit(op="turn", x=self._x, y=self._y, **{"from": frm, "to": to})
        self._heading = to % 360.0
        return self
    rt = right

    @_records
    def left(self, angle):
        frm = self._heading
        to = frm + angle
        self._emit(op="turn", x=self._x, y=self._y, **{"from": frm, "to": to})
        self._heading = to % 360.0
        return self
    lt = left

    @_records
    def setheading(self, to_angle):
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
    def goto(self, x, y=None):
        if y is None:
            x, y = x
        self._goto(float(x), float(y))
        return self
    setpos = setposition = goto

    @_records
    def setx(self, x):
        self._goto(float(x), self._y)
        return self

    @_records
    def sety(self, y):
        self._goto(self._x, float(y))
        return self

    @_records
    def home(self):
        self._goto(0.0, 0.0)
        self._turn_to(0.0)
        return self

    @_records
    def circle(self, radius, extent=None, steps=None):
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
            self._goto(self._x + length * math.cos(rad),
                       self._y + length * math.sin(rad))
            self._heading += w
        self._heading -= w2
        self._heading %= 360.0
        return self

    # -- pen ---------------------------------------------------------------- #
    @_records
    def penup(self):
        self._pendown = False
        self._emit(op="pen", down=False)
        return self
    pu = up = penup

    @_records
    def pendown(self):
        self._pendown = True
        self._emit(op="pen", down=True)
        return self
    pd = down = pendown

    @_records
    def pensize(self, width=None):
        if width is None:
            return self._pensize
        self._pensize = width
        self._emit(op="width", width=width)
        return self
    width_ = pensize  # 'width' name is taken by the trait; use .pensize()

    @_records
    def pencolor(self, *args):
        if not args:
            return self._pencolor
        col = _as_color(args[0] if len(args) == 1 else tuple(args))
        self._pencolor = col
        self._emit(op="color", color=col)
        return self

    @_records
    def fillcolor(self, *args):
        if not args:
            return self._fillcolor
        self._fillcolor = _as_color(args[0] if len(args) == 1 else tuple(args))
        return self

    @_records
    def color(self, *args):
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
        self._filling = True
        self._fillpath = [(self._x, self._y)]
        return self

    @_records
    def end_fill(self):
        if self._filling and len(self._fillpath) > 1:
            self._emit(op="fill", points=[list(p) for p in self._fillpath],
                       color=self._fillcolor)
        self._filling = False
        self._fillpath = []
        return self

    # -- markers / text ----------------------------------------------------- #
    @_records
    def dot(self, size=None, *color):
        if size is None:
            size = max(self._pensize + 4, 2 * self._pensize)
        col = _as_color(color[0] if len(color) == 1 else (tuple(color) if color else self._pencolor))
        self._emit(op="dot", x=self._x, y=self._y, size=size, color=col)
        return self

    @_records
    def stamp(self):
        self._emit(op="stamp", x=self._x, y=self._y,
                   heading=self._heading, color=self._pencolor)
        return len(self._events)

    @_records
    def write(self, text, align="left", font=("sans-serif", 14, "normal")):
        family, size = font[0], font[1]
        self._emit(op="write", x=self._x, y=self._y, text=str(text),
                   color=self._pencolor, align=align, font=f"{size}px {family}")
        return self

    # -- visibility / screen ------------------------------------------------ #
    @_records
    def showturtle(self):
        self._visible = True
        self._emit(op="show")
        return self
    st = showturtle

    @_records
    def hideturtle(self):
        self._visible = False
        self._emit(op="hide")
        return self
    ht = hideturtle

    @_records
    def bgcolor(self, *args):
        if not args:
            return self.bg
        self.bg = _as_color(args[0] if len(args) == 1 else tuple(args))
        self._emit(op="bgcolor", color=self.bg)
        return self

    @_records
    def clear(self):
        self._emit(op="clear")
        return self

    @_records
    def reset(self):
        self._events = []
        self._x = self._y = self._heading = 0.0
        self._pendown = True
        self._pencolor = self._fillcolor = "black"
        self._pensize = 1
        self._visible = True
        self._filling = False
        self._fillpath = []
        return self

    @_records
    def speed(self, s=None):
        if s is None:
            return self._speed
        if isinstance(s, str):
            s = _SPEEDS.get(s, 6)
        self._speed = max(0, min(10, int(s)))
        return self

    # -- getters (no events) ------------------------------------------------ #
    def position(self):
        return (self._x, self._y)
    pos = position

    def xcor(self):
        return self._x

    def ycor(self):
        return self._y

    def heading(self):
        return self._heading

    def isdown(self):
        return self._pendown

    def isvisible(self):
        return self._visible

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
        # set source_lines first so the frontend has it before events trigger render
        self.source_lines = self._get_source()
        self.events = list(self._events)

    def show(self):
        """Flush recorded commands and display the widget now."""
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
