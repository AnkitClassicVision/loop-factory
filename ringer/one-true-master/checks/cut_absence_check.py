#!/usr/bin/env python3
"""Prove that session-grid cut spans are absent from a rendered program.

The instrument references the louder of host_audio and guest_audio for every
cut, searches the complete rendered program with normalized cross-correlation,
and treats a confident location as evidence that the cut was not applied.
Two nearby kept windows must locate as inverse controls before the run can
certify absence. When both stems are silent, the instrument locates source
audio following the cut and verifies that its render position reflects the
cut's removal. The older splice-silence check is retained only as a fallback
when no usable post-cut anchor exists.

Exit codes:
  0  every cut passes its speech or silent-cut proof and both controls locate
  1  at least one cut is found, not applied, ambiguous, or fails its fallback
  2  inputs or evidence are unusable, including failed controls
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np


SAMPLE_RATE = 16000
SILENCE_DBFS = -55.0  # Below this level a stem lacks speech/audio worth matching.
SILENCE_FLOOR_DBFS = -50.0  # Encoded noise below this sample level counts as silence.
WINDOW_S = 3.0  # Inspect this much render audio on each side of a silent cut splice.
MIN_GAP_S = 0.5  # Never accept a silent run this long at a removed-gap splice.
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
ANCHOR_PROBE_S = 6.0  # Post-cut source audio used to prove the rendered position.
ANCHOR_SEARCH_MAX_S = 15.0  # Maximum forward slide from the cut end for usable audio.
ANCHOR_WINDOW_S = 8.0  # Search radius around the expected applied render position.
ANCHOR_TOLERANCE_S = 0.75  # Maximum position error for either edit hypothesis.
ANCHOR_MIN_PEAK = 0.60  # Minimum absolute normalized correlation for a verdict.
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


def render_splice_position(cut, cuts, head_offset_s):
    """Map a source-timeline cut start to its splice on the rendered timeline."""
    removed_before_s = sum(
        prior["end_s"] - prior["start_s"]
        for prior in cuts
        if prior["end_s"] <= cut["start_s"]
    )
    return cut["start_s"] - removed_before_s + head_offset_s


def measure_silent_runs(render_path, render_duration_s, render_pos_s):
    """Measure consecutive below-floor samples around a render splice."""
    if render_pos_s < 0.0 or render_pos_s > render_duration_s:
        raise InputError(
            f"render splice {format_s(render_pos_s)}s is outside render duration "
            f"{format_s(render_duration_s)}s"
        )

    window_start_s = max(0.0, render_pos_s - WINDOW_S)
    window_end_s = min(render_duration_s, render_pos_s + WINDOW_S)
    window_duration_s = window_end_s - window_start_s
    if window_duration_s <= 0.0:
        raise InputError(
            f"render splice {format_s(render_pos_s)}s has no decodable inspection window"
        )
    samples = decode_audio(render_path, window_start_s, window_duration_s)
    floor_amplitude = 10.0 ** (SILENCE_FLOOR_DBFS / 20.0)
    silent = np.abs(samples) < floor_amplitude

    padded = np.concatenate(
        (np.array([False]), silent, np.array([False]))
    ).astype(np.int8)
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    if starts.size:
        lengths = ends - starts
        longest_index = int(np.argmax(lengths))
        longest_samples = int(lengths[longest_index])
        longest_start_sample = int(starts[longest_index])
        longest_end_sample = int(ends[longest_index])
        longest_start_s = window_start_s + longest_start_sample / SAMPLE_RATE
        longest_end_s = window_start_s + longest_end_sample / SAMPLE_RATE
    else:
        longest_samples = 0
        longest_start_s = None
        longest_end_s = None

    return {
        "window_start_s": window_start_s,
        "window_end_s": window_end_s,
        "window_samples": int(samples.size),
        "silence_floor_dbfs": SILENCE_FLOOR_DBFS,
        "longest_silent_run_s": longest_samples / SAMPLE_RATE,
        "longest_silent_run_start_s": longest_start_s,
        "longest_silent_run_end_s": longest_end_s,
    }


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


def select_anchor_probe(stems, cut_end_s, stem_duration_s):
    """Return the earliest full-length post-cut probe above the stem silence gate."""
    latest_start_s = min(
        cut_end_s + ANCHOR_SEARCH_MAX_S,
        stem_duration_s - ANCHOR_PROBE_S,
    )
    if latest_start_s < cut_end_s:
        return None

    search_duration_s = latest_start_s - cut_end_s + ANCHOR_PROBE_S
    probe_samples = int(round(ANCHOR_PROBE_S * SAMPLE_RATE))
    decoded = {}
    rolling_rms = {}
    for role in ("host", "guest"):
        samples = decode_audio(stems[role], cut_end_s, search_duration_s)
        decoded[role] = samples
        squared = samples.astype(np.float64) ** 2
        cumulative = np.concatenate(([0.0], np.cumsum(squared)))
        window_energy = (
            cumulative[probe_samples:] - cumulative[:-probe_samples]
        )
        rolling_rms[role] = np.sqrt(
            np.maximum(window_energy / probe_samples, 0.0)
        )

    possible_starts = min(values.size for values in rolling_rms.values())
    if possible_starts <= 0:
        return None
    threshold_rms = 10.0 ** (SILENCE_DBFS / 20.0)
    usable = np.zeros(possible_starts, dtype=bool)
    for values in rolling_rms.values():
        usable |= values[:possible_starts] >= threshold_rms
    usable_indices = np.flatnonzero(usable)
    if not usable_indices.size:
        return None

    slide_samples = int(usable_indices[0])
    measured = []
    for role in ("host", "guest"):
        samples = decoded[role][slide_samples:slide_samples + probe_samples]
        measured.append((dbfs(samples), role, samples))
    measured.sort(key=lambda item: item[0], reverse=True)
    best_level, best_role, best_samples = measured[0]
    levels = {role: level for level, role, _samples in measured}
    slide_s = slide_samples / SAMPLE_RATE
    source_start_s = cut_end_s + slide_s
    return {
        "role": best_role,
        "level": best_level,
        "samples": best_samples,
        "levels": levels,
        "slide_s": slide_s,
        "source_start_s": source_start_s,
        "source_end_s": source_start_s + ANCHOR_PROBE_S,
    }


def removed_duration_before(source_start_s, cuts):
    """Count every receipt cut ending at or before a source position."""
    return sum(
        cut["end_s"] - cut["start_s"]
        for cut in cuts
        if cut["end_s"] <= source_start_s
    )


def anchor_correlation_block(needle, haystack, first_lag, lag_count):
    """Return sample-normalized FFT correlation for every requested anchor lag."""
    segment_end = first_lag + lag_count + needle.size - 1
    segment = np.asarray(haystack[first_lag:segment_end], dtype=np.float64)
    centered_needle = needle.astype(np.float64) - float(np.mean(needle))
    needle_energy = float(np.sqrt(np.sum(centered_needle ** 2)))
    if needle_energy == 0.0:
        return np.zeros(lag_count, dtype=np.float64)

    fft_size = 1
    while fft_size < segment.size + needle.size:
        fft_size *= 2
    numerator = np.fft.irfft(
        np.fft.rfft(segment, fft_size)
        * np.conj(np.fft.rfft(centered_needle, fft_size)),
        fft_size,
    )[:lag_count]

    cumulative = np.concatenate(([0.0], np.cumsum(segment)))
    squared_cumulative = np.concatenate(([0.0], np.cumsum(segment ** 2)))
    window_sum = (
        cumulative[needle.size:needle.size + lag_count]
        - cumulative[:lag_count]
    )
    window_squared_sum = (
        squared_cumulative[needle.size:needle.size + lag_count]
        - squared_cumulative[:lag_count]
    )
    window_energy = np.sqrt(
        np.maximum(
            window_squared_sum - (window_sum ** 2) / needle.size,
            1e-12,
        )
    )
    return numerator / (needle_energy * window_energy)


def locate_position_anchor(needle, render_audio, expected_s):
    """Locate an anchor at every sample lag inside the expected-position window."""
    possible_lags = render_audio.size - needle.size + 1
    if possible_lags <= 0:
        return None

    first_lag = max(
        0,
        int(math.ceil((expected_s - ANCHOR_WINDOW_S) * SAMPLE_RATE)),
    )
    last_lag = min(
        possible_lags - 1,
        int(math.floor((expected_s + ANCHOR_WINDOW_S) * SAMPLE_RATE)),
    )
    if last_lag < first_lag:
        return None

    lag_count = last_lag - first_lag + 1
    corr = anchor_correlation_block(
        needle, render_audio, first_lag, lag_count
    )
    local_index = int(np.argmax(np.abs(corr)))
    return {
        "lag": first_lag + local_index,
        "peak": float(corr[local_index]),
        "window_start_s": first_lag / SAMPLE_RATE,
        "window_end_s": last_lag / SAMPLE_RATE,
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Prove session-grid cut spans are absent from rendered audio."
    )
    parser.add_argument("--render", help="rendered program media")
    parser.add_argument("--stems", help="directory with host/guest audio stems")
    parser.add_argument("--cuts", help="JSON list of applied cut spans")
    parser.add_argument("--json", dest="json_out", help="optional JSON receipt path")
    parser.add_argument(
        "--head-offset-s",
        type=float,
        default=0.0,
        help=(
            "seconds prepended before the edited body in the render (default: 0.0); "
            "pass the intro duration for a full final, and leave 0 for the bare edited body"
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run hermetic ffmpeg synthetic green/red/unusable proofs",
    )
    args = parser.parse_args(argv)
    if not args.self_test:
        missing = [
            option
            for option, value in (
                ("--render", args.render),
                ("--stems", args.stems),
                ("--cuts", args.cuts),
            )
            if not value
        ]
        if missing:
            parser.error(f"the following arguments are required: {', '.join(missing)}")
    return args


def run_check(args):
    receipt = {
        "instrument": "cut_absence_check",
        "render": args.render,
        "stems": args.stems,
        "cuts_file": args.cuts,
        "head_offset_s": finite_or_none(args.head_offset_s),
        "thresholds": {
            "sample_rate_hz": SAMPLE_RATE,
            "silence_dbfs": SILENCE_DBFS,
            "silence_floor_dbfs": SILENCE_FLOOR_DBFS,
            "silence_window_s": rounded_s(WINDOW_S),
            "min_gap_s": rounded_s(MIN_GAP_S),
            "locate_min_peak": LOCATE_MIN_PEAK,
            "locate_min_margin": LOCATE_MIN_MARGIN,
            "runner_guard_s": rounded_s(RUNNER_GUARD_S),
            "control_target_s": rounded_s(CONTROL_TARGET_S),
            "control_min_s": rounded_s(CONTROL_MIN_S),
            "control_edge_gap_s": rounded_s(CONTROL_EDGE_GAP_S),
            "required_controls": REQUIRED_CONTROLS,
            "min_cut_match_s": rounded_s(MIN_CUT_MATCH_S),
            "anchor_probe_s": rounded_s(ANCHOR_PROBE_S),
            "anchor_search_max_s": rounded_s(ANCHOR_SEARCH_MAX_S),
            "anchor_window_s": rounded_s(ANCHOR_WINDOW_S),
            "anchor_tolerance_s": rounded_s(ANCHOR_TOLERANCE_S),
            "anchor_min_peak": ANCHOR_MIN_PEAK,
        },
        "cut_results": [],
        "control_results": [],
    }

    try:
        if not math.isfinite(args.head_offset_s) or args.head_offset_s < 0.0:
            raise InputError("--head-offset-s must be a finite non-negative number")
        if not os.path.isfile(args.render):
            raise InputError(f"render does not exist: {args.render}")
        stems = find_stems(args.stems)
        cuts = load_cuts(args.cuts)
        durations = {role: probe_duration(path) for role, path in stems.items()}
        stem_duration = min(durations.values())
        render_duration = probe_duration(args.render)
        receipt["render_duration_s"] = rounded_s(render_duration)
        receipt["stem_duration_s"] = rounded_s(stem_duration)

        render_positions = {}
        for cut in cuts:
            if cut["end_s"] > stem_duration:
                raise InputError(
                    f"cut {cut['id']} ends at {format_s(cut['end_s'])}s, beyond "
                    f"the common stem duration {format_s(stem_duration)}s"
                )
            render_pos_s = render_splice_position(cut, cuts, args.head_offset_s)
            if render_pos_s < 0.0 or render_pos_s > render_duration:
                raise InputError(
                    f"cut {cut['id']} maps to render splice "
                    f"{format_s(render_pos_s)}s, outside render duration "
                    f"{format_s(render_duration)}s"
                )
            render_positions[cut["id"]] = render_pos_s

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
        silent_failure_count = 0
        anchor_applied_count = 0
        fallback_pass_count = 0
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
                row = {
                    **base_row,
                    "mode": "unknown",
                    "status": "UNMEASURABLE",
                    "why": str(exc),
                }
                receipt["cut_results"].append(row)
                print(f"{prefix} mode=UNKNOWN status=UNMEASURABLE WHY={exc}")
                continue

            levels_evidence = {
                "ref": role,
                "ref_dbfs": finite_or_none(level, 1),
                "host_dbfs": finite_or_none(levels["host"], 1),
                "guest_dbfs": finite_or_none(levels["guest"], 1),
            }
            if level < SILENCE_DBFS:
                render_pos_s = render_positions[cut["id"]]
                try:
                    anchor = select_anchor_probe(
                        stems, cut["end_s"], stem_duration
                    )
                except InputError as exc:
                    unmeasurable_count += 1
                    why = (
                        f"both stems are silent below {SILENCE_DBFS:.1f} dBFS, "
                        f"but post-cut anchor extraction is unusable: {exc}"
                    )
                    receipt["cut_results"].append(
                        {
                            **base_row,
                            **levels_evidence,
                            "mode": "POSITION_ANCHOR",
                            "render_pos_s": rounded_s(render_pos_s),
                            "status": "UNMEASURABLE",
                            "why": why,
                        }
                    )
                    print(
                        f"{prefix} host_dbfs={levels['host']:.1f} "
                        f"guest_dbfs={levels['guest']:.1f} mode=POSITION_ANCHOR "
                        f"render_pos_s={format_s(render_pos_s)} "
                        f"status=UNMEASURABLE WHY={why}"
                    )
                    continue

                if anchor is not None:
                    removed_before_s = removed_duration_before(
                        anchor["source_start_s"], cuts
                    )
                    expected_applied_s = (
                        args.head_offset_s
                        + anchor["source_start_s"]
                        - removed_before_s
                    )
                    expected_not_applied_s = expected_applied_s + duration_s
                    match = locate_position_anchor(
                        anchor["samples"], render_audio, expected_applied_s
                    )
                    if match is None:
                        peak_r = None
                        implied_s = None
                        applied_distance_s = None
                        not_applied_distance_s = None
                        status = "AMBIGUOUS"
                        why = (
                            "render has no full anchor lag inside the expected "
                            "position window"
                        )
                    else:
                        peak_r = match["peak"]
                        implied_s = match["lag"] / SAMPLE_RATE
                        applied_distance_s = abs(implied_s - expected_applied_s)
                        not_applied_distance_s = abs(
                            implied_s - expected_not_applied_s
                        )
                        strong = abs(peak_r) >= ANCHOR_MIN_PEAK
                        applied_closer = (
                            applied_distance_s < not_applied_distance_s
                        )
                        not_applied_closer = (
                            not_applied_distance_s < applied_distance_s
                        )
                        if (
                            strong
                            and applied_distance_s <= ANCHOR_TOLERANCE_S
                            and applied_closer
                        ):
                            status = "CUT_APPLIED"
                            why = (
                                f"anchor is {format_s(applied_distance_s)}s from "
                                "the applied position"
                            )
                        elif (
                            strong
                            and not_applied_distance_s <= ANCHOR_TOLERANCE_S
                            and not_applied_closer
                        ):
                            status = "CUT_NOT_APPLIED"
                            why = (
                                f"anchor is {format_s(not_applied_distance_s)}s "
                                "from the not-applied position"
                            )
                        elif not strong:
                            status = "AMBIGUOUS"
                            why = (
                                f"peak |r| {abs(peak_r):.3f} is below "
                                f"{ANCHOR_MIN_PEAK:.3f}"
                            )
                        elif math.isclose(
                            applied_distance_s,
                            not_applied_distance_s,
                            abs_tol=0.5 / SAMPLE_RATE,
                        ):
                            status = "AMBIGUOUS"
                            why = (
                                "anchor is equally close to the applied and "
                                "not-applied positions"
                            )
                        else:
                            status = "AMBIGUOUS"
                            why = (
                                f"anchor is outside the "
                                f"{format_s(ANCHOR_TOLERANCE_S)}s tolerance "
                                "for both positions"
                            )

                    if status == "CUT_APPLIED":
                        anchor_applied_count += 1
                    else:
                        silent_failure_count += 1

                    row = {
                        **base_row,
                        **levels_evidence,
                        "mode": "POSITION_ANCHOR",
                        "probe_ref": anchor["role"],
                        "probe_ref_dbfs": finite_or_none(anchor["level"], 1),
                        "probe_host_dbfs": finite_or_none(
                            anchor["levels"]["host"], 1
                        ),
                        "probe_guest_dbfs": finite_or_none(
                            anchor["levels"]["guest"], 1
                        ),
                        "probe_source_start_s": rounded_s(
                            anchor["source_start_s"]
                        ),
                        "probe_source_end_s": rounded_s(
                            anchor["source_end_s"]
                        ),
                        "probe_slide_s": rounded_s(anchor["slide_s"]),
                        "removed_duration_before_probe_s": rounded_s(
                            removed_before_s
                        ),
                        "expected_applied_render_pos_s": rounded_s(
                            expected_applied_s
                        ),
                        "expected_not_applied_render_pos_s": rounded_s(
                            expected_not_applied_s
                        ),
                        "search_window_start_s": (
                            rounded_s(match["window_start_s"])
                            if match is not None
                            else None
                        ),
                        "search_window_end_s": (
                            rounded_s(match["window_end_s"])
                            if match is not None
                            else None
                        ),
                        "peak_r": (
                            round(peak_r, 3) if peak_r is not None else None
                        ),
                        "peak_abs_r": (
                            round(abs(peak_r), 3)
                            if peak_r is not None
                            else None
                        ),
                        "implied_render_pos_s": (
                            rounded_s(implied_s)
                            if implied_s is not None
                            else None
                        ),
                        "distance_to_applied_s": (
                            rounded_s(applied_distance_s)
                            if applied_distance_s is not None
                            else None
                        ),
                        "distance_to_not_applied_s": (
                            rounded_s(not_applied_distance_s)
                            if not_applied_distance_s is not None
                            else None
                        ),
                        "status": status,
                        "why": why,
                    }
                    receipt["cut_results"].append(row)
                    peak_text = (
                        f"{peak_r:.3f}" if peak_r is not None else "none"
                    )
                    implied_text = (
                        format_s(implied_s) if implied_s is not None else "none"
                    )
                    applied_distance_text = (
                        format_s(applied_distance_s)
                        if applied_distance_s is not None
                        else "none"
                    )
                    not_applied_distance_text = (
                        format_s(not_applied_distance_s)
                        if not_applied_distance_s is not None
                        else "none"
                    )
                    print(
                        f"{prefix} host_dbfs={levels['host']:.1f} "
                        f"guest_dbfs={levels['guest']:.1f} "
                        f"mode=POSITION_ANCHOR probe_ref={anchor['role']} "
                        f"probe_start_s={format_s(anchor['source_start_s'])} "
                        f"probe_end_s={format_s(anchor['source_end_s'])} "
                        f"probe_slide_s={format_s(anchor['slide_s'])} "
                        f"peak_r={peak_text} implied_render_pos_s={implied_text} "
                        f"expected_applied_render_pos_s="
                        f"{format_s(expected_applied_s)} "
                        f"expected_not_applied_render_pos_s="
                        f"{format_s(expected_not_applied_s)} "
                        f"distance_to_applied_s={applied_distance_text} "
                        f"distance_to_not_applied_s="
                        f"{not_applied_distance_text} status={status}"
                        + (f" WHY={why}" if status != "CUT_APPLIED" else "")
                    )
                    continue

                max_gap_s = max(0.6 * duration_s, MIN_GAP_S)
                try:
                    silence = measure_silent_runs(
                        args.render, render_duration, render_pos_s
                    )
                except InputError as exc:
                    unmeasurable_count += 1
                    why = (
                        "no energetic post-cut anchor exists within "
                        f"{format_s(ANCHOR_SEARCH_MAX_S)}s, and the fallback "
                        f"render-gap measurement is unusable: {exc}"
                    )
                    receipt["cut_results"].append(
                        {
                            **base_row,
                            **levels_evidence,
                            "mode": "SILENCE_FALLBACK",
                            "render_pos_s": rounded_s(render_pos_s),
                            "max_allowed_gap_s": rounded_s(max_gap_s),
                            "status": "UNMEASURABLE",
                            "why": why,
                        }
                    )
                    print(
                        f"{prefix} host_dbfs={levels['host']:.1f} "
                        f"guest_dbfs={levels['guest']:.1f} "
                        f"mode=SILENCE_FALLBACK "
                        f"render_pos_s={format_s(render_pos_s)} "
                        f"max_allowed_gap_s={format_s(max_gap_s)} "
                        f"status=UNMEASURABLE WHY={why}"
                    )
                    continue

                longest_s = silence["longest_silent_run_s"]
                silence_gone = longest_s < max_gap_s
                if silence_gone:
                    fallback_pass_count += 1
                    status = "SILENCE_GONE"
                    why = (
                        f"longest silent run {format_s(longest_s)}s is below "
                        f"{format_s(max_gap_s)}s"
                    )
                else:
                    silent_failure_count += 1
                    status = "SILENCE_PRESENT"
                    why = (
                        f"silent run {format_s(longest_s)}s at "
                        f"{format_s(silence['longest_silent_run_start_s'])}-"
                        f"{format_s(silence['longest_silent_run_end_s'])}s "
                        f"is not shorter than {format_s(max_gap_s)}s"
                    )
                row = {
                    **base_row,
                    **levels_evidence,
                    "mode": "SILENCE_FALLBACK",
                    "render_pos_s": rounded_s(render_pos_s),
                    "window_start_s": rounded_s(silence["window_start_s"]),
                    "window_end_s": rounded_s(silence["window_end_s"]),
                    "window_samples": silence["window_samples"],
                    "silence_floor_dbfs": silence["silence_floor_dbfs"],
                    "max_allowed_gap_s": rounded_s(max_gap_s),
                    "longest_silent_run_s": rounded_s(longest_s),
                    "longest_silent_run_start_s": (
                        rounded_s(silence["longest_silent_run_start_s"])
                        if silence["longest_silent_run_start_s"] is not None
                        else None
                    ),
                    "longest_silent_run_end_s": (
                        rounded_s(silence["longest_silent_run_end_s"])
                        if silence["longest_silent_run_end_s"] is not None
                        else None
                    ),
                    "status": status,
                    "why": why,
                }
                receipt["cut_results"].append(row)
                run_start = (
                    format_s(silence["longest_silent_run_start_s"])
                    if silence["longest_silent_run_start_s"] is not None
                    else "none"
                )
                run_end = (
                    format_s(silence["longest_silent_run_end_s"])
                    if silence["longest_silent_run_end_s"] is not None
                    else "none"
                )
                line = (
                    f"{prefix} host_dbfs={levels['host']:.1f} "
                    f"guest_dbfs={levels['guest']:.1f} mode=SILENCE_FALLBACK "
                    f"render_pos_s={format_s(render_pos_s)} "
                    f"window_start_s={format_s(silence['window_start_s'])} "
                    f"window_end_s={format_s(silence['window_end_s'])} "
                    f"silence_floor_dbfs={SILENCE_FLOOR_DBFS:.1f} "
                    f"max_allowed_gap_s={format_s(max_gap_s)} "
                    f"longest_silent_run_s={format_s(longest_s)} "
                    f"longest_silent_run_start_s={run_start} "
                    f"longest_silent_run_end_s={run_end} status={status}"
                )
                print(line + (f" WHY={why}" if not silence_gone else ""))
                continue
            if duration_s < MIN_CUT_MATCH_S:
                unmeasurable_count += 1
                why = (
                    f"cut is shorter than the {format_s(MIN_CUT_MATCH_S)}s "
                    f"minimum reliable match window; no evidence, not passed"
                )
                receipt["cut_results"].append(
                    {
                        **base_row,
                        **levels_evidence,
                        "mode": "fingerprint",
                        "status": "UNMEASURABLE",
                        "why": why,
                    }
                )
                print(
                    f"{prefix} ref={role} ref_dbfs={level:.1f} "
                    f"mode=FINGERPRINT status=UNMEASURABLE WHY={why}"
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
                "mode": "fingerprint",
                "status": status,
                "why": why,
            }
            receipt["cut_results"].append(row)
            print(
                f"{prefix} ref={role} ref_dbfs={level:.1f} "
                f"mode=FINGERPRINT {print_match_fields(match)} status={status}"
                + (f" WHY={why}" if found else "")
            )

        if found_count or silent_failure_count:
            exit_code = 1
            verdict = "FAIL"
            problems = []
            if found_count:
                problems.append(
                    f"{found_count} cut span(s) confidently located in the render"
                )
            if silent_failure_count:
                problems.append(
                    f"{silent_failure_count} silent cut proof(s) failed"
                )
            summary = "; ".join(problems)
        elif not controls_ok:
            exit_code = 2
            verdict = "UNUSABLE"
            summary = (
                "inverse controls failed; this run cannot certify cut absence"
            )
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
            silent_parts = []
            if anchor_applied_count:
                silent_parts.append(
                    f"{anchor_applied_count} silent cut(s) CUT_APPLIED"
                )
            if fallback_pass_count:
                silent_parts.append(
                    f"{fallback_pass_count} silent fallback(s) SILENCE_GONE"
                )
            silent_summary = (
                "; " + ", ".join(silent_parts) if silent_parts else ""
            )
            summary = (
                f"all {len(cuts)} cut span(s) passed{silent_summary} and "
                f"both kept controls located"
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


def self_test_make_pause_stem(
    path, amplitude, seed, pause_start_s, pause_duration_s, total_duration_s=40.0
):
    after_duration_s = total_duration_s - pause_start_s - pause_duration_s
    before_source = (
        f"anoisesrc=color=pink:amplitude={amplitude}:sample_rate={SAMPLE_RATE}:"
        f"duration={format_s(pause_start_s)}:seed={seed}"
    )
    silence_source = (
        f"anullsrc=sample_rate={SAMPLE_RATE}:channel_layout=mono:"
        f"duration={format_s(pause_duration_s)}"
    )
    after_source = (
        f"anoisesrc=color=pink:amplitude={amplitude}:sample_rate={SAMPLE_RATE}:"
        f"duration={format_s(after_duration_s)}:seed={seed + 1}"
    )
    run_media_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            before_source,
            "-f",
            "lavfi",
            "-i",
            silence_source,
            "-f",
            "lavfi",
            "-i",
            after_source,
            "-filter_complex",
            "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map",
            "[out]",
            "-c:a",
            "pcm_f32le",
            path,
        ],
        f"self-test could not create synthetic stem {path}",
    )


def self_test_make_stem(path, amplitude, seed):
    self_test_make_pause_stem(path, amplitude, seed, 20.0, 1.2)


def self_test_make_render(source, path, segments):
    filters = []
    labels = []
    for index, (start_s, end_s) in enumerate(segments):
        label = f"a{index}"
        filters.append(
            f"[0:a]atrim=start={start_s}:end={end_s},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(segments)}:v=0:a=1[out]"
    )
    run_media_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-c:a",
            "pcm_f32le",
            path,
        ],
        f"self-test could not create synthetic render {path}",
    )


def self_test_args(render, stems, cuts, json_out):
    return argparse.Namespace(
        render=render,
        stems=stems,
        cuts=cuts,
        json_out=json_out,
        head_offset_s=0.0,
        self_test=False,
    )


def read_self_test_receipt(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"self-test cannot read receipt {path}: {exc}") from exc


def find_cut_result(receipt, cut_id):
    for row in receipt.get("cut_results", []):
        if row.get("id") == cut_id:
            return row
    return {}


def run_self_test():
    failures = []
    try:
        with tempfile.TemporaryDirectory(prefix="cut-absence-self-test-") as temp_dir:
            stems_dir = os.path.join(temp_dir, "stems")
            os.mkdir(stems_dir)
            host_stem = os.path.join(stems_dir, "host_audio.wav")
            guest_stem = os.path.join(stems_dir, "guest_audio.wav")
            edited_render = os.path.join(temp_dir, "edited.wav")
            unedited_render = os.path.join(temp_dir, "unedited-gap.wav")
            cuts_path = os.path.join(temp_dir, "cuts.json")
            beyond_path = os.path.join(temp_dir, "cuts-beyond.json")
            partial_stems_dir = os.path.join(temp_dir, "partial-stems")
            os.mkdir(partial_stems_dir)
            partial_host_stem = os.path.join(
                partial_stems_dir, "host_audio.wav"
            )
            partial_guest_stem = os.path.join(
                partial_stems_dir, "guest_audio.wav"
            )
            partial_render = os.path.join(temp_dir, "partial-pause.wav")
            partial_cuts_path = os.path.join(
                temp_dir, "cuts-partial-pause.json"
            )

            self_test_make_stem(host_stem, 0.20, 1103)
            self_test_make_stem(guest_stem, 0.10, 2207)
            self_test_make_render(
                host_stem,
                edited_render,
                [(0.0, 8.0), (9.0, 20.0), (21.2, 40.0)],
            )
            # The speech cut is applied, while the silent gap is deliberately retained.
            self_test_make_render(
                host_stem,
                unedited_render,
                [(0.0, 8.0), (9.0, 40.0)],
            )
            synthetic_cuts = [
                {"id": "speech-cut", "start_s": 8.0, "end_s": 9.0},
                {"id": "silent-cut", "start_s": 20.0, "end_s": 21.2},
            ]
            write_json(cuts_path, synthetic_cuts)
            write_json(
                beyond_path,
                [{"id": "beyond-media", "start_s": 39.5, "end_s": 41.0}],
            )
            self_test_make_pause_stem(
                partial_host_stem, 0.20, 3301, 20.0, 2.5
            )
            self_test_make_pause_stem(
                partial_guest_stem, 0.10, 4409, 20.0, 2.5
            )
            self_test_make_render(
                partial_host_stem,
                partial_render,
                [(0.0, 20.75), (21.75, 40.0)],
            )
            partial_cuts = [
                {
                    "id": "partial-pause-cut",
                    "start_s": 20.75,
                    "end_s": 21.75,
                }
            ]
            write_json(partial_cuts_path, partial_cuts)
            default_splice = render_splice_position(
                synthetic_cuts[1], synthetic_cuts, 0.0
            )
            offset_splice = render_splice_position(
                synthetic_cuts[1], synthetic_cuts, 2.5
            )
            print(
                "SELF_TEST_MEASURE case=splice-mapping "
                f"default_render_pos_s={format_s(default_splice)} "
                f"head_offset_s=2.500 offset_render_pos_s="
                f"{format_s(offset_splice)}"
            )
            if not math.isclose(default_splice, 19.0, abs_tol=1e-9):
                failures.append(
                    f"default splice was {default_splice}, expected 19.0"
                )
            if not math.isclose(offset_splice, 21.5, abs_tol=1e-9):
                failures.append(
                    f"offset splice was {offset_splice}, expected 21.5"
                )

            edited_json = os.path.join(temp_dir, "edited.json")
            print("SELF_TEST_CASE name=edited-gap-removed expected_exit=0")
            edited_code = run_check(
                self_test_args(
                    edited_render, stems_dir, cuts_path, edited_json
                )
            )
            edited_receipt = read_self_test_receipt(edited_json)
            edited_silent = find_cut_result(edited_receipt, "silent-cut")
            edited_speech = find_cut_result(edited_receipt, "speech-cut")
            print(
                "SELF_TEST_MEASURE case=edited-gap-removed "
                f"mode={edited_silent.get('mode', 'missing')} "
                f"silent_status={edited_silent.get('status', 'missing')} "
                f"peak_r={edited_silent.get('peak_r', 'missing')} "
                f"distance_to_applied_s="
                f"{edited_silent.get('distance_to_applied_s', 'missing')} "
                f"distance_to_not_applied_s="
                f"{edited_silent.get('distance_to_not_applied_s', 'missing')} "
                f"speech_status={edited_speech.get('status', 'missing')} "
                f"exit={edited_code}"
            )
            if edited_code != 0:
                failures.append(f"edited render exited {edited_code}, expected 0")
            if edited_silent.get("mode") != "POSITION_ANCHOR":
                failures.append(
                    "edited render silent cut did not use POSITION_ANCHOR"
                )
            if edited_silent.get("status") != "CUT_APPLIED":
                failures.append(
                    "edited render silent cut was not CUT_APPLIED"
                )
            if edited_speech.get("status") != "ABSENT":
                failures.append("edited render speech cut was not ABSENT")

            unedited_json = os.path.join(temp_dir, "unedited.json")
            print("SELF_TEST_CASE name=unedited-gap-retained expected_exit=1")
            unedited_code = run_check(
                self_test_args(
                    unedited_render, stems_dir, cuts_path, unedited_json
                )
            )
            unedited_receipt = read_self_test_receipt(unedited_json)
            unedited_silent = find_cut_result(unedited_receipt, "silent-cut")
            unedited_speech = find_cut_result(unedited_receipt, "speech-cut")
            print(
                "SELF_TEST_MEASURE case=unedited-gap-retained "
                f"mode={unedited_silent.get('mode', 'missing')} "
                f"silent_status={unedited_silent.get('status', 'missing')} "
                f"peak_r={unedited_silent.get('peak_r', 'missing')} "
                f"distance_to_applied_s="
                f"{unedited_silent.get('distance_to_applied_s', 'missing')} "
                f"distance_to_not_applied_s="
                f"{unedited_silent.get('distance_to_not_applied_s', 'missing')} "
                f"speech_status={unedited_speech.get('status', 'missing')} "
                f"exit={unedited_code}"
            )
            if unedited_code != 1:
                failures.append(
                    f"unedited render exited {unedited_code}, expected 1"
                )
            if unedited_silent.get("mode") != "POSITION_ANCHOR":
                failures.append(
                    "unedited render silent cut did not use POSITION_ANCHOR"
                )
            if unedited_silent.get("status") != "CUT_NOT_APPLIED":
                failures.append(
                    "unedited render silent cut was not CUT_NOT_APPLIED"
                )
            if unedited_speech.get("status") != "ABSENT":
                failures.append("unedited render speech cut was not ABSENT")

            partial_json = os.path.join(temp_dir, "partial-pause.json")
            partial_splice_s = render_splice_position(
                partial_cuts[0], partial_cuts, 0.0
            )
            retained_silence = measure_silent_runs(
                partial_render,
                probe_duration(partial_render),
                partial_splice_s,
            )
            retained_silence_s = retained_silence["longest_silent_run_s"]
            old_max_gap_s = max(
                0.6
                * (
                    partial_cuts[0]["end_s"]
                    - partial_cuts[0]["start_s"]
                ),
                MIN_GAP_S,
            )
            old_heuristic_would_fail = retained_silence_s >= old_max_gap_s
            print("SELF_TEST_CASE name=partial-pause-middle-cut expected_exit=0")
            partial_code = run_check(
                self_test_args(
                    partial_render,
                    partial_stems_dir,
                    partial_cuts_path,
                    partial_json,
                )
            )
            partial_receipt = read_self_test_receipt(partial_json)
            partial_silent = find_cut_result(
                partial_receipt, "partial-pause-cut"
            )
            print(
                "SELF_TEST_MEASURE case=partial-pause-middle-cut "
                f"retained_silence_s={format_s(retained_silence_s)} "
                f"old_max_gap_s={format_s(old_max_gap_s)} "
                f"old_heuristic_would_fail={str(old_heuristic_would_fail).lower()} "
                f"mode={partial_silent.get('mode', 'missing')} "
                f"silent_status={partial_silent.get('status', 'missing')} "
                f"peak_r={partial_silent.get('peak_r', 'missing')} "
                f"distance_to_applied_s="
                f"{partial_silent.get('distance_to_applied_s', 'missing')} "
                f"distance_to_not_applied_s="
                f"{partial_silent.get('distance_to_not_applied_s', 'missing')} "
                f"exit={partial_code}"
            )
            if retained_silence_s < 1.45:
                failures.append(
                    "partial-pause render retained less than 1.45s of silence"
                )
            if not old_heuristic_would_fail:
                failures.append(
                    "partial-pause case would not fail the old silence heuristic"
                )
            if partial_code != 0:
                failures.append(
                    f"partial-pause render exited {partial_code}, expected 0"
                )
            if partial_silent.get("mode") != "POSITION_ANCHOR":
                failures.append(
                    "partial-pause silent cut did not use POSITION_ANCHOR"
                )
            if partial_silent.get("status") != "CUT_APPLIED":
                failures.append(
                    "partial-pause silent cut was not CUT_APPLIED"
                )

            beyond_json = os.path.join(temp_dir, "beyond.json")
            print("SELF_TEST_CASE name=span-beyond-media expected_exit=2")
            beyond_code = run_check(
                self_test_args(
                    edited_render, stems_dir, beyond_path, beyond_json
                )
            )
            beyond_receipt = read_self_test_receipt(beyond_json)
            print(
                "SELF_TEST_MEASURE case=span-beyond-media "
                f"status={beyond_receipt.get('verdict', 'missing')} "
                f"exit={beyond_code}"
            )
            if beyond_code != 2:
                failures.append(
                    f"beyond-media receipt exited {beyond_code}, expected 2"
                )
    except (InputError, OSError) as exc:
        failures.append(str(exc))

    if failures:
        for failure in failures:
            print(f"SELF_TEST_ASSERT status=FAIL WHY={failure}")
        print(
            f"SELF_TEST status=FAIL exit=1 WHY={len(failures)} assertion(s) failed"
        )
        return 1
    print("SELF_TEST_ASSERT status=PASS green=green red=red unusable=unusable")
    print("SELF_TEST status=PASS exit=0 WHY=all synthetic proofs matched")
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
