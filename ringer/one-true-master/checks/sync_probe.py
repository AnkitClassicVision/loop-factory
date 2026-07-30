#!/usr/bin/env python3
"""Measure whether a rendered program is still on the session grid.

The idea: you do not need to re-render an episode to know whether its timing
is right. Take a short window of a known-good stem at session time T, find
where that audio ACTUALLY lands in the rendered artifact by cross-correlation,
and report the lag. A grid-conformant render has lag ~0 at every probe point.

Each probe costs about a second, so a whole episode can be certified in
seconds instead of the ~40 minutes a production render takes.

Usage:
  sync_probe.py REFERENCE TARGET T1 [T2 ...] [--window S] [--search S]
                [--tolerance-ms N] [--json OUT]

  REFERENCE  a stem on the session grid (e.g. stems/host_audio.flac)
  TARGET     the artifact under test (e.g. composite.mp4)
  T1..       session-time probe points, in seconds

Exit 0 only when every probe is locatable AND within tolerance, so this can
be used directly as an executed gate.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

SR = 16000  # plenty for correlation, and keeps the extraction cheap


def extract(path, start_s, duration_s, dest):
    """Pull a mono 16 kHz PCM window. Accurate seek (-ss after -i is slower
    but exact); these windows are short so the cost is fine."""
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", "%.3f" % max(0.0, start_s),
        "-t", "%.3f" % duration_s,
        "-i", path,
        "-map", "a:0",
        "-ac", "1", "-ar", str(SR),
        "-f", "f32le", dest,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    data = np.fromfile(dest, dtype=np.float32)
    return data


def normalized_xcorr(needle, haystack):
    """Return (lag_samples, peak, second_peak) for needle inside haystack."""
    needle = needle - needle.mean()
    haystack = haystack - haystack.mean()
    n_energy = np.sqrt((needle ** 2).sum())
    if n_energy == 0:
        return None, 0.0, 0.0
    needle = needle / n_energy

    # FFT correlation over the full haystack.
    size = 1
    while size < len(haystack) + len(needle):
        size *= 2
    spec = np.fft.rfft(haystack, size) * np.conj(np.fft.rfft(needle, size))
    corr = np.fft.irfft(spec, size)[: len(haystack) - len(needle) + 1]

    # Normalize by local haystack energy so a loud passage cannot fake a peak.
    cumulative = np.concatenate([[0.0], np.cumsum(haystack ** 2)])
    windows = cumulative[len(needle):] - cumulative[: len(corr)]
    windows = np.sqrt(np.maximum(windows, 1e-12))
    corr = corr / windows

    best = int(np.argmax(corr))
    peak = float(corr[best])

    # Second-best peak outside a guard band, to prove the match is distinctive.
    guard = int(0.25 * SR)
    masked = corr.copy()
    lo, hi = max(0, best - guard), min(len(corr), best + guard + 1)
    masked[lo:hi] = -np.inf
    second = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    return best, peak, second


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("target")
    ap.add_argument("points", nargs="+", type=float)
    ap.add_argument("--window", type=float, default=8.0,
                    help="seconds of reference audio per probe")
    ap.add_argument("--search", type=float, default=12.0,
                    help="+/- seconds of target searched around the point")
    ap.add_argument("--tolerance-ms", type=float, default=40.0)
    ap.add_argument("--min-peak", type=float, default=0.35,
                    help="correlation floor below which a probe is UNLOCATABLE")
    ap.add_argument("--min-margin", type=float, default=1.25,
                    help="peak must beat the runner-up by this ratio")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    for path in (args.reference, args.target):
        if not os.path.isfile(path):
            print("FAIL: missing input: %s" % path)
            return 1

    results = []
    worst = 0.0
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        for point in args.points:
            ref_raw = os.path.join(tmp, "ref.raw")
            tgt_raw = os.path.join(tmp, "tgt.raw")
            search_start = max(0.0, point - args.search)
            try:
                needle = extract(args.reference, point, args.window, ref_raw)
                haystack = extract(args.target, search_start,
                                   args.window + 2 * args.search, tgt_raw)
            except subprocess.CalledProcessError as exc:
                failures.append("t=%.1fs extraction failed: %s"
                                % (point, exc.stderr.decode()[:200]))
                continue

            if len(needle) < SR or len(haystack) <= len(needle):
                failures.append(
                    "t=%.1fs not enough audio (needle=%d, haystack=%d samples)"
                    % (point, len(needle), len(haystack)))
                continue

            lag, peak, second = normalized_xcorr(needle, haystack)
            if lag is None:
                failures.append("t=%.1fs reference window is silent" % point)
                continue

            found_at = search_start + lag / SR
            delta_ms = (found_at - point) * 1000.0
            margin = peak / second if second > 0 else float("inf")
            locatable = peak >= args.min_peak and margin >= args.min_margin

            row = {
                "session_s": point,
                "found_at_s": round(found_at, 4),
                "delta_ms": round(delta_ms, 1),
                "peak": round(peak, 3),
                "margin": round(margin, 2) if np.isfinite(margin) else None,
                "locatable": locatable,
            }
            results.append(row)

            status = "ok"
            if not locatable:
                status = "UNLOCATABLE"
                failures.append(
                    "t=%.1fs UNLOCATABLE (peak=%.3f < %.2f or margin=%.2f < %.2f)"
                    " — cannot certify this point, treat as unmeasured, not passed"
                    % (point, peak, args.min_peak,
                       margin if np.isfinite(margin) else 99, args.min_margin))
            elif abs(delta_ms) > args.tolerance_ms:
                status = "DRIFT"
                failures.append(
                    "t=%.1fs DRIFT %+.1fms exceeds +/-%.1fms (audio found at %.4fs)"
                    % (point, delta_ms, args.tolerance_ms, found_at))
            if locatable:
                worst = max(worst, abs(delta_ms))

            print("t=%-8.1f found=%-10.4f delta=%+8.1fms  peak=%.3f  margin=%-6s %s"
                  % (point, found_at, delta_ms, peak,
                     ("%.2f" % margin) if np.isfinite(margin) else "inf", status))

    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump({"reference": args.reference, "target": args.target,
                       "tolerance_ms": args.tolerance_ms,
                       "probes": results, "failures": failures}, handle, indent=2)

    print()
    if failures:
        print("FAIL: %d of %d probes did not certify" % (len(failures), len(args.points)))
        for line in failures:
            print("  - " + line)
        return 1

    print("PASS: %d probes, all locatable, worst drift %.1fms within +/-%.1fms"
          % (len(results), worst, args.tolerance_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
