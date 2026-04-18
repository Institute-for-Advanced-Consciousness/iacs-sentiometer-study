"""Task 00: pre-session questionnaire via Google Form in an embedded webview.

The Sentiometer is sensitive to multiple consciousnesses in the room, so we
want the RAs to leave before the participant starts the recorded portion.
That means the questionnaire has to be part of the session itself, bracketed
by the same LSL marker stream as every other task, so the RA can launch the
session once and walk out.

We embed the form in a pywebview window so the participant never leaves the
P013 process. pywebview on macOS uses the system WKWebView (no extra
runtime). The form URL is set once in ``config/session_defaults.yaml``; the
participant ID is appended as a prefilled field if the RA has mapped the
Google Form ``entry.<id>`` to the participant-ID question.

Completion is detected by watching the window URL: Google Forms submits to
``…/formResponse``, so when the URL contains ``formResponse`` we close the
window and return. If the participant closes the window without submitting
we raise ``RuntimeError``; the launcher catches it and the session
continues to Task 01 with the failure logged.

Markers (all on ``P013_Task_Markers``):

* ``task00_start`` — task entry
* ``task00_questionnaire_start`` — form is on screen, participant may begin
* ``task00_form_open`` — pywebview window is up with the form URL loaded
  (fired alongside ``task00_questionnaire_start`` — same event, clearer
  label for analysis)
* ``task00_form_submitted`` — Google Form redirect to ``/formResponse``
* ``task00_questionnaire_end`` — paired with ``task00_form_submitted``;
  the "everything after this point is the recorded task suite" boundary
* ``task00_form_closed_without_submit`` — window closed before submit
* ``task00_demo_skipped`` — demo mode bypassed the form entirely
* ``task00_not_configured`` — no ``form_url`` set in YAML (production error)
* ``task00_end`` — task exit

Standalone demo (no launcher)::

    uv run python -m tasks.00_questionnaire.task --participant-id DEMO
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click
from pylsl import StreamOutlet, local_clock

from tasks.common.lsl_markers import create_demo_outlet, send_marker

log = logging.getLogger(__name__)


# ----- URL construction -----------------------------------------------------


def build_form_url(
    base_url: str,
    *,
    participant_entry_id: str | None = None,
    participant_id: str | None = None,
) -> str:
    """Return *base_url* with the participant ID prefilled if configured.

    Google Forms lets you prefill a field by appending ``entry.<id>=<value>``
    to the view URL. The RA generates this ID once by clicking "Get
    pre-filled link" on the form in edit mode — we store it in
    ``task00_questionnaire.participant_id_entry_id`` in the YAML.

    If either *participant_entry_id* or *participant_id* is missing/empty,
    the base URL is returned unchanged so the participant types their ID
    manually.
    """
    if not participant_entry_id or not participant_id:
        return base_url
    sep = "&" if "?" in base_url else "?"
    # safe="" so "/" and other reserved chars get percent-encoded; we're
    # building a query param, not a path.
    return f"{base_url}{sep}{participant_entry_id}={quote(str(participant_id), safe='')}"


def is_submission_url(url: str | None, submission_marker: str) -> bool:
    """Return True if *url* is on Google's ``/formResponse`` endpoint.

    NOTE: a URL on ``/formResponse`` is NECESSARY but not SUFFICIENT to
    conclude the form was submitted. Google Forms POSTs to
    ``/formResponse`` on every page transition in a multi-page form, so
    after the participant clicks "Next" the URL is already
    ``/formResponse`` even though the next page of questions has just
    rendered. The loaded handler pairs this URL check with
    :func:`is_confirmation_page` — which inspects the DOM for the
    "Your response has been recorded" marker — before treating the form
    as genuinely submitted.
    """
    if not url:
        return False
    return submission_marker in url


# JS snippet run after every ``loaded`` event. Returns true when the DOM
# matches Google's "Your response has been recorded" confirmation page —
# i.e. the form is REALLY done. Returns false on intermediate pages that
# also happen to live at ``/formResponse`` (each Next click in a multi-
# page form lands there before the next page is rendered).
#
# Three signals, any one is enough:
#   1. Confirmation text in the body (works across locales that still
#      render the English phrase; most deployments are English).
#   2. "Submit another response" link, which only appears on the
#      confirmation page.
#   3. No remaining interactive inputs at all — a strict fallback for
#      forms whose "Confirmation message" has been customised to
#      something other than the default wording.
_CONFIRMATION_DETECTOR_JS = """
(function() {
    try {
        var bodyText = (document.body && document.body.innerText) || '';
        if (bodyText.indexOf('Your response has been recorded') !== -1) return true;
        if (bodyText.toLowerCase().indexOf('submit another response') !== -1) return true;
        var hasInput = document.querySelector(
            'input:not([type=hidden]):not([type=submit]), textarea, ' +
            '[role=radio], [role=listbox], [role=checkbox], [role=textbox]'
        );
        return !hasInput;
    } catch (e) {
        return false;
    }
})();
"""


def is_confirmation_page(window) -> bool:
    """Evaluate the confirmation-detector JS inside *window*.

    Swallows all exceptions — if the eval fails we default to "not yet",
    which means we wait for another ``loaded`` event rather than
    prematurely closing the form.
    """
    try:
        result = window.evaluate_js(_CONFIRMATION_DETECTOR_JS)
    except Exception:  # noqa: BLE001
        return False
    return bool(result)


# ----- Webview loop ---------------------------------------------------------


def _await_form_submission(
    url: str,
    *,
    submission_marker: str,
    window_title: str,
    fullscreen: bool,
) -> bool:
    """Open *url* in a pywebview window and block until submitted or closed.

    Returns True if the Google Form redirected to ``/formResponse``
    (= successful submit), False if the participant closed the window
    first. The function is separated from :func:`run` so the test suite
    can stub it out without importing pywebview.
    """
    import webview  # noqa: PLC0415 — heavy optional dep, import on demand

    state = {"submitted": False, "destroyed": False}
    window_holder: dict[str, Any] = {}

    def on_loaded() -> None:
        window = window_holder.get("w")
        if window is None or state["destroyed"]:
            return
        try:
            current = window.get_current_url()
        except Exception:  # noqa: BLE001
            current = None
        # Two-gate check: URL must be on /formResponse AND the DOM must
        # look like Google's confirmation page. The URL flips to
        # /formResponse on every Next click in a multi-page form, so
        # without the DOM check we close the window as soon as the
        # participant advances past page 1. See is_submission_url() and
        # is_confirmation_page() for the reasoning.
        if not is_submission_url(current, submission_marker):
            return
        if not is_confirmation_page(window):
            return
        state["submitted"] = True
        state["destroyed"] = True
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    window = webview.create_window(
        window_title,
        url=url,
        fullscreen=fullscreen,
        confirm_close=False,
    )
    window_holder["w"] = window
    window.events.loaded += on_loaded
    webview.start()  # blocks on the calling (main) thread
    return bool(state["submitted"])


# ----- Behavioural log ------------------------------------------------------


def _write_behavioural_log(
    output_dir: Path,
    participant_id: str,
    entries: list[dict],
) -> Path:
    """Persist the per-event log to ``task00_questionnaire_<pid>.csv``."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"task00_questionnaire_{participant_id}_{ts}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["lsl_timestamp", "event", "details"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in entries:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return path


# ----- Entry point ----------------------------------------------------------


def run(
    outlet: StreamOutlet | None = None,
    config: dict | None = None,
    participant_id: str = "DEMO",
    demo: bool = False,
    output_dir: Path | str | None = None,
    *,
    _webview_fn=None,
) -> None:
    """Run the pre-session questionnaire block.

    Parameters mirror every other task in the suite. ``_webview_fn`` is a
    test hook: if provided, it's called in place of
    :func:`_await_form_submission` so the unit tests can simulate submit
    / close outcomes without touching pywebview. Real sessions never set
    it.
    """
    config = config or {}

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    data_dir = Path(output_dir) if output_dir else Path.cwd() / "data"
    participant_dir = data_dir / participant_id

    events: list[dict] = []

    def _log(event: str, details: str = "") -> None:
        events.append(
            {"lsl_timestamp": local_clock(), "event": event, "details": details}
        )

    send_marker(outlet, "task00_start")
    _log("task00_start")

    try:
        # ---- Demo bypass ----
        if demo and config.get("demo_skip", True):
            send_marker(outlet, "task00_demo_skipped")
            _log("task00_demo_skipped")
            return

        # ---- Config validation ----
        base_url = str(config.get("form_url", "")).strip()
        placeholder = base_url.lower().startswith("<") or base_url == ""
        if placeholder:
            send_marker(outlet, "task00_not_configured")
            _log("task00_not_configured", f"form_url={base_url!r}")
            raise RuntimeError(
                "Task 00: form_url is not set in session_defaults.yaml "
                "(task00_questionnaire.form_url). Set the Google Form "
                "'viewform' URL before running a real session."
            )

        entry_id = (
            str(config.get("participant_id_entry_id", "")).strip()
            if config.get("prefill_participant_id", False)
            else ""
        )
        url = build_form_url(
            base_url,
            participant_entry_id=entry_id or None,
            participant_id=participant_id,
        )
        submission_marker = str(config.get("submission_url_marker", "formResponse"))
        window_title = str(config.get("window_title", "P013 Pre-Session Questionnaire"))
        fullscreen = bool(config.get("fullscreen", True))

        send_marker(outlet, "task00_form_open")
        _log("task00_form_open", url)
        # Explicit "questionnaire start" boundary so downstream analysis
        # can cleanly segment the pre-session self-report window on the
        # same P013_Task_Markers stream as every other task boundary.
        # Same event as form_open; kept separate for marker-reference
        # clarity.
        send_marker(outlet, "task00_questionnaire_start")
        _log("task00_questionnaire_start")

        webview_fn = _webview_fn or _await_form_submission
        submitted = webview_fn(
            url,
            submission_marker=submission_marker,
            window_title=window_title,
            fullscreen=fullscreen,
        )

        if submitted:
            send_marker(outlet, "task00_form_submitted")
            _log("task00_form_submitted")
            # Paired with task00_questionnaire_start — everything after
            # this marker is the recorded task suite.
            send_marker(outlet, "task00_questionnaire_end")
            _log("task00_questionnaire_end")
        else:
            send_marker(outlet, "task00_form_closed_without_submit")
            _log("task00_form_closed_without_submit")
            raise RuntimeError(
                "Task 00: questionnaire window closed before the form was "
                "submitted. Ask the participant to complete and submit the "
                "form, then restart with --skip-to 1 to resume from Task 00."
            )

    finally:
        send_marker(outlet, "task00_end")
        _log("task00_end")
        try:
            _write_behavioural_log(participant_dir, participant_id, events)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to write task00 behavioural log: %s", exc)
        if own_outlet:
            del outlet


# ----- Standalone CLI -------------------------------------------------------


@click.command()
@click.option(
    "--participant-id",
    "-p",
    default="DEMO",
    help="Participant ID (prefilled into the Form if entry_id is configured).",
)
@click.option("--demo", is_flag=True, help="Run in demo mode (skips the form).")
@click.option(
    "--form-url",
    default=None,
    help="Override the Google Form viewform URL (bypasses YAML).",
)
@click.option(
    "--entry-id",
    default=None,
    help="Google Form entry.<id> for the participant-ID field (enables prefill).",
)
def _cli(
    participant_id: str,
    demo: bool,
    form_url: str | None,
    entry_id: str | None,
) -> None:
    """Run Task 00 standalone (uses default YAML unless overridden)."""
    from tasks.common.config import get_task_config, load_session_config  # noqa: PLC0415

    cfg = get_task_config(load_session_config(), "task00_questionnaire")
    if form_url:
        cfg["form_url"] = form_url
    if entry_id:
        cfg["participant_id_entry_id"] = entry_id
        cfg["prefill_participant_id"] = True

    run(
        config=cfg,
        participant_id=participant_id,
        demo=demo,
        output_dir=Path.cwd() / "data",
    )


if __name__ == "__main__":
    _cli()
