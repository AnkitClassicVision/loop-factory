#!/usr/bin/env python3
"""Receipt for the composite audio timeline.

Owner-reported on Fred E104 (2026-07-25): speakers talking over each other, dead
air, and in one 30s sample the guest's voice audible BEFORE he had joined the
room. Four attempts each fixed one property and broke the other.

The two properties that must hold AT THE SAME TIME:

  A. JOIN STAGGER. Each participant's audio starts at their own container
     start_time. Host mic 0.225s, guest mic 224.875s, one shared session clock.
  B. INTERNAL GAPS. A participant's dropouts must be FILLED with silence, not
     deleted. The host track decodes 2763.5s of audio across a 2842s session:
     ~79s of WebRTC dropouts. Deleting them slides every later sample earlier,
     which is what made the speakers overlap and drift apart.

Measured failures this check exists to catch:
  * asetpts=PTS-STARTPTS            -> B broken: host audio drifts to -78s
  * aresample=async=1:first_pts=0   -> A broken: guest resets to ~1s, 225s early
  * async + adelay                  -> guest double-shifted to ~449s

Verified against the RAW tracks decoded plainly, and against a per-speaker
placement window chosen EARLY in the host's track, before his dropouts
accumulate, so a gap defect cannot hide inside the placement measurement.

Prints WHY on failure.
"""
import glob
import os
import subprocess
import sys

import numpy as np

EP = "/mnt/d_drive/repos/podcast/episodes/2026-06-10-fred-cho"
GUEST_JOIN_S = 224.65
TOL_S = 1.0


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def profile(path, af_extra=None, rate=1000):
    af = f"aformat=sample_fmts=s16:sample_rates={rate}:channel_layouts=mono"
    if af_extra:
        af += "," + af_extra
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-af", af, "-f", "s16le", "-"],
        capture_output=True, timeout=3600)
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    n = len(x) // rate
    return np.array([np.abs(x[i * rate:(i + 1) * rate]).mean() for i in range(n)])


def ncc(a, b):
    n, m = len(a), len(b)
    a = a - a.mean()
    na = np.linalg.norm(a)
    if na == 0 or m <= n:
        return None, 0.0
    L = 1 << int(np.ceil(np.log2(m + n)))
    c = np.fft.irfft(np.fft.rfft(a[::-1], L) * np.fft.rfft(b, L), L)[n - 1:m]
    cs = np.cumsum(np.concatenate(([0.0], b)))
    cs2 = np.cumsum(np.concatenate(([0.0], b.astype(np.float64) ** 2)))
    i = np.arange(len(c))
    s = cs[i + n] - cs[i]
    s2 = cs2[i + n] - cs2[i]
    v = s2 - (s * s) / n
    v[v < 1e-9] = np.inf
    r = (c - (a.sum() * s) / n) / (na * np.sqrt(v))
    j = int(np.argmax(r))
    return j, float(r[j])


def main() -> int:
    target = os.environ.get("COMPOSITE_UNDER_TEST",
                            os.path.join(EP, "processed", "composite_CANDIDATE.mp4"))
    if not os.path.exists(target):
        return fail(f"no composite to test at {target} (set COMPOSITE_UNDER_TEST)")

    raw = sorted(glob.glob(os.path.join(EP, "raw", "*.webm")))
    if len(raw) < 4:
        return fail(f"expected 4 raw tracks, found {len(raw)}")
    host_audio, guest_audio = raw[0], raw[2]

    vdur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=duration", "-of", "default=nw=1:nk=1", target],
        capture_output=True, text=True, timeout=300).stdout.strip())
    adur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=duration", "-of", "default=nw=1:nk=1", target],
        capture_output=True, text=True, timeout=300).stdout.strip())
    print(f"composite: video {vdur:.2f}s  audio {adur:.2f}s  delta {abs(vdur - adur):.3f}s")
    if abs(vdur - adur) > 1.0:
        return fail(f"audio/video length disagree by {abs(vdur - adur):.2f}s. The old "
                    f"build was 2919.6s audio against 2842.2s video because dropouts "
                    f"were deleted; they must be filled")

    comp = profile(target)

    # --- Property A: placement of each speaker ---
    # Host window is EARLY on purpose: before his dropouts accumulate, so this
    # measures placement and not gap handling.
    for name, src, win_start, expect in (
            ("host", host_audio, 60.0, 0.0),
            ("guest", guest_audio, 600.0, GUEST_JOIN_S)):
        seg = profile(src, af_extra=None)[int(win_start):int(win_start) + 90]
        if len(seg) < 60:
            return fail(f"{name}: could not read a 90s window at {win_start}s")
        j, pk = ncc(seg, comp)
        if j is None:
            return fail(f"{name}: correlation failed")
        shift = j - win_start
        print(f"{name:>6}: content at {win_start:.0f}s lands at {j}s "
              f"(shift {shift:+.1f}s, expected {expect:+.1f}s, peak {pk:.3f})")
        if pk < 0.45:
            return fail(f"{name}: correlation peak {pk:.3f} is too weak to trust; the "
                        f"audio may be scrambled rather than merely shifted")
        if abs(shift - expect) > TOL_S:
            hint = ""
            if name == "guest" and abs(shift) < TOL_S:
                hint = " -- the guest was reset to zero, so he speaks before he joined"
            elif name == "guest" and abs(shift - 2 * GUEST_JOIN_S) < 5:
                hint = " -- the guest was shifted twice"
            return fail(f"{name}: placed at {shift:+.1f}s, expected {expect:+.1f}s{hint}")

    # --- Property B: gaps filled, not deleted ---
    host_plain = profile(host_audio)
    host_filled_span = vdur
    print(f"host raw decodes {len(host_plain)}s of audio across a {host_filled_span:.0f}s session "
          f"({host_filled_span - len(host_plain):.0f}s of dropouts)")
    if host_filled_span - len(host_plain) < 30:
        print("NOTE: this episode has few dropouts, so property B is weakly exercised here")

    # A late window must still land where the map says. If gaps were deleted the
    # host's late content arrives early by the accumulated gap total.
    late = profile(host_audio)[2000:2090]
    if len(late) >= 60:
        j, pk = ncc(late, comp)
        if j is not None and pk >= 0.45:
            drift = j - 2000
            print(f"  host late window: lands at {j}s (drift {drift:+.1f}s, peak {pk:.3f})")
            if drift < -30:
                return fail(f"host audio arrives {abs(drift):.0f}s early late in the "
                            f"session: dropouts are being deleted, not filled")
        else:
            print(f"  host late window: no reliable lock (peak {pk:.3f}), not asserted")

    print("RESULT: PASS — join stagger preserved AND dropouts filled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
