#!/usr/bin/env python3
"""Verify that rendered podcast bumpers are present, unique, placed, and level-matched.

The loudness receipt uses ffmpeg's ``loudnorm`` filter in one-pass measurement
mode and reads its ``input_i`` value.  ``loudnorm`` implements EBU R128
integrated loudness, and its JSON output is used because it is machine-readable
and avoids parsing locale-dependent console summaries.

Exit codes:
  0: every measured property passed
  1: at least one measured property was violated
  2: an input or measurement was unusable
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys

import numpy as np


SAMPLE_RATE = 16000  # 16 kHz mono retains ample bumper detail while keeping FFTs tractable.
DEFAULT_TOLERANCE_LU = 3.0  # A 3 LU bumper/body gap is noticeable without being needlessly strict.
EDGE_SEARCH_PADDING_S = 10.0  # Ten seconds catches modest bumper shifts without searching the program.
PLACEMENT_TOLERANCE_S = 0.250  # 250 ms permits codec padding but rejects editorial misplacement.
MIN_CORRELATION_PEAK = 0.35  # Below 0.35, normalized similarity is too weak to prove presence.
MIN_PEAK_MARGIN = 1.25  # The best peak must beat alternatives by 25% to prove one location.
RUNNER_GUARD_S = 0.250  # Nearby samples describe one correlation peak, not competing matches.
REFERENCE_SILENCE_DBFS = -55.0  # Quieter references do not contain enough evidence to match reliably.
LEAK_MIN_CORRELATION_PEAK = 0.65  # A 0.65 body peak is strong evidence of copied bumper audio.
BODY_SCAN_CHUNK_S = 60.0  # One-minute FFT chunks bound memory while covering every body start time.
SUBPROCESS_TIMEOUT_S = 300.0  # Five minutes distinguishes a stuck media probe from a slow decode.
CORRELATION_ENERGY_FLOOR = 1e-12  # This floor prevents zero-energy windows from dividing by zero.
LOUDNORM_TARGET_I = -24.0  # A fixed target enables loudnorm analysis; only measured input_i is consumed.
LOUDNORM_TARGET_LRA = 7.0  # A conventional fixed range keeps measurements comparable across spans.
LOUDNORM_TARGET_TP = -2.0  # A fixed true-peak target is required by loudnorm but does not affect input_i.


class UnusableInput(Exception):
    """Raised when the requested property cannot be measured."""


def seconds(value):
    """Format every timestamp as seconds with exactly three decimals."""
    return "%.3f" % value


def finite_or_none(value):
    return round(float(value), 3) if value is not None and math.isfinite(value) else None


def run_command(command, label):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise UnusableInput("%s: required executable not found: %s" % (label, command[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise UnusableInput(
            "%s: command exceeded %.3f seconds" % (label, SUBPROCESS_TIMEOUT_S)
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        if not detail:
            detail = "media command returned exit %d" % exc.returncode
        raise UnusableInput("%s: %s" % (label, detail)) from exc


def probe_media(path, label):
    if not os.path.isfile(path):
        raise UnusableInput("%s: file does not exist: %s" % (label, path))
    if not os.access(path, os.R_OK):
        raise UnusableInput("%s: file is not readable: %s" % (label, path))

    completed = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index:format=duration",
            "-of",
            "json",
            path,
        ],
        "probe %s" % label,
    )
    try:
        data = json.loads(completed.stdout.decode("utf-8"))
        duration = float(data["format"]["duration"])
        streams = data.get("streams", [])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UnusableInput("%s: ffprobe did not return a usable duration" % label) from exc
    if not streams:
        raise UnusableInput("%s: no readable audio stream" % label)
    if not math.isfinite(duration) or duration <= 0.0:
        raise UnusableInput("%s: invalid duration %r" % (label, duration))
    return duration


def extract_audio(path, start_s=0.0, duration_s=None, label="audio"):
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        seconds(max(0.0, start_s)),
    ]
    if duration_s is not None:
        command.extend(["-t", seconds(max(0.0, duration_s))])
    command.extend(
        [
            "-i",
            path,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    completed = run_command(command, "extract %s" % label)
    samples = np.frombuffer(completed.stdout, dtype=np.float32).copy()
    if samples.size == 0:
        raise UnusableInput("%s: ffmpeg decoded no audio samples" % label)
    return samples


def dbfs(samples):
    if samples.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20.0 * math.log10(rms) if rms > 0.0 else -math.inf


def correlation_scores(needle, haystack):
    """Return normalized FFT cross-correlation for every complete placement."""
    if needle.size == 0 or haystack.size < needle.size:
        return np.empty(0, dtype=np.float64)

    centered_needle = needle.astype(np.float64, copy=False) - float(needle.mean())
    centered_haystack = haystack.astype(np.float64, copy=False) - float(haystack.mean())
    needle_energy = float(np.sqrt(np.sum(centered_needle ** 2)))
    if needle_energy == 0.0:
        return np.empty(0, dtype=np.float64)
    centered_needle = centered_needle / needle_energy

    fft_size = 1
    while fft_size < centered_haystack.size + centered_needle.size:
        fft_size *= 2
    correlation = np.fft.irfft(
        np.fft.rfft(centered_haystack, fft_size)
        * np.conj(np.fft.rfft(centered_needle, fft_size)),
        fft_size,
    )[: centered_haystack.size - centered_needle.size + 1]

    squared = centered_haystack ** 2
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    window_energy = np.sqrt(
        np.maximum(
            cumulative[centered_needle.size :]
            - cumulative[: correlation.size],
            CORRELATION_ENERGY_FLOOR,
        )
    )
    return correlation / window_energy


def best_and_runner(scores):
    """Return the strongest peak and the strongest distinct alternative."""
    if scores.size == 0:
        return None, 0.0, 0.0
    best = int(np.argmax(scores))
    peak = float(scores[best])
    guard = int(round(RUNNER_GUARD_S * SAMPLE_RATE))
    masked = scores.copy()
    masked[max(0, best - guard) : min(scores.size, best + guard + 1)] = -np.inf
    runner = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    return best, peak, runner


def margin_ratio(peak, runner):
    if runner <= 0.0:
        return math.inf if peak > 0.0 else 0.0
    return peak / runner


def locate(needle, haystack):
    best, peak, runner = best_and_runner(correlation_scores(needle, haystack))
    return best, peak, runner, margin_ratio(peak, runner)


def presence_check(name, needle, haystack, window_start_s, expected_start_s, final_duration_s):
    best, peak, runner, margin = locate(needle, haystack)
    needle_duration_s = needle.size / SAMPLE_RATE
    if best is None:
        return {
            "item": "%s_presence" % name,
            "status": "fail",
            "found_start_s": None,
            "found_end_s": None,
            "expected_start_s": seconds(expected_start_s),
            "lag_s": None,
            "peak": round(peak, 3),
            "runner_up": round(runner, 3),
            "peak_margin": finite_or_none(margin),
            "why": "search window is shorter than the reference bumper",
        }

    found_start_s = window_start_s + best / SAMPLE_RATE
    found_end_s = found_start_s + needle_duration_s
    lag_s = found_start_s - expected_start_s
    confident = peak >= MIN_CORRELATION_PEAK and margin >= MIN_PEAK_MARGIN
    placed = abs(lag_s) <= PLACEMENT_TOLERANCE_S
    if name == "outro":
        placed = placed and abs(found_end_s - final_duration_s) <= PLACEMENT_TOLERANCE_S

    if not confident:
        if peak < MIN_CORRELATION_PEAK:
            why = "peak %.3f is below minimum %.3f; bumper presence is unproven" % (
                peak,
                MIN_CORRELATION_PEAK,
            )
        else:
            why = (
                "peak/runner margin %.3f is below minimum %.3f; location is ambiguous"
                % (margin, MIN_PEAK_MARGIN)
            )
        status = "fail"
    elif not placed:
        why = "lag %+.3f seconds exceeds placement tolerance +/-%.3f seconds" % (
            lag_s,
            PLACEMENT_TOLERANCE_S,
        )
        status = "fail"
    else:
        why = "confident match is within the endpoint placement tolerance"
        status = "pass"

    return {
        "item": "%s_presence" % name,
        "status": status,
        "found_start_s": seconds(found_start_s),
        "found_end_s": seconds(found_end_s),
        "expected_start_s": seconds(expected_start_s),
        "lag_s": seconds(lag_s),
        "peak": round(peak, 3),
        "runner_up": round(runner, 3),
        "peak_margin": finite_or_none(margin),
        "why": why,
    }


def scan_body_for_bumper(name, final_path, needle, body_start_s, body_end_s):
    """Scan every possible bumper start in the body using bounded FFT chunks."""
    needle_duration_s = needle.size / SAMPLE_RATE
    latest_start_s = body_end_s - needle_duration_s
    if latest_start_s < body_start_s:
        return {
            "item": "%s_uniqueness" % name,
            "status": "pass",
            "found_start_s": None,
            "peak": 0.0,
            "runner_up": 0.0,
            "peak_margin": None,
            "why": "program body is shorter than this bumper, so a complete copy cannot fit",
        }

    chunks = []
    cursor_s = body_start_s
    while cursor_s <= latest_start_s:
        remaining_start_span_s = latest_start_s - cursor_s
        core_s = min(BODY_SCAN_CHUNK_S, remaining_start_span_s)
        extraction_s = min(
            body_end_s - cursor_s,
            core_s + needle_duration_s + 1.0 / SAMPLE_RATE,
        )
        haystack = extract_audio(
            final_path,
            cursor_s,
            extraction_s,
            "%s body chunk at %s seconds" % (name, seconds(cursor_s)),
        )
        scores = correlation_scores(needle, haystack)
        wanted_candidates = int(round(core_s * SAMPLE_RATE)) + 1
        scores = scores[:wanted_candidates]
        best, peak, runner = best_and_runner(scores)
        if best is not None:
            chunks.append(
                {
                    "peak": peak,
                    "runner": runner,
                    "start_s": cursor_s + best / SAMPLE_RATE,
                }
            )
        if remaining_start_span_s <= BODY_SCAN_CHUNK_S:
            break
        cursor_s += BODY_SCAN_CHUNK_S

    if not chunks:
        raise UnusableInput("%s uniqueness: body scan produced no correlation evidence" % name)

    strongest = max(chunks, key=lambda item: item["peak"])
    guard_s = RUNNER_GUARD_S
    runner_candidates = [strongest["runner"]]
    for chunk in chunks:
        if chunk is strongest:
            continue
        if abs(chunk["start_s"] - strongest["start_s"]) > guard_s:
            runner_candidates.append(chunk["peak"])
        runner_candidates.append(chunk["runner"])
    runner = max(runner_candidates)
    peak = strongest["peak"]
    margin = margin_ratio(peak, runner)
    leaked = peak >= LEAK_MIN_CORRELATION_PEAK
    if leaked:
        status = "fail"
        why = (
            "body peak %.3f is at or above leak threshold %.3f; bumper audio appears in the middle"
            % (peak, LEAK_MIN_CORRELATION_PEAK)
        )
    else:
        status = "pass"
        why = "strongest body peak is below the copied-bumper leak threshold"

    return {
        "item": "%s_uniqueness" % name,
        "status": status,
        "found_start_s": seconds(strongest["start_s"]),
        "peak": round(peak, 3),
        "runner_up": round(runner, 3),
        "peak_margin": finite_or_none(margin),
        "why": why,
    }


def integrated_loudness(path, start_s, duration_s, label):
    command = [
        "ffmpeg",
        "-v",
        "info",
        "-nostdin",
        "-ss",
        seconds(start_s),
        "-t",
        seconds(duration_s),
        "-i",
        path,
        "-map",
        "0:a:0",
        "-af",
        (
            "loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:print_format=json"
            % (LOUDNORM_TARGET_I, LOUDNORM_TARGET_LRA, LOUDNORM_TARGET_TP)
        ),
        "-f",
        "null",
        "-",
    ]
    completed = run_command(command, "measure %s loudness" % label)
    stderr = completed.stderr.decode("utf-8", "replace")
    objects = re.findall(r"\{[^{}]*\}", stderr, flags=re.DOTALL)
    for candidate in reversed(objects):
        try:
            payload = json.loads(candidate)
            measured = float(payload["input_i"])
            return measured
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    raise UnusableInput("%s loudness: loudnorm returned no input_i measurement" % label)


def loudness_checks(final_path, intro_duration_s, outro_duration_s, final_duration_s, tolerance_lu):
    body_start_s = intro_duration_s
    body_end_s = final_duration_s - outro_duration_s
    body_duration_s = body_end_s - body_start_s
    if body_duration_s <= 0.0:
        measurements = {
            "intro_lufs": None,
            "body_lufs": None,
            "outro_lufs": None,
            "intro_delta_lu": None,
            "outro_delta_lu": None,
        }
        return measurements, [
            {
                "item": "levels",
                "status": "fail",
                **measurements,
                "why": "final is too short to contain both bumpers and a program body",
            }
        ]

    intro_lufs = integrated_loudness(final_path, 0.0, intro_duration_s, "intro span")
    body_lufs = integrated_loudness(
        final_path,
        body_start_s,
        body_duration_s,
        "program body",
    )
    outro_lufs = integrated_loudness(
        final_path,
        body_end_s,
        outro_duration_s,
        "outro span",
    )
    values = (intro_lufs, body_lufs, outro_lufs)
    if not all(math.isfinite(value) for value in values):
        measurements = {
            "intro_lufs": finite_or_none(intro_lufs),
            "body_lufs": finite_or_none(body_lufs),
            "outro_lufs": finite_or_none(outro_lufs),
            "intro_delta_lu": None,
            "outro_delta_lu": None,
        }
        return measurements, [
            {
                "item": "levels",
                "status": "fail",
                **measurements,
                "why": "at least one rendered span has no finite integrated loudness",
            }
        ]

    intro_delta_lu = intro_lufs - body_lufs
    outro_delta_lu = outro_lufs - body_lufs
    measurements = {
        "intro_lufs": round(intro_lufs, 3),
        "body_lufs": round(body_lufs, 3),
        "outro_lufs": round(outro_lufs, 3),
        "intro_delta_lu": round(intro_delta_lu, 3),
        "outro_delta_lu": round(outro_delta_lu, 3),
    }
    checks = []
    for name, loudness, delta in (
        ("intro", intro_lufs, intro_delta_lu),
        ("outro", outro_lufs, outro_delta_lu),
    ):
        matched = abs(delta) <= tolerance_lu
        checks.append(
            {
                "item": "%s_level" % name,
                "status": "pass" if matched else "fail",
                "bumper_lufs": round(loudness, 3),
                "body_lufs": round(body_lufs, 3),
                "delta_lu": round(delta, 3),
                "tolerance_lu": round(tolerance_lu, 3),
                "why": (
                    "absolute bumper/body delta is within %.3f LU" % tolerance_lu
                    if matched
                    else "absolute bumper/body delta %.3f LU exceeds tolerance %.3f LU"
                    % (abs(delta), tolerance_lu)
                ),
            }
        )
    return measurements, checks


def print_check(check):
    item = check["item"].upper()
    status = check["status"].upper()
    fields = []
    for key, value in check.items():
        if key in ("item", "status", "why") or value is None:
            continue
        fields.append("%s=%s" % (key, value))
    print("%s %-4s %s" % (item, status, " ".join(fields)))
    if check["status"] != "pass":
        print("  WHY: %s" % check["why"])


def write_json(path, receipt):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise UnusableInput("JSON receipt could not be written to %s: %s" % (path, exc)) from exc


def positive_float(value):
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError("must be a finite number at or above zero")
    return parsed


def build_parser():
    parser = argparse.ArgumentParser(
        description="Prove rendered bumpers are present, endpoint-placed, unique, and level-matched."
    )
    parser.add_argument("--final", required=True, help="final rendered episode")
    parser.add_argument("--intro", required=True, help="intro bumper reference")
    parser.add_argument("--outro", required=True, help="outro bumper reference")
    parser.add_argument(
        "--tolerance-lu",
        type=positive_float,
        default=DEFAULT_TOLERANCE_LU,
        help="maximum absolute bumper/body loudness difference (default: %(default)s)",
    )
    parser.add_argument("--json", dest="json_out", help="optional JSON receipt output")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    receipt = {
        "schema": "bumper_check_receipt_v1",
        "outcome": "error",
        "exit_code": 2,
        "inputs": {
            "final": args.final,
            "intro": args.intro,
            "outro": args.outro,
        },
        "method": {
            "matching": "mono 16 kHz normalized FFT cross-correlation",
            "loudness": "ffmpeg loudnorm one-pass input_i (EBU R128 JSON)",
        },
        "thresholds": {
            "placement_tolerance_s": seconds(PLACEMENT_TOLERANCE_S),
            "minimum_presence_peak": MIN_CORRELATION_PEAK,
            "minimum_peak_margin": MIN_PEAK_MARGIN,
            "minimum_leak_peak": LEAK_MIN_CORRELATION_PEAK,
            "tolerance_lu": round(args.tolerance_lu, 3),
        },
        "measurements": {},
        "checks": [],
        "errors": [],
    }

    try:
        final_duration_s = probe_media(args.final, "final")
        intro_probe_duration_s = probe_media(args.intro, "intro")
        outro_probe_duration_s = probe_media(args.outro, "outro")

        intro_samples = extract_audio(args.intro, label="intro reference")
        outro_samples = extract_audio(args.outro, label="outro reference")
        intro_level_dbfs = dbfs(intro_samples)
        outro_level_dbfs = dbfs(outro_samples)
        if intro_level_dbfs < REFERENCE_SILENCE_DBFS:
            raise UnusableInput(
                "intro reference is %.3f dBFS, below usable threshold %.3f dBFS"
                % (intro_level_dbfs, REFERENCE_SILENCE_DBFS)
            )
        if outro_level_dbfs < REFERENCE_SILENCE_DBFS:
            raise UnusableInput(
                "outro reference is %.3f dBFS, below usable threshold %.3f dBFS"
                % (outro_level_dbfs, REFERENCE_SILENCE_DBFS)
            )

        intro_duration_s = intro_samples.size / SAMPLE_RATE
        outro_duration_s = outro_samples.size / SAMPLE_RATE
        receipt["inputs"].update(
            {
                "final_duration_s": seconds(final_duration_s),
                "intro_probe_duration_s": seconds(intro_probe_duration_s),
                "intro_decoded_duration_s": seconds(intro_duration_s),
                "intro_dbfs": round(intro_level_dbfs, 3),
                "outro_probe_duration_s": seconds(outro_probe_duration_s),
                "outro_decoded_duration_s": seconds(outro_duration_s),
                "outro_dbfs": round(outro_level_dbfs, 3),
            }
        )

        print(
            "INPUT final_duration_s=%s intro_duration_s=%s outro_duration_s=%s"
            % (
                seconds(final_duration_s),
                seconds(intro_duration_s),
                seconds(outro_duration_s),
            )
        )
        print(
            "LOUDNESS_METHOD ffmpeg_loudnorm input_i EBU_R128 JSON: "
            "machine-readable integrated loudness without locale-dependent summaries"
        )

        intro_window_s = min(final_duration_s, intro_duration_s + EDGE_SEARCH_PADDING_S)
        intro_haystack = extract_audio(
            args.final,
            0.0,
            intro_window_s,
            "final intro search window",
        )
        intro_presence = presence_check(
            "intro",
            intro_samples,
            intro_haystack,
            0.0,
            0.0,
            final_duration_s,
        )
        receipt["checks"].append(intro_presence)

        outro_window_s = min(final_duration_s, outro_duration_s + EDGE_SEARCH_PADDING_S)
        outro_window_start_s = max(0.0, final_duration_s - outro_window_s)
        outro_haystack = extract_audio(
            args.final,
            outro_window_start_s,
            outro_window_s,
            "final outro search window",
        )
        expected_outro_start_s = final_duration_s - outro_duration_s
        outro_presence = presence_check(
            "outro",
            outro_samples,
            outro_haystack,
            outro_window_start_s,
            expected_outro_start_s,
            final_duration_s,
        )
        receipt["checks"].append(outro_presence)
        receipt["measurements"]["locate_lags_s"] = {
            "intro": intro_presence["lag_s"],
            "outro": outro_presence["lag_s"],
        }

        body_start_s = intro_duration_s
        body_end_s = final_duration_s - outro_duration_s
        if body_end_s <= body_start_s:
            receipt["checks"].append(
                {
                    "item": "program_body",
                    "status": "fail",
                    "start_s": seconds(body_start_s),
                    "end_s": seconds(body_end_s),
                    "why": "final duration leaves no non-overlapping program body between bumpers",
                }
            )
        else:
            receipt["checks"].append(
                scan_body_for_bumper(
                    "intro",
                    args.final,
                    intro_samples,
                    body_start_s,
                    body_end_s,
                )
            )
            receipt["checks"].append(
                scan_body_for_bumper(
                    "outro",
                    args.final,
                    outro_samples,
                    body_start_s,
                    body_end_s,
                )
            )

        loudness, level_checks = loudness_checks(
            args.final,
            intro_duration_s,
            outro_duration_s,
            final_duration_s,
            args.tolerance_lu,
        )
        receipt["measurements"]["loudness"] = loudness
        receipt["checks"].extend(level_checks)

        print(
            "LEVELS intro_lufs=%s body_lufs=%s outro_lufs=%s "
            "intro_delta_lu=%s outro_delta_lu=%s"
            % (
                loudness["intro_lufs"],
                loudness["body_lufs"],
                loudness["outro_lufs"],
                loudness["intro_delta_lu"],
                loudness["outro_delta_lu"],
            )
        )

        for check in receipt["checks"]:
            print_check(check)

        failed = [check for check in receipt["checks"] if check["status"] != "pass"]
        if failed:
            exit_code = 1
            receipt["outcome"] = "fail"
            print(
                "FAIL: %d of %d checks violated the bumper contract"
                % (len(failed), len(receipt["checks"]))
            )
        else:
            exit_code = 0
            receipt["outcome"] = "pass"
            print(
                "PASS: all %d checks prove endpoint presence, uniqueness, and level match"
                % len(receipt["checks"])
            )
        receipt["exit_code"] = exit_code

    except UnusableInput as exc:
        exit_code = 2
        receipt["outcome"] = "error"
        receipt["exit_code"] = exit_code
        receipt["errors"].append(str(exc))
        print("ERROR: %s" % exc)
        print("UNUSABLE: no receipt can pass when the requested evidence could not be measured")

    if args.json_out:
        try:
            write_json(args.json_out, receipt)
            print("JSON_RECEIPT %s" % args.json_out)
        except UnusableInput as exc:
            print("ERROR: %s" % exc)
            return 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
