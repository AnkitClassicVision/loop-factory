#!/usr/bin/env python3
"""Prove that a finished render contains no unintended black-frame gaps.

Exit codes:
  0  no over-threshold black span occurs in the program body
  1  at least one over-threshold black span occurs in the program body
  2  an input is missing, malformed, unreadable, or cannot be measured
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys


# A 0.10s gap is about three frames at 30 fps. Shorter detections can be a
# single dark codec-transition frame, so they are reported but do not fail.
DEFAULT_MAX_BLACK_S = 0.10
BLACKDETECT_MIN_S = 0.05
BLACK_PIXEL_THRESHOLD = 0.10
FADE_GRACE_S = 3.0  # A fully contained intro/outro fade may legitimately be black.
EDIT_BOUNDARY_WINDOW_S = 0.10  # Three 30 fps frames on either side catches concat flashes.
BOUNDARY_EPSILON_S = 0.050  # Permit container and frame-grid rounding in the sidecar.
TIMESTAMP_DECIMALS = 3

NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
BLACK_SPAN_RE = re.compile(
    r"black_start:(?P<start>%s)\s+"
    r"black_end:(?P<end>%s)\s+"
    r"black_duration:(?P<duration>%s)"
    % (NUMBER_PATTERN, NUMBER_PATTERN, NUMBER_PATTERN)
)


class InputError(Exception):
    """The requested measurement cannot be made from the supplied inputs."""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prove a finished video has no unintended black gaps.",
        epilog=(
            "Black spans at or below %(threshold).2fs are evidence only. "
            "Longer spans fail unless fully contained in the first or last "
            "%(grace).1fs."
            % {"threshold": DEFAULT_MAX_BLACK_S, "grace": FADE_GRACE_S}
        ),
    )
    parser.add_argument("--render", required=True, help="Finished video render")
    parser.add_argument(
        "--boundaries",
        help="Optional angle_switched .boundaries.json sidecar",
    )
    parser.add_argument(
        "--max-black-s",
        type=float,
        default=DEFAULT_MAX_BLACK_S,
        help="Longest permitted body black span in seconds (default: %(default)s)",
    )
    parser.add_argument("--json", dest="json_out", help="Optional receipt output path")
    return parser.parse_args()


def require_file(path, label):
    if not os.path.isfile(path):
        raise InputError("%s does not exist or is not a regular file: %s" % (label, path))


def finite_number(value, label):
    if isinstance(value, bool):
        raise InputError("%s must be a finite number" % label)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError("%s must be a finite number" % label) from exc
    if not math.isfinite(number):
        raise InputError("%s must be a finite number" % label)
    return number


def load_json_file(path, label):
    require_file(path, label)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("%s is not readable JSON: %s" % (label, exc)) from exc


def probe_video_duration(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=index,duration:format=duration",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
    except FileNotFoundError as exc:
        raise InputError("ffprobe is unavailable: %s" % exc) from exc
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise InputError("ffprobe could not read render: %s" % detail.strip()) from exc

    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        raise InputError("render has no video stream: %s" % path)

    candidates = []
    if isinstance(streams[0], dict) and streams[0].get("duration") is not None:
        candidates.append(streams[0]["duration"])
    format_payload = payload.get("format")
    if isinstance(format_payload, dict) and format_payload.get("duration") is not None:
        candidates.append(format_payload["duration"])

    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0.0:
            return duration
    raise InputError("render video duration is missing or not a positive finite value: %s" % path)


def normalize_edit_boundaries(payload, render_duration):
    rows = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise InputError(
            "boundaries must be a non-empty list or an object with a non-empty segments list"
        )

    normalized = []
    previous_end = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("boundary segment %d is not an object" % index)
        start = finite_number(
            row.get("edited_start_s"), "boundary segment %d edited_start_s" % index
        )
        end = finite_number(
            row.get("edited_end_s"), "boundary segment %d edited_end_s" % index
        )
        if start < 0.0 or end <= start:
            raise InputError(
                "boundary segment %d must have edited_start_s >= 0 and edited_end_s > start"
                % index
            )
        if previous_end is not None:
            if abs(start - previous_end) > BOUNDARY_EPSILON_S:
                raise InputError(
                    "boundary segment %d starts at %.3fs but the previous segment ends "
                    "at %.3fs; edited spans are not contiguous"
                    % (index, start, previous_end)
                )
            normalized.append(start)
        previous_end = end

    if previous_end > render_duration + BOUNDARY_EPSILON_S:
        raise InputError(
            "boundaries end at %.3fs but render duration is only %.3fs"
            % (previous_end, render_duration)
        )
    return normalized


def detect_black_spans(path):
    filter_spec = "blackdetect=d=%.2f:pix_th=%.2f" % (
        BLACKDETECT_MIN_S,
        BLACK_PIXEL_THRESHOLD,
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        path,
        "-vf",
        filter_spec,
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise InputError("ffmpeg is unavailable: %s" % exc) from exc
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise InputError(
            "ffmpeg blackdetect failed with exit %d: %s"
            % (result.returncode, detail or "no diagnostic output")
        )

    matches = list(BLACK_SPAN_RE.finditer(result.stderr))
    reported_starts = result.stderr.count("black_start:")
    if reported_starts != len(matches):
        raise InputError(
            "ffmpeg reported %d black start(s), but only %d complete span(s) could be parsed"
            % (reported_starts, len(matches))
        )

    spans = []
    for index, match in enumerate(matches):
        start = finite_number(match.group("start"), "black span %d start" % index)
        end = finite_number(match.group("end"), "black span %d end" % index)
        duration = finite_number(match.group("duration"), "black span %d duration" % index)
        if start < 0.0 or end < start or duration < 0.0:
            raise InputError("ffmpeg returned an invalid black span at item %d" % index)
        spans.append({"start_s_raw": start, "end_s_raw": end, "duration_s_raw": duration})
    return spans


def boundary_distance(span_start, span_end, boundary):
    if span_start <= boundary <= span_end:
        return 0.0
    return min(abs(boundary - span_start), abs(boundary - span_end))


def classify_span(span, render_duration, max_black_s, edit_boundaries):
    start = span["start_s_raw"]
    end = span["end_s_raw"]
    duration = span["duration_s_raw"]
    first_grace = end <= FADE_GRACE_S
    last_grace = start >= max(0.0, render_duration - FADE_GRACE_S)

    near_boundaries = []
    for boundary in edit_boundaries:
        distance = boundary_distance(start, end, boundary)
        if distance <= EDIT_BOUNDARY_WINDOW_S:
            near_boundaries.append((boundary, distance))
    near_boundaries.sort(key=lambda item: (item[1], item[0]))

    if duration <= max_black_s:
        verdict = "PASS_SUBTHRESHOLD"
        why = None
    elif first_grace or last_grace:
        verdict = "EXEMPT_EDGE_FADE"
        why = None
    elif near_boundaries:
        verdict = "FAIL_EDIT_POINT_FLASH"
        boundary, distance = near_boundaries[0]
        why = (
            "black span lasts %.3fs > %.3fs and is %.3fs from edit boundary %.3fs "
            "(window +/-%.3fs)"
            % (duration, max_black_s, distance, boundary, EDIT_BOUNDARY_WINDOW_S)
        )
    else:
        verdict = "FAIL_BODY_BLACK"
        why = (
            "black span lasts %.3fs > %.3fs and occurs outside the %.3fs "
            "intro/outro fade grace"
            % (duration, max_black_s, FADE_GRACE_S)
        )

    return {
        "start_s": round(start, TIMESTAMP_DECIMALS),
        "end_s": round(end, TIMESTAMP_DECIMALS),
        "duration_s": round(duration, TIMESTAMP_DECIMALS),
        "verdict": verdict,
        "near_edit_boundary": bool(near_boundaries),
        "edit_boundaries_s": [
            round(boundary, TIMESTAMP_DECIMALS) for boundary, _distance in near_boundaries
        ],
        "nearest_boundary_distance_s": (
            round(near_boundaries[0][1], TIMESTAMP_DECIMALS) if near_boundaries else None
        ),
        "why": why,
    }


def print_span(index, row):
    line = (
        "black=%04d start=%.3fs end=%.3fs duration=%.3fs verdict=%s"
        % (
            index,
            row["start_s"],
            row["end_s"],
            row["duration_s"],
            row["verdict"],
        )
    )
    if row["near_edit_boundary"]:
        line += " edit_boundaries=%s" % ",".join(
            "%.3fs" % value for value in row["edit_boundaries_s"]
        )
    if row["why"]:
        line += " WHY=%s" % row["why"]
    print(line)


def write_receipt(path, receipt):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise InputError("could not write --json receipt %s: %s" % (path, exc)) from exc


def error_receipt(args, message):
    receipt = {
        "check": "blackframe_check",
        "status": "UNUSABLE",
        "exit_code": 2,
        "error": message,
        "inputs": {
            "render": args.render,
            "boundaries": args.boundaries,
        },
    }
    if args.json_out:
        try:
            write_receipt(args.json_out, receipt)
        except InputError as write_error:
            print("UNUSABLE: %s" % write_error, file=sys.stderr)
    print("UNUSABLE: %s" % message, file=sys.stderr)
    return 2


def run(args):
    if not math.isfinite(args.max_black_s) or args.max_black_s < 0.0:
        raise InputError("--max-black-s must be a non-negative finite number")

    require_file(args.render, "render")
    render_duration = probe_video_duration(args.render)
    edit_boundaries = []
    if args.boundaries:
        edit_boundaries = normalize_edit_boundaries(
            load_json_file(args.boundaries, "boundaries"), render_duration
        )

    detected = detect_black_spans(args.render)
    rows = [
        classify_span(span, render_duration, args.max_black_s, edit_boundaries)
        for span in detected
    ]
    for index, row in enumerate(rows):
        print_span(index, row)

    failures = [
        row for row in rows if row["verdict"] in {"FAIL_BODY_BLACK", "FAIL_EDIT_POINT_FLASH"}
    ]
    exit_code = 1 if failures else 0
    status = "FAIL" if failures else "PASS"
    receipt = {
        "check": "blackframe_check",
        "status": status,
        "exit_code": exit_code,
        "inputs": {
            "render": args.render,
            "boundaries": args.boundaries,
        },
        "render_duration_s": round(render_duration, TIMESTAMP_DECIMALS),
        "thresholds": {
            "max_black_s": args.max_black_s,
            "blackdetect_min_s": BLACKDETECT_MIN_S,
            "black_pixel_threshold": BLACK_PIXEL_THRESHOLD,
            "fade_grace_s": FADE_GRACE_S,
            "edit_boundary_window_s": EDIT_BOUNDARY_WINDOW_S,
        },
        "edit_boundaries_s": [
            round(value, TIMESTAMP_DECIMALS) for value in edit_boundaries
        ],
        "black_spans": rows,
        "detected_count": len(rows),
        "failure_count": len(failures),
    }
    write_receipt(args.json_out, receipt)

    if failures:
        edit_flashes = sum(row["verdict"] == "FAIL_EDIT_POINT_FLASH" for row in failures)
        print(
            "FAIL: %d over-threshold body black span(s); %d at edit point(s)."
            % (len(failures), edit_flashes)
        )
        return 1
    print(
        "PASS: %d black span(s) detected; none exceeded %.3fs in the program body."
        % (len(rows), args.max_black_s)
    )
    return 0


def main():
    args = parse_args()
    try:
        return run(args)
    except InputError as exc:
        return error_receipt(args, str(exc))


if __name__ == "__main__":
    sys.exit(main())
