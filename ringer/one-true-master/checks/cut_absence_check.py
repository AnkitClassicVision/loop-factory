#!/usr/bin/env python3
"""Prove that session-grid cut spans are absent from a rendered program.

The instrument references the louder of host_audio and guest_audio for every
cut, searches the complete rendered program with normalized cross-correlation,
and treats a confident location as evidence that the cut was not applied.
Two nearby kept windows must locate as inverse controls before the run can
certify absence.

Exit codes:
  0  every measurable cut is absent and both kept controls locate
  1  at least one cut confidently locates in the render
  2  inputs or evidence are unusable, including failed controls
"""

import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np


SAMPLE_RATE = 16000
SILENCE_DBFS = -55.0  # Below this level a stem lacks speech/audio worth matching.
LOCATE_MIN_PEAK = 0.35  # Matches the proven episode checker's minimum correlation evidence.
LOCATE_MIN_MARGIN = 1.25  # A peak must beat the next distinct peak by 25% to be confident.
RUNNER_GUARD_S = 0.250  # Nearby correlation samples are one peak, not independent evidence.
CONTROL_TARGET_S = 8.0  # Eight seconds gives kept controls enough distinctive speech context.
CONTROL_MIN_S = 2.0  # Shorter controls are too easy to match spuriously in a full episode.
CONTROL_EDGE_GAP_S = 0.250  # Keep controls clear of edit boundaries and transition artifacts.
REQUIRED_CONTROLS = 2  # Two distinct kept matches provide the required inverse control.
MIN_CUT_MATCH_S = 0.250  # Briefer cut audio is insufficient for a reliable full-program search.
MIN_EXTRACT_COVERAGE = 0.98  # A window missing over 2% of samples is not the requested evidence.
CORRELATION_BLOCK_S = 120.0  # Bound FFT memory while still searching every rendered sample.
AUDIO_EXTENSIONS = ("flac", "wav", "m4a", "mp3", "aac", "ogg")


class InputError(Exception):
    """The requested measurement cannot be performed."""


def format_s(value):
    return f"{value:.3f}"


def rounded_s(value):
    return round(float(value), 3)


def run_media_command(command, description):
    try:
        return subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise InputError(f"{command[0]} is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        if detail:
            detail = detail.splitlines()[-1]
            raise InputError(f"{description}: {detail}") from exc
        raise InputError(description) from exc


def probe_duration(path):
    result = run_media_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            path,
        ],
        f"cannot read media duration from {path}",
    )
    try:
        duration = float(result.stdout.decode("utf-8").strip())
    except (TypeError, ValueError) as exc:
        raise InputError(f"ffprobe returned no usable duration for {path}") from exc
    if not math.isfinite(duration) or duration <= 0.0:
        raise InputError(f"media duration is not positive for {path}")
    return duration


def decode_audio(path, start_s=None, duration_s=None):
    command = ["ffmpeg", "-v", "error"]
    if start_s is not None:
        command.extend(["-ss", format_s(max(0.0, start_s))])
    if duration_s is not None:
        command.extend(["-t", format_s(duration_s)])
    command.extend(
        [
            "-i",
            path,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    result = run_media_command(command, f"cannot decode audio from {path}")
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    if samples.size == 0:
        raise InputError(f"decoded audio is empty for {path}")
    if not np.isfinite(samples).all():
        raise InputError(f"decoded audio contains non-finite samples for {path}")
    if duration_s is not None:
        expected = int(round(duration_s * SAMPLE_RATE))
        if samples.size < expected * MIN_EXTRACT_COVERAGE:
            raise InputError(
                f"short audio extraction from {path}: got {samples.size} samples, "
                f"expected about {expected}"
            )
        if samples.size > expected:
            samples = samples[:expected]
    return samples


def dbfs(samples):
    if samples.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20.0 * math.log10(rms) if rms > 0.0 else -math.inf


def correlation_block(needle, haystack, first_lag, lag_count):
    """Return normalized FFT correlation for a contiguous range of lags."""
    segment_end = first_lag + lag_count + needle.size - 1
    segment = np.asarray(haystack[first_lag:segment_end], dtype=np.float32)
    centered_needle = needle - needle.mean()
    centered_segment = segment - segment.mean()
    needle_energy = float(np.sqrt(np.sum(centered_needle.astype(np.float64) ** 2)))
    if needle_energy == 0.0:
        return np.zeros(lag_count, dtype=np.float64)
    normalized_needle = centered_needle / needle_energy

    fft_size = 1
    while fft_size < segment.size + needle.size:
        fft_size *= 2
    corr = np.fft.irfft(
        np.fft.rfft(centered_segment, fft_size)
        * np.conj(np.fft.rfft(normalized_needle, fft_size)),
        fft_size,
    )[:lag_count]

    squared = centered_segment.astype(np.float64) ** 2
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    window_energy = np.sqrt(
        np.maximum(
            cumulative[needle.size:needle.size + lag_count]
            - cumulative[:lag_count],
            1e-12,
        )
    )
    return corr / window_energy


def locate_in_full_render(needle, render_audio):
    """Return the exact global peak and distinct runner-up over the full render."""
    possible_lags = render_audio.size - needle.size + 1
    if possible_lags <= 0:
        return None

    block_lags = max(1, int(round(CORRELATION_BLOCK_S * SAMPLE_RATE)))
    blocks = []
    best_lag = None
    best_peak = -math.inf

    for first_lag in range(0, possible_lags, block_lags):
        lag_count = min(block_lags, possible_lags - first_lag)
        corr = correlation_block(needle, render_audio, first_lag, lag_count)
        local_index = int(np.argmax(corr))
        local_peak = float(corr[local_index])
        blocks.append((first_lag, lag_count, local_peak))
        if local_peak > best_peak:
            best_peak = local_peak
            best_lag = first_lag + local_index

    guard = int(round(RUNNER_GUARD_S * SAMPLE_RATE))
    runner = -math.inf
    for first_lag, lag_count, local_peak in blocks:
        last_lag = first_lag + lag_count - 1
        if last_lag < best_lag - guard or first_lag > best_lag + guard:
            runner = max(runner, local_peak)
            continue

        corr = correlation_block(needle, render_audio, first_lag, lag_count)
        forbidden_start = max(0, best_lag - guard - first_lag)
        forbidden_end = min(lag_count, best_lag + guard - first_lag + 1)
        corr[forbidden_start:forbidden_end] = -math.inf
        if np.isfinite(corr).any():
            runner = max(runner, float(np.max(corr)))

    if not math.isfinite(runner):
        runner = 0.0
    margin = best_peak / runner if runner > 0.0 else math.inf
    return {
        "lag": int(best_lag),
        "peak": float(best_peak),
        "runner_up": float(runner),
        "margin": float(margin),
        "located": best_peak >= LOCATE_MIN_PEAK and margin >= LOCATE_MIN_MARGIN,
    }


def find_stems(stems_dir):
    if not os.path.isdir(stems_dir):
        raise InputError(f"stems directory does not exist: {stems_dir}")
    stems = {}
    for role in ("host", "guest"):
        for extension in AUDIO_EXTENSIONS:
            candidate = os.path.join(stems_dir, f"{role}_audio.{extension}")
            if os.path.isfile(candidate):
                stems[role] = candidate
                break
        if role not in stems:
            raise InputError(
                f"missing {role}_audio stem under {stems_dir} "
                f"(tried: {', '.join(AUDIO_EXTENSIONS)})"
            )
    return stems


def load_cuts(path):
    if not os.path.isfile(path):
        raise InputError(f"cuts JSON does not exist: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read cuts JSON {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise InputError("cuts JSON must be a non-empty list")

    cuts = []
    seen_ids = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InputError(f"cut at index {index} is not an object")
        cut_id = item.get("id")
        if not isinstance(cut_id, str) or not cut_id.strip():
            raise InputError(f"cut at index {index} has no non-empty string id")
        if cut_id in seen_ids:
            raise InputError(f"duplicate cut id: {cut_id}")
        seen_ids.add(cut_id)
        try:
            start_s = float(item["start_s"])
            end_s = float(item["end_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InputError(f"cut {cut_id} needs numeric start_s and end_s") from exc
        if not math.isfinite(start_s) or not math.isfinite(end_s):
            raise InputError(f"cut {cut_id} has a non-finite timestamp")
        if start_s < 0.0 or end_s <= start_s:
            raise InputError(
                f"cut {cut_id} has invalid range "
                f"{format_s(start_s)}-{format_s(end_s)}"
            )
        cuts.append({"id": cut_id, "start_s": start_s, "end_s": end_s})
    return cuts


def merged_cut_intervals(cuts):
    intervals = sorted((cut["start_s"], cut["end_s"]) for cut in cuts)
    merged = []
    for start_s, end_s in intervals:
        if merged and start_s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_s))
        else:
            merged.append((start_s, end_s))
    return merged


def control_candidates(cuts, stem_duration):
    """Build kept windows immediately before and after merged cut regions."""
    intervals = merged_cut_intervals(cuts)
    candidates = []
    for index, (cut_start, cut_end) in enumerate(intervals):
        previous_end = intervals[index - 1][1] if index else 0.0
        next_start = intervals[index + 1][0] if index + 1 < len(intervals) else stem_duration

        left_end = cut_start - CONTROL_EDGE_GAP_S
        left_available = left_end - previous_end
        left_duration = min(CONTROL_TARGET_S, left_available)
        if left_duration >= CONTROL_MIN_S:
            candidates.append(
                {
                    "side": "before",
                    "adjacent_cut_start_s": cut_start,
                    "start_s": left_end - left_duration,
                    "end_s": left_end,
                }
            )

        right_start = cut_end + CONTROL_EDGE_GAP_S
        right_available = next_start - right_start
        right_duration = min(CONTROL_TARGET_S, right_available)
        if right_duration >= CONTROL_MIN_S:
            candidates.append(
                {
                    "side": "after",
                    "adjacent_cut_start_s": cut_start,
                    "start_s": right_start,
                    "end_s": right_start + right_duration,
                }
            )
    return candidates


def loudest_reference(stems, start_s, end_s):
    duration_s = end_s - start_s
    measured = []
    for role in ("host", "guest"):
        samples = decode_audio(stems[role], start_s, duration_s)
        measured.append((dbfs(samples), role, samples))
    measured.sort(key=lambda item: item[0], reverse=True)
    best_level, best_role, best_samples = measured[0]
    levels = {role: level for level, role, _samples in measured}
    return best_role, best_level, best_samples, levels


def finite_or_none(value, digits=3):
    return round(float(value), digits) if math.isfinite(value) else None


def match_evidence(match):
    if match is None:
        return {
            "found_at_s": None,
            "peak": None,
            "runner_up": None,
            "margin": None,
        }
    return {
        "found_at_s": rounded_s(match["lag"] / SAMPLE_RATE),
        "peak": round(match["peak"], 3),
        "runner_up": round(match["runner_up"], 3),
        "margin": round(match["margin"], 3) if math.isfinite(match["margin"]) else None,
    }


def print_match_fields(match):
    if match is None:
        return "found_at_s=none peak=none runner_up=none margin=none"
    margin = f"{match['margin']:.3f}" if math.isfinite(match["margin"]) else "inf"
    return (
        f"found_at_s={format_s(match['lag'] / SAMPLE_RATE)} "
        f"peak={match['peak']:.3f} runner_up={match['runner_up']:.3f} "
        f"margin={margin}"
    )


def write_json(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise InputError(f"cannot write JSON receipt {path}: {exc}") from exc


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prove session-grid cut spans are absent from rendered audio."
    )
    parser.add_argument("--render", required=True, help="rendered program media")
    parser.add_argument("--stems", required=True, help="directory with host/guest audio stems")
    parser.add_argument("--cuts", required=True, help="JSON list of applied cut spans")
    parser.add_argument("--json", dest="json_out", help="optional JSON receipt path")
    return parser.parse_args()


def main():
    args = parse_args()
    receipt = {
        "instrument": "cut_absence_check",
        "render": args.render,
        "stems": args.stems,
        "cuts_file": args.cuts,
        "thresholds": {
            "sample_rate_hz": SAMPLE_RATE,
            "silence_dbfs": SILENCE_DBFS,
            "locate_min_peak": LOCATE_MIN_PEAK,
            "locate_min_margin": LOCATE_MIN_MARGIN,
            "runner_guard_s": rounded_s(RUNNER_GUARD_S),
            "control_target_s": rounded_s(CONTROL_TARGET_S),
            "control_min_s": rounded_s(CONTROL_MIN_S),
            "control_edge_gap_s": rounded_s(CONTROL_EDGE_GAP_S),
            "required_controls": REQUIRED_CONTROLS,
            "min_cut_match_s": rounded_s(MIN_CUT_MATCH_S),
        },
        "cut_results": [],
        "control_results": [],
    }

    try:
        if not os.path.isfile(args.render):
            raise InputError(f"render does not exist: {args.render}")
        stems = find_stems(args.stems)
        cuts = load_cuts(args.cuts)
        durations = {role: probe_duration(path) for role, path in stems.items()}
        stem_duration = min(durations.values())
        render_duration = probe_duration(args.render)
        receipt["render_duration_s"] = rounded_s(render_duration)
        receipt["stem_duration_s"] = rounded_s(stem_duration)

        for cut in cuts:
            if cut["end_s"] > stem_duration:
                raise InputError(
                    f"cut {cut['id']} ends at {format_s(cut['end_s'])}s, beyond "
                    f"the common stem duration {format_s(stem_duration)}s"
                )

        render_audio = decode_audio(args.render)
        print(
            f"RENDER duration_s={format_s(render_duration)} "
            f"samples={render_audio.size} search=full-program"
        )

        control_pool = []
        for candidate in control_candidates(cuts, stem_duration):
            try:
                role, level, samples, levels = loudest_reference(
                    stems, candidate["start_s"], candidate["end_s"]
                )
            except InputError as exc:
                candidate["selection_error"] = str(exc)
                continue
            candidate.update(
                {
                    "ref": role,
                    "ref_dbfs": level,
                    "samples": samples,
                    "levels": levels,
                }
            )
            if level >= SILENCE_DBFS:
                control_pool.append(candidate)

        control_pool.sort(
            key=lambda item: (
                -item["ref_dbfs"],
                item["start_s"],
                item["side"],
            )
        )
        selected_controls = []
        for candidate in control_pool:
            overlaps_selected = any(
                candidate["start_s"] < selected["end_s"]
                and candidate["end_s"] > selected["start_s"]
                for selected in selected_controls
            )
            if not overlaps_selected:
                selected_controls.append(candidate)
            if len(selected_controls) == REQUIRED_CONTROLS:
                break
        if len(selected_controls) < REQUIRED_CONTROLS:
            why = (
                f"only {len(selected_controls)} adjacent kept window(s) carried "
                f"audio above {SILENCE_DBFS:.1f} dBFS without overlapping; "
                f"{REQUIRED_CONTROLS} are required"
            )
            print(f"CONTROL_SETUP status=UNMEASURABLE WHY={why}")
            receipt["control_setup_error"] = why

        controls_ok = len(selected_controls) == REQUIRED_CONTROLS
        for number, control in enumerate(selected_controls, start=1):
            match = locate_in_full_render(control["samples"], render_audio)
            located = bool(match and match["located"])
            controls_ok = controls_ok and located
            evidence = match_evidence(match)
            row = {
                "id": f"kept-{number}",
                "status": "LOCATED" if located else "UNMEASURABLE",
                "side": control["side"],
                "start_s": rounded_s(control["start_s"]),
                "end_s": rounded_s(control["end_s"]),
                "ref": control["ref"],
                "ref_dbfs": finite_or_none(control["ref_dbfs"], 1),
                "host_dbfs": finite_or_none(control["levels"]["host"], 1),
                "guest_dbfs": finite_or_none(control["levels"]["guest"], 1),
                **evidence,
            }
            if not located:
                if match is None:
                    why = "kept window is longer than the rendered audio"
                else:
                    why = (
                        f"kept window did not meet locate thresholds "
                        f"(peak >= {LOCATE_MIN_PEAK:.3f} and "
                        f"margin >= {LOCATE_MIN_MARGIN:.3f})"
                    )
                row["why"] = why
            receipt["control_results"].append(row)
            line = (
                f"CONTROL id=kept-{number} side={control['side']} "
                f"start_s={format_s(control['start_s'])} "
                f"end_s={format_s(control['end_s'])} ref={control['ref']} "
                f"ref_dbfs={control['ref_dbfs']:.1f} {print_match_fields(match)}"
            )
            if located:
                print(f"{line} status=LOCATED")
            else:
                print(f"{line} status=UNMEASURABLE WHY={row['why']}")

        found_count = 0
        unmeasurable_count = 0
        for cut in cuts:
            duration_s = cut["end_s"] - cut["start_s"]
            base_row = {
                "id": cut["id"],
                "start_s": rounded_s(cut["start_s"]),
                "end_s": rounded_s(cut["end_s"]),
                "duration_s": rounded_s(duration_s),
            }
            prefix = (
                f"CUT id={cut['id']} start_s={format_s(cut['start_s'])} "
                f"end_s={format_s(cut['end_s'])} duration_s={format_s(duration_s)}"
            )
            try:
                role, level, needle, levels = loudest_reference(
                    stems, cut["start_s"], cut["end_s"]
                )
            except InputError as exc:
                unmeasurable_count += 1
                row = {**base_row, "status": "UNMEASURABLE", "why": str(exc)}
                receipt["cut_results"].append(row)
                print(f"{prefix} status=UNMEASURABLE WHY={exc}")
                continue

            levels_evidence = {
                "ref": role,
                "ref_dbfs": finite_or_none(level, 1),
                "host_dbfs": finite_or_none(levels["host"], 1),
                "guest_dbfs": finite_or_none(levels["guest"], 1),
            }
            if level < SILENCE_DBFS:
                unmeasurable_count += 1
                why = (
                    f"both stems are silent below {SILENCE_DBFS:.1f} dBFS "
                    f"(host={levels['host']:.1f}, guest={levels['guest']:.1f}); "
                    f"no reference evidence, not passed"
                )
                receipt["cut_results"].append(
                    {**base_row, **levels_evidence, "status": "UNMEASURABLE", "why": why}
                )
                print(
                    f"{prefix} host_dbfs={levels['host']:.1f} "
                    f"guest_dbfs={levels['guest']:.1f} "
                    f"status=UNMEASURABLE WHY={why}"
                )
                continue
            if duration_s < MIN_CUT_MATCH_S:
                unmeasurable_count += 1
                why = (
                    f"cut is shorter than the {format_s(MIN_CUT_MATCH_S)}s "
                    f"minimum reliable match window; no evidence, not passed"
                )
                receipt["cut_results"].append(
                    {**base_row, **levels_evidence, "status": "UNMEASURABLE", "why": why}
                )
                print(
                    f"{prefix} ref={role} ref_dbfs={level:.1f} "
                    f"status=UNMEASURABLE WHY={why}"
                )
                continue

            match = locate_in_full_render(needle, render_audio)
            found = bool(match and match["located"])
            evidence = match_evidence(match)
            if found:
                found_count += 1
                why = (
                    f"cut audio confidently located in render: peak >= "
                    f"{LOCATE_MIN_PEAK:.3f} and margin >= {LOCATE_MIN_MARGIN:.3f}"
                )
                status = "FOUND"
            else:
                status = "ABSENT"
                if match is None:
                    why = "render is shorter than the cut span, so the full span cannot occur"
                elif match["peak"] < LOCATE_MIN_PEAK:
                    why = (
                        f"strongest peak {match['peak']:.3f} is below "
                        f"{LOCATE_MIN_PEAK:.3f}"
                    )
                else:
                    why = (
                        f"peak margin {match['margin']:.3f} is below "
                        f"{LOCATE_MIN_MARGIN:.3f}"
                    )
            row = {
                **base_row,
                **levels_evidence,
                **evidence,
                "status": status,
                "why": why,
            }
            receipt["cut_results"].append(row)
            print(
                f"{prefix} ref={role} ref_dbfs={level:.1f} "
                f"{print_match_fields(match)} status={status}"
                + (f" WHY={why}" if found else "")
            )

        if not controls_ok:
            exit_code = 2
            verdict = "UNUSABLE"
            summary = (
                "inverse controls failed; this run cannot certify cut absence"
            )
        elif found_count:
            exit_code = 1
            verdict = "FAIL"
            summary = f"{found_count} cut span(s) confidently located in the render"
        elif unmeasurable_count:
            exit_code = 2
            verdict = "UNUSABLE"
            summary = (
                f"{unmeasurable_count} cut span(s) had no usable reference evidence; "
                f"unmeasurable is not passed"
            )
        else:
            exit_code = 0
            verdict = "PASS"
            summary = (
                f"all {len(cuts)} cut span(s) absent and both kept controls located"
            )

        receipt["verdict"] = verdict
        receipt["summary"] = summary
        receipt["exit_code"] = exit_code
        print(f"RESULT status={verdict} exit={exit_code} WHY={summary}")
        if args.json_out:
            write_json(args.json_out, receipt)
        return exit_code

    except InputError as exc:
        receipt["verdict"] = "UNUSABLE"
        receipt["summary"] = str(exc)
        receipt["exit_code"] = 2
        print(f"RESULT status=UNUSABLE exit=2 WHY={exc}")
        if args.json_out:
            try:
                write_json(args.json_out, receipt)
            except InputError as write_exc:
                print(f"JSON status=UNUSABLE WHY={write_exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
