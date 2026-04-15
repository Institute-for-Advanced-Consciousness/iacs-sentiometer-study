#!/usr/bin/env python3
"""
vayl_lsl_bridge.py — Trigger Vayl SSVEP stimulation + push LSL markers.

Controls the Vayl desktop app's full-screen checkerboard overlay from Python
and streams timestamped events + real-time frequency to LSL.

SETUP:
    pip install pylsl

FREQUENCY NOTE:
    Pattern-reversal checkerboard → effective SSVEP = 2x carrier.
      40 Hz stim → --start-hz 20
       1 Hz stim → --end-hz 0.5
    LSL streams report the doubled (effective) frequency automatically.

QUICK START:
    python vayl_lsl_bridge.py --start-hz 20 --end-hz 0.5 --duration 10 \\
                              --lsl-stream VaylStim --wait

USAGE (as library):
    from vayl_lsl_bridge import VaylBridge
    bridge = VaylBridge(lsl_stream_name="VaylStim")
    bridge.start_ramp(start_hz=20, end_hz=0.5, duration_seconds=10)
    bridge.wait_for_ramp(10)
    bridge.turn_off()

LSL STREAMS (two outlets created per --lsl-stream name):
    "{name}"      — Marker stream (irregular, string, JSON events)
    "{name}_Freq" — Continuous effective SSVEP Hz (250 samp/s, float32)

    wallTimeMs in marker JSON = server-side epoch ms at native GPU call
    (~1-5 ms before LSL push; sub-ms accurate to actual visual onset).

API (http://127.0.0.1:9471, localhost only):
    POST /api/carrier-ramp/start  {"startHz", "endHz", "durationSeconds"}
    POST /api/carrier-ramp/stop
    POST /api/overlay/off         (500ms fade-out)
    GET  /api/status
"""

import argparse
import json
import sys
import threading
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Default API endpoint (Vayl desktop app, localhost only)
# ---------------------------------------------------------------------------
VAYL_API_URL = "http://127.0.0.1:9471"

# ---------------------------------------------------------------------------
# SSVEP frequency multiplier: pattern-reversal checkerboard produces TWO
# visual events per carrier cycle (black→white AND white→black), so the
# effective SSVEP stimulation frequency is 2× the carrier frequency.
# All LSL outputs (markers + continuous stream) report the effective rate.
# ---------------------------------------------------------------------------
SSVEP_FREQ_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Optional: pylsl for LSL marker streaming
# ---------------------------------------------------------------------------
try:
    from pylsl import StreamInfo, StreamOutlet, local_clock
    HAS_LSL = True
except ImportError:
    HAS_LSL = False


class VaylBridge:
    """
    Bridge between a Python research script and the Vayl desktop app.

    Triggers carrier-frequency ramps via the local HTTP API and optionally
    pushes timestamped markers to an LSL stream for EEG alignment.

    Parameters
    ----------
    api_url : str
        Base URL of the Vayl API (default: http://127.0.0.1:9471).
    api_secret : str or None
        Bearer token for API auth. Only needed if VAYL_API_SECRET is set
        in the Vayl app's environment. Pass None for no auth.
    lsl_stream_name : str or None
        If provided, two LSL outlets are created:
        1. "{name}" — irregular marker stream (start/stop/off events)
        2. "{name}_Freq" — continuous float stream with carrier Hz
        Pass None to skip LSL entirely.
    lsl_stream_type : str
        LSL stream type for the marker stream (default: "Markers").
    lsl_data_rate : int
        Sample rate in Hz for the continuous frequency stream (default: 250).
    """

    def __init__(
        self,
        api_url=VAYL_API_URL,
        api_secret=None,
        lsl_stream_name=None,
        lsl_stream_type="Markers",
        lsl_data_rate=250,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_secret = api_secret
        self.outlet = None
        self._freq_outlet = None
        self._freq_thread = None
        self._freq_stop = threading.Event()
        self._lsl_data_rate = lsl_data_rate

        # ── Create LSL outlets if requested ───────────────────────────
        if lsl_stream_name:
            if not HAS_LSL:
                raise ImportError(
                    "pylsl is required for LSL streaming. "
                    "Install it with:  pip install pylsl"
                )
            # Marker stream — irregular rate, string channel (JSON events)
            info = StreamInfo(
                name=lsl_stream_name,
                type=lsl_stream_type,
                channel_count=1,
                nominal_srate=0,        # irregular rate (marker stream)
                channel_format="string",
                source_id=f"vayl_bridge_{lsl_stream_name}",
            )
            self.outlet = StreamOutlet(info)

            # Continuous frequency stream — regular rate, float channel (Hz)
            freq_info = StreamInfo(
                name=f"{lsl_stream_name}_Freq",
                type="Stimulus",
                channel_count=1,
                nominal_srate=lsl_data_rate,
                channel_format="float32",
                source_id=f"vayl_freq_{lsl_stream_name}",
            )
            self._freq_outlet = StreamOutlet(freq_info)

            print(f"[VaylBridge] LSL outlets created:")
            print(f"  Markers: '{lsl_stream_name}' (type={lsl_stream_type})")
            print(f"  Freq:    '{lsl_stream_name}_Freq' "
                  f"(type=Stimulus, {lsl_data_rate} Hz)")

    # ------------------------------------------------------------------
    # Continuous frequency stream (background thread)
    # ------------------------------------------------------------------

    def _stream_frequency_loop(self, start_hz, end_hz, duration_seconds):
        """Background thread: push computed carrier frequency at regular rate.

        While the ramp is running, pushes the linearly-interpolated frequency.
        After the ramp finishes, keeps pushing end_hz (overlay is still on)
        until _freq_stop is signalled by turn_off() or stop_ramp().
        """
        rate = self._lsl_data_rate
        interval = 1.0 / rate
        start_time = local_clock()

        while not self._freq_stop.is_set():
            now = local_clock()
            elapsed = now - start_time

            if elapsed >= duration_seconds:
                # Ramp finished — overlay still on at end frequency
                freq = end_hz
            else:
                # Linear interpolation between start and end
                freq = start_hz + (end_hz - start_hz) * (elapsed / duration_seconds)

            self._freq_outlet.push_sample([freq], now)

            # Sleep until next sample (interruptible via _freq_stop)
            next_sample = start_time + (int(elapsed * rate) + 1) * interval
            sleep_dur = next_sample - local_clock()
            if sleep_dur > 0:
                self._freq_stop.wait(sleep_dur)

    def _start_freq_stream(self, start_hz, end_hz, duration_seconds):
        """Spawn the frequency streaming thread."""
        self._stop_freq_stream()
        self._freq_stop.clear()
        self._freq_thread = threading.Thread(
            target=self._stream_frequency_loop,
            args=(start_hz, end_hz, duration_seconds),
            daemon=True,
        )
        self._freq_thread.start()

    def _stop_freq_stream(self):
        """Stop the frequency streaming thread and push a final 0 Hz sample."""
        if self._freq_thread and self._freq_thread.is_alive():
            self._freq_stop.set()
            self._freq_thread.join(timeout=1.0)
            self._freq_thread = None
            # Push 0 Hz to mark overlay-off in the continuous stream
            if self._freq_outlet:
                self._freq_outlet.push_sample([0.0], local_clock())

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, method, path, body=None):
        """Send an HTTP request to the Vayl API and return parsed JSON."""
        url = f"{self.api_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_secret:
            req.add_header("Authorization", f"Bearer {self.api_secret}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Vayl API error {e.code} on {method} {path}: {body_text}"
            )
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot reach Vayl API at {url} — is the app running? ({e})"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def status(self):
        """Check that the Vayl app is running and the API is reachable."""
        return self._request("GET", "/api/status")

    def start_ramp(self, start_hz, end_hz, duration_seconds):
        """
        Start a carrier-frequency ramp on the Vayl overlay.

        The overlay turns on immediately (if not already on) at `start_hz`
        and sweeps to `end_hz` over `duration_seconds`. The overlay stays
        on after the ramp finishes — call turn_off() to fade it out.

        Parameters
        ----------
        start_hz : float
            Starting carrier frequency in Hz (e.g., 40).
        end_hz : float
            Ending carrier frequency in Hz (same as start_hz for constant).
        duration_seconds : float
            Duration of the frequency sweep in seconds.

        Returns
        -------
        dict
            API response with 'status', 'params', and 'timing' fields.
            timing.wallTimeMs is the server-side Unix epoch ms of ramp onset.
        """
        result = self._request("POST", "/api/carrier-ramp/start", {
            "startHz": start_hz,
            "endHz": end_hz,
            "durationSeconds": duration_seconds,
        })

        # ── Push LSL marker ───────────────────────────────────────────
        # Report effective SSVEP frequency (2× carrier for pattern-reversal)
        if self.outlet:
            eff_start = start_hz * SSVEP_FREQ_MULTIPLIER
            eff_end = end_hz * SSVEP_FREQ_MULTIPLIER
            marker = json.dumps({
                "event": "ramp_start",
                "stimFreqHz": eff_start,
                "stimFreqEndHz": eff_end,
                "carrierHz": start_hz,
                "carrierEndHz": end_hz,
                "durationSeconds": duration_seconds,
                "wallTimeMs": result["timing"]["wallTimeMs"],
            })
            self.outlet.push_sample([marker], local_clock())
            print(f"[VaylBridge] LSL marker: ramp_start "
                  f"(stim {eff_start}->{eff_end} Hz, "
                  f"carrier {start_hz}->{end_hz} Hz, {duration_seconds}s)")

        # ── Start continuous frequency stream (effective SSVEP Hz) ────
        if self._freq_outlet:
            eff_start = start_hz * SSVEP_FREQ_MULTIPLIER
            eff_end = end_hz * SSVEP_FREQ_MULTIPLIER
            self._start_freq_stream(eff_start, eff_end, duration_seconds)
            print(f"[VaylBridge] Freq stream: {eff_start}->{eff_end} Hz "
                  f"(effective SSVEP) at {self._lsl_data_rate} samp/s")

        return result

    def stop_ramp(self):
        """
        Stop the currently running carrier ramp immediately.

        Returns
        -------
        dict
            API response with 'status' and 'timing' fields.
        """
        result = self._request("POST", "/api/carrier-ramp/stop")
        self._stop_freq_stream()

        if self.outlet:
            marker = json.dumps({
                "event": "ramp_stop",
                "wallTimeMs": result["timing"]["wallTimeMs"],
            })
            self.outlet.push_sample([marker], local_clock())
            print("[VaylBridge] LSL marker: ramp_stop")

        return result

    def turn_off(self):
        """
        Turn off the overlay with a 500ms fade-out.

        If a carrier ramp is still in progress, it is stopped cleanly
        before the fade begins (no visual snap/artifact). The continuous
        frequency stream pushes a final 0 Hz sample and stops.

        Returns
        -------
        dict
            API response with 'status' and 'timing' fields.
        """
        result = self._request("POST", "/api/overlay/off")
        self._stop_freq_stream()

        if self.outlet:
            marker = json.dumps({
                "event": "overlay_off",
                "wallTimeMs": result["timing"]["wallTimeMs"],
            })
            self.outlet.push_sample([marker], local_clock())
            print("[VaylBridge] LSL marker: overlay_off")

        return result

    def wait_for_ramp(self, duration_seconds, extra_buffer=0.5):
        """
        Block until the ramp is expected to have completed.

        Parameters
        ----------
        duration_seconds : float
            The ramp duration (should match what was passed to start_ramp).
        extra_buffer : float
            Extra seconds to wait after the expected end (default 0.5).
        """
        total = duration_seconds + extra_buffer
        print(f"[VaylBridge] Waiting {total:.1f}s for ramp to complete...")
        time.sleep(total)


# ======================================================================
# CLI entry point
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Trigger Vayl carrier-frequency ramp + push LSL markers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 40 Hz effective SSVEP for 10s (carrier=20 Hz × 2 pattern-reversal):
  python vayl_lsl_bridge.py --start-hz 20 --end-hz 20 --duration 10 \\
                            --lsl-stream VaylStim

  # Ramp from 40 Hz down to 1 Hz effective over 10s, wait for completion:
  python vayl_lsl_bridge.py --start-hz 20 --end-hz 0.5 --duration 10 \\
                            --lsl-stream VaylStim --wait

  # Just trigger, no LSL:
  python vayl_lsl_bridge.py --start-hz 20 --end-hz 0.5 --duration 10
        """,
    )
    parser.add_argument(
        "--start-hz", type=float, required=True,
        help="Start carrier Hz (effective SSVEP = 2×; e.g. 20 for 40 Hz stim)",
    )
    parser.add_argument(
        "--end-hz", type=float, required=True,
        help="End carrier Hz (e.g. 0.5 for 1 Hz stim; same as start-hz for constant)",
    )
    parser.add_argument(
        "--duration", type=float, required=True,
        help="Ramp duration in seconds",
    )
    parser.add_argument(
        "--lsl-stream", type=str, default=None,
        help="LSL stream name to push markers to (omit to skip LSL)",
    )
    parser.add_argument(
        "--api-url", type=str, default=VAYL_API_URL,
        help=f"Vayl API base URL (default: {VAYL_API_URL})",
    )
    parser.add_argument(
        "--api-secret", type=str, default=None,
        help="Bearer token for API auth (only if VAYL_API_SECRET is set in Vayl)",
    )
    parser.add_argument(
        "--wait", action="store_true",
        help="Block until the ramp completes before exiting",
    )
    args = parser.parse_args()

    # ── Initialize bridge ─────────────────────────────────────────────
    try:
        bridge = VaylBridge(
            api_url=args.api_url,
            api_secret=args.api_secret,
            lsl_stream_name=args.lsl_stream,
        )
    except ImportError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # ── Check Vayl is running ─────────────────────────────────────────
    try:
        status = bridge.status()
        print(f"[VaylBridge] Connected to Vayl v{status.get('version', '?')}")
    except (ConnectionError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Make sure the Vayl desktop app is running.", file=sys.stderr)
        return 1

    # ── Start ramp ────────────────────────────────────────────────────
    result = bridge.start_ramp(args.start_hz, args.end_hz, args.duration)

    t = result["timing"]
    eff_start = args.start_hz * SSVEP_FREQ_MULTIPLIER
    eff_end = args.end_hz * SSVEP_FREQ_MULTIPLIER
    print(f"\n  Carrier:       {args.start_hz} -> {args.end_hz} Hz "
          f"over {args.duration}s")
    print(f"  Effective SSVEP: {eff_start} -> {eff_end} Hz "
          f"(2× carrier, pattern-reversal)")
    print(f"  Wall time:     {t['wallTimeISO']}")
    print(f"  Epoch ms:      {t['wallTimeMs']}")
    print(f"  Native call:   {t['nativeCallMs']:.3f} ms")
    print(f"  Ramp ends:     {t['rampEndISO']}")

    # ── Optionally wait for ramp to finish, then turn off ─────────────
    if args.wait:
        bridge.wait_for_ramp(args.duration)
        print("  Ramp complete — turning off overlay...")
        bridge.turn_off()
        print("  Overlay fading out (500ms).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
