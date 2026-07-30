#!/usr/bin/env python3
"""Prove that a finished program is mastered at a correct, safe level.

This receipt measures whole-program integrated loudness and true peak with
FFmpeg's EBU R128 filter. It never substitutes momentary or short-term
loudness for the integrated result.

Exit codes:
  0  measured loudness and true peak both pass
  1  a measured limit is violated
  2  the input or measurement is unusable
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys


# -16 LUFS is the common Apple/Spotify podcast mastering norm.
DEFAULT_TARGET_LUFS = -16.0
# +/-1.5 LU permits normal mastering variation without accepting a quiet or
# aggressively loud program.
DEFAULT_TOLERANCE_LU = 1.5
# -1 dBTP leaves headroom for lossy platform encoding and prevents clipping.
DEFAULT_MAX_TRUE_PEAK_DBTP = -1.0

NUMBER = r"(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)|[+-]?(?:inf|nan))"


def audio_streams(path):
    """Return audio streams reported by ffprobe."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index,codec_type",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RuntimeError("ffprobe could not run: %s" % exc) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffprobe returned no diagnostic"
        raise RuntimeError("ffprobe could not inspect the file: %s" % detail)
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("ffprobe returned invalid stream data") from exc
    return payload.get("streams", [])


def parse_measurement(stderr):
    """Parse only the final EBU R128 Summary, never per-frame readings."""
    # FFmpeg prefixes filter output with "[Parsed_ebur128_...]", including the
    # Summary line, so anchor on the trailing label rather than line start.
    summary_positions = [
        match.end() for match in re.finditer(r"(?m)\bSummary:\s*$", stderr)
    ]
    if not summary_positions:
        raise RuntimeError("FFmpeg produced no EBU R128 Summary")
    summary = stderr[summary_positions[-1]:]

    integrated_match = re.search(
        r"Integrated loudness:.*?\bI:\s*(%s)\s*LUFS" % NUMBER,
        summary,
        flags=re.DOTALL | re.IGNORECASE,
    )
    lra_match = re.search(
        r"Loudness range:.*?\bLRA:\s*(%s)\s*LU\b" % NUMBER,
        summary,
        flags=re.DOTALL | re.IGNORECASE,
    )
    true_peak_match = re.search(
        r"True peak:.*?\bPeak:\s*(%s)\s*dB(?:FS|TP)\b" % NUMBER,
        summary,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not integrated_match or not true_peak_match:
        raise RuntimeError(
            "FFmpeg's EBU R128 Summary lacks integrated loudness or true peak"
        )

    integrated_lufs = float(integrated_match.group(1))
    true_peak_dbtp = float(true_peak_match.group(1))
    lra_lu = float(lra_match.group(1)) if lra_match else None
    required = (integrated_lufs, true_peak_dbtp)
    if not all(math.isfinite(value) for value in required):
        raise RuntimeError(
            "integrated loudness or true peak is non-finite; "
            "the audio cannot be certified"
        )
    if lra_lu is not None and not math.isfinite(lra_lu):
        lra_lu = None
    return integrated_lufs, lra_lu, true_peak_dbtp


def measure(path):
    """Measure the first audio stream over the complete program."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-map",
        "0:a:0",
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RuntimeError("ffmpeg could not run: %s" % exc) from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        last_line = detail[-1] if detail else "ffmpeg returned no diagnostic"
        raise RuntimeError("ffmpeg could not measure the audio: %s" % last_line)
    return parse_measurement(result.stderr), command


def write_json(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise RuntimeError("could not write JSON receipt %s: %s" % (path, exc)) from exc


def unusable(message, args, payload=None):
    print("UNUSABLE: %s" % message)
    print("WHY: a missing or unmeasurable audio artifact is not evidence of a pass.")
    if args.json_out:
        receipt = payload or {}
        receipt.update(
            {
                "final": args.final,
                "status": "unusable",
                "exit_code": 2,
                "why": message,
            }
        )
        try:
            write_json(args.json_out, receipt)
        except RuntimeError as exc:
            print("UNUSABLE: %s" % exc)
    return 2


def main():
    parser = argparse.ArgumentParser(
        description="Receipt gate for whole-program podcast master loudness."
    )
    parser.add_argument("--final", required=True)
    parser.add_argument(
        "--target-lufs", type=float, default=DEFAULT_TARGET_LUFS
    )
    parser.add_argument(
        "--tolerance-lu", type=float, default=DEFAULT_TOLERANCE_LU
    )
    parser.add_argument(
        "--max-true-peak-dbtp",
        type=float,
        default=DEFAULT_MAX_TRUE_PEAK_DBTP,
    )
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args()

    limits = (args.target_lufs, args.tolerance_lu, args.max_true_peak_dbtp)
    if not all(math.isfinite(value) for value in limits):
        return unusable("all numeric limits must be finite", args)
    if args.tolerance_lu < 0.0:
        return unusable("--tolerance-lu must be zero or greater", args)
    if not os.path.isfile(args.final):
        return unusable("final program does not exist: %s" % args.final, args)

    try:
        streams = audio_streams(args.final)
    except RuntimeError as exc:
        return unusable(str(exc), args)
    if not streams:
        return unusable("final program has no audio stream: %s" % args.final, args)

    try:
        measurements, command = measure(args.final)
    except RuntimeError as exc:
        return unusable(str(exc), args)
    integrated_lufs, lra_lu, true_peak_dbtp = measurements

    minimum_lufs = args.target_lufs - args.tolerance_lu
    maximum_lufs = args.target_lufs + args.tolerance_lu
    loudness_pass = minimum_lufs <= integrated_lufs <= maximum_lufs
    peak_pass = true_peak_dbtp <= args.max_true_peak_dbtp

    loudness_status = "PASS" if loudness_pass else "FAIL"
    peak_status = "PASS" if peak_pass else "FAIL"
    print(
        "INTEGRATED_LOUDNESS: %.3f LUFS | allowed %.3f to %.3f LUFS | %s"
        % (integrated_lufs, minimum_lufs, maximum_lufs, loudness_status)
    )
    if not loudness_pass:
        direction = "too quiet" if integrated_lufs < minimum_lufs else "too loud"
        print(
            "WHY: the whole-program integrated loudness is %s for the "
            "%.3f LUFS target +/- %.3f LU."
            % (direction, args.target_lufs, args.tolerance_lu)
        )

    if lra_lu is None:
        print("LOUDNESS_RANGE: unavailable | INFO (not a gated limit)")
    else:
        print("LOUDNESS_RANGE: %.3f LU | INFO (not a gated limit)" % lra_lu)

    print(
        "TRUE_PEAK: %.3f dBTP | maximum %.3f dBTP | %s"
        % (true_peak_dbtp, args.max_true_peak_dbtp, peak_status)
    )
    if not peak_pass:
        print(
            "WHY: true peak exceeds the %.3f dBTP safety ceiling, risking "
            "clipping or platform encoding penalties."
            % args.max_true_peak_dbtp
        )

    passed = loudness_pass and peak_pass
    receipt = {
        "final": args.final,
        "measurement": {
            "integrated_lufs": round(integrated_lufs, 3),
            "loudness_range_lu": (
                round(lra_lu, 3) if lra_lu is not None else None
            ),
            "true_peak_dbtp": round(true_peak_dbtp, 3),
            "scope": "whole program, first audio stream",
            "standard": "EBU R128",
        },
        "limits": {
            "target_lufs": args.target_lufs,
            "tolerance_lu": args.tolerance_lu,
            "minimum_lufs": minimum_lufs,
            "maximum_lufs": maximum_lufs,
            "max_true_peak_dbtp": args.max_true_peak_dbtp,
        },
        "checks": {
            "integrated_loudness": {
                "pass": loudness_pass,
                "why": (
                    "within the target window"
                    if loudness_pass
                    else "outside the target window"
                ),
            },
            "true_peak": {
                "pass": peak_pass,
                "why": (
                    "at or below the safety ceiling"
                    if peak_pass
                    else "above the safety ceiling"
                ),
            },
        },
        "ffmpeg_command": command,
        "status": "pass" if passed else "fail",
        "exit_code": 0 if passed else 1,
    }
    if args.json_out:
        try:
            write_json(args.json_out, receipt)
        except RuntimeError as exc:
            return unusable(str(exc), args, receipt)

    print()
    if not passed:
        failures = []
        if not loudness_pass:
            failures.append("integrated loudness")
        if not peak_pass:
            failures.append("true peak")
        print("FAIL: measured master violates %s limit(s)." % " and ".join(failures))
        return 1

    print(
        "PASS: integrated loudness and true peak are within the master limits."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
