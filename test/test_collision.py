"""Tests for collision detection and the on_collision hook.

Collisions are detected in Python at move time (headless). By default the turtle stops at the
first obstacle/edge it hits; ``on_collision(..., stop=False)`` restores notify-only motion.
"""
import json

import pytest

from turtle_widget import Turtle, widget


def _t():
    return Turtle(autoshow=False)


def test_no_hook_means_no_collisions():
    t = _t(); t.add_circle(100, 0, 20)
    t.forward(300)
    assert t.pos() == (300.0, 0.0)                # passes straight through


def test_stops_at_circle_surface():
    hits = []
    t = _t(); t.add_circle(100, 0, 20)
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.forward(300)
    assert t.pos() == pytest.approx((80, 0))      # stopped at the near surface
    assert len(hits) == 1
    c = hits[0]
    assert c.obstacle.kind == "circle" and c.index == 0
    assert c.point == pytest.approx((80, 0))
    assert c.normal == pytest.approx((-1, 0))
    assert c.distance == pytest.approx(80)
    assert c.pos == pytest.approx((80, 0))        # probe pos == where it stopped


def test_stops_at_canvas_edge():
    hits = []
    t = _t(); t.on_collision(lambda c: hits.append(c))    # walls on by default
    t.forward(400)
    assert t.pos() == pytest.approx((250, 0))             # stopped at the east wall
    assert len(hits) == 1 and hits[0].obstacle.kind == "wall"


def test_stops_at_first_of_many():
    hits = []
    t = _t(); t.add_circle(150, 0, 10); t.add_circle(60, 0, 10)
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.forward(300)
    assert len(hits) == 1                          # only the first
    assert hits[0].distance == pytest.approx(50)
    assert t.pos() == pytest.approx((50, 0))


def test_drawn_line_truncated_to_contact():
    t = _t(); t.add_circle(100, 0, 20)
    t.on_collision(lambda c: None, walls=False)    # no-op handler: just stop
    t.forward(300)
    last_line = [e for e in t._events if e["op"] == "line"][-1]
    assert (last_line["x2"], last_line["y2"]) == pytest.approx((80, 0))


def test_stops_at_nearest_obstacle_not_the_one_behind():
    hits = []
    t = _t()
    t.add_segment(40, -50, 40, 50)                 # vertical wall at x=40
    t.add_rectangle(80, -20, 120, 20)              # box behind it
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.forward(300)
    assert len(hits) == 1 and hits[0].obstacle.kind == "segment"
    assert t.pos() == pytest.approx((40, 0))


def test_stop_false_completes_and_reports_all():
    hits = []
    t = _t(); t.add_circle(60, 0, 10); t.add_circle(150, 0, 10)
    t.on_collision(lambda c: hits.append(c), stop=False, walls=False)
    t.forward(300)
    assert t.pos() == (300.0, 0.0)                 # motion unchanged
    assert [round(c.distance) for c in hits] == [50, 140]


def test_circle_stops_at_first_chord_contact():
    hits = []
    t = _t(); t.add_rectangle(-50, 70, 50, 90)     # bar across the top of the arc (peak y=80)
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.circle(40)
    assert len(hits) == 1 and hits[0].obstacle.kind == "polygon"
    assert t.ycor() == pytest.approx(70)           # stopped at the bar, mid-arc
    assert t.xcor() > 0


def test_trail_self_collision_opt_in():
    def run(trail):
        t = _t()
        t.penup(); t.goto(-50, 50); t.pendown(); t.goto(50, 50)   # a wall at y=50
        t.penup(); t.goto(0, 0); t.pendown()
        hits = []
        t.on_collision(lambda c: hits.append(c), walls=False, trail=trail)
        t.setheading(90); t.forward(120)
        return [c.obstacle.kind for c in hits], t.ycor()
    kinds_off, y_off = run(False)
    assert kinds_off == [] and y_off == pytest.approx(120)        # straight through
    kinds_on, y_on = run(True)
    assert kinds_on == ["trail"] and y_on == pytest.approx(50)    # stopped at its own line


def test_probe_state_in_collision():
    hits = []
    t = _t(); t.add_circle(100, 0, 20)
    t.pensize(4); t.pencolor("teal"); t.penup()
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.forward(300)
    c = hits[0]
    assert c.pos == pytest.approx((80, 0))
    assert c.angle == pytest.approx(0)             # head-on (perpendicular) hit
    assert c.isdown is False and c.isvisible is True
    assert c.pencolor == "teal" and c.pensize == 4 and c.speed == 6


def test_handler_can_move_without_recursion():
    calls = [0]
    t = _t(); t.add_circle(50, 0, 10)

    def react(c):
        calls[0] += 1
        t.pencolor("red"); t.forward(5)            # handler move must not re-trigger

    t.on_collision(react, walls=False)
    t.forward(200)
    assert calls[0] == 1
    assert any(e["op"] == "color" for e in t._events)
    json.dumps(t._events)


def test_on_collision_none_disables():
    hits = []
    t = _t(); t.add_circle(100, 0, 20)
    t.on_collision(lambda c: hits.append(c), walls=False)
    t.on_collision(None)
    t.forward(300)
    assert hits == [] and t.pos() == (300.0, 0.0)


def test_collision_angle_is_signed_incidence_from_normal():
    # head-on into a vertical wall -> 0 deg (perpendicular)
    h = []
    t = _t(); t.add_segment(100, -300, 100, 300)
    t.on_collision(lambda c: h.append(c), walls=False)
    t.forward(300, speed=2)
    assert h[0].angle == pytest.approx(0)
    assert h[0].speed == pytest.approx(12)         # effective move speed (global 6 x 2)


def test_collision_angle_sign_distinguishes_glancing_sides():
    # +30 heading -> +30 incidence; -30 heading -> -30 (mirror side); 0 would be head-on
    hp = []
    tp = _t(); tp.add_segment(100, -600, 100, 600)
    tp.on_collision(lambda c: hp.append(c), walls=False)
    tp.setheading(30); tp.forward(400)
    assert hp[0].angle == pytest.approx(30)

    hm = []
    tm = _t(); tm.add_segment(100, -600, 100, 600)
    tm.on_collision(lambda c: hm.append(c), walls=False)
    tm.setheading(-30); tm.forward(400)
    assert hm[0].angle == pytest.approx(-30)


def test_collision_geometry_helpers():
    h = widget._seg_hit(0, 0, 1, 0, 5, -1, 5, 1, 10)
    assert h is not None
    t, n = h
    assert t == pytest.approx(5) and n == pytest.approx((-1, 0))
    assert widget._seg_hit(0, 0, 1, 0, 5, -1, 5, 1, 3) is None     # beyond max_t
    t2, n2 = widget._circle_hit(0, 0, 1, 0, 10, 0, 2, 50)
    assert t2 == pytest.approx(8) and n2 == pytest.approx((-1, 0))


def test_invisible_obstacle_still_collides():
    h = []
    t = _t(); t.add_rectangle(40, -20, 60, 20, visible=False)   # invisible wall
    t.on_collision(lambda c: h.append(c), walls=False)
    t.forward(300)
    assert h and h[0].obstacle.kind == "polygon"
    assert t.pos() == pytest.approx((40, 0))                    # still stopped at it


def test_collision_obstacle_is_the_shape_object_with_label():
    h = []
    t = _t(); t.add_circle(100, 0, 20, label="rock")
    t.on_collision(lambda c: h.append(c), walls=False)
    t.forward(300)
    ob = h[0].obstacle
    assert isinstance(ob, widget.Obstacle)
    assert ob.kind == "circle" and ob.label == "rock" and ob.index == 0
    assert (ob.x, ob.y, ob.r) == (100, 0, 20)        # geometry lives on the object
    assert ob is t._obstacles[0]                     # the registered shape itself


def test_collision_wall_obstacle_has_no_label_or_index():
    h = []
    t = _t(); t.on_collision(lambda c: h.append(c))   # walls on by default
    t.forward(400)
    ob = h[0].obstacle
    assert ob.kind == "wall" and ob.label is None and ob.index is None


def test_sense_false_obstacle_still_collides():
    # sense=False hides a shape from distance_ahead/sense, but it still collides.
    h = []
    t = _t(); t.add_circle(100, 0, 20, sense=False)
    assert t.distance_ahead(walls=False, trail=False) is None   # not sensable
    t.on_collision(lambda c: h.append(c), walls=False)
    t.forward(300)
    assert h and h[0].obstacle.kind == "circle"
    assert t.pos() == pytest.approx((80, 0))                    # still stops at it


# -- nr_collisions counter -------------------------------------------------- #

def test_nr_collisions_starts_zero_and_needs_a_handler():
    t = _t(); t.add_circle(100, 0, 20)
    assert t.nr_collisions == 0
    t.forward(300)                              # no on_collision -> detection off
    assert t.nr_collisions == 0 and t.pos() == (300.0, 0.0)


def test_nr_collisions_increments_once_per_colliding_move():
    t = _t(); t.add_circle(100, 0, 20)
    t.on_collision(lambda c: None, walls=False)
    t.forward(300)
    assert t.nr_collisions == 1


def test_nr_collisions_accumulates_across_moves():
    t = _t()
    t.on_collision(lambda c: t.right(180))      # bounce: turn around on each wall hit
    t.forward(400)                              # hits east wall
    assert t.nr_collisions == 1
    t.forward(600)                              # now heading west, hits west wall
    assert t.nr_collisions == 2


def test_nr_collisions_counts_every_crossing_when_stop_false():
    t = _t(); t.add_circle(60, 0, 10); t.add_circle(150, 0, 10)
    t.on_collision(lambda c: None, stop=False, walls=False)
    t.forward(300)                              # passes through both circles
    assert t.nr_collisions == 2


def test_nr_collisions_reset_to_zero():
    t = _t(); t.add_circle(100, 0, 20)
    t.on_collision(lambda c: None, walls=False)
    t.forward(300)
    assert t.nr_collisions == 1
    t.reset()
    assert t.nr_collisions == 0


def test_total_movement_counts_only_distance_actually_travelled():
    t = _t(); t.add_circle(100, 0, 20)          # surface at x=80
    t.on_collision(lambda c: None, walls=False)
    t.forward(300)                              # requested 300, stopped at 80
    assert t.pos() == pytest.approx((80, 0))
    assert t.total_movement == pytest.approx(80)   # not 300
