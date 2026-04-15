"""Tests for src/tasks/04_mind_state/*.

Drives the orchestrator, the game loop, and the meditation block headlessly
with a mock ``TaskIO`` so Pygame is not required. The main end-to-end test
runs the full orchestrator with a scripted game input sequence that
guarantees at least one jump (space press on specific frames) and at least
one collision (obstacle arrives at the player while the input is idle), and
asserts every one of the 19 Task 04 marker types appears via a real LSL
roundtrip.

Pure-state update tests exercise :func:`game.update_game_state` directly and
verify the five in-game event types (jump_start, jump_end, speed_increase,
obstacle_appear, collision) are produced under controlled conditions.
"""

from __future__ import annotations

import csv
import importlib
import random
from pathlib import Path

import pytest
from pylsl import StreamInlet, resolve_byprop

from tasks.common.lsl_markers import create_session_outlet, send_marker

task_mod = importlib.import_module("tasks.04_mind_state.task")
game_mod = importlib.import_module("tasks.04_mind_state.game")
meditation_mod = importlib.import_module("tasks.04_mind_state.meditation")


# ----- Mock TaskIO -----------------------------------------------------------


class MockTaskIO:
    """Headless TaskIO: scripted inputs for the game loop, no-op rendering."""

    def __init__(self, input_script=None, tick_dt: float = 1 / 60) -> None:
        self.input_script = input_script or []
        self.tick_dt = tick_dt
        self.tick_count = 0
        self._frame_idx = 0
        self.draw_calls = 0
        self.gong_calls = 0
        self.shown_texts: list[str] = []
        self.break_frames: list[int] = []
        self.black_screens = 0
        self.waits: list[float] = []

    def tick(self, fps: int) -> float:
        self.tick_count += 1
        return self.tick_dt

    def get_input_state(self) -> dict:
        if self._frame_idx < len(self.input_script):
            state = dict(self.input_script[self._frame_idx])
        else:
            state = {
                "space_pressed": False,
                "space_held": False,
                "space_released": False,
            }
        self._frame_idx += 1
        return state

    def draw_game_frame(self, state: dict) -> None:
        self.draw_calls += 1

    def show_text_and_wait(self, text: str, wait_key: str) -> None:
        self.shown_texts.append(text)

    def show_break_frame(self, remaining: int) -> None:
        self.break_frames.append(remaining)

    def show_black_screen(self) -> None:
        self.black_screens += 1

    def play_gong(self) -> None:
        self.gong_calls += 1

    def check_escape(self) -> None:
        return None

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)


# ----- Inlet helpers ---------------------------------------------------------


def _drain_inlet(inlet: StreamInlet) -> list[str]:
    markers: list[str] = []
    while True:
        sample, _ = inlet.pull_sample(timeout=0.2)
        if sample is None:
            break
        markers.append(sample[0])
    return markers


@pytest.fixture()
def captured_marker_outlet():
    outlet = create_session_outlet("MINDSTATE_TEST")
    streams = resolve_byprop(
        "source_id", "P013_MINDSTATE_TEST", minimum=1, timeout=5.0
    )
    assert streams, "Could not resolve test marker stream"
    inlet = StreamInlet(streams[0])
    inlet.open_stream(timeout=5.0)

    for _ in range(50):
        send_marker(outlet, "__handshake__")
        sample, _ = inlet.pull_sample(timeout=0.1)
        if sample is not None:
            break
    else:
        pytest.fail("Inlet never connected after 50 handshake attempts")

    while True:
        extra, _ = inlet.pull_sample(timeout=0.05)
        if extra is None:
            break

    yield outlet, inlet
    del inlet
    del outlet


# ----- Pure-helper tests: game state --------------------------------------


def _base_game_cfg() -> dict:
    return {
        "game_start_speed": 1.0,
        "game_max_speed": 2.5,
        "game_duration_s": 300.0,
        "game_speed_increment_interval_s": 30.0,
        "obstacle_types": ["spike", "tall_rect", "low_barrier"],
        "jump_hold_max_ms": 300,
        "initial_spawn_delay_s": 2.0,
    }


class TestUpdateGameState:
    def test_init_state(self):
        state = game_mod.init_game_state(_base_game_cfg())
        assert state["player_x"] == game_mod.PLAYER_SPAWN_X
        assert state["player_y"] == game_mod.GROUND_Y
        assert state["elapsed_s"] == 0.0
        assert state["obstacles"] == []
        assert state["speed_multiplier"] == 1.0

    def test_jump_start_emitted_on_space_press(self):
        cfg = _base_game_cfg()
        state = game_mod.init_game_state(cfg)
        events = game_mod.update_game_state(
            state,
            0.016,
            {"space_pressed": True, "space_held": True, "space_released": False},
            cfg,
            random.Random(0),
        )
        assert any(e["type"] == "jump_start" for e in events)
        assert state["is_jumping"] is True

    def test_jump_end_emitted_on_release(self):
        cfg = _base_game_cfg()
        state = game_mod.init_game_state(cfg)
        # Press
        game_mod.update_game_state(
            state,
            0.016,
            {"space_pressed": True, "space_held": True, "space_released": False},
            cfg,
            random.Random(0),
        )
        # Release mid-air
        events = game_mod.update_game_state(
            state,
            0.016,
            {"space_pressed": False, "space_held": False, "space_released": True},
            cfg,
            random.Random(0),
        )
        assert any(e["type"] == "jump_end" for e in events)
        assert state["is_jumping"] is False

    def test_speed_increase_fires_at_step_boundary(self):
        cfg = _base_game_cfg()
        cfg["game_speed_increment_interval_s"] = 0.1
        cfg["game_duration_s"] = 1.0
        state = game_mod.init_game_state(cfg)
        # Advance past one step boundary in a single frame
        events = game_mod.update_game_state(
            state, 0.15, {}, cfg, random.Random(0)
        )
        assert any(e["type"] == "speed_increase" for e in events)
        assert state["speed_multiplier"] > 1.0

    def test_obstacle_spawns_after_delay(self):
        cfg = _base_game_cfg()
        cfg["initial_spawn_delay_s"] = 0.1
        state = game_mod.init_game_state(cfg)
        events = game_mod.update_game_state(
            state, 0.2, {}, cfg, random.Random(0)
        )
        assert any(e["type"] == "obstacle_appear" for e in events)
        assert len(state["obstacles"]) == 1

    def test_collision_detected_and_player_respawned(self):
        cfg = _base_game_cfg()
        state = game_mod.init_game_state(cfg)
        state["obstacles"].append(
            {
                "type": "spike",
                "x": float(game_mod.PLAYER_SPAWN_X),
                "y": float(game_mod.GROUND_Y - 48),
                "w": 36.0,
                "h": 48.0,
                "passed": False,
            }
        )
        events = game_mod.update_game_state(
            state, 0.016, {}, cfg, random.Random(0)
        )
        assert any(e["type"] == "collision" for e in events)
        assert state["player_x"] == game_mod.PLAYER_SPAWN_X
        assert state["is_jumping"] is False
        assert state["collision_flash_s"] > 0


# ----- run_game_block test --------------------------------------------------


class TestRunGameBlock:
    def test_timer_stops_at_duration(self, captured_marker_outlet):
        outlet, _inlet = captured_marker_outlet
        cfg = _base_game_cfg()
        cfg["game_duration_s"] = 1.0
        mock_io = MockTaskIO()
        game_mod.run_game_block(
            outlet=outlet,
            io=mock_io,
            config=cfg,
            rng=random.Random(0),
            duration_s=1.0,
        )
        # Exactly ~60 ticks for 1 s at dt=1/60 (allow ±2 for fp rounding)
        assert 58 <= mock_io.tick_count <= 62
        assert mock_io.draw_calls == mock_io.tick_count


# ----- run_meditation_block test --------------------------------------------


class TestRunMeditationBlock:
    def test_emits_all_six_markers_in_order(self, captured_marker_outlet):
        outlet, inlet = captured_marker_outlet
        mock_io = MockTaskIO()
        meditation_mod.run_meditation_block(outlet, mock_io, duration_s=0.1)
        markers = _drain_inlet(inlet)

        expected = [
            "task04_meditation_instructions_start",
            "task04_meditation_instructions_end",
            "task04_meditation_gong_start",
            "task04_meditation_start",
            "task04_meditation_gong_end",
            "task04_meditation_end",
        ]
        for m in expected:
            assert m in markers, f"Missing marker {m}"
        # Ordering
        indices = [markers.index(m) for m in expected]
        assert indices == sorted(indices)

        # Two gongs: start and end
        assert mock_io.gong_calls == 2
        # Black screen shown once during meditation
        assert mock_io.black_screens == 1
        # wait() called once for the silent timer (plus possibly break waits,
        # but this test only runs the meditation block)
        assert 0.1 in mock_io.waits


# ----- Full orchestrator test -----------------------------------------------


class TestFullOrchestrator:
    @pytest.fixture()
    def small_config(self) -> dict:
        # Fast but large enough for an obstacle to traverse from spawn to the
        # player: at speed 3.0 * 380 px/s = 1140 px/s, 1120 px takes ~0.98 s.
        # First obstacle spawns at t=0.3, reaches player around t=1.28.
        return {
            "game_duration_s": 3.0,
            "break_duration_s": 2,
            "meditation_duration_s": 0.5,
            "game_start_speed": 3.0,
            "game_max_speed": 4.0,
            "game_speed_increment_interval_s": 0.5,
            "jump_hold_max_ms": 300,
            "obstacle_types": ["spike", "tall_rect", "low_barrier"],
            "initial_spawn_delay_s": 0.3,
            "gong_file": "assets/sounds/Simple_Gong.wav",
        }

    def test_full_run_emits_all_19_markers(
        self,
        captured_marker_outlet,
        small_config: dict,
        tmp_path: Path,
    ):
        outlet, inlet = captured_marker_outlet

        # Input script: one jump at frames 30-40 (0.5-0.67 s) so we get
        # both jump_start and jump_end well before any obstacle reaches the
        # player (~frame 77). The rest of the run is idle -- the obstacle
        # naturally collides with the stationary player, triggering the
        # collision marker.
        script = []
        for i in range(200):
            if i == 30:
                script.append(
                    {
                        "space_pressed": True,
                        "space_held": True,
                        "space_released": False,
                    }
                )
            elif 30 < i < 40:
                script.append(
                    {
                        "space_pressed": False,
                        "space_held": True,
                        "space_released": False,
                    }
                )
            elif i == 40:
                script.append(
                    {
                        "space_pressed": False,
                        "space_held": False,
                        "space_released": True,
                    }
                )
            else:
                script.append(
                    {
                        "space_pressed": False,
                        "space_held": False,
                        "space_released": False,
                    }
                )

        mock_io = MockTaskIO(input_script=script)

        log_path = task_mod.run(
            outlet=outlet,
            config=small_config,
            participant_id="PYTEST_T04",
            io=mock_io,
            rng_seed=42,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        marker_set = set(markers)

        expected = {
            # Session boundaries
            "task04_start",
            "task04_end",
            # Overall instructions
            "task04_instructions_start",
            "task04_instructions_end",
            # Game boundaries
            "task04_game_start",
            "task04_game_end",
            # In-game events
            "task04_obstacle_appear",
            "task04_jump_start",
            "task04_jump_end",
            "task04_collision",
            "task04_speed_increase",
            # Break
            "task04_break_start",
            "task04_break_end",
            # Meditation
            "task04_meditation_instructions_start",
            "task04_meditation_instructions_end",
            "task04_meditation_gong_start",
            "task04_meditation_start",
            "task04_meditation_gong_end",
            "task04_meditation_end",
        }
        assert len(expected) == 19  # sanity: 19 distinct markers per spec
        missing = expected - marker_set
        assert not missing, f"Missing marker types: {sorted(missing)}"

        # Ordering: task04_start is first, task04_end is last
        assert markers[0] == "task04_start"
        assert markers[-1] == "task04_end"

        # Phase ordering: instructions -> game -> break -> meditation
        assert markers.index("task04_instructions_end") < markers.index(
            "task04_game_start"
        )
        assert markers.index("task04_game_end") < markers.index("task04_break_start")
        assert markers.index("task04_break_end") < markers.index(
            "task04_meditation_instructions_start"
        )
        assert markers.index("task04_meditation_end") < markers.index("task04_end")

        # Two gongs (start + end of meditation)
        assert mock_io.gong_calls == 2
        # Black screen shown once (during meditation)
        assert mock_io.black_screens == 1
        # Break showed per-second countdown frames
        assert len(mock_io.break_frames) == small_config["break_duration_s"]
        assert mock_io.break_frames[0] == small_config["break_duration_s"]
        assert mock_io.break_frames[-1] == 1

        # Behavioral log exists and has the expected column schema
        assert log_path.exists()
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert list(rows[0].keys()) == ["timestamp", "phase", "event_type", "details"]
        phases = {r["phase"] for r in rows}
        assert {"game", "break", "meditation"}.issubset(phases)
