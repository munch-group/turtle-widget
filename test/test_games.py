"""Tests for the two game harnesses in turtle_widget.games.

Both run_obstacle_course/run_gladiator build their own Turtle(s) internally
(autoshow=False here so no real widget/comm is ever opened) and return a namedtuple
Result carrying the finished turtle(s) for inspection. Same headless style as
test_widget.py/test_collision.py/test_sensing.py/test_multi_turtle.py: assert on the
Result and on turtle state/`_events`, not on the drawn canvas.
"""
import math

import pytest

from turtle_widget import run_obstacle_course, run_gladiator


# -- obstacle course: maze / determinism --------------------------------------- #

def test_maze_false_lets_a_straight_line_strategy_reach_the_end():
    def straight(t, budget):
        # two attempts: home spawns inside the start marker (matching the original
        # prototype), so the first forward() immediately exits through its boundary
        # (a "start"-labelled hit, ignored) before the second actually reaches "end"
        for _ in range(2):
            t.forward(4 * 500)

    result = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=False)
    assert result.won is True
    assert result.reason == "reached_end"
    assert result.score is not None


def test_maze_true_by_default_registers_more_obstacles_than_maze_false():
    noop = lambda t, budget: None
    with_maze = run_obstacle_course(noop, maze=True, autoshow=False, show_scoreboard=False)
    without_maze = run_obstacle_course(noop, maze=False, autoshow=False, show_scoreboard=False)
    assert len(with_maze.turtle._obstacles) > 2
    assert len(without_maze.turtle._obstacles) == 2       # just start + end


def test_home_spawns_inside_the_start_marker_matching_the_original_prototype():
    # Deliberately reproduces the original prototype's home=(-240,-240) quirk (only
    # ~14 units from the start circle's centre, well inside its 30-unit radius) so
    # run_obstacle_course's default course is a faithful port, not just a lookalike.
    noop = lambda t, budget: None
    result = run_obstacle_course(noop, autoshow=False, show_scoreboard=False)
    start_ob = next(ob for ob in result.turtle._obstacles if ob.label == "start")
    dist = math.hypot(result.turtle.xcor() - start_ob.x, result.turtle.ycor() - start_ob.y)
    assert dist < start_ob.r


def test_default_start_end_sit_exactly_at_the_canvas_corners():
    # Also a fidelity check against the original prototype, which hardcoded
    # add_circle(-250,-250,...) / add_circle(250,250,...) for a 500-canvas.
    noop = lambda t, budget: None
    result = run_obstacle_course(noop, canvas_size=500, autoshow=False, show_scoreboard=False)
    obstacles = {ob.label: ob for ob in result.turtle._obstacles if ob.label in ("start", "end")}
    assert (obstacles["start"].x, obstacles["start"].y) == (-250, -250)
    assert (obstacles["end"].x, obstacles["end"].y) == (250, 250)


def test_bounce_never_escapes_the_canvas_even_near_a_corner():
    # Regression test: a bounce move issued from inside on_collision is not itself
    # collision-checked (handler moves are re-entrancy-guarded -- see CLAUDE.md
    # "Collisions"), so an unclamped bounce near a canvas corner could punch
    # straight through the adjacent wall. This exact strategy/course combination
    # was observed to do exactly that before the clamp fix (turtle ended up at
    # y=1610, far outside the 500x500 canvas).
    def hug_the_walls(t, move_budget):
        while t.total_movement <= move_budget:
            d = t.distance_ahead(draw=True, trail=False)
            if d and d.distance < 60:
                t.right(10) if d.angle < 0 else t.left(10)
            t.forward(5)

    result = run_obstacle_course(hug_the_walls, move_budget=2000,
                                  autoshow=False, show_scoreboard=False)
    half = 250
    for e in result.turtle._events:
        if e["op"] in ("line", "move"):
            assert -half <= e["x2"] <= half and -half <= e["y2"] <= half


# -- obstacle course: outcomes -------------------------------------------------- #

def test_budget_exceeded_ends_the_game_as_a_loss():
    def bump_wall(t, budget):
        t.setheading(135)                # off the start-end diagonal: hits only the wall
        t.forward(1000)

    result = run_obstacle_course(bump_wall, move_budget=0, maze=False,
                                  autoshow=False, show_scoreboard=False)
    assert result.won is False
    assert result.reason == "budget_exceeded"
    assert result.score is None


def test_infinite_student_loop_is_still_terminated_by_the_budget():
    def bad(t, budget):
        t.setheading(135)
        while True:
            t.forward(1000)

    result = run_obstacle_course(bad, move_budget=0, maze=False,
                                  autoshow=False, show_scoreboard=False)
    assert result.reason == "budget_exceeded"     # must not hang


def test_strategy_returning_early_still_yields_a_well_formed_result():
    result = run_obstacle_course(lambda t, budget: None, autoshow=False, show_scoreboard=False)
    assert result.won is False
    assert result.reason == "strategy_returned"
    assert result.score is None
    assert result.turtle.total_movement == 0


def test_reaching_end_computes_the_documented_score_formula():
    def straight(t, budget):
        for _ in range(2):
            t.forward(2000)

    result = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=False)
    t = result.turtle
    expected = (t.total_movement + t.nr_collisions + t.nr_right + t.nr_left
                + t.nr_distance_ahead + t.nr_sense)
    assert result.score == pytest.approx(expected)


def test_start_and_end_are_not_sensed_but_still_collide():
    def straight(t, budget):
        d = t.distance_ahead(walls=False)
        assert d is None                  # sense=False hides start/end from sensing
        for _ in range(2):
            t.forward(2000)

    result = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=False)
    assert result.won is True             # ...but on_collision still catches it via label


def test_obstacle_course_strategy_exception_propagates_normally():
    def bad(t, budget):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run_obstacle_course(bad, autoshow=False, show_scoreboard=False)


# -- obstacle course: config / regression tests --------------------------------- #

def test_obstacle_course_show_scoreboard_false_skips_drawing():
    def straight(t, budget):
        t.forward(2000)

    result_no = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=False)
    result_yes = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=True)
    assert not any(e["op"] == "write" for e in result_no.turtle._events)
    assert any(e["op"] == "write" for e in result_yes.turtle._events)


def test_canvas_is_square():
    result = run_obstacle_course(lambda t, b: None, canvas_size=400,
                                  autoshow=False, show_scoreboard=False)
    assert result.turtle._canvas.width == 400
    assert result.turtle._canvas.height == 400


def test_obstacles_stay_visible_through_the_scoreboard():
    def straight(t, budget):
        for _ in range(2):
            t.forward(2000)

    result = run_obstacle_course(straight, autoshow=False, show_scoreboard=True)
    assert not any(e["op"] == "obstacles_visible" for e in result.turtle._events)
    assert all(ob.visible for ob in result.turtle._obstacles)


def test_scoreboard_ends_with_the_original_underline_and_spin_flourish():
    def straight(t, budget):
        for _ in range(2):
            t.forward(2000)

    result = run_obstacle_course(straight, maze=False, autoshow=False, show_scoreboard=True)
    last_two = result.turtle._events[-2:]
    assert last_two[0]["op"] == "line" and last_two[0]["x2"] - last_two[0]["x1"] == 250
    assert last_two[1]["op"] == "turn" and last_two[1]["to"] - last_two[1]["from"] == -1000


# -- gladiator: arena setup ------------------------------------------------------ #

def test_default_homes_place_turtles_facing_each_other():
    noop = lambda me, other: None
    result = run_gladiator(noop, noop, max_rounds=0, autoshow=False, show_scoreboard=False)
    a, b = result.turtle_a, result.turtle_b
    assert a.xcor() < 0 < b.xcor()
    assert a.heading() == pytest.approx(0)
    assert b.heading() == pytest.approx(180)
    assert result.reason == "draw"
    assert result.rounds == 0


def test_hitbox_radius_is_applied_to_both_turtles():
    noop = lambda me, other: None
    result = run_gladiator(noop, noop, hitbox_radius=42, max_rounds=0,
                            autoshow=False, show_scoreboard=False)
    assert result.turtle_a.hitbox() == 42
    assert result.turtle_b.hitbox() == 42


def test_custom_homes_override_the_default_arena():
    noop = lambda me, other: None
    homes = ((-100, 50, 0), (100, -50, 180))
    result = run_gladiator(noop, noop, homes=homes, max_rounds=0,
                            autoshow=False, show_scoreboard=False)
    assert result.turtle_a.position() == pytest.approx((-100, 50))
    assert result.turtle_b.position() == pytest.approx((100, -50))


def test_arena_margin_out_of_range_raises():
    noop = lambda me, other: None
    with pytest.raises(ValueError):
        run_gladiator(noop, noop, arena_margin=300, autoshow=False, show_scoreboard=False)


# -- gladiator: duel outcomes ----------------------------------------------------- #

def test_a_wins_by_ramming_b():
    def charge(me, other):
        me.forward(20)

    def hold(me, other):
        pass

    result = run_gladiator(charge, hold, autoshow=False, show_scoreboard=False)
    assert result.winner == "a"
    assert result.reason == "ram"


def test_b_wins_by_ramming_a():
    def charge(me, other):
        me.forward(20)

    def hold(me, other):
        pass

    result = run_gladiator(hold, charge, autoshow=False, show_scoreboard=False)
    assert result.winner == "b"
    assert result.reason == "ram"


def test_draw_when_round_cap_reached_before_any_ram():
    noop = lambda me, other: None
    result = run_gladiator(noop, noop, max_rounds=5, autoshow=False, show_scoreboard=False)
    assert result.winner is None
    assert result.reason == "draw"
    assert result.rounds == 5


def test_wall_hit_does_not_end_the_duel():
    def bump_wall(me, other):
        me.setheading(180)                # away from the opponent, into the west wall
        me.forward(1000)

    def hold(me, other):
        pass

    result = run_gladiator(bump_wall, hold, max_rounds=1, canvas_size=500,
                            autoshow=False, show_scoreboard=False)
    assert result.reason == "draw"                              # wall hit didn't end it...
    assert result.turtle_a.xcor() == pytest.approx(-250)         # ...but did truncate the move


def test_gladiator_show_scoreboard_false_skips_drawing():
    def charge(me, other):
        me.forward(20)

    def hold(me, other):
        pass

    result_no = run_gladiator(charge, hold, autoshow=False, show_scoreboard=False)
    result_yes = run_gladiator(charge, hold, autoshow=False, show_scoreboard=True)
    assert not any(e["op"] == "write" for e in result_no.turtle_a._events)
    assert any(e["op"] == "write" for e in result_yes.turtle_a._events)


def test_gladiator_scoreboard_ends_with_the_original_underline_and_spin_flourish():
    def charge(me, other):
        me.forward(20)

    def hold(me, other):
        pass

    result = run_gladiator(charge, hold, autoshow=False, show_scoreboard=True)
    last_two = result.turtle_a._events[-2:]
    assert last_two[0]["op"] == "line" and last_two[0]["x2"] - last_two[0]["x1"] == 250
    assert last_two[1]["op"] == "turn" and last_two[1]["to"] - last_two[1]["from"] == -1000


def test_gladiator_strategy_exception_propagates_normally():
    def bad(me, other):
        raise ValueError("boom")

    def hold(me, other):
        pass

    with pytest.raises(ValueError):
        run_gladiator(bad, hold, autoshow=False, show_scoreboard=False)
