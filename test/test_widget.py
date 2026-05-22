"""Headless geometry and event-stream tests for the turtle-widget ``Turtle``.

The Python side computes all geometry and records JSON-serializable *events*; the
browser only replays them. That makes the whole turtle testable without a browser:
build a ``Turtle(autoshow=False)``, run commands, and assert on ``pos()``/``heading()``
and on ``t._events``.
"""
import json
import math

import pytest

from turtle_widget import Turtle


def _t():
    return Turtle(autoshow=False)


def test_initial_state():
    t = _t()
    assert t.pos() == (0.0, 0.0)
    assert t.heading() == 0.0
    assert t.isdown() is True
    assert t.isvisible() is True


def test_forward_left_geometry():
    t = _t()
    t.forward(100); t.left(90); t.forward(100)
    assert t.xcor() == pytest.approx(100)
    assert t.ycor() == pytest.approx(100)
    assert t.heading() % 360 == pytest.approx(90)


def test_backward():
    t = _t()
    t.backward(50)
    assert t.xcor() == pytest.approx(-50)
    assert t.ycor() == pytest.approx(0)


def test_right_left_wrap_heading():
    assert _t().right(90).heading() == pytest.approx(270)   # 0 - 90 -> 270
    assert _t().left(90).heading() == pytest.approx(90)


def test_setheading_takes_shortest_path():
    # turning to 270 should rotate -90 (clockwise), not +270.
    t = _t()
    t.setheading(270)
    assert t.heading() == pytest.approx(270)
    turn = t._events[-1]
    assert turn["op"] == "turn"
    assert turn["to"] - turn["from"] == pytest.approx(-90)


def test_home_does_not_overspin():
    t = _t()
    t.forward(50); t.left(90)          # heading 90, at (50, 0)
    t.home()
    assert t.xcor() == pytest.approx(0)
    assert t.ycor() == pytest.approx(0)
    assert t.heading() == pytest.approx(0)
    turn = t._events[-1]
    assert turn["op"] == "turn"
    assert turn["to"] - turn["from"] == pytest.approx(-90)   # shortest path, not +270


def test_multi_turn_spin_preserved():
    # right(720) resolves to heading 0 but must record the full 720 spin.
    t = _t()
    t.right(720)
    assert t.heading() == pytest.approx(0)
    turn = t._events[-1]
    assert turn["to"] - turn["from"] == pytest.approx(-720)


def test_penup_emits_move_not_line():
    t = _t()
    t.penup()
    assert t.isdown() is False
    t.forward(10)
    ops = [e["op"] for e in t._events]
    assert "move" in ops and "line" not in ops
    t.pendown().forward(10)
    assert any(e["op"] == "line" for e in t._events)


def test_pensize_get_and_set():
    t = _t()
    assert t.pensize(3) is t
    assert t.pensize() == 3
    ev = t._events[-1]
    assert ev["op"] == "width" and ev["width"] == 3


@pytest.mark.parametrize("arg,expected", [
    ("red", "red"),
    ("#ff8800", "#ff8800"),
    ((1.0, 0.84, 0.0), "rgb(255,214,0)"),   # floats in [0,1] -> 0-255 scale
    ((255, 128, 0), "rgb(255,128,0)"),       # ints -> used as-is
])
def test_pencolor_conversion(arg, expected):
    t = _t()
    t.pencolor(arg)
    assert t.pencolor() == expected
    assert t._events[-1]["op"] == "color"
    assert t._events[-1]["color"] == expected


def test_circle_decomposes_into_chords_and_closes():
    t = _t()
    t.circle(60)
    lines = [e for e in t._events if e["op"] == "line"]
    assert len(lines) > 3                          # many small chords, no arc op
    assert math.hypot(t.xcor(), t.ycor()) < 1e-6   # returns to its start
    assert t.heading() % 360 == pytest.approx(0)


def test_fills_markers_and_screen_ops():
    t = _t()
    t.begin_fill()
    for _ in range(3):
        t.forward(40); t.left(120)
    t.end_fill()
    t.dot(10, "red")
    t.stamp()
    t.write("hi")
    t.bgcolor("lightblue")
    t.clear()
    ops = {e["op"] for e in t._events}
    assert {"fill", "dot", "stamp", "write", "bgcolor", "clear"} <= ops
    fill = next(e for e in t._events if e["op"] == "fill")
    assert len(fill["points"]) >= 3


def test_speed_words_and_clamping():
    assert _t().speed(0).speed() == 0
    assert _t().speed("slowest").speed() == 1
    assert _t().speed("fast").speed() == 10
    assert _t().speed(100).speed() == 10           # clamped to 10
    assert _t().speed(-5).speed() == 0             # clamped to 0


def test_events_json_serializable_with_line_and_speed():
    t = _t()
    t.forward(50); t.right(45); t.circle(20); t.dot(); t.write("x")
    payload = json.dumps(t._events)                # must not raise
    assert payload
    assert all("line" in e and "speed" in e for e in t._events)


def test_reset_clears_state_and_events():
    t = _t()
    t.forward(100); t.left(90); t.pensize(5)
    t.reset()
    assert t._events == []
    assert t.pos() == (0.0, 0.0)
    assert t.heading() == 0.0
    assert t.pensize() == 1
