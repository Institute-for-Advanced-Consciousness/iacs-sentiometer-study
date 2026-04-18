"""Tests for Task 00 (pre-session questionnaire).

The webview call is stubbed via the ``_webview_fn`` hook in
:func:`tasks.00_questionnaire.task.run`, so these tests never import
``webview`` and never touch Cocoa. We verify:

* URL construction prefills the participant ID when ``entry_id`` is set.
* Submission detection matches Google's ``/formResponse`` redirect.
* Markers emitted in the expected order for submit / close / demo / bad-
  config paths.
* Behavioural CSV is written to ``output_dir/<pid>/task00_questionnaire_*.csv``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tasks.common.lsl_markers import create_demo_outlet

task00 = importlib.import_module("tasks.00_questionnaire.task")


# ----- URL construction -----------------------------------------------------


class TestBuildFormUrl:
    def test_no_prefill_returns_base(self):
        url = task00.build_form_url("https://forms.gle/ABC")
        assert url == "https://forms.gle/ABC"

    def test_prefill_appends_entry(self):
        url = task00.build_form_url(
            "https://forms.gle/ABC",
            participant_entry_id="entry.1234567890",
            participant_id="P001",
        )
        assert url == "https://forms.gle/ABC?entry.1234567890=P001"

    def test_prefill_preserves_existing_query(self):
        url = task00.build_form_url(
            "https://forms.gle/ABC?usp=sf_link",
            participant_entry_id="entry.1234567890",
            participant_id="P001",
        )
        assert url == "https://forms.gle/ABC?usp=sf_link&entry.1234567890=P001"

    def test_prefill_url_encodes_pid(self):
        url = task00.build_form_url(
            "https://forms.gle/ABC",
            participant_entry_id="entry.999",
            participant_id="P 001/abc",
        )
        assert "P%20001%2Fabc" in url

    def test_missing_entry_returns_base(self):
        url = task00.build_form_url(
            "https://forms.gle/ABC",
            participant_entry_id="",
            participant_id="P001",
        )
        assert url == "https://forms.gle/ABC"

    def test_missing_pid_returns_base(self):
        url = task00.build_form_url(
            "https://forms.gle/ABC",
            participant_entry_id="entry.999",
            participant_id=None,
        )
        assert url == "https://forms.gle/ABC"


# ----- Submission detection -------------------------------------------------


class TestIsConfirmationPage:
    """Verify the DOM-based confirmation detector.

    The test uses a minimal fake "window" whose only capability is
    ``evaluate_js(script) -> <value>``. We assert the detector returns
    the JS result as a bool and swallows exceptions.
    """

    class _FakeWindow:
        def __init__(self, js_result):
            self._result = js_result

        def evaluate_js(self, _script):
            if isinstance(self._result, BaseException):
                raise self._result
            return self._result

    def test_js_true_returns_true(self):
        assert task00.is_confirmation_page(self._FakeWindow(True)) is True

    def test_js_false_returns_false(self):
        assert task00.is_confirmation_page(self._FakeWindow(False)) is False

    def test_js_none_returns_false(self):
        assert task00.is_confirmation_page(self._FakeWindow(None)) is False

    def test_eval_exception_is_swallowed(self):
        assert (
            task00.is_confirmation_page(self._FakeWindow(RuntimeError("js err")))
            is False
        )


class TestIsSubmissionUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://docs.google.com/forms/d/e/XYZ/formResponse",
            "https://docs.google.com/forms/d/e/XYZ/formResponse?...",
            "https://docs.google.com/forms/.../formResponse?embedded=true",
        ],
    )
    def test_match(self, url):
        assert task00.is_submission_url(url, "formResponse") is True

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "https://docs.google.com/forms/d/e/XYZ/viewform",
            "https://example.com/",
        ],
    )
    def test_no_match(self, url):
        assert task00.is_submission_url(url, "formResponse") is False


# ----- End-to-end run() -----------------------------------------------------


class TestRun:
    def test_demo_mode_skips_webview(self, tmp_path: Path):
        outlet = create_demo_outlet()
        captured = {"called": False}

        def fake_webview(*args, **kwargs):
            captured["called"] = True
            return True

        task00.run(
            outlet=outlet,
            config={"form_url": "https://forms.gle/ABC", "demo_skip": True},
            participant_id="P001",
            demo=True,
            output_dir=tmp_path,
            _webview_fn=fake_webview,
        )
        assert captured["called"] is False
        csv_files = list((tmp_path / "P001").glob("task00_questionnaire_*.csv"))
        assert len(csv_files) == 1
        content = csv_files[0].read_text()
        assert "task00_start" in content
        assert "task00_demo_skipped" in content
        assert "task00_end" in content

    def test_submit_happy_path(self, tmp_path: Path):
        outlet = create_demo_outlet()

        def fake_webview(url, *, submission_marker, window_title, fullscreen):
            assert "entry.999=P001" in url
            assert submission_marker == "formResponse"
            return True

        task00.run(
            outlet=outlet,
            config={
                "form_url": "https://forms.gle/ABC",
                "prefill_participant_id": True,
                "participant_id_entry_id": "entry.999",
                "submission_url_marker": "formResponse",
                "fullscreen": False,
            },
            participant_id="P001",
            demo=False,
            output_dir=tmp_path,
            _webview_fn=fake_webview,
        )
        csv_files = list((tmp_path / "P001").glob("task00_questionnaire_*.csv"))
        assert len(csv_files) == 1
        events = csv_files[0].read_text()
        for marker in (
            "task00_start",
            "task00_form_open",
            "task00_questionnaire_start",
            "task00_form_submitted",
            "task00_questionnaire_end",
            "task00_end",
        ):
            assert marker in events
        assert "task00_form_closed_without_submit" not in events
        # Start boundary must precede end boundary in the on-disk log.
        lines = events.splitlines()
        start_idx = next(
            i for i, ln in enumerate(lines) if "task00_questionnaire_start" in ln
        )
        end_idx = next(
            i for i, ln in enumerate(lines) if "task00_questionnaire_end" in ln
        )
        assert start_idx < end_idx

    def test_closed_without_submit_raises(self, tmp_path: Path):
        outlet = create_demo_outlet()
        with pytest.raises(RuntimeError, match="closed before the form"):
            task00.run(
                outlet=outlet,
                config={
                    "form_url": "https://forms.gle/ABC",
                    "prefill_participant_id": False,
                },
                participant_id="P002",
                demo=False,
                output_dir=tmp_path,
                _webview_fn=lambda *_a, **_k: False,
            )
        csv_files = list((tmp_path / "P002").glob("task00_questionnaire_*.csv"))
        assert len(csv_files) == 1
        events = csv_files[0].read_text()
        assert "task00_form_closed_without_submit" in events
        assert "task00_end" in events  # finally-block still fires
        # Without a real submission we must NOT emit the questionnaire_end
        # boundary — downstream analysis relies on that marker to confirm
        # the form was completed.
        assert "task00_questionnaire_end" not in events
        assert "task00_questionnaire_start" in events  # form DID open

    def test_placeholder_url_raises_with_clear_message(self, tmp_path: Path):
        outlet = create_demo_outlet()
        with pytest.raises(RuntimeError, match="form_url is not set"):
            task00.run(
                outlet=outlet,
                config={"form_url": "<SET-FORM-URL>"},
                participant_id="P003",
                demo=False,
                output_dir=tmp_path,
                _webview_fn=lambda *_a, **_k: True,
            )
        csv_files = list((tmp_path / "P003").glob("task00_questionnaire_*.csv"))
        assert len(csv_files) == 1
        events = csv_files[0].read_text()
        assert "task00_not_configured" in events

    def test_empty_url_also_raises(self, tmp_path: Path):
        outlet = create_demo_outlet()
        with pytest.raises(RuntimeError, match="form_url is not set"):
            task00.run(
                outlet=outlet,
                config={"form_url": ""},
                participant_id="P004",
                demo=False,
                output_dir=tmp_path,
                _webview_fn=lambda *_a, **_k: True,
            )

    def test_demo_still_runs_if_demo_skip_disabled(self, tmp_path: Path):
        """If the YAML explicitly sets demo_skip=false, demo mode must still
        exercise the webview path. Useful for developing the webview layer
        locally against a throwaway form.
        """
        outlet = create_demo_outlet()
        captured = {"called": False}

        def fake_webview(url, **_kwargs):
            captured["called"] = True
            return True

        task00.run(
            outlet=outlet,
            config={
                "form_url": "https://forms.gle/ABC",
                "demo_skip": False,
            },
            participant_id="P005",
            demo=True,
            output_dir=tmp_path,
            _webview_fn=fake_webview,
        )
        assert captured["called"] is True


# ----- Launcher integration -------------------------------------------------


def test_task00_is_first_in_launcher_order():
    """The questionnaire must run before any Sentiometer-recorded task —
    that's the whole point of the 'RA leaves the room' flow.
    """
    from tasks.launcher import TASK_ORDER

    assert TASK_ORDER[0] == (
        "task00_questionnaire",
        "tasks.00_questionnaire.task",
    )


def test_task00_has_display_name():
    from tasks.launcher import TASK_DISPLAY_NAMES

    assert "task00_questionnaire" in TASK_DISPLAY_NAMES
    assert "Task 00" in TASK_DISPLAY_NAMES["task00_questionnaire"]


def test_task00_config_section_present():
    from tasks.common.config import get_task_config, load_session_config

    cfg = get_task_config(load_session_config(), "task00_questionnaire")
    assert "form_url" in cfg
    assert "submission_url_marker" in cfg
    assert cfg["submission_url_marker"] == "formResponse"
    assert cfg["demo_skip"] is True
