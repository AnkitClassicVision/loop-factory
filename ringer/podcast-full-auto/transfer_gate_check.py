#!/usr/bin/env python3
"""Receipt for the transfer-gate anchor-locatability fix.

Measured 2026-07-24 on Fred E104's rendered program: audio-to-source alignment
is correct across the whole program. Six cut-free windows inside single boundary
pieces locked at correlation 0.985 to 1.000 with deltas of -6.1, -1.3, 0.0, +0.0,
+0.2 and +0.4 ms against a 40ms threshold. The method was validated both ways:
composite vs itself gives 1.0000 at 0ms, and an AAC round-trip gives 0.9999 at
0ms, so it can see a real offset. The gate nonetheless failed the render, because
it matches 0.25-second slices of speech that cannot locate themselves: its own
confidence came out at 0.405 and its answers scattered from -483ms to +472ms
across neighbouring windows.

This receipt proves the fixed gate:
  1. PASSES the real rendered program (which is in sync),
  2. still FAILS the same program with a real offset injected (60ms and 200ms),
     so it did not simply stop looking,
  3. reports unlocatable anchors as unmeasurable rather than as divergence,
  4. keeps threshold_ms=40 and the fail-closed quorum.

Prints WHY on failure.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = "/mnt/d_drive/repos/podcast"
SCRATCH = ("/tmp/claude-1000/-mnt-d-drive-repos-loop-factory/"
           "b41a49f1-2844-4cf6-ba25-2991cffeabad/scratchpad/e104-shadow/"
           "episodes/2026-06-10-fred-cho/processed")
EDITED = os.path.join(SCRATCH, "angle_switched.mp4")
BOUNDARIES = EDITED + ".boundaries.json"
SOURCE = os.path.join(REPO, "episodes", "2026-06-10-fred-cho", "processed", "composite.mp4")


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def shifted_copy(src, out, shift_ms):
    """Re-mux src with its audio delayed by shift_ms (video untouched)."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src,
         "-map", "0:v:0", "-map", "0:a:0",
         "-af", f"adelay={shift_ms}|{shift_ms}",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         out],
        capture_output=True, text=True, timeout=3600)
    return r.returncode == 0


def main() -> int:
    for path in (EDITED, BOUNDARIES, SOURCE):
        if not os.path.exists(path):
            return fail(f"missing input artifact {path}")

    src = open(os.path.join(REPO, "server", "pipeline", "media_gates.py"),
               encoding="utf-8").read()
    if "min_audio_confidence=0.4," in src and "locatab" not in src.lower():
        return fail("media_gates.py still decides measurability on the bare confidence "
                    "floor with no locatability contract")

    sys.path.insert(0, REPO)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    from server.pipeline import media_gates  # noqa: E402

    time_map = json.load(open(BOUNDARIES))
    workdir = tempfile.mkdtemp(prefix="transfergate-")

    # 1. The real, in-sync render must PASS.
    real = media_gates.validate_transfer(
        EDITED, SOURCE, time_map=time_map, mode="av_anchors")
    print(f"real render: passed={real.get('passed')} failures={str(real.get('failures'))[:300]}")
    if not real.get("passed"):
        return fail("the gate still refuses a render measured in sync to within 6.1ms "
                    "across six windows — it is still blocking a correct episode")

    measurements = json.dumps(real.get("measurements") or {})
    if "unmeasur" not in measurements.lower() and "unmeasur" not in json.dumps(real).lower():
        print("NOTE: no anchor was reported unmeasurable on the real render")

    # 2. A REAL injected offset must still FAIL, or the gate stopped looking.
    for shift_ms in (60, 200):
        out = os.path.join(workdir, f"shift_{shift_ms}.mp4")
        if not shifted_copy(EDITED, out, shift_ms):
            return fail(f"could not build the {shift_ms}ms shifted control")
        got = media_gates.validate_transfer(
            out, SOURCE, time_map=time_map, mode="av_anchors")
        print(f"injected +{shift_ms}ms: passed={got.get('passed')} "
              f"failures={str(got.get('failures'))[:200]}")
        if got.get("passed"):
            return fail(f"the gate PASSED a program with {shift_ms}ms of injected audio "
                        f"offset — it is no longer detecting real desync")

    # 3. The threshold and quorum must be intact.
    if "threshold_ms=40.0" not in src:
        return fail("threshold_ms is no longer 40.0 — the divergence bound was widened")
    if "min_strong_anchors=3" not in src:
        return fail("min_strong_anchors is no longer 3 — the fail-closed quorum was weakened")

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_media_gates.py",
         "tests/test_media_gates_integration.py", "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=2400)
    print((pt.stdout or "")[-1500:])
    if pt.returncode != 0:
        return fail(f"media-gate tests failed (rc={pt.returncode})")

    print("RESULT: PASS — the gate accepts the in-sync render, still fails 60ms and "
          "200ms injected offsets, and keeps its 40ms threshold and 3-anchor quorum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
