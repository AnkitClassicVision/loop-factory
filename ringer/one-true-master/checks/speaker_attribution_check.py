#!/usr/bin/env python3
"""Verify that long camera shots show the speaker dominating the source stems.

Exit codes:
  0  every measurable dominant-speaker segment has compatible camera attribution
  1  at least one measured segment shows the other speaker
  2  inputs are missing, unreadable, malformed, or provide no usable evidence
"""

import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np


SAMPLE_RATE = 16000
MIN_SEGMENT_S = 4.0  # Shorter reaction shots are legitimate editorial style, not attribution claims.
DOMINANCE_MARGIN_DB = 6.0  # A 6 dB lead is about twice the amplitude and avoids calling small mix differences dominance.
SILENCE_DBFS = -55.0  # Below -55 dBFS, the louder stem is too quiet to count as speech evidence.
BOUNDARY_EPSILON_S = 0.050  # Allow 50 ms for container and frame-grid duration rounding.
MIN_SAMPLE_COVERAGE_RATIO = 0.98  # Require 98% of a requested window so truncated stem reads cannot masquerade as evidence.
TIMESTAMP_DECIMALS = 3
MULTI_PERSON_ANGLES = frozenset({"sbs", "split", "split-screen", "both"})


class InputError(Exception):
    """The requested measurement cannot be made from the supplied inputs."""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prove that long camera shots show the dominant speaker.",
        epilog=(
            "Shots shorter than %.1fs are skipped because brief reaction shots are "
            "legitimate editorial style." % MIN_SEGMENT_S
        ),
    )
    parser.add_argument("--render", required=True, help="Rendered episode video")
    parser.add_argument(
        "--boundaries",
        required=True,
        help="Render .boundaries.json containing edited/source spans and angle",
    )
    parser.add_argument("--stems", required=True, help="Directory containing speaker stems")
    parser.add_argument(
        "--offsets",
        required=True,
        help='JSON object mapping angle labels to stem files, e.g. {"host":"host_audio.flac"}',
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DOMINANCE_MARGIN_DB,
        help="Speaker dominance margin in dB (default: %(default)s)",
    )
    parser.add_argument(
        "--dropout-holes",
        help="Optional JSON list of {at_abs_s, duration_s} forced-switch windows",
    )
    parser.add_argument("--json", dest="json_out", help="Optional receipt output path")
    return parser.parse_args()


def require_file(path, label):
    if not os.path.isfile(path):
        raise InputError("%s does not exist or is not a regular file: %s" % (label, path))


def load_json_file(path, label):
    require_file(path, label)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("%s is not readable JSON: %s" % (label, exc)) from exc


def load_json_argument(value, label):
    if os.path.isfile(value):
        return load_json_file(value, label)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise InputError(
            "%s must be inline JSON or the path to a readable JSON file: %s" % (label, exc)
        ) from exc


def probe_duration(path, require_video=False):
    if require_video:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=index:format=duration",
            "-of",
            "json",
        ]
    else:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
        ]
    command.append(path)
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        if require_video and not payload.get("streams"):
            raise InputError("render has no readable video stream: %s" % path)
        duration = float(payload["format"]["duration"])
    except FileNotFoundError as exc:
        raise InputError("ffprobe is unavailable: %s" % exc) from exc
    except InputError:
        raise
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise InputError("ffprobe could not read %s: %s" % (path, detail.strip())) from exc
    if not math.isfinite(duration) or duration <= 0.0:
        raise InputError("media has no positive finite duration: %s" % path)
    return duration


def extract_mono_f32(path, start_s, duration_s):
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        "%.6f" % start_s,
        "-t",
        "%.6f" % duration_s,
        "-i",
        path,
        "-map",
        "a:0",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise InputError("ffmpeg is unavailable: %s" % exc) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise InputError("ffmpeg could not decode %s: %s" % (path, detail)) from exc
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    expected = max(1, round(duration_s * SAMPLE_RATE))
    if samples.size / expected < MIN_SAMPLE_COVERAGE_RATIO:
        raise InputError(
            "decoded only %d of about %d requested samples from %s at %.3fs"
            % (samples.size, expected, path, start_s)
        )
    return samples


def dbfs(samples):
    if samples.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20.0 * math.log10(rms) if rms > 0.0 else -math.inf


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


def first_present(row, names, label):
    for name in names:
        if name in row:
            return row[name]
    raise InputError("%s is missing (accepted keys: %s)" % (label, ", ".join(names)))


def normalize_boundaries(payload):
    if isinstance(payload, dict):
        rows = payload.get("segments")
    else:
        rows = payload
    if not isinstance(rows, list) or not rows:
        raise InputError("boundaries must be a non-empty list or an object with a non-empty segments list")

    normalized = []
    previous_edited_end = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("boundary segment %d is not an object" % index)
        prefix = "boundary segment %d" % index
        edited_start = finite_number(
            first_present(row, ("edited_start_s",), prefix + " edited start"),
            prefix + " edited_start_s",
        )
        edited_end = finite_number(
            first_present(row, ("edited_end_s",), prefix + " edited end"),
            prefix + " edited_end_s",
        )
        source_start = finite_number(
            first_present(
                row,
                ("source_start_s", "source_session_start_s"),
                prefix + " source start",
            ),
            prefix + " source start",
        )
        source_end = finite_number(
            first_present(
                row,
                ("source_end_s", "source_session_end_s"),
                prefix + " source end",
            ),
            prefix + " source end",
        )
        angle_value = first_present(
            row, ("angle", "chosen_angle", "camera", "speaker"), prefix + " angle"
        )
        if not isinstance(angle_value, str) or not angle_value.strip():
            raise InputError("%s angle must be a non-empty string" % prefix)
        if min(edited_start, source_start) < 0.0:
            raise InputError("%s contains a negative timestamp" % prefix)
        if edited_end <= edited_start or source_end <= source_start:
            raise InputError("%s has a non-positive span" % prefix)
        if (
            previous_edited_end is not None
            and edited_start < previous_edited_end - BOUNDARY_EPSILON_S
        ):
            raise InputError("%s overlaps or is out of edited-time order" % prefix)
        previous_edited_end = edited_end
        normalized.append(
            {
                "index": index,
                "edited_start_s": edited_start,
                "edited_end_s": edited_end,
                "source_start_s": source_start,
                "source_end_s": source_end,
                "angle": angle_value.strip().lower(),
            }
        )
    return normalized


def normalize_offsets(payload, stems_dir):
    if not isinstance(payload, dict):
        raise InputError("--offsets must decode to a JSON object")
    normalized = {}
    for raw_angle, raw_path in payload.items():
        if not isinstance(raw_angle, str) or not isinstance(raw_path, str) or not raw_path:
            raise InputError("--offsets keys and values must be non-empty strings")
        angle = raw_angle.strip().lower()
        if angle in normalized:
            raise InputError("--offsets contains duplicate angle label after normalization: %s" % angle)
        stem_path = raw_path if os.path.isabs(raw_path) else os.path.join(stems_dir, raw_path)
        require_file(stem_path, "%s stem" % angle)
        normalized[angle] = stem_path
    missing = [role for role in ("host", "guest") if role not in normalized]
    if missing:
        raise InputError("--offsets must map both host and guest stems; missing: %s" % ", ".join(missing))
    return normalized


def normalize_holes(payload):
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise InputError("--dropout-holes must contain a JSON list")
    holes = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise InputError("dropout hole %d is not an object" % index)
        start = finite_number(row.get("at_abs_s"), "dropout hole %d at_abs_s" % index)
        duration = finite_number(row.get("duration_s"), "dropout hole %d duration_s" % index)
        if start < 0.0 or duration <= 0.0:
            raise InputError("dropout hole %d must have at_abs_s >= 0 and duration_s > 0" % index)
        holes.append({"at_abs_s": start, "duration_s": duration, "end_abs_s": start + duration})
    return holes


def containing_dropout(segment, holes):
    for hole in holes:
        if (
            segment["source_start_s"] >= hole["at_abs_s"] - BOUNDARY_EPSILON_S
            and segment["source_end_s"] <= hole["end_abs_s"] + BOUNDARY_EPSILON_S
        ):
            return hole
    return None


def dominant_speaker(host_level, guest_level, margin_db):
    louder = max(host_level, guest_level)
    if louder < SILENCE_DBFS:
        return None, None
    delta = host_level - guest_level
    if delta >= margin_db:
        return "host", delta
    if delta <= -margin_db:
        return "guest", -delta
    return None, abs(delta)


def json_level(level):
    return round(level, 3) if math.isfinite(level) else None


def display_level(level):
    if level is None:
        return "n/a"
    return "%.1f" % level if math.isfinite(level) else "-inf"


def format_segment(row):
    return (
        "segment=%04d edited=[%.3f, %.3f] source=[%.3f, %.3f] "
        "angle=%s host_dbfs=%s guest_dbfs=%s verdict=%s"
        % (
            row["index"],
            row["edited_start_s"],
            row["edited_end_s"],
            row["source_start_s"],
            row["source_end_s"],
            row["angle_shown"],
            display_level(row["host_dbfs_raw"]),
            display_level(row["guest_dbfs_raw"]),
            row["verdict"],
        )
    )


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
        "status": "UNUSABLE",
        "exit_code": 2,
        "error": message,
        "inputs": {
            "render": args.render,
            "boundaries": args.boundaries,
            "stems": args.stems,
            "offsets": args.offsets,
            "dropout_holes": args.dropout_holes,
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
    if not math.isfinite(args.tolerance) or args.tolerance < 0.0:
        raise InputError("--tolerance must be a non-negative finite dB value")

    require_file(args.render, "render")
    require_file(args.boundaries, "boundaries")
    if not os.path.isdir(args.stems):
        raise InputError("stems does not exist or is not a directory: %s" % args.stems)

    render_duration = probe_duration(args.render, require_video=True)
    boundaries = normalize_boundaries(load_json_file(args.boundaries, "boundaries"))
    offsets = normalize_offsets(load_json_argument(args.offsets, "offsets"), args.stems)
    holes = normalize_holes(
        load_json_file(args.dropout_holes, "dropout holes") if args.dropout_holes else None
    )

    allowed_angles = set(offsets) | set(MULTI_PERSON_ANGLES)
    unknown_angles = sorted({row["angle"] for row in boundaries} - allowed_angles)
    if unknown_angles:
        raise InputError(
            "boundary angles have no speaker mapping and are not known multi-person angles: %s"
            % ", ".join(unknown_angles)
        )
    if boundaries[-1]["edited_end_s"] > render_duration + BOUNDARY_EPSILON_S:
        raise InputError(
            "boundaries end at %.3fs but render duration is only %.3fs"
            % (boundaries[-1]["edited_end_s"], render_duration)
        )

    stem_durations = {role: probe_duration(offsets[role]) for role in ("host", "guest")}
    latest_source = max(row["source_end_s"] for row in boundaries)
    for role, duration in stem_durations.items():
        if latest_source > duration + BOUNDARY_EPSILON_S:
            raise InputError(
                "%s stem is %.3fs but a boundary needs source time %.3fs"
                % (role, duration, latest_source)
            )

    rows = []
    failures = 0
    dominant_evidence = 0
    measured = 0
    for segment in boundaries:
        edited_duration = segment["edited_end_s"] - segment["edited_start_s"]
        base = {
            "index": segment["index"],
            "edited_start_s": round(segment["edited_start_s"], TIMESTAMP_DECIMALS),
            "edited_end_s": round(segment["edited_end_s"], TIMESTAMP_DECIMALS),
            "source_start_s": round(segment["source_start_s"], TIMESTAMP_DECIMALS),
            "source_end_s": round(segment["source_end_s"], TIMESTAMP_DECIMALS),
            "angle_shown": segment["angle"],
            "host_dbfs": None,
            "guest_dbfs": None,
            "host_dbfs_raw": None,
            "guest_dbfs_raw": None,
            "dominant_speaker": None,
            "dominance_db": None,
            "why": None,
        }
        if edited_duration < MIN_SEGMENT_S:
            base["verdict"] = "SKIP_SHORT"
            base["why"] = (
                "edited duration %.3fs is below the %.3fs minimum; short reaction shots are exempt"
                % (edited_duration, MIN_SEGMENT_S)
            )
            rows.append(base)
            print(format_segment(base))
            continue

        source_duration = segment["source_end_s"] - segment["source_start_s"]
        host_level = dbfs(
            extract_mono_f32(offsets["host"], segment["source_start_s"], source_duration)
        )
        guest_level = dbfs(
            extract_mono_f32(offsets["guest"], segment["source_start_s"], source_duration)
        )
        measured += 1
        dominant, dominance_db = dominant_speaker(host_level, guest_level, args.tolerance)
        base["host_dbfs"] = json_level(host_level)
        base["guest_dbfs"] = json_level(guest_level)
        base["host_dbfs_raw"] = host_level
        base["guest_dbfs_raw"] = guest_level
        base["dominant_speaker"] = dominant
        base["dominance_db"] = (
            round(dominance_db, 3) if dominance_db is not None and math.isfinite(dominance_db) else None
        )

        if dominant is None:
            base["verdict"] = "NO_DOMINANT_SPEAKER"
            if max(host_level, guest_level) < SILENCE_DBFS:
                base["why"] = "both stems are below the %.1f dBFS speech floor" % SILENCE_DBFS
            else:
                base["why"] = "stem difference is below the %.1f dB dominance margin" % args.tolerance
        else:
            dominant_evidence += 1
            dropout = containing_dropout(segment, holes)
            if dropout is not None:
                base["verdict"] = "EXEMPT_DROPOUT"
                base["why"] = (
                    "source span is inside caller-supplied dropout [%.3f, %.3f]"
                    % (dropout["at_abs_s"], dropout["end_abs_s"])
                )
                base["dropout"] = {
                    "at_abs_s": round(dropout["at_abs_s"], TIMESTAMP_DECIMALS),
                    "duration_s": round(dropout["duration_s"], TIMESTAMP_DECIMALS),
                }
            elif segment["angle"] in MULTI_PERSON_ANGLES or segment["angle"] == dominant:
                base["verdict"] = "PASS"
                base["why"] = (
                    "%s leads by %.1f dB and angle %s includes that speaker"
                    % (dominant, dominance_db, segment["angle"])
                )
            else:
                failures += 1
                base["verdict"] = "FAIL"
                base["why"] = (
                    "%s dominates by %.1f dB, but angle %s shows the other speaker"
                    % (dominant, dominance_db, segment["angle"])
                )
        rows.append(base)
        print(format_segment(base))
        if base["verdict"] == "FAIL":
            print("  WHY: %s" % base["why"])

    if measured == 0:
        status, exit_code = "UNUSABLE", 2
        summary_text = "no segment met the %.3fs minimum; there is no attribution evidence" % MIN_SEGMENT_S
    elif dominant_evidence == 0:
        status, exit_code = "UNUSABLE", 2
        summary_text = "no measured segment contained a speaker dominant by %.1f dB; no evidence is not a pass" % args.tolerance
    elif failures:
        status, exit_code = "FAIL", 1
        summary_text = "%d segment(s) show the other speaker during dominant speech" % failures
    else:
        status, exit_code = "PASS", 0
        summary_text = "%d dominant-speaker segment(s) were compatible with the shown angle" % dominant_evidence

    json_rows = []
    for row in rows:
        json_rows.append({key: value for key, value in row.items() if not key.endswith("_raw")})
    receipt = {
        "status": status,
        "exit_code": exit_code,
        "inputs": {
            "render": args.render,
            "boundaries": args.boundaries,
            "stems": args.stems,
            "offsets": args.offsets,
            "dropout_holes": args.dropout_holes,
        },
        "thresholds": {
            "minimum_segment_s": MIN_SEGMENT_S,
            "minimum_segment_rationale": "short reaction shots are legitimate editorial style",
            "dominance_margin_db": args.tolerance,
            "dominance_margin_rationale": "the 6 dB default is about twice the amplitude and avoids small mix differences",
            "silence_dbfs": SILENCE_DBFS,
            "silence_rationale": "quieter stem energy is not reliable speech evidence",
        },
        "media": {
            "render_duration_s": round(render_duration, TIMESTAMP_DECIMALS),
            "host_stem_duration_s": round(stem_durations["host"], TIMESTAMP_DECIMALS),
            "guest_stem_duration_s": round(stem_durations["guest"], TIMESTAMP_DECIMALS),
        },
        "segments": json_rows,
        "summary": {
            "total_segments": len(rows),
            "measured_segments": measured,
            "dominant_evidence_segments": dominant_evidence,
            "failures": failures,
            "message": summary_text,
        },
    }
    write_receipt(args.json_out, receipt)
    print("%s: %s" % (status, summary_text))
    return exit_code


def main():
    args = parse_args()
    try:
        return run(args)
    except InputError as exc:
        return error_receipt(args, str(exc))


if __name__ == "__main__":
    sys.exit(main())
