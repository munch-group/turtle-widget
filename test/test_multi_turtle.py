"""Tests for two-or-more turtles sharing one canvas.

``new_turtle()``/``other_turtle()`` join a turtle to another turtle's canvas: shared
committed art, obstacles, and sensed trail (see CLAUDE.md "World state"), but each
turtle keeps its own position/heading/pen/collision-config/counters. These tests are
headless, same style as test_widget.py/test_collision.py/test_sensing.py: build
turtles with ``autoshow=False`` and assert on state and ``t._events``.
"""
import json

import pytest

from turtle_widget import Turtle


def _t():
    return Turtle(autoshow=False)


# -- new_turtle / other_turtle basics ---------------------------------------- #

def test_new_turtle_joins_the_same_canvas():
    t1 = _t()
    t2 = t1.new_turtle()
    assert t2 is not t1
    assert t2._canvas is t1
    assert t2.pos() == (0.0, 0.0)               # independent turtle-level state


def test_new_turtle_assigns_distinct_ids():
    t1 = _t()
    t2 = t1.new_turtle()
    t3 = t1.new_turtle()
    assert len({t1._turtle_id, t2._turtle_id, t3._turtle_id}) == 3


def test_separate_canvases_each_start_ids_at_zero():
    a = _t()
    b = _t()
    assert a._turtle_id == 0 and b._turtle_id == 0    # independent canvases/id spaces


def test_new_turtle_color_sets_pencolor_without_affecting_others():
    t1 = _t()
    t2 = t1.new_turtle(color="red")
    assert t2.pencolor() == "red"
    assert t1.pencolor() == "black"


def test_new_turtle_home_kwarg_is_independent_of_the_other_turtle():
    t1 = _t()
    t1.forward(100)
    t2 = t1.new_turtle(home=(30, 40, 90))
    assert t2.pos() == pytest.approx((30, 40))
    assert t2.heading() == pytest.approx(90)
    assert t1.pos() == pytest.approx((100, 0))


def test_new_turtle_never_opens_a_comm():
    # The whole point of new_turtle() over other_turtle(): it never constructs (and
    # discards) a real Canvas/widget for the joined turtle.
    t1 = _t()
    t2 = t1.new_turtle()
    assert t2.comm is None


def test_other_turtle_joins_an_already_constructed_turtle():
    t1 = _t()
    standalone = Turtle(autoshow=False)
    standalone.forward(20)                       # state before joining is kept
    joined = t1.other_turtle(standalone)
    assert joined is standalone
    assert joined._canvas is t1
    assert joined.pos() == pytest.approx((20, 0))


def test_other_turtle_reassigns_a_fresh_id_to_avoid_collisions():
    t1 = _t()
    t3 = _t()                     # its own canvas -> also starts at id 0
    assert t3._turtle_id == 0
    t1.other_turtle(t3)
    assert t3._turtle_id == 1     # re-assigned, else it would collide with t1's own id 0


def test_other_turtle_unhooks_the_joined_turtles_autoshow():
    t1 = _t()
    standalone = Turtle(autoshow=False)
    standalone._hook = "sentinel"    # simulate a real autoshow hook having been registered
    t1.other_turtle(standalone)
    assert standalone._hook is None


# -- shared event stream ------------------------------------------------------ #

def test_events_from_both_turtles_share_one_stream_in_program_order():
    t1 = _t()
    t2 = t1.new_turtle()
    t1.forward(10)
    t2.forward(20)
    t1.forward(30)
    line_ops = [e["turtle"] for e in t1._events if e["op"] == "line"]
    assert line_ops == [t1._turtle_id, t2._turtle_id, t1._turtle_id]


def test_events_are_json_serializable_with_turtle_id():
    t1 = _t()
    t2 = t1.new_turtle(color="blue")
    t1.forward(10)
    t2.forward(10)
    payload = json.dumps(t1._events)
    assert payload
    assert all("turtle" in e for e in t1._events)


# -- shared world: obstacles + trail ------------------------------------------ #

def test_obstacle_registered_by_one_turtle_is_sensed_by_another():
    t1 = _t()
    t1.add_circle(100, 0, 20, label="rock")
    t2 = t1.new_turtle()
    d = t2.distance_ahead(walls=False, trail=False)
    assert d is not None and d.obstacle.label == "rock"


def test_turtle_senses_another_turtles_trail():
    t1 = _t()
    t1.forward(100)                              # trail (0,0)-(100,0)
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(50, 80); t2.pendown(); t2.setheading(270)
    d = t2.distance_ahead(walls=False)
    assert d.kind == "trail"
    assert d.distance == pytest.approx(80)


def test_turtle_collides_with_another_turtles_trail_when_opted_in():
    t1 = _t()
    t1.forward(100)                              # a "wall" along y=0 from x=0..100
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(50, 80); t2.pendown(); t2.setheading(270)
    hits = []
    t2.on_collision(lambda c: hits.append(c), walls=False, trail=True)
    t2.forward(200)
    assert len(hits) == 1 and hits[0].obstacle.kind == "trail"
    assert t2.ycor() == pytest.approx(0)


def test_collision_config_stays_per_turtle():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.on_collision(lambda c: None, walls=False, trail=True)
    assert t1._collision_cb is None              # t2's opt-in doesn't affect t1


# -- reset() / clear() on a shared canvas ------------------------------------- #

def test_reset_on_joined_turtle_does_not_clobber_the_canvas():
    t1 = _t()
    t1.forward(100)
    t1.add_circle(10, 10, 5)
    t2 = t1.new_turtle()
    t2.forward(30)
    n_events, n_obstacles = len(t1._events), len(t1._obstacles)

    t2.reset()

    assert len(t1._events) == n_events + 1        # only t2's new teleport was appended
    assert t1._events[-1]["op"] == "teleport"
    assert t1._events[-1]["turtle"] == t2._turtle_id
    assert len(t1._obstacles) == n_obstacles       # t1's obstacle survives
    assert t2.pos() == (0.0, 0.0)                  # t2 itself is reset
    assert t1.pos() == pytest.approx((100, 0))     # t1 untouched


def test_clear_wipes_whole_shared_canvas_but_keeps_obstacles():
    t1 = _t()
    t1.add_circle(10, 10, 5)
    t2 = t1.new_turtle()
    t1.forward(50)
    t2.forward(50)
    assert len(t1._trail_segments) == 2

    t2.clear()                                     # any turtle can clear the shared canvas

    assert t1._canvas._trail_segments == []
    assert len(t1._obstacles) == 1                 # obstacles survive
    assert t1.pos() == pytest.approx((50, 0))       # positions untouched
    assert t2.pos() == pytest.approx((50, 0))


# -- the documented dead-trait-value caveat ----------------------------------- #

def test_joined_turtle_canvas_traits_are_dead_use_canvas_instead():
    t1 = Turtle(autoshow=False, width=300, height=200)
    t2 = t1.new_turtle()
    assert t1.width == 300 and t1.height == 200
    assert t2._canvas.width == 300 and t2._canvas.height == 200   # the real, shared canvas
    assert t2.width == 500 and t2.height == 500                    # t2's own dead defaults


# -- turtles as obstacles (hitbox) -------------------------------------------- #

def test_hitbox_defaults_to_none():
    t = _t()
    assert t.hitbox() is None


def test_hitbox_get_and_set():
    t = _t()
    assert t.hitbox(20) is t                # chainable
    assert t.hitbox() == 20
    t.hitbox(None)                          # disables it again
    assert t.hitbox() is None


def test_turtle_with_no_hitbox_is_invisible_to_others():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(100, 0); t2.pendown()
    assert t1.distance_ahead(walls=False, trail=False) is None
    assert t1.sense(walls=False, trail=False) == []
    assert t1.nearest(walls=False, trail=False) is None


def test_turtle_hitbox_detected_by_distance_ahead():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(100, 0); t2.pendown()
    t2.hitbox(20)
    d = t1.distance_ahead(walls=False, trail=False)
    assert d is not None
    assert d.kind == "turtle"
    assert d.obstacle.turtle is t2
    assert d.distance == pytest.approx(80)          # 100 - hitbox radius


def test_turtle_hitbox_detected_by_sense_and_nearest():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(0, 50); t2.pendown()
    t2.hitbox(10)
    dets = t1.sense(walls=False, trail=False)
    assert len(dets) == 1 and dets[0].kind == "turtle" and dets[0].obstacle.turtle is t2
    nearest = t1.nearest(walls=False, trail=False)
    assert nearest.obstacle.turtle is t2


def test_turtles_false_excludes_hitboxes_even_if_set():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(100, 0); t2.pendown()
    t2.hitbox(20)
    assert t1.distance_ahead(walls=False, trail=False, turtles=False) is None
    assert t1.sense(walls=False, trail=False, turtles=False) == []


def test_hitbox_does_not_make_a_turtle_sense_itself():
    t1 = _t()
    t1.hitbox(50)                                    # only turtle around; has its own hitbox
    assert t1.distance_ahead(walls=False, trail=False) is None
    assert t1.sense(walls=False, trail=False) == []


def test_hitbox_does_not_affect_this_turtles_own_collisions_with_obstacles():
    # A turtle's hitbox only governs how *other* turtles see it -- it must not change
    # how this turtle collides with ordinary obstacles/walls/trail.
    def stop_position(hitbox_radius):
        t = _t()
        t.add_circle(50, 0, 10)                      # surface at x=40
        if hitbox_radius is not None:
            t.hitbox(hitbox_radius)
        t.on_collision(lambda c: None, walls=False)
        t.forward(100)
        return t.xcor()

    assert stop_position(None) == pytest.approx(40)
    assert stop_position(30) == pytest.approx(40)    # identical regardless of own hitbox


def test_on_collision_turtles_defaults_to_off():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(100, 0); t2.pendown()
    t2.hitbox(20)
    hits = []
    t1.on_collision(lambda c: hits.append(c), walls=False)   # turtles= omitted -> False
    t1.forward(200)
    assert hits == []
    assert t1.xcor() == pytest.approx(200)           # passes straight through


def test_on_collision_turtles_true_stops_at_the_other_turtles_hitbox():
    t1 = _t()
    t2 = t1.new_turtle()
    t2.penup(); t2.goto(100, 0); t2.pendown()
    t2.hitbox(20)
    hits = []
    t1.on_collision(lambda c: hits.append(c), walls=False, turtles=True)
    t1.forward(200)
    assert len(hits) == 1
    assert hits[0].obstacle.kind == "turtle"
    assert hits[0].obstacle.turtle is t2
    assert t1.xcor() == pytest.approx(80)
    assert t1.nr_collisions == 1                     # same counter as any other collision kind


def test_hitbox_and_collision_turtles_flag_survive_reset():
    t = _t()
    t.hitbox(15)
    t.on_collision(lambda c: None, turtles=True)
    t.reset()
    assert t.hitbox() == 15                          # standing policy, not drawing state
    assert t._collision_turtles is True
