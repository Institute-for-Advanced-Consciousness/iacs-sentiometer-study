"""Task 04 gameplay block: custom rhythm-runner (Geometry Dash analog).

The game state is advanced by :func:`update_game_state`, a pure function that
takes a state dict, a frame dt, an input state dict, the config, and an RNG
and returns a list of events for that frame. The block runner
:func:`run_game_block` wraps this in a frame loop that polls a ``TaskIO`` for
input and rendering, emits LSL markers for each event, and returns the full
event log for CSV output.

Pygame is never imported here -- the IO layer owns all rendering, input
polling, and clock ticking, so this module can be exercised by a mock IO in
tests without Pygame installed.

Design notes:
* No audio of any kind during the game block (by design -- keeps the EEG
  comparison between gameplay and meditation clean).
* Game ends at exactly ``duration_s`` regardless of player state.
* Collisions reset the player horizontally to the spawn x and remove the
  colliding obstacle (instant respawn, no death screen).
"""

from __future__ import annotations

import random
from typing import Any

from pylsl import StreamOutlet, local_clock

from tasks.common.lsl_markers import send_marker

# ----- Display layout constants (logical pixel space) -----------------------
# The renderer can draw these to any window size. 1280x720 is a convenient
# reference frame. Ground is at y=520 in that space.

SCREEN_W = 1280
SCREEN_H = 720
GROUND_Y = 520
PLAYER_SPAWN_X = 200
PLAYER_W = 48
PLAYER_H = 48

# ----- Physics constants -----------------------------------------------------

BASE_PIXEL_SPEED = 380.0  # pixels per second at speed_multiplier = 1.0
GRAVITY = 2400.0  # pixels / s^2
JUMP_INITIAL_VY = -600.0  # pixels / s (negative = upward)
HOLD_BOOST = 2200.0  # extra upward acceleration while space held (pixels / s^2)

# ----- Obstacle catalog ------------------------------------------------------
# Width / height and vertical placement relative to GROUND_Y.

OBSTACLE_SHAPES: dict[str, dict] = {
    "spike": {"w": 36, "h": 48, "y_offset": -48},
    "tall_rect": {"w": 40, "h": 140, "y_offset": -140},
    "low_barrier": {"w": 80, "h": 24, "y_offset": -24},
}


# ----- State helpers ---------------------------------------------------------


def init_game_state(config: dict) -> dict[str, Any]:
    """Create a fresh game state dict.

    The state is plain data (dicts, lists, numbers) so it serializes cleanly
    for behavioral logging and is trivially inspectable from tests.
    """
    return {
        "player_x": PLAYER_SPAWN_X,
        "player_y": GROUND_Y,
        "player_vy": 0.0,
        "is_jumping": False,
        "jump_held_ms": 0.0,
        "obstacles": [],
        "score": 0,
        "speed_multiplier": float(config["game_start_speed"]),
        "elapsed_s": 0.0,
        "next_obstacle_in_s": float(config.get("initial_spawn_delay_s", 2.0)),
        "last_speed_step": 0,
        "collision_flash_s": 0.0,
        "particles": [],
        "bg_offset_far": 0.0,
        "bg_offset_near": 0.0,
    }


def _spawn_obstacle(obstacle_type: str) -> dict:
    shape = OBSTACLE_SHAPES[obstacle_type]
    return {
        "type": obstacle_type,
        "x": float(SCREEN_W + 40),
        "y": float(GROUND_Y + shape["y_offset"]),
        "w": float(shape["w"]),
        "h": float(shape["h"]),
        "passed": False,
    }


def _aabb_collide(a: tuple, b: tuple) -> bool:
    """Axis-aligned bounding box collision test."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


# ----- Pure state update -----------------------------------------------------


def update_game_state(
    state: dict,
    dt_s: float,
    input_state: dict,
    config: dict,
    rng: random.Random,
) -> list[dict]:
    """Advance the game state by *dt_s* seconds.

    Returns a list of events that occurred this frame. Each event is a dict
    with at least a ``type`` key (one of ``"obstacle_appear"``, ``"jump_start"``,
    ``"jump_end"``, ``"collision"``, ``"speed_increase"``) plus any
    event-specific payload.
    """
    events: list[dict] = []
    state["elapsed_s"] += dt_s

    # Speed ramp: linear from game_start_speed -> game_max_speed over the
    # total game duration, sampled at game_speed_increment_interval_s boundaries.
    step_interval = float(config["game_speed_increment_interval_s"])
    total_duration = float(config["game_duration_s"])
    if step_interval > 0:
        step = int(state["elapsed_s"] // step_interval)
        if step > state["last_speed_step"]:
            total_steps = max(1, int(total_duration // step_interval))
            progress = min(1.0, step / total_steps)
            start = float(config["game_start_speed"])
            top = float(config["game_max_speed"])
            state["speed_multiplier"] = start + progress * (top - start)
            state["last_speed_step"] = step
            events.append(
                {
                    "type": "speed_increase",
                    "speed_level": state["speed_multiplier"],
                }
            )

    # Obstacle spawning: interval shrinks linearly with the speed multiplier
    state["next_obstacle_in_s"] -= dt_s
    if state["next_obstacle_in_s"] <= 0:
        obstacle_type = rng.choice(list(config["obstacle_types"]))
        state["obstacles"].append(_spawn_obstacle(obstacle_type))
        events.append({"type": "obstacle_appear", "obstacle_type": obstacle_type})
        start = float(config["game_start_speed"])
        top = float(config["game_max_speed"])
        denom = max(1e-6, top - start)
        speed_frac = max(0.0, min(1.0, (state["speed_multiplier"] - start) / denom))
        base_interval = 2.0
        min_interval = 0.8
        state["next_obstacle_in_s"] = base_interval - speed_frac * (
            base_interval - min_interval
        )

    # Move obstacles left
    dx = state["speed_multiplier"] * BASE_PIXEL_SPEED * dt_s
    for obs in state["obstacles"]:
        obs["x"] -= dx
        if not obs["passed"] and obs["x"] + obs["w"] < state["player_x"]:
            obs["passed"] = True
            state["score"] += 10
    state["obstacles"] = [o for o in state["obstacles"] if o["x"] + o["w"] > -50]

    # Parallax background offsets (visual only, no marker emission)
    state["bg_offset_far"] = (state["bg_offset_far"] + dx * 0.25) % SCREEN_W
    state["bg_offset_near"] = (state["bg_offset_near"] + dx * 0.55) % SCREEN_W

    # Player input / physics
    space_pressed = bool(input_state.get("space_pressed", False))
    space_released = bool(input_state.get("space_released", False))
    space_held = bool(input_state.get("space_held", False))

    on_ground = state["player_y"] >= GROUND_Y - 0.01
    if space_pressed and not state["is_jumping"] and on_ground:
        state["is_jumping"] = True
        state["jump_held_ms"] = 0.0
        state["player_vy"] = JUMP_INITIAL_VY
        events.append({"type": "jump_start"})

    hold_cap_ms = float(config["jump_hold_max_ms"])
    if state["is_jumping"] and space_held and state["jump_held_ms"] < hold_cap_ms:
        state["jump_held_ms"] += dt_s * 1000.0
        state["player_vy"] -= HOLD_BOOST * dt_s

    if space_released and state["is_jumping"]:
        events.append({"type": "jump_end", "jump_height_ms": state["jump_held_ms"]})
        state["is_jumping"] = False

    state["player_vy"] += GRAVITY * dt_s
    state["player_y"] += state["player_vy"] * dt_s

    if state["player_y"] >= GROUND_Y:
        if state["is_jumping"]:
            events.append(
                {"type": "jump_end", "jump_height_ms": state["jump_held_ms"]}
            )
            state["is_jumping"] = False
        state["player_y"] = GROUND_Y
        state["player_vy"] = 0.0
        state["jump_held_ms"] = 0.0

    # Collision detection (with a brief post-collision grace window)
    if state["collision_flash_s"] > 0:
        state["collision_flash_s"] = max(0.0, state["collision_flash_s"] - dt_s)
    else:
        player_rect = (
            state["player_x"],
            state["player_y"] - PLAYER_H,
            PLAYER_W,
            PLAYER_H,
        )
        hit = None
        for obs in state["obstacles"]:
            if _aabb_collide(
                player_rect,
                (obs["x"], obs["y"], obs["w"], obs["h"]),
            ):
                hit = obs
                break
        if hit is not None:
            events.append({"type": "collision", "obstacle_type": hit["type"]})
            state["obstacles"] = [o for o in state["obstacles"] if o is not hit]
            state["player_x"] = PLAYER_SPAWN_X
            state["player_y"] = GROUND_Y
            state["player_vy"] = 0.0
            state["is_jumping"] = False
            state["jump_held_ms"] = 0.0
            state["collision_flash_s"] = 0.5
            state["particles"] = _make_collision_particles(rng)

    # Decay particles
    alive: list[dict] = []
    for p in state["particles"]:
        p["life_s"] -= dt_s
        if p["life_s"] > 0:
            p["x"] += p["vx"] * dt_s
            p["y"] += p["vy"] * dt_s
            p["vy"] += GRAVITY * 0.3 * dt_s
            alive.append(p)
    state["particles"] = alive

    return events


def _make_collision_particles(rng: random.Random) -> list[dict]:
    """Return a starter list of collision-burst particles."""
    particles = []
    for _ in range(16):
        particles.append(
            {
                "x": float(PLAYER_SPAWN_X + PLAYER_W / 2),
                "y": float(GROUND_Y - PLAYER_H / 2),
                "vx": rng.uniform(-220, 220),
                "vy": rng.uniform(-360, -40),
                "life_s": rng.uniform(0.3, 0.7),
                "color": (
                    rng.randint(150, 255),
                    rng.randint(80, 220),
                    rng.randint(80, 220),
                ),
            }
        )
    return particles


# ----- Block runner ----------------------------------------------------------


_EVENT_TO_MARKER = {
    "obstacle_appear": "task04_obstacle_appear",
    "jump_start": "task04_jump_start",
    "jump_end": "task04_jump_end",
    "collision": "task04_collision",
    "speed_increase": "task04_speed_increase",
}


def run_game_block(
    outlet: StreamOutlet,
    io,
    config: dict,
    rng: random.Random,
    duration_s: float,
) -> list[dict]:
    """Run the gameplay block for *duration_s* seconds.

    Drives the frame loop via the ``TaskIO`` (which owns Pygame in production
    and returns scripted inputs in tests). Emits one LSL marker per event and
    returns the full list of events (timestamped with ``local_clock()``) for
    the behavioral log.
    """
    state = init_game_state(config)
    log: list[dict] = []

    while state["elapsed_s"] < duration_s:
        io.check_escape()
        dt_s = io.tick(60)
        input_state = io.get_input_state()

        frame_events = update_game_state(state, dt_s, input_state, config, rng)

        for event in frame_events:
            marker = _EVENT_TO_MARKER.get(event["type"])
            if marker is not None:
                send_marker(outlet, marker)
            event_log = dict(event)
            event_log["timestamp"] = local_clock()
            event_log["phase"] = "game"
            event_log["score"] = state["score"]
            event_log["speed_level"] = state["speed_multiplier"]
            log.append(event_log)

        io.draw_game_frame(state)

    return log
