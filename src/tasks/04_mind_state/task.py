"""Task 04: Mind-State Switching (orchestrator).

Three sequential blocks wrapped by a single ``TaskIO`` lifecycle:

1. **Gameplay** (``game.run_game_block``) -- Pygame rhythm-runner, 5 min.
2. **Break** -- gray screen with a 1 Hz countdown, 1 min.
3. **Meditation** (``meditation.run_meditation_block``) -- instructions
   screen -> black screen + start gong -> silent 5 min -> end gong ->
   completion screen.

Architecture: **Pygame throughout.** We considered splitting the render
layer across Pygame (game) + PsychoPy (meditation screens), but the
window-lifecycle churn (closing one framework's window to open another's
mid-task) is fragile and adds zero scientific value. A single Pygame
display covers everything: the game's animated scene, the break's text +
countdown, the meditation instructions, and the meditation's all-black
screen. Audio for the gongs goes through ``pygame.mixer``. The only
framework this task depends on is Pygame (and the stdlib ``pathlib``);
PsychoPy is not imported here.

Like Tasks 01-03, side-effecting I/O is bundled into a ``TaskIO``
dataclass and Pygame is imported lazily inside :func:`_build_pygame_io`
so the module imports cleanly on the Windows dev box. Tests inject a
``MockTaskIO`` that drives the game loop with scripted inputs and
bypasses all real rendering / audio / timing.
"""

from __future__ import annotations

import csv
import importlib
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pylsl import StreamOutlet, local_clock

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_demo_outlet, send_marker

# The task directory name starts with a digit (``04_mind_state``), which is
# not a valid Python identifier, so a plain ``from . import game`` inside
# this module does not work. Resolve the sibling submodules via importlib
# (which goes through the file system) instead.
game_mod = importlib.import_module("tasks.04_mind_state.game")
meditation_mod = importlib.import_module("tasks.04_mind_state.meditation")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

TASK_NAME = "task04_mind_state"

INSTRUCTIONS_TEXT = (
    "In this task, you will first play a short game for 5 minutes, "
    "then take a brief break, and then meditate for 5 minutes.\n\n"
    "During the game, press and hold SPACEBAR to jump. Hold longer for a "
    "higher jump. Avoid the obstacles!\n\n"
    "There are no sounds during the game.\n\n"
    "Press spacebar to begin."
)


# ----- I/O bundle (mockable) -------------------------------------------------


@dataclass
class TaskIO:
    """Side-effecting callables shared across the three blocks."""

    show_text_and_wait: Callable[[str, str], None]
    """Show *text* on a gray screen and block until *wait_key* is pressed."""

    show_break_frame: Callable[[int], None]
    """Render one frame of the break countdown (gray bg + text)."""

    show_black_screen: Callable[[], None]
    """Clear the window to pure black (meditation)."""

    play_gong: Callable[[], None]
    """Play the meditation start/end gong (blocking or async is acceptable)."""

    tick: Callable[[int], float]
    """Advance the game-loop frame clock; return seconds since the last tick."""

    get_input_state: Callable[[], dict]
    """Return ``{space_pressed, space_held, space_released}`` for the current frame."""

    draw_game_frame: Callable[[dict], None]
    """Render one game frame from the supplied game state dict."""

    check_escape: Callable[[], None]
    """Raise :class:`EscapePressedError` if Escape has been pressed."""

    wait: Callable[[float], None]
    """Sleep for *seconds*."""


def _build_pygame_io(demo: bool, gong_path: Path) -> tuple[TaskIO, Callable[[], None]]:
    """Construct a real Pygame-backed ``TaskIO``.

    Imports Pygame lazily so this module is importable on dev machines
    without Pygame installed. Initializes one display window that is reused
    for all three blocks and one mixer channel for the gong.
    """
    import pygame  # noqa: PLC0415

    pygame.init()
    pygame.mixer.init()

    flags = 0 if demo else pygame.FULLSCREEN
    screen = pygame.display.set_mode((game_mod.SCREEN_W, game_mod.SCREEN_H), flags)
    pygame.display.set_caption("IACS Task 04 -- Mind-State Switching")
    clock = pygame.time.Clock()
    font_big = pygame.font.SysFont(None, 48)
    font_huge = pygame.font.SysFont(None, 96)

    try:
        gong_sound = pygame.mixer.Sound(str(gong_path))
    except Exception:
        gong_sound = None
        log.warning("Could not load gong at %s; meditation will be silent", gong_path)

    space_held_state = {"held": False, "pressed_this_frame": False, "released_this_frame": False}

    def _poll_events() -> None:
        space_held_state["pressed_this_frame"] = False
        space_held_state["released_this_frame"] = False
        for ev in pygame.event.get():
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_SPACE:
                space_held_state["held"] = True
                space_held_state["pressed_this_frame"] = True
            elif ev.type == pygame.KEYUP and ev.key == pygame.K_SPACE:
                space_held_state["held"] = False
                space_held_state["released_this_frame"] = True
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                from tasks.common.display import EscapePressedError  # noqa: PLC0415

                raise EscapePressedError

    def show_text_and_wait(text: str, wait_key: str) -> None:
        waiting = True
        while waiting:
            screen.fill((40, 40, 40))
            y = game_mod.SCREEN_H // 2 - 200
            for line in text.split("\n"):
                surf = font_big.render(line, True, (230, 230, 230))
                rect = surf.get_rect(center=(game_mod.SCREEN_W // 2, y))
                screen.blit(surf, rect)
                y += 56
            pygame.display.flip()
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN:
                    if wait_key == "space" and ev.key == pygame.K_SPACE:
                        waiting = False
                    elif ev.key == pygame.K_ESCAPE:
                        from tasks.common.display import (  # noqa: PLC0415
                            EscapePressedError,
                        )

                        raise EscapePressedError
            clock.tick(30)

    def show_break_frame(remaining_seconds: int) -> None:
        screen.fill((50, 50, 50))
        text = (
            "Take a moment to relax and stretch.\n"
            f"The meditation will begin in {remaining_seconds} seconds."
        )
        y = game_mod.SCREEN_H // 2 - 60
        for line in text.split("\n"):
            surf = font_big.render(line, True, (220, 220, 220))
            rect = surf.get_rect(center=(game_mod.SCREEN_W // 2, y))
            screen.blit(surf, rect)
            y += 56
        pygame.display.flip()
        pygame.event.pump()

    def show_black_screen() -> None:
        screen.fill((0, 0, 0))
        pygame.display.flip()
        pygame.event.pump()

    def play_gong() -> None:
        if gong_sound is not None:
            gong_sound.play()

    def tick(fps: int) -> float:
        _poll_events()
        dt_ms = clock.tick(fps)
        return dt_ms / 1000.0

    def get_input_state() -> dict:
        return {
            "space_pressed": space_held_state["pressed_this_frame"],
            "space_held": space_held_state["held"],
            "space_released": space_held_state["released_this_frame"],
        }

    def draw_game_frame(state: dict) -> None:
        # Parallax background
        screen.fill((18, 22, 32))
        for layer_speed, shade in ((0.25, (28, 36, 52)), (0.55, (44, 56, 80))):
            offset = (
                state["bg_offset_far"]
                if layer_speed < 0.4
                else state["bg_offset_near"]
            )
            for i in range(-1, 3):
                x = int(-offset + i * (game_mod.SCREEN_W / 2))
                pygame.draw.rect(
                    screen,
                    shade,
                    (x, 80 + (40 if layer_speed > 0.4 else 0), 520, 260),
                )

        # Ground
        pygame.draw.rect(
            screen,
            (68, 80, 100),
            (0, game_mod.GROUND_Y, game_mod.SCREEN_W, game_mod.SCREEN_H - game_mod.GROUND_Y),
        )
        for gx in range(0, game_mod.SCREEN_W, 80):
            pygame.draw.line(
                screen,
                (90, 104, 130),
                (gx, game_mod.GROUND_Y),
                (gx, game_mod.SCREEN_H),
                1,
            )

        # Obstacles
        for obs in state["obstacles"]:
            color = {
                "spike": (255, 120, 90),
                "tall_rect": (220, 180, 80),
                "low_barrier": (160, 240, 140),
            }.get(obs["type"], (200, 200, 200))
            pygame.draw.rect(
                screen,
                color,
                (int(obs["x"]), int(obs["y"]), int(obs["w"]), int(obs["h"])),
            )

        # Player
        player_rect = pygame.Rect(
            int(state["player_x"]),
            int(state["player_y"] - game_mod.PLAYER_H),
            game_mod.PLAYER_W,
            game_mod.PLAYER_H,
        )
        player_color = (100, 230, 240)
        if state["collision_flash_s"] > 0:
            flash = int(255 * (state["collision_flash_s"] / 0.5))
            player_color = (min(255, 180 + flash), 230, 240)
        pygame.draw.rect(screen, player_color, player_rect)

        # Particles
        for p in state["particles"]:
            pygame.draw.rect(screen, p["color"], (int(p["x"]), int(p["y"]), 5, 5))

        # HUD
        score_surf = font_big.render(f"Score {state['score']}", True, (230, 230, 230))
        screen.blit(score_surf, (20, 20))
        speed_surf = font_big.render(
            f"{state['speed_multiplier']:.2f}x", True, (180, 200, 230)
        )
        screen.blit(speed_surf, (game_mod.SCREEN_W - 140, 20))

        if state["collision_flash_s"] > 0:
            overlay = pygame.Surface(
                (game_mod.SCREEN_W, game_mod.SCREEN_H), pygame.SRCALPHA
            )
            alpha = int(160 * (state["collision_flash_s"] / 0.5))
            overlay.fill((255, 255, 255, alpha))
            screen.blit(overlay, (0, 0))

        pygame.display.flip()

    def check_escape() -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                from tasks.common.display import EscapePressedError  # noqa: PLC0415

                raise EscapePressedError

    def wait(seconds: float) -> None:
        pygame.time.wait(int(seconds * 1000))

    def cleanup() -> None:
        pygame.quit()

    io = TaskIO(
        show_text_and_wait=show_text_and_wait,
        show_break_frame=show_break_frame,
        show_black_screen=show_black_screen,
        play_gong=play_gong,
        tick=tick,
        get_input_state=get_input_state,
        draw_game_frame=draw_game_frame,
        check_escape=check_escape,
        wait=wait,
    )
    # The HUD uses font_huge too in case we extend it; silence linters:
    _ = font_huge
    return io, cleanup


# ----- Break runner ----------------------------------------------------------


def _run_break(outlet: StreamOutlet, io: TaskIO, duration_s: int) -> list[dict]:
    send_marker(outlet, "task04_break_start")
    log_entries: list[dict] = [
        {"timestamp": local_clock(), "phase": "break", "type": "break_start"}
    ]
    for remaining in range(int(duration_s), 0, -1):
        io.check_escape()
        io.show_break_frame(remaining)
        io.wait(1.0)
    send_marker(outlet, "task04_break_end")
    log_entries.append(
        {"timestamp": local_clock(), "phase": "break", "type": "break_end"}
    )
    return log_entries


# ----- Behavioral log --------------------------------------------------------


_LOG_FIELDS = ("timestamp", "phase", "event_type", "details")


def _save_behavioral_log(
    entries: list[dict],
    participant_id: str,
    output_dir: Path,
) -> Path:
    """Flatten *entries* into a 4-column CSV for the behavioral log."""
    out = output_dir / participant_id
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"task04_mind_state_{int(local_clock() * 1000)}.csv"
    with open(log_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_LOG_FIELDS)
        for e in entries:
            event_type = e.get("type", "")
            details_parts = []
            for key, value in e.items():
                if key in ("timestamp", "phase", "type"):
                    continue
                details_parts.append(f"{key}={value}")
            writer.writerow(
                [
                    f"{e.get('timestamp', 0.0):.6f}",
                    e.get("phase", ""),
                    event_type,
                    "; ".join(details_parts),
                ]
            )
    return log_path


# ----- Main entry point ------------------------------------------------------


def run(
    outlet: StreamOutlet | None = None,
    *,
    config: dict | None = None,
    participant_id: str = "DEMO",
    demo: bool = False,
    io: TaskIO | None = None,
    rng_seed: int | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Run the Mind-State Switching task end-to-end.

    Parameters mirror Tasks 01-03. In ``demo=True`` mode: 30 s game, 10 s
    break, 30 s meditation (the gong still plays). Total ~70 s.
    """
    if config is None:
        config = get_task_config(load_session_config(), TASK_NAME)
    else:
        config = dict(config)

    if demo:
        config["game_duration_s"] = 30
        config["break_duration_s"] = 10
        config["meditation_duration_s"] = 30
        config["game_speed_increment_interval_s"] = 10

    if output_dir is None:
        output_dir = DATA_DIR

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    if io is None:
        gong_path = REPO_ROOT / config["gong_file"]
        io, cleanup = _build_pygame_io(demo=demo, gong_path=gong_path)

    rng = random.Random(rng_seed)
    all_entries: list[dict] = []

    try:
        send_marker(outlet, "task04_start")
        all_entries.append(
            {"timestamp": local_clock(), "phase": "session", "type": "task_start"}
        )

        # ----- Instructions -----
        send_marker(outlet, "task04_instructions_start")
        io.show_text_and_wait(INSTRUCTIONS_TEXT, "space")
        send_marker(outlet, "task04_instructions_end")
        all_entries.append(
            {
                "timestamp": local_clock(),
                "phase": "session",
                "type": "instructions_end",
            }
        )

        # ----- Block 1: Game -----
        send_marker(outlet, "task04_game_start")
        game_entries = game_mod.run_game_block(
            outlet=outlet,
            io=io,
            config=config,
            rng=rng,
            duration_s=float(config["game_duration_s"]),
        )
        all_entries.extend(game_entries)
        send_marker(outlet, "task04_game_end")

        # ----- Break -----
        break_entries = _run_break(outlet, io, int(config["break_duration_s"]))
        all_entries.extend(break_entries)

        # ----- Block 2: Meditation -----
        meditation_entries = meditation_mod.run_meditation_block(
            outlet=outlet,
            io=io,
            duration_s=float(config["meditation_duration_s"]),
        )
        all_entries.extend(meditation_entries)

        log_path = _save_behavioral_log(all_entries, participant_id, output_dir)

        send_marker(outlet, "task04_end")
        log.info("Task 04 complete: %s", log_path)
        return log_path
    finally:
        cleanup()
        if own_outlet:
            del outlet
