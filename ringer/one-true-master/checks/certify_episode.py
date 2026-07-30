#!/usr/bin/env python3
"""Certify that a rendered program is still on the session grid.

The manual probe needed a human to pick which speaker to reference at each
point: on 2026-07-26 two of five probes came back UNLOCATABLE purely because the
host was silent there (-79.9 dB and -87.3 dB) and there was nothing to
correlate. Correct behavior, useless as a gate.

This picks the reference automatically. For each probe point it measures both
speakers' stems, references whichever one is actually talking, and reports the
point as UNUSABLE only when neither is. A point where nobody speaks is not a
failure, it is an absence of evidence, and the two are never conflated.

Exit 0 only when enough points certify AND none of them drifted, so this can be
wired directly as a gate.

Usage:
  certify_episode.py --stems DIR --target FILE [--points N] [--tolerance-ms MS]
                     [--min-points N] [--window S] [--search S] [--json OUT]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

SR = 16000
SILENCE_DBFS = -55.0  # below this a stem is not carrying speech worth matching


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def extract(path, start_s, dur_s, dest):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0.0, start_s),
         "-t", "%.3f" % dur_s, "-i", path, "-map", "a:0", "-ac", "1",
         "-ar", str(SR), "-f", "f32le", dest],
        check=True, capture_output=True)
    return np.fromfile(dest, dtype=np.float32)


def dbfs(samples):
    if samples.size == 0:
        return -np.inf
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20.0 * np.log10(rms) if rms > 0 else -np.inf


def locate(needle, haystack):
    """Normalized cross-correlation. Returns (lag_samples, peak, runner_up)."""
    needle = needle - needle.mean()
    haystack = haystack - haystack.mean()
    energy = np.sqrt((needle ** 2).sum())
    if energy == 0:
        return None, 0.0, 0.0
    needle = needle / energy

    size = 1
    while size < len(haystack) + len(needle):
        size *= 2
    corr = np.fft.irfft(
        np.fft.rfft(haystack, size) * np.conj(np.fft.rfft(needle, size)), size
    )[: len(haystack) - len(needle) + 1]

    cum = np.concatenate([[0.0], np.cumsum(haystack ** 2)])
    win = np.sqrt(np.maximum(cum[len(needle):] - cum[: len(corr)], 1e-12))
    corr = corr / win

    best = int(np.argmax(corr))
    peak = float(corr[best])
    guard = int(0.25 * SR)
    masked = corr.copy()
    masked[max(0, best - guard):min(len(corr), best + guard + 1)] = -np.inf
    runner = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    return best, peak, runner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stems", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--points", type=int, default=12)
    ap.add_argument("--min-points", type=int, default=8)
    ap.add_argument("--tolerance-ms", type=float, default=40.0)
    ap.add_argument("--window", type=float, default=8.0)
    ap.add_argument("--search", type=float, default=15.0)
    ap.add_argument("--min-peak", type=float, default=0.35)
    ap.add_argument("--min-margin", type=float, default=1.25)
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    refs = {}
    for role in ("host", "guest"):
        for ext in ("flac", "wav", "m4a"):
            p = os.path.join(args.stems, "%s_audio.%s" % (role, ext))
            if os.path.isfile(p):
                refs[role] = p
                break
    if not refs:
        print("FAIL: no host_audio/guest_audio stem found under %s" % args.stems)
        return 1
    if not os.path.isfile(args.target):
        print("FAIL: target does not exist: %s" % args.target)
        return 1

    duration = min(probe_duration(p) for p in refs.values())
    # Spread points across the body, avoiding the very edges where a window
    # would run off the end.
    margin = args.window + 2.0
    span = duration - 2 * margin
    if span <= 0:
        print("FAIL: stems are only %.1fs, too short to probe" % duration)
        return 1
    points = [margin + span * i / (args.points - 1) for i in range(args.points)]

    rows, certified, unusable, drifted = [], 0, 0, []
    worst = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        for point in points:
            # Reference whichever speaker is actually talking here.
            best_role, best_level, best_needle = None, -np.inf, None
            for role, path in refs.items():
                needle = extract(path, point, args.window,
                                 os.path.join(tmp, "n.raw"))
                level = dbfs(needle)
                if level > best_level:
                    best_role, best_level, best_needle = role, level, needle

            if best_level < SILENCE_DBFS:
                unusable += 1
                rows.append({"session_s": round(point, 2), "status": "no-speech",
                             "loudest_dbfs": round(float(best_level), 1)})
                print("t=%-8.1f no speech on either stem (%.1f dBFS) — not evidence, skipped"
                      % (point, best_level))
                continue

            start = max(0.0, point - args.search)
            hay = extract(args.target, start, args.window + 2 * args.search,
                          os.path.join(tmp, "h.raw"))
            if len(hay) <= len(best_needle):
                unusable += 1
                rows.append({"session_s": round(point, 2), "status": "short-target"})
                continue

            lag, peak, runner = locate(best_needle, hay)
            found = start + lag / SR
            delta = (found - point) * 1000.0
            margin_ratio = peak / runner if runner > 0 else float("inf")
            ok = peak >= args.min_peak and margin_ratio >= args.min_margin

            row = {"session_s": round(point, 2), "ref": best_role,
                   "ref_dbfs": round(float(best_level), 1),
                   "found_at_s": round(found, 4), "delta_ms": round(delta, 1),
                   "peak": round(peak, 3)}
            if not ok:
                unusable += 1
                row["status"] = "unlocatable"
                print("t=%-8.1f ref=%-5s UNLOCATABLE peak=%.3f — not evidence, skipped"
                      % (point, best_role, peak))
            elif abs(delta) > args.tolerance_ms:
                certified += 1
                drifted.append((point, best_role, delta))
                worst = max(worst, abs(delta))
                row["status"] = "drift"
                print("t=%-8.1f ref=%-5s delta=%+8.1fms  DRIFT" % (point, best_role, delta))
            else:
                certified += 1
                worst = max(worst, abs(delta))
                row["status"] = "ok"
                print("t=%-8.1f ref=%-5s delta=%+8.1fms  ok" % (point, best_role, delta))
            rows.append(row)

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"stems": args.stems, "target": args.target,
                       "tolerance_ms": args.tolerance_ms, "probes": rows}, fh, indent=2)

    print()
    if drifted:
        print("FAIL: %d probe(s) drifted beyond +/-%.1fms, worst %.1fms"
              % (len(drifted), args.tolerance_ms, worst))
        for point, role, delta in drifted:
            print("  - t=%.1fs ref=%s delta=%+.1fms" % (point, role, delta))
        return 1
    if certified < args.min_points:
        print("FAIL: only %d of %d probes were usable, need at least %d. "
              "%d had no speech or could not be located.\n"
              "Too little evidence to certify is NOT a pass."
              % (certified, len(points), args.min_points, unusable))
        return 1

    print("PASS: %d of %d probes certified, worst drift %.1fms within +/-%.1fms. "
          "%d skipped as no-evidence." % (certified, len(points), worst,
                                          args.tolerance_ms, unusable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
