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


def test_sethome_changes_where_home_goes():
    t = _t()
    assert t.sethome(50, 50, heading=90) is t      # chainable, does not move
    assert t.pos() == (0.0, 0.0)                    # sethome doesn't move the turtle
    t.forward(30)
    t.home()
    assert t.pos() == pytest.approx((50, 50))
    assert t.heading() == pytest.approx(90)


def test_constructor_home_spawns_there_and_emits_teleport():
    t = Turtle(autoshow=False, home=(-100, 80, 90))
    assert t.pos() == pytest.approx((-100, 80))      # spawns at home, not the origin
    assert t.heading() == pytest.approx(90)
    tp = t._events[0]                                 # leading teleport places the marker
    assert tp["op"] == "teleport"
    assert (tp["x"], tp["y"], tp["heading"]) == pytest.approx((-100, 80, 90))
    t.forward(10)                                     # first drawing begins at home
    seg = next(e for e in t._events if e["op"] in ("line", "move"))
    assert (seg["x1"], seg["y1"]) == pytest.approx((-100, 80))


def test_default_turtle_emits_no_teleport():
    t = _t(); t.forward(10)
    assert all(e["op"] != "teleport" for e in t._events)
    assert Turtle(autoshow=False, home=(0, 0))._events == []   # explicit origin: no teleport


def test_reset_respawns_at_home():
    t = Turtle(autoshow=False, home=(60, 60))
    t.forward(50); t.reset()
    assert t.pos() == pytest.approx((60, 60))         # reset returns to home, not origin
    assert t._events[0]["op"] == "teleport"           # and re-emits the spawn


def test_sethome_accepts_tuple_with_optional_heading():
    a = _t(); a.sethome((10, 20)); a.home()
    assert a.pos() == pytest.approx((10, 20)) and a.heading() == pytest.approx(0)
    b = Turtle(autoshow=False, home=(10, 20, 45)); b.home()
    assert b.pos() == pytest.approx((10, 20)) and b.heading() == pytest.approx(45)


def test_default_home_is_origin():
    t = _t(); t.forward(40); t.left(90)
    t.home()
    assert t.pos() == (0.0, 0.0) and t.heading() == pytest.approx(0)


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


def test_per_move_speed_multiplier_scales_events():
    t = _t(); t.speed(6)
    t.forward(50)
    assert t._events[-1]["speed"] == 6             # default 1.0x
    t.forward(50, speed=2)
    assert t._events[-1]["speed"] == 12            # 2x faster
    t.left(90, speed=0.5)
    assert t._events[-1]["op"] == "turn" and t._events[-1]["speed"] == 3
    t.forward(50)
    assert t._events[-1]["speed"] == 6             # multiplier resets between moves
    assert t.speed() == 6                          # global speed unchanged


def test_per_move_speed_multiplier_on_circle_and_instant():
    t = _t(); t.speed(5)
    t.circle(40, speed=2)                          # every chord scaled
    assert {e["speed"] for e in t._events if e["op"] == "line"} == {10}
    z = _t(); z.speed(0)                           # global instant stays instant
    z.forward(50, speed=5)
    assert z._events[-1]["speed"] == 0


def test_events_json_serializable_with_line_and_speed():
    t = _t()
    t.forward(50); t.right(45); t.circle(20); t.dot(); t.write("x")
    payload = json.dumps(t._events)                # must not raise
    assert payload
    assert all("line" in e and "speed" in e for e in t._events)


def test_reset_on_solo_turtle_still_wipes_its_own_canvas():
    # reset() is defined in terms of "wipe canvas-level state only when this turtle
    # IS its own canvas" (self._canvas is self) -- true for every solo turtle, so the
    # common single-turtle case is unchanged: a completely fresh canvas, same as
    # before this widget supported sharing one. (See test/test_multi_turtle.py for
    # the shared-canvas case, where reset() must NOT wipe another turtle's history --
    # that's the whole reason for the "is self" condition.)
    t = _t()
    t.forward(100); t.left(90); t.pensize(5)
    t.reset()
    assert len(t._events) == 1                     # prior events are gone...
    assert t._events[0]["op"] == "teleport"         # ...replaced by just the new teleport
    assert t.pos() == (0.0, 0.0)
    assert t.heading() == 0.0
    assert t.pensize() == 1


def test_duration_sums_line_and_turn_time():
    t = _t(); t.speed(6)
    t.forward(150)                 # 150 px at 6*150 px/s
    t.left(90)                     # 90 deg at 6*180 deg/s
    t.dot(10)                      # instant: contributes nothing
    assert t.duration() == pytest.approx(150 / (6 * 150) + 90 / (6 * 180))


def test_duration_zero_when_instant_or_empty():
    t = _t(); t.speed(0)           # speed 0 -> everything snaps into place
    t.forward(150); t.left(90)
    assert t.duration() == 0.0
    assert _t().duration() == 0.0  # nothing drawn yet


def test_duration_respects_per_move_multiplier():
    t = _t(); t.speed(6)
    t.forward(150, speed=2)        # effective speed 12
    assert t.duration() == pytest.approx(150 / (12 * 150))


def test_left_right_call_counters():
    t = _t()
    assert t.nr_left == 0 and t.nr_right == 0
    t.left(10); t.left(20); t.right(5)
    assert t.nr_left == 2 and t.nr_right == 1
    t.lt(1); t.rt(1)                          # aliases are the same methods, so they count
    assert t.nr_left == 3 and t.nr_right == 2


def test_turn_helpers_do_not_inflate_left_right():
    # setheading/circle/home steer via internal helpers, not the public left/right
    t = _t()
    t.setheading(90); t.circle(20); t.home()
    assert t.nr_left == 0 and t.nr_right == 0


def test_call_counters_reset():
    t = _t()
    t.left(10); t.right(10)
    t.reset()
    assert t.nr_left == 0 and t.nr_right == 0


def test_total_movement_sums_distance_and_ignores_turns():
    t = _t()
    assert t.total_movement == 0.0
    t.forward(100)
    t.left(90)                               # turning is not movement
    t.forward(50)
    assert t.total_movement == pytest.approx(150)


def test_total_movement_counts_penup_travel_and_diagonals():
    t = _t()
    t.penup(); t.forward(40); t.pendown()    # pen-up travel still counts -> 40
    t.goto(70, 40)                           # from (40,0): (30,40) -> 50
    assert t.total_movement == pytest.approx(40 + 50)


def test_total_movement_reset():
    t = _t(); t.forward(100); t.backward(30)
    assert t.total_movement == pytest.approx(130)
    t.reset()
    assert t.total_movement == 0.0
