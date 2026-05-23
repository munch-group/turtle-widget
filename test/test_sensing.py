"""Tests for obstacle registration and sensing.

All geometry is computed in Python, so sensing is fully headless-testable:
register obstacles / draw a trail, then assert on ``distance_ahead``, ``sense``
and ``nearest``.
"""
import json

import pytest

from turtle_widget import Turtle
from turtle_widget.widget import (
    Detection, Obstacle, _rel_angle, _ray_circle_t, _ray_segment_t, _seg_closest_point,
)


def _t():
    return Turtle(autoshow=False)


# -- geometry helpers ------------------------------------------------------- #

def test_seg_closest_point_clamps_to_endpoints():
    assert _seg_closest_point(10, 5, 0, 0, 4, 0) == (4, 0)    # beyond -> end B
    assert _seg_closest_point(-3, 5, 0, 0, 4, 0) == (0, 0)    # before -> end A
    assert _seg_closest_point(2, 5, 0, 0, 4, 0) == (2, 0)     # projects inside


def test_ray_segment_hit_and_miss():
    assert _ray_segment_t(0, 0, 1, 0, 5, -1, 5, 1) == pytest.approx(5)   # east -> wall x=5
    assert _ray_segment_t(0, 0, -1, 0, 5, -1, 5, 1) is None             # west -> miss


def test_ray_circle_first_intersection():
    assert _ray_circle_t(0, 0, 1, 0, 10, 0, 2) == pytest.approx(8)       # near side at 8


def test_rel_angle_sign_convention():
    # facing east (heading 0): left/north is +90, right/south is -90, behind is 180
    assert _rel_angle(0, 10, 0, 0, 0) == pytest.approx(90)
    assert _rel_angle(0, -10, 0, 0, 0) == pytest.approx(-90)
    assert _rel_angle(10, 0, 0, 0, 0) == pytest.approx(0)
    assert abs(_rel_angle(-10, 0, 0, 0, 0)) == pytest.approx(180)


# -- registering obstacles -------------------------------------------------- #

def test_add_shapes_register_and_emit():
    t = _t()
    t.add_circle(50, 0, 10)
    t.add_segment(-20, -20, 20, 20)
    t.add_polygon([(0, 0), (10, 0), (10, 10)])
    t.add_rectangle(-30, -30, -10, -10)
    assert len(t._obstacles) == 4
    assert [e["kind"] for e in t._events if e["op"] == "obstacle"] == \
        ["circle", "segment", "polygon", "polygon"]
    json.dumps(t._events)


def test_add_rectangle_normalises_corners():
    t = _t()
    t.add_rectangle(10, 10, -10, -10)        # corners in any order
    assert t._obstacles[0]["points"] == [[-10, -10], [10, -10], [10, 10], [-10, 10]]


# -- distance_ahead --------------------------------------------------------- #

def test_distance_ahead_returns_detection():
    t = _t(); t.add_circle(100, 0, 20)
    d = t.distance_ahead(walls=False, trail=False)
    assert isinstance(d, Detection)
    assert d.distance == pytest.approx(80)
    assert d.angle == pytest.approx(0)            # head-on into the circle (incidence 0)
    assert d.point == pytest.approx((80, 0))
    assert d.kind == "circle" and d.index == 0


def test_distance_ahead_angle_is_signed_incidence():
    # heading +30 / -30 into a vertical wall -> incidence +30 / -30 (0 would be head-on)
    for hd, exp in ((0, 0), (30, 30), (-30, -30)):
        t = _t(); t.add_segment(100, -600, 100, 600); t.setheading(hd)
        assert t.distance_ahead(walls=False, trail=False).angle == pytest.approx(exp)


def test_distance_ahead_none_when_clear():
    t = _t(); t.add_circle(0, 100, 10)        # due north, nothing ahead (east)
    assert t.distance_ahead(walls=False, trail=False) is None


def test_distance_ahead_respects_max_distance():
    t = _t(); t.add_circle(100, 0, 20)        # surface at 80
    assert t.distance_ahead(70, walls=False, trail=False) is None
    assert t.distance_ahead(90, walls=False, trail=False).distance == pytest.approx(80)


def test_distance_ahead_canvas_wall():
    t = _t(); t.penup(); t.goto(200, 0)       # 50 from east wall on a 500x500 canvas
    assert t.distance_ahead(trail=False).distance == pytest.approx(50)


def test_distance_ahead_through_trail():
    t = _t()
    t.penup(); t.goto(-50, 0); t.pendown(); t.goto(50, 0)   # horizontal wall at y=0
    t.penup(); t.goto(0, 80); t.setheading(270)             # above it, facing south
    assert t.distance_ahead(walls=False).distance == pytest.approx(80)


def test_polygon_obstacle_ray_and_nearest():
    t = _t()
    t.add_polygon([(40, -10), (60, -10), (60, 10), (40, 10)])   # box ahead (east)
    assert t.distance_ahead(walls=False, trail=False).distance == pytest.approx(40)
    d = t.nearest(walls=False, trail=False)
    assert d.kind == "polygon" and d.distance == pytest.approx(40)


# -- sense ------------------------------------------------------------------ #

def test_sense_returns_sorted_detections():
    t = _t(); t.add_circle(150, 0, 10); t.add_circle(60, 0, 10)
    s = t.sense(walls=False, trail=False)
    assert all(isinstance(d, Detection) for d in s)
    assert [round(d.distance) for d in s] == [50, 140]


def test_sense_angle_kind_index_point():
    t = _t(); t.add_circle(0, 100, 10)        # north
    d = t.sense(walls=False, trail=False)[0]
    assert d.kind == "circle" and d.index == 0
    assert d.distance == pytest.approx(90)
    assert d.angle == pytest.approx(90)       # to the left
    assert d.point == pytest.approx((0, 90))


def test_sense_max_distance_filters():
    t = _t(); t.add_circle(60, 0, 10); t.add_circle(200, 0, 10)
    assert len(t.sense(100, walls=False, trail=False)) == 1


def test_sense_wall_nearest_edge():
    t = _t(); t.penup(); t.goto(0, 200)       # near the top wall (y=250)
    d = t.nearest(trail=False)
    assert d.kind == "wall" and d.distance == pytest.approx(50)
    assert d.angle == pytest.approx(90)       # north is to the left when facing east


def test_sense_ignores_point_being_touched():
    t = _t(); t.forward(100)                  # trail (0,0)-(100,0); turtle AT (100,0)
    assert t.sense(walls=False) == []         # standing on its own endpoint -> nothing


def test_sense_trail_detected_after_moving_off():
    t = _t(); t.forward(100)                  # trail (0,0)-(100,0)
    t.penup(); t.goto(100, 60)                # step off the trail (no new trail)
    d = t.nearest(walls=False)
    assert d.kind == "trail"
    assert d.distance == pytest.approx(60)
    assert d.point == pytest.approx((100, 0))


# -- nearest ---------------------------------------------------------------- #

def test_nearest_none_when_nothing_in_range():
    assert _t().nearest(walls=False, trail=False) is None


def test_nearest_is_the_closest():
    t = _t(); t.add_circle(150, 0, 10); t.add_circle(60, 0, 10)
    assert t.nearest(walls=False, trail=False).distance == pytest.approx(50)


# -- visualization events --------------------------------------------------- #

def test_draw_emits_serializable_sense_events():
    t = _t(); t.add_circle(80, 0, 10)
    t.distance_ahead(draw=True, walls=False, trail=False)
    t.sense(draw=True, walls=False, trail=False)
    t.nearest(draw=True, walls=False, trail=False)
    sense_evs = [e for e in t._events if e["op"] == "sense"]
    assert len(sense_evs) == 3
    assert all("rays" in e for e in sense_evs)
    json.dumps(t._events)


def test_sense_ray_color_kwarg():
    t = _t(); t.add_circle(80, 0, 10)
    t.distance_ahead(draw=True, walls=False, trail=False)                 # default
    t.sense(draw=True, color="blue", walls=False, trail=False)            # CSS string
    t.nearest(draw=True, color=(0, 255, 0), walls=False, trail=False)     # rgb tuple
    colors = [e["color"] for e in t._events if e["op"] == "sense"]
    assert colors == ["red", "blue", "rgb(0,255,0)"]
    json.dumps(t._events)


def test_queries_emit_nothing_without_draw():
    t = _t(); t.add_circle(80, 0, 10)
    before = len(t._events)
    t.distance_ahead(walls=False, trail=False)
    t.sense(walls=False, trail=False)
    t.nearest(walls=False, trail=False)
    assert len(t._events) == before


# -- clear / reset ---------------------------------------------------------- #

def test_clear_keeps_obstacles_resets_trail():
    t = _t(); t.add_circle(50, 0, 10); t.forward(30)
    t.clear()
    assert len(t._obstacles) == 1 and t._trail_segments == []
    assert t.nearest(walls=False).kind == "circle"      # circle still sensed, trail gone


def test_reset_clears_obstacles_and_trail():
    t = _t(); t.add_circle(50, 0, 10); t.forward(30)
    t.reset()
    assert t._obstacles == [] and t._trail_segments == []
    assert t.sense(walls=False, trail=False) == []


# -- obstacle visibility ---------------------------------------------------- #

def test_obstacle_visible_flag_and_invisible_still_sensed():
    t = _t()
    t.add_circle(50, 0, 10)                       # visible (default)
    t.add_circle(0, 50, 10, visible=False)        # invisible
    ev = [e for e in t._events if e["op"] == "obstacle"]
    assert ev[0]["visible"] is True and ev[1]["visible"] is False
    assert len(t.sense(walls=False, trail=False)) == 2     # visibility is rendering-only


def test_show_hide_obstacles_emit_events_and_update_state():
    t = _t(); t.add_circle(0, 0, 5)
    t.hide_obstacles()
    assert t._obstacles[0]["visible"] is False
    t.show_obstacles()
    assert t._obstacles[0]["visible"] is True
    vis = [e["visible"] for e in t._events if e["op"] == "obstacles_visible"]
    assert vis == [False, True]


# -- obstacle objects and labels -------------------------------------------- #

def test_detection_carries_labelled_obstacle_object():
    t = _t(); t.add_circle(100, 0, 20, label="rock")
    d = t.distance_ahead(walls=False, trail=False)
    assert isinstance(d.obstacle, Obstacle)
    assert d.obstacle.kind == "circle" and d.obstacle.label == "rock"
    assert d.kind == "circle" and d.index == 0          # mirrored convenience fields
    assert d.obstacle is t._obstacles[0]                # the registered shape itself
    s = t.sense(walls=False, trail=False)[0]
    assert s.obstacle is t._obstacles[0] and s.obstacle.label == "rock"


def test_label_emitted_on_obstacle_event():
    t = _t(); t.add_segment(0, 0, 10, 0, label="fence")
    ev = [e for e in t._events if e["op"] == "obstacle"][0]
    assert ev["label"] == "fence"
    assert t._obstacles[0].label == "fence"             # attribute access on the dict


def test_wall_and_trail_detections_have_synthetic_obstacles():
    t = _t(); t.penup(); t.goto(0, 200)                 # near the top wall
    wall = t.nearest(trail=False).obstacle
    assert wall.kind == "wall" and wall.label is None and wall.index is None
    u = _t(); u.forward(100); u.penup(); u.goto(100, 60)
    trail = u.nearest(walls=False).obstacle
    assert trail.kind == "trail" and trail.index is None


# -- sense=False (not detectable by the sensing queries) -------------------- #

def test_sense_false_hidden_from_all_queries():
    t = _t(); t.add_circle(100, 0, 20, sense=False)
    assert t.distance_ahead(walls=False, trail=False) is None
    assert t.sense(walls=False, trail=False) == []
    assert t.nearest(walls=False, trail=False) is None


def test_sense_false_still_registers_and_is_drawn():
    t = _t(); t.add_circle(100, 0, 20, sense=False)
    assert len(t._obstacles) == 1 and t._obstacles[0].sense is False
    ev = [e for e in t._events if e["op"] == "obstacle"][0]
    assert ev["sense"] is False and ev["visible"] is True     # drawn, just not sensed


def test_sense_flag_is_per_shape():
    t = _t()
    t.add_circle(60, 0, 10, sense=False)            # nearer, not sensable
    t.add_circle(150, 0, 10)                         # farther, sensable
    d = t.distance_ahead(walls=False, trail=False)
    assert d is not None and d.distance == pytest.approx(140)   # skips the near one
    assert [round(x.distance) for x in t.sense(walls=False, trail=False)] == [140]


def test_sense_false_honoured_on_all_shape_types():
    t = _t()
    t.add_circle(40, 0, 5, sense=False)
    t.add_segment(60, -50, 60, 50, sense=False)
    t.add_rectangle(80, -10, 100, 10, sense=False)
    t.add_polygon([(120, -5), (130, 0), (120, 5)], sense=False)
    assert t.sense(walls=False, trail=False) == []
    assert t.distance_ahead(walls=False, trail=False) is None


def test_sense_true_is_default():
    t = _t(); t.add_segment(100, -50, 100, 50)       # no sense= -> sensable
    assert t._obstacles[0].sense is True
    assert t.distance_ahead(walls=False, trail=False).distance == pytest.approx(100)


# -- sense / distance_ahead call counters ----------------------------------- #

def test_sense_and_distance_ahead_call_counters():
    t = _t(); t.add_circle(100, 0, 20)
    assert t.nr_sense == 0 and t.nr_distance_ahead == 0
    t.sense(); t.sense(walls=False)
    t.distance_ahead()
    assert t.nr_sense == 2 and t.nr_distance_ahead == 1


def test_nearest_does_not_increment_nr_sense():
    t = _t(); t.add_circle(100, 0, 20)
    t.nearest(); t.nearest(walls=False)              # nearest calls _scan, not sense()
    assert t.nr_sense == 0


def test_sense_distance_ahead_counters_reset():
    t = _t(); t.add_circle(100, 0, 20)
    t.sense(); t.distance_ahead()
    t.reset()
    assert t.nr_sense == 0 and t.nr_distance_ahead == 0
