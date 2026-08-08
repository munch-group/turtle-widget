"""Two ready-made game harnesses built entirely on the public ``Turtle`` API.

``run_obstacle_course`` drives a maze-navigation game: a student-supplied
``strategy(turtle, move_budget)`` steers one turtle from a start marker to an end
marker through a grid of rectangular obstacles.

``run_gladiator`` drives a step-based, alternating duel between two turtles sharing
one canvas: student-supplied ``strategy(me, opponent)`` functions each perform one
bounded action per call; the harness calls A, then B, round after round, checking
after every call whether the mover's ``hitbox`` rammed the other turtle.

Both harnesses are pure orchestration over the existing obstacles/sensing/collision/
hitbox primitives on ``Turtle`` -- see CLAUDE.md's "Obstacles & sensing" / "Turtles as
obstacles" / "Collisions" sections for the underlying mechanics they build on.
"""
import collections
import math

from .widget import Turtle


class _GameOver(Exception):
    """Internal control-flow signal -- not part of the public API.

    Raised (from an ``on_collision`` handler, or right after a strategy call returns
    normally) to unwind out of student code the instant a game-ending condition is
    reached. Carries the already-built ``Result`` so the harness needs only one
    ``try``/``except`` wrapped around the whole run.
    """
    def __init__(self, result):
        super().__init__(result)
        self.result = result


#: Outcome of :func:`run_obstacle_course`. ``score`` is set only when ``won`` is
#: True (lower is better: total movement plus one penalty point per action taken --
#: see the score formula in ``run_obstacle_course``'s Notes). ``reason`` is one of
#: ``"reached_end"``, ``"budget_exceeded"`` or ``"strategy_returned"``. ``turtle`` is
#: the ``Turtle`` used to play the course, left in its final state for inspection.
ObstacleCourseResult = collections.namedtuple(
    "ObstacleCourseResult",
    ["won", "reason", "score", "duration", "total_movement", "turns",
     "distance_ahead_calls", "sense_calls", "collisions", "turtle"])

#: Outcome of :func:`run_gladiator`. ``winner`` is ``"a"``, ``"b"``, or ``None`` for a
#: draw. ``reason`` is ``"ram"`` or ``"draw"``. ``rounds`` counts alternating
#: round-pairs completed. ``turtle_a``/``turtle_b`` are left in their final state for
#: inspection.
GladiatorResult = collections.namedtuple(
    "GladiatorResult", ["winner", "reason", "rounds", "turtle_a", "turtle_b"])


# --- obstacle course --------------------------------------------------------- #

def _default_start_end(canvas_size):
    half = canvas_size / 2
    return (-half, -half), (half, half)


def _default_home(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    heading = math.degrees(math.atan2(dy, dx))
    offset = 10 * math.sqrt(2)   # matches the original prototype's home=(-240, -240):
                                  # a fixed 10-unit diagonal nudge off the corner, which
                                  # (deliberately, matching that prototype) still lands
                                  # inside the default 30-radius start marker
    hx = start[0] + offset * math.cos(math.radians(heading))
    hy = start[1] + offset * math.sin(math.radians(heading))
    return (hx, hy, heading)


def _build_maze(t, canvas_size, obstacle_size, obstacle_sep):
    c = canvas_size // 2
    size, sep = obstacle_size, obstacle_sep
    for i in range(-c - size // 2, c, size + sep):
        for j in range(-c - size // 2, c, size + sep):
            if i < -c + size + sep and j < -c + size + sep:
                continue
            if i > c - 2 * (size + sep) and j > c - 2 * (size + sep):
                continue
            t.add_rectangle(i, j, i + size, j + size)


def _score(t):
    """Lower is better: distance travelled plus one penalty point per action taken."""
    return (t.total_movement + t.nr_collisions + t.nr_right + t.nr_left
            + t.nr_distance_ahead + t.nr_sense)


def _draw_end_flourish(t, canvas_size, y):
    """Underline the scoreboard text and spin in place -- the original prototype's
    end-of-game flourish, reproduced verbatim (same 250-unit underline at speed=0.7,
    same 1000-degree spin) for both games."""
    half = canvas_size / 2
    t.goto(-half + 20, y - 5)
    t.pendown()
    t.goto(-half + 20 + 250, y - 5, speed=0.7)
    t.right(1000)


def _draw_scoreboard(t, canvas_size, end, obstacle_size, result):
    half = canvas_size / 2
    t.penup()
    t.clear()   # wipe the drawn trail only -- obstacles stay registered and visible
    if result.won:
        t.goto(end)
        t.pencolor("green")
        t.dot(2 * obstacle_size)
    t.pencolor("black")
    font = ("sans-serif", 20, "normal")
    line_height = 25
    lines = [
        "Reached end!" if result.won else f"Did not finish ({result.reason})",
        f"Time: {result.duration:.3f}",
        f"Moved: {result.total_movement:.3f}",
        f"Turns: {result.turns}",
        f"Dist. ahead: {result.distance_ahead_calls}",
        f"Sense: {result.sense_calls}",
        f"Collisions: {result.collisions}",
    ]
    if result.won:
        lines.append(f"Score: {result.score:.3f}")
    y = 200
    for line in lines:
        t.goto(-half + 20, y)
        t.write(line, font=font)
        y -= line_height
    _draw_end_flourish(t, canvas_size, y)


def run_obstacle_course(strategy, *, move_budget=2000, canvas_size=500,
                         obstacle_size=30, obstacle_sep=30, maze=True,
                         start=None, end=None, home=None, speed=5,
                         show_scoreboard=True, autoshow=True):
    """Run a maze-navigation game and return the outcome.

    Builds a canvas with a red **start** marker, a green **end** marker, and (unless
    ``maze=False``) a grid of small square obstacles between them, spawns a turtle at
    ``start`` facing ``end``, and hands control to ``strategy``. The game ends the
    instant the turtle reaches ``end`` (a win) or its total movement exceeds
    ``move_budget`` (a loss); either way ``run_obstacle_course`` returns a
    :class:`ObstacleCourseResult`.

    Parameters
    ----------
    strategy : callable
        Called once as ``strategy(turtle, move_budget)``. Typically a loop that
        drives the turtle with ordinary commands (``forward``, ``left``/``right``,
        ``distance_ahead``, ``sense``, ...) until it reaches ``end`` or gives up.
    move_budget : float, optional
        Movement cap (turtle units), passed through to ``strategy`` as a hint and
        also enforced by the harness itself -- so even a non-terminating strategy
        (e.g. ``while True: ...``) is safely stopped once it crosses this cap.
        Default 2000.
    canvas_size : int, optional
        Side length of the (square) canvas. Default 500.
    obstacle_size, obstacle_sep : float, optional
        Size of each maze cell and the gap between cells. Default 30, 30.
    maze : bool, optional
        If True (default), fill the arena with a grid of square obstacles between
        start and end. If False, only the start/end markers and the canvas edges are
        present -- a gentle first exercise, and a deterministic setup for tests.
    start, end : tuple of float, optional
        Centres of the start/end markers. Default to opposite corners of the canvas.
    home : tuple of float, optional
        Where the turtle spawns, as ``(x, y, heading)``. Defaults to a point a short,
        fixed distance in from ``start``, facing ``end`` -- close enough that, with
        the default ``obstacle_size``, it lands inside the start marker itself; the
        turtle's first move harmlessly exits through it (see Notes).
    speed : int, optional
        Turtle drawing speed. Default 5.
    show_scoreboard : bool, optional
        If True (default), clear the drawn trail (the maze obstacles stay visible)
        and write a summary on the canvas when the game ends. Set False for
        silent/headless runs.
    autoshow : bool, optional
        Passed straight through to the internal ``Turtle``. Default True; pass False
        for headless use such as tests.

    Returns
    -------
    ObstacleCourseResult
        The outcome -- see its field descriptions for details.

    Notes
    -----
    Start and end are registered with ``sense=False`` (invisible to
    ``distance_ahead``/``sense``/``nearest``, so a strategy's own sensing isn't
    confused by the goal) but remain fully collidable -- ``on_collision`` is never
    filtered by ``sense``. The handler tells them apart from ordinary maze cells via
    ``Collision.obstacle.label`` (``"start"``/``"end"``/``None``); a ``"start"`` hit is
    ignored, so the turtle spawning inside it (the default ``home``) is harmless. The
    score, set only on a win, is ``total_movement + nr_collisions + nr_right +
    nr_left + nr_distance_ahead + nr_sense`` -- lower is better.
    """
    default_start, default_end = _default_start_end(canvas_size)
    start = start if start is not None else default_start
    end = end if end is not None else default_end
    home = home if home is not None else _default_home(start, end)

    t = Turtle(canvas_size, canvas_size, autoshow=autoshow, home=home)
    t.speed(speed)
    t.add_circle(start[0], start[1], obstacle_size, color="red", label="start", sense=False)
    t.add_circle(end[0], end[1], obstacle_size, color="green", label="end", sense=False)
    if maze:
        _build_maze(t, canvas_size, obstacle_size, obstacle_sep)

    def finish(reason):
        t.on_collision(None)   # stop reacting to contacts before any scoreboard drawing
        won = reason == "reached_end"
        if won:
            # Marking the win is part of reaching "end", not just scoreboard
            # decoration -- do it unconditionally, matching the original prototype,
            # so show_scoreboard=False still leaves the turtle sitting on a lit-up
            # end marker rather than wherever collision truncation happened to stop.
            t.penup()
            t.goto(end)
            t.pencolor("green")
            t.dot(2 * obstacle_size)
            t.pencolor("black")
        result = ObstacleCourseResult(
            won=won, reason=reason, score=(_score(t) if won else None),
            duration=t.duration(), total_movement=t.total_movement,
            turns=t.nr_left + t.nr_right, distance_ahead_calls=t.nr_distance_ahead,
            sense_calls=t.nr_sense, collisions=t.nr_collisions, turtle=t)
        if show_scoreboard:
            _draw_scoreboard(t, canvas_size, end, obstacle_size, result)
        raise _GameOver(result)

    def on_hit(c):
        label = c.obstacle.label
        if label == "start":
            return
        if label == "end":
            finish("reached_end")
        elif t.total_movement >= move_budget:
            finish("budget_exceeded")
        else:
            t.penup()
            t.goto(c.point)
            t.right(180 + 2 * c.angle, speed=0)
            t.pendown()
            t.pencolor("magenta")
            t.dot(25)
            t.pencolor("black")
            # A plain forward() here is not collision-checked (handler moves are
            # re-entrancy-guarded -- see CLAUDE.md "Collisions"), so near a canvas
            # corner it could otherwise punch straight through the adjacent wall.
            # Clamp the destination into the canvas instead; for every ordinary
            # (non-corner) bounce this lands exactly where forward(c.speed) would.
            half = canvas_size / 2
            rad = math.radians(t.heading())
            nx = max(-half, min(half, t.xcor() + c.speed * math.cos(rad)))
            ny = max(-half, min(half, t.ycor() + c.speed * math.sin(rad)))
            t.goto(nx, ny)

    t.on_collision(on_hit)
    try:
        strategy(t, move_budget)
        finish("strategy_returned")
    except _GameOver as e:
        return e.result


# --- gladiator ---------------------------------------------------------------- #

def _default_arena_homes(canvas_size, arena_margin):
    if not (0 < arena_margin < canvas_size / 2):
        raise ValueError("arena_margin must be between 0 and canvas_size / 2")
    hx = canvas_size / 2 - arena_margin
    return (-hx, 0, 0), (hx, 0, 180)


def _draw_gladiator_scoreboard(a, canvas_size, result):
    if result.winner == "a":
        text = "A wins!"
    elif result.winner == "b":
        text = "B wins!"
    else:
        text = "Draw!"
    a.penup()
    a.pencolor("black")
    y = canvas_size / 2 - 30
    a.goto(-canvas_size / 2 + 20, y)
    a.write(f"{text} ({result.reason}, round {result.rounds})",
            font=("sans-serif", 20, "normal"))
    _draw_end_flourish(a, canvas_size, y)


def run_gladiator(strategy_a, strategy_b, *, max_rounds=500, canvas_size=500,
                   hitbox_radius=15, arena_margin=60, homes=None,
                   colors=("blue", "red"), speed=6, show_scoreboard=True,
                   autoshow=True):
    """Run a step-based, alternating gladiator duel and return the outcome.

    Two turtles share one canvas, spawned on opposite walls facing each other. Each
    round, ``strategy_a(a, b)`` is called, then ``strategy_b(b, a)`` -- each call
    should perform **one bounded action** (e.g. sense, maybe turn, move a short
    step), not loop to completion. After each call the harness checks whether that
    turtle's move rammed the other's ``hitbox``; the first to do so wins. Running out
    of ``max_rounds`` without a ram is a draw.

    Parameters
    ----------
    strategy_a, strategy_b : callable
        Each called as ``strategy(me, opponent)`` once per round.
    max_rounds : int, optional
        Round cap before a draw is declared. ``0`` ends immediately in a draw
        without calling either strategy -- useful for inspecting the initial arena.
        Default 500.
    canvas_size : int, optional
        Side length of the (square) shared canvas. Default 500.
    hitbox_radius : float, optional
        Collision radius for both turtles -- see :func:`Turtle.hitbox`. Default 15.
    arena_margin : float, optional
        Distance from each side wall to that turtle's default home, when ``homes``
        is not given. Must satisfy ``0 < arena_margin < canvas_size / 2``.
        Default 60.
    homes : tuple of two (x, y, heading) tuples, optional
        Explicit spawn points, overriding the auto-computed opposite-walls,
        facing-each-other default.
    colors : tuple of two (str or None), optional
        Pen colours for A and B, for telling them apart. Default ``("blue", "red")``.
    speed : int, optional
        Turtle drawing speed. Default 6.
    show_scoreboard : bool, optional
        If True (default), write the outcome on the canvas when the duel ends.
    autoshow : bool, optional
        Passed straight through to the internal turtles. Default True; pass False
        for headless use such as tests.

    Returns
    -------
    GladiatorResult
        The outcome -- see its field descriptions for details.

    Notes
    -----
    Both turtles get ``on_collision(handler, walls=True, turtles=True)``. A wall hit
    (``obstacle.kind == "wall"``) truncates that turtle's move to the arena boundary
    -- a wasted action -- but does **not** end the duel; only ``obstacle.kind ==
    "turtle"`` does. Neither turtle collides with drawn trail.
    """
    if homes is not None:
        home_a, home_b = homes
    else:
        home_a, home_b = _default_arena_homes(canvas_size, arena_margin)

    a = Turtle(canvas_size, canvas_size, autoshow=autoshow, home=home_a)
    if colors[0] is not None:
        a.pencolor(colors[0])
    a.speed(speed)
    b = a.new_turtle(color=colors[1], home=home_b)
    b.speed(speed)
    a.hitbox(hitbox_radius)
    b.hitbox(hitbox_radius)

    round_num = 0

    def finish(winner, reason):
        a.on_collision(None)   # stop reacting to contacts before any scoreboard drawing
        b.on_collision(None)
        result = GladiatorResult(winner=winner, reason=reason, rounds=round_num,
                                  turtle_a=a, turtle_b=b)
        if show_scoreboard:
            _draw_gladiator_scoreboard(a, canvas_size, result)
        raise _GameOver(result)

    def make_handler(me_label):
        def handler(c):
            if c.obstacle.kind == "turtle":
                finish(me_label, "ram")
        return handler

    a.on_collision(make_handler("a"), walls=True, turtles=True)
    b.on_collision(make_handler("b"), walls=True, turtles=True)

    try:
        for round_num in range(1, max_rounds + 1):
            strategy_a(a, b)
            strategy_b(b, a)
        finish(None, "draw")
    except _GameOver as e:
        return e.result
