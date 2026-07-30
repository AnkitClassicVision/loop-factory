#!/usr/bin/env python3
"""Verify that rendered podcast clips came from their claimed source spans.

Exit 0: every declared clip variant locates at its expected source-window lag.
Exit 1: at least one measured clip violates that claim or lacks evidence.
Exit 2: an input is missing, malformed, or cannot be measured.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np


SAMPLE_RATE = 16000  # 16 kHz mono preserves speech detail while keeping FFTs modest.
SILENCE_DBFS = -55.0  # Quieter stem windows do not contain speech worth matching.
MIN_PEAK = 0.35  # The reference certifier's floor rejects weak accidental matches.
MIN_MARGIN_RATIO = 1.25  # The best match must clearly beat the next distinct candidate.
RUNNER_GUARD_S = 0.500  # Nearby samples belong to the same correlation peak, not a rival.
CLIP_LAG_TOLERANCE_S = 1.5  # Manifest rounding and codec priming may shift source starts.
MAX_LAG_ERROR_S = CLIP_LAG_TOLERANCE_S  # Legacy receipt field retained for compatibility.
MIN_AUDIO_S = 0.250  # Shorter material cannot provide a dependable speech fingerprint.
MAX_CODEC_TRIM_S = 0.250  # Permit only small codec tail padding when clip exceeds the source.
TIME_EPSILON_S = 0.001  # Millisecond rounding noise must not invalidate ordered timestamps.

STEM_EXTENSIONS = ("flac", "wav", "m4a", "mp3")
LEGACY_OUTPUT_FIELDS = (
    ("vertical", "vertical_path"),
    ("linkedin", "landscape_path"),
)


class InputError(Exception):
    """An input could not be used to make a measurement."""


def fmt_s(value):
    return "%.3f" % value


def rounded_s(value):
    return round(float(value), 3)


def finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("%s must be a number" % field)
    result = float(value)
    if not math.isfinite(result):
        raise InputError("%s must be finite" % field)
    return result


def command_error(tool, path, exc):
    if isinstance(exc, FileNotFoundError):
        return "%s executable was not found" % tool
    stderr = getattr(exc, "stderr", b"")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    detail = " ".join(str(stderr).strip().split())
    if detail:
        return "%s could not read %s: %s" % (tool, path, detail)
    return "%s could not read %s" % (tool, path)


def probe_duration(path):
    try:
        proc = subprocess.run(
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
            check=True,
            capture_output=True,
            text=True,
        )
        duration = float(proc.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        raise InputError(command_error("ffprobe", path, exc)) from exc
    if not math.isfinite(duration) or duration < MIN_AUDIO_S:
        raise InputError(
            "media duration is unusable for %s: %s seconds"
            % (path, fmt_s(duration) if math.isfinite(duration) else "non-finite")
        )
    return duration


def extract_audio(path, start_s, duration_s):
    if duration_s < MIN_AUDIO_S:
        raise InputError(
            "requested audio window is only %s seconds for %s"
            % (fmt_s(duration_s), path)
        )
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                fmt_s(max(0.0, start_s)),
                "-t",
                fmt_s(duration_s),
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
            ],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise InputError(command_error("ffmpeg", path, exc)) from exc
    samples = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    if samples.size < int(MIN_AUDIO_S * SAMPLE_RATE):
        raise InputError(
            "ffmpeg returned only %s seconds of audio for %s"
            % (fmt_s(samples.size / SAMPLE_RATE), path)
        )
    return samples


def dbfs(samples):
    if samples.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20.0 * math.log10(rms) if rms > 0.0 else -math.inf


def locate(needle, haystack):
    """Return the best valid lag and absolute, window-normalized FFT correlations."""
    if needle.size == 0 or haystack.size <= needle.size:
        return None, 0.0, 0.0

    needle = needle.astype(np.float64)
    haystack = haystack.astype(np.float64)
    needle -= float(needle.mean())
    needle_energy = float(np.sqrt(np.sum(needle * needle)))
    if needle_energy == 0.0:
        return None, 0.0, 0.0

    fft_size = 1
    while fft_size < haystack.size + needle.size - 1:
        fft_size *= 2
    corr = np.fft.irfft(
        np.fft.rfft(haystack, fft_size)
        * np.conj(np.fft.rfft(needle, fft_size)),
        fft_size,
    )[: haystack.size - needle.size + 1]

    cumulative_sum = np.concatenate(
        ([0.0], np.cumsum(haystack, dtype=np.float64))
    )
    cumulative_square = np.concatenate(
        ([0.0], np.cumsum(haystack * haystack, dtype=np.float64))
    )
    window_sum = cumulative_sum[needle.size :] - cumulative_sum[: corr.size]
    window_square = (
        cumulative_square[needle.size :] - cumulative_square[: corr.size]
    )
    window_variance = np.maximum(
        window_square - (window_sum * window_sum) / needle.size,
        0.0,
    )
    denominator = needle_energy * np.sqrt(window_variance)
    usable = denominator > np.finfo(np.float64).tiny
    corr[usable] /= denominator[usable]
    corr[~usable] = 0.0
    scores = np.abs(np.clip(corr, -1.0, 1.0))

    best = int(np.argmax(scores))
    peak = float(scores[best])
    guard = int(RUNNER_GUARD_S * SAMPLE_RATE)
    rivals = scores.copy()
    rivals[max(0, best - guard) : min(corr.size, best + guard + 1)] = -np.inf
    runner = float(np.max(rivals)) if np.isfinite(rivals).any() else 0.0
    return best, peak, runner


def find_stems(stems_dir):
    found = {}
    for role in ("host", "guest"):
        for extension in STEM_EXTENSIONS:
            candidate = os.path.join(stems_dir, "%s_audio.%s" % (role, extension))
            if os.path.isfile(candidate):
                found[role] = candidate
                break
    return found


def output_variants(entry):
    outputs = entry.get("outputs")
    if outputs is not None:
        if not isinstance(outputs, dict):
            raise InputError("outputs must be an object")
        variants = list(outputs.items())
    else:
        variants = [
            (variant, entry[field])
            for variant, field in LEGACY_OUTPUT_FIELDS
            if entry.get(field) is not None
        ]
    if not variants:
        raise InputError("clip has no declared output files")
    return variants


def resolve_output_path(declared_path, manifest_dir, clips_dir):
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise InputError("output path must be a non-empty string")
    if clips_dir:
        return os.path.join(clips_dir, os.path.basename(declared_path))
    if os.path.isabs(declared_path):
        return declared_path
    return os.path.join(manifest_dir, declared_path)


def margin_ratio(peak, runner):
    if runner <= 0.0:
        return math.inf if peak > 0.0 else 0.0
    return peak / runner


def ratio_for_json(value):
    return "inf" if math.isinf(value) else round(float(value), 3)


def unusable_variant(variant, path, why):
    return {
        "variant": str(variant),
        "file": path,
        "status": "UNUSABLE",
        "why": why,
    }


def measure_variant(
    variant,
    path,
    source,
    source_window_start,
    claimed_source_start,
    claimed_source_duration,
    claimed_final_duration,
):
    try:
        clip_duration = probe_duration(path)
        extraction_duration = min(clip_duration, claimed_final_duration)
        clip_audio = extract_audio(path, 0.0, extraction_duration)
    except InputError as exc:
        return unusable_variant(variant, path, str(exc))

    trimmed_s = 0.0
    claimed_source_samples = int(round(claimed_source_duration * SAMPLE_RATE))
    if clip_audio.size > claimed_source_samples:
        excess = (clip_audio.size - claimed_source_samples) / SAMPLE_RATE
        if excess > MAX_CODEC_TRIM_S:
            why = (
                "clip audio is %s seconds longer than the claimed source window; "
                "it cannot locate inside that span" % fmt_s(excess)
            )
            return {
                "variant": str(variant),
                "file": path,
                "status": "FAIL",
                "why": why,
                "clip_duration_s": rounded_s(clip_duration),
                "measured_audio_s": rounded_s(clip_audio.size / SAMPLE_RATE),
                "best_match_lag_s": None,
            }
        trimmed_s = excess
        clip_audio = clip_audio[:claimed_source_samples]

    if source.size <= clip_audio.size:
        return {
            "variant": str(variant),
            "file": path,
            "status": "FAIL",
            "why": (
                "the padded stem extraction contains no lag search space; "
                "the source media does not provide the required tolerance"
            ),
            "clip_duration_s": rounded_s(clip_duration),
            "measured_audio_s": rounded_s(clip_audio.size / SAMPLE_RATE),
            "best_match_lag_s": None,
        }

    lag_samples, peak, runner = locate(clip_audio, source)
    if lag_samples is None:
        return {
            "variant": str(variant),
            "file": path,
            "status": "FAIL",
            "why": "clip audio has no usable waveform energy to correlate",
            "clip_duration_s": rounded_s(clip_duration),
            "measured_audio_s": rounded_s(clip_audio.size / SAMPLE_RATE),
            "best_match_lag_s": None,
        }

    lag_s = lag_samples / SAMPLE_RATE
    expected_lag_s = claimed_source_start - source_window_start
    lag_error_s = lag_s - expected_lag_s
    implied_source_start_s = source_window_start + lag_s
    source_start_offset_s = implied_source_start_s - claimed_source_start
    ratio = margin_ratio(peak, runner)
    failures = []
    if peak < MIN_PEAK:
        failures.append(
            "peak |r| %.3f is below %.3f, so the claimed source is not a strong match"
            % (peak, MIN_PEAK)
        )
    if ratio < MIN_MARGIN_RATIO:
        failures.append(
            "peak/runner-up margin %.3f is below %.3f, so the match is ambiguous"
            % (ratio, MIN_MARGIN_RATIO)
        )
    if abs(source_start_offset_s) > CLIP_LAG_TOLERANCE_S:
        failures.append(
            "implied source start is %s seconds, offset %s seconds from the "
            "claimed %s seconds, exceeding the %s-second tolerance"
            % (
                fmt_s(implied_source_start_s),
                fmt_s(source_start_offset_s),
                fmt_s(claimed_source_start),
                fmt_s(CLIP_LAG_TOLERANCE_S),
            )
        )

    row = {
        "variant": str(variant),
        "file": path,
        "status": "FAIL" if failures else "PASS",
        "clip_duration_s": rounded_s(clip_duration),
        "measured_audio_s": rounded_s(clip_audio.size / SAMPLE_RATE),
        "codec_tail_trimmed_s": rounded_s(trimmed_s),
        "expected_lag_s": rounded_s(expected_lag_s),
        "best_match_lag_s": rounded_s(lag_s),
        "lag_error_s": rounded_s(lag_error_s),
        "implied_source_start_s": rounded_s(implied_source_start_s),
        "source_start_offset_s": rounded_s(source_start_offset_s),
        "search_positions": int(source.size - clip_audio.size + 1),
        "peak": round(peak, 3),
        "runner_up": round(runner, 3),
        "margin_ratio": ratio_for_json(ratio),
    }
    if failures:
        row["why"] = "; ".join(failures)
    return row


def validate_times(entry):
    raw_start = finite_number(entry.get("raw_start_s"), "raw_start_s")
    raw_end = finite_number(entry.get("raw_end_s"), "raw_end_s")
    final_start = finite_number(entry.get("final_start_s"), "final_start_s")
    final_end = finite_number(entry.get("final_end_s"), "final_end_s")
    if raw_start < 0.0 or final_start < 0.0:
        raise InputError("start timestamps must be non-negative")
    if raw_end - raw_start < MIN_AUDIO_S:
        raise InputError("raw source span must be at least %s seconds" % fmt_s(MIN_AUDIO_S))
    if final_end - final_start < MIN_AUDIO_S:
        raise InputError("final clip span must be at least %s seconds" % fmt_s(MIN_AUDIO_S))
    if raw_end + TIME_EPSILON_S < raw_start or final_end + TIME_EPSILON_S < final_start:
        raise InputError("end timestamps must follow start timestamps")
    return raw_start, raw_end, final_start, final_end


def print_variant(row):
    label = "  variant=%s %s" % (row["variant"], row["status"])
    if row["status"] == "UNUSABLE":
        print("%s WHY: %s" % (label, row["why"]))
        return
    if row.get("best_match_lag_s") is None:
        print("%s best_lag_s=n/a WHY: %s" % (label, row["why"]))
        return
    ratio = row["margin_ratio"]
    ratio_text = ratio if isinstance(ratio, str) else "%.3f" % ratio
    evidence = (
        "peak=%.3f runner=%.3f margin=%s best_lag_s=%s "
        "expected_lag_s=%s lag_error_s=%s implied_source_start_s=%s "
        "source_start_offset_s=%s"
        % (
            row["peak"],
            row["runner_up"],
            ratio_text,
            fmt_s(row["best_match_lag_s"]),
            fmt_s(row["expected_lag_s"]),
            fmt_s(row["lag_error_s"]),
            fmt_s(row["implied_source_start_s"]),
            fmt_s(row["source_start_offset_s"]),
        )
    )
    if row["status"] == "FAIL":
        print("%s %s WHY: %s" % (label, evidence, row["why"]))
    else:
        print("%s %s" % (label, evidence))


def measure_clip(entry, position, stems, manifest_dir, clips_dir):
    if not isinstance(entry, dict):
        clip_id = position
        base = {"index": clip_id, "position": position}
        why = "manifest clip entry must be an object"
        row = dict(base, status="UNUSABLE", why=why, variants=[])
        print("CLIP %s UNUSABLE WHY: %s" % (clip_id, why))
        return row
    clip_id = entry.get("index", position)
    base = {"index": clip_id, "position": position}

    try:
        raw_start, raw_end, final_start, final_end = validate_times(entry)
        variants = output_variants(entry)
    except InputError as exc:
        why = str(exc)
        row = dict(base, status="UNUSABLE", why=why, variants=[])
        print("CLIP %s UNUSABLE WHY: %s" % (clip_id, why))
        return row

    base.update(
        {
            "raw_start_s": rounded_s(raw_start),
            "raw_end_s": rounded_s(raw_end),
            "final_start_s": rounded_s(final_start),
            "final_end_s": rounded_s(final_end),
        }
    )
    raw_duration = raw_end - raw_start
    final_duration = final_end - final_start

    stem_windows = {}
    stem_window_starts = {}
    stem_window_ends = {}
    stem_levels = {}
    stem_errors = {}
    for role, path in stems.items():
        try:
            stem_duration = probe_duration(path)
            window_start = max(0.0, raw_start - CLIP_LAG_TOLERANCE_S)
            window_end = min(stem_duration, raw_end + CLIP_LAG_TOLERANCE_S)
            samples = extract_audio(path, window_start, window_end - window_start)
            stem_windows[role] = samples
            stem_window_starts[role] = window_start
            stem_window_ends[role] = window_start + samples.size / SAMPLE_RATE
            level_start = int(round((raw_start - window_start) * SAMPLE_RATE))
            level_end = level_start + int(round(raw_duration * SAMPLE_RATE))
            level_samples = samples[max(0, level_start) : max(0, level_end)]
            stem_levels[role] = dbfs(level_samples)
        except InputError as exc:
            stem_errors[role] = str(exc)

    if stem_errors:
        why = "; ".join(
            "%s stem: %s" % (role, error)
            for role, error in sorted(stem_errors.items())
        )
        rows = []
        for variant, declared_path in variants:
            try:
                path = resolve_output_path(declared_path, manifest_dir, clips_dir)
            except InputError:
                path = str(declared_path)
            rows.append(unusable_variant(variant, path, why))
        row = dict(base, status="UNUSABLE", why=why, variants=rows)
        print(
            "CLIP %s raw=%s..%s final=%s..%s UNUSABLE WHY: %s"
            % (
                clip_id,
                fmt_s(raw_start),
                fmt_s(raw_end),
                fmt_s(final_start),
                fmt_s(final_end),
                why,
            )
        )
        for variant_row in rows:
            print_variant(variant_row)
        return row

    speaker = max(stem_levels, key=stem_levels.get)
    level = stem_levels[speaker]
    levels_json = {
        role: round(value, 1) if math.isfinite(value) else "-inf"
        for role, value in stem_levels.items()
    }
    base.update(
        {
            "selected_speaker": speaker,
            "selected_speaker_dbfs": round(level, 1) if math.isfinite(level) else "-inf",
            "stem_levels_dbfs": levels_json,
            "source_window_start_s": rounded_s(stem_window_starts[speaker]),
            "source_window_end_s": rounded_s(stem_window_ends[speaker]),
        }
    )

    print(
        "CLIP %s raw=%s..%s final=%s..%s speaker=%s level=%.1f_dBFS"
        % (
            clip_id,
            fmt_s(raw_start),
            fmt_s(raw_end),
            fmt_s(final_start),
            fmt_s(final_end),
            speaker,
            level,
        )
    )

    if level < SILENCE_DBFS:
        why = (
            "neither available stem carries measurable speech in the claimed "
            "source span; no evidence is not a pass"
        )
        rows = []
        for variant, declared_path in variants:
            try:
                path = resolve_output_path(declared_path, manifest_dir, clips_dir)
            except InputError:
                path = str(declared_path)
            failed = {
                "variant": str(variant),
                "file": path,
                "status": "FAIL",
                "why": why,
                "best_match_lag_s": None,
            }
            rows.append(failed)
            print_variant(failed)
        return dict(base, status="FAIL", why=why, variants=rows)

    rows = []
    source = stem_windows[speaker]
    source_window_start = stem_window_starts[speaker]
    for variant, declared_path in variants:
        try:
            path = resolve_output_path(declared_path, manifest_dir, clips_dir)
            if not os.path.isfile(path):
                raise InputError("output file does not exist: %s" % path)
            variant_row = measure_variant(
                variant,
                path,
                source,
                source_window_start,
                raw_start,
                raw_duration,
                final_duration,
            )
        except InputError as exc:
            path = str(declared_path)
            variant_row = unusable_variant(variant, path, str(exc))
        rows.append(variant_row)
        print_variant(variant_row)

    statuses = {item["status"] for item in rows}
    if "UNUSABLE" in statuses:
        status = "UNUSABLE"
    elif "FAIL" in statuses:
        status = "FAIL"
    else:
        status = "PASS"
    why = "; ".join(
        "%s: %s" % (item["variant"], item["why"])
        for item in rows
        if item["status"] != "PASS"
    )
    result = dict(base, status=status, variants=rows)
    if why:
        result["why"] = why
        print("CLIP %s VERDICT %s WHY: %s" % (clip_id, status, why))
    else:
        print("CLIP %s VERDICT PASS" % clip_id)
    return result


def load_manifest(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError as exc:
        raise InputError("manifest does not exist: %s" % path) from exc
    except OSError as exc:
        raise InputError("manifest cannot be read: %s: %s" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise InputError(
            "manifest is not valid JSON: line %d column %d: %s"
            % (exc.lineno, exc.colno, exc.msg)
        ) from exc
    if not isinstance(manifest, dict):
        raise InputError("manifest root must be an object")
    clips = manifest.get("clips")
    if not isinstance(clips, list):
        raise InputError("manifest clips must be an array")
    if not clips:
        raise InputError("manifest has no clips; no evidence is not a pass")
    return clips


def write_json(path, receipt):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise InputError("cannot write JSON receipt %s: %s" % (path, exc)) from exc


def run_self_test_ffmpeg(command, output_path):
    try:
        subprocess.run(command, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise InputError(command_error("ffmpeg", output_path, exc)) from exc


def run_self_test():
    cases = (
        ("exact", 20.00, 20.00, "PASS", 0.00),
        ("rounded-420ms", 20.42, 20.00, "PASS", 0.42),
        ("wrong-source", 45.00, 20.00, "FAIL", None),
        ("outside-tolerance", 20.00, 22.00, "FAIL", None),
    )
    try:
        with tempfile.TemporaryDirectory(prefix="clip-words-check-") as temp_dir:
            stem_path = os.path.join(temp_dir, "host_audio.wav")
            run_self_test_ffmpeg(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        "anoisesrc=color=white:seed=20260728:"
                        "duration=60:sample_rate=%d" % SAMPLE_RATE
                    ),
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    stem_path,
                ],
                stem_path,
            )

            clip_paths = {}
            for actual_start in sorted({case[1] for case in cases}):
                clip_path = os.path.join(
                    temp_dir, "clip_%s.wav" % fmt_s(actual_start).replace(".", "_")
                )
                run_self_test_ffmpeg(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        fmt_s(actual_start),
                        "-t",
                        "6.000",
                        "-i",
                        stem_path,
                        "-map",
                        "0:a:0",
                        "-ac",
                        "1",
                        "-ar",
                        str(SAMPLE_RATE),
                        "-c:a",
                        "pcm_s16le",
                        "-y",
                        clip_path,
                    ],
                    clip_path,
                )
                clip_paths[actual_start] = clip_path

            failures = []
            stems = {"host": stem_path}
            for position, (
                label,
                actual_start,
                claimed_start,
                expected_status,
                expected_offset,
            ) in enumerate(cases, 1):
                entry = {
                    "index": label,
                    "raw_start_s": claimed_start,
                    "raw_end_s": claimed_start + 6.0,
                    "final_start_s": 0.0,
                    "final_end_s": 6.0,
                    "outputs": {"self-test": clip_paths[actual_start]},
                }
                result = measure_clip(
                    entry, position, stems, temp_dir, clips_dir=None
                )
                observed_status = result["status"]
                variant = result["variants"][0]
                observed_offset = variant.get("source_start_offset_s")
                print(
                    "SELF-TEST %s expected=%s observed=%s offset_s=%s"
                    % (
                        label,
                        expected_status,
                        observed_status,
                        (
                            fmt_s(observed_offset)
                            if observed_offset is not None
                            else "n/a"
                        ),
                    )
                )
                if observed_status != expected_status:
                    failures.append(
                        "%s expected %s but observed %s"
                        % (label, expected_status, observed_status)
                    )
                if expected_offset is not None:
                    if observed_offset is None or abs(
                        observed_offset - expected_offset
                    ) > 0.010:
                        failures.append(
                            "%s expected offset %s seconds but observed %s"
                            % (
                                label,
                                fmt_s(expected_offset),
                                (
                                    fmt_s(observed_offset)
                                    if observed_offset is not None
                                    else "n/a"
                                ),
                            )
                        )
                    if variant.get("search_positions", 0) <= 1:
                        failures.append(
                            "%s did not exercise a non-degenerate lag search" % label
                        )
    except InputError as exc:
        print("SELF-TEST UNUSABLE: %s" % exc)
        return 2

    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: %s" % failure)
        return 1
    print("SELF-TEST PASS: 2 green cases passed and 2 red cases failed.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Prove rendered clips contain their claimed stem material."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run a hermetic ffmpeg-generated lag-tolerance test",
    )
    parser.add_argument("--manifest")
    parser.add_argument("--stems")
    parser.add_argument("--clips-dir")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.manifest:
        parser.error("--manifest is required unless --self-test is used")
    if not args.stems:
        parser.error("--stems is required unless --self-test is used")

    receipt = {
        "manifest": args.manifest,
        "stems": args.stems,
        "clips_dir": args.clips_dir,
        "thresholds": {
            "sample_rate_hz": SAMPLE_RATE,
            "silence_dbfs": SILENCE_DBFS,
            "min_peak": MIN_PEAK,
            "min_margin_ratio": MIN_MARGIN_RATIO,
            "runner_guard_s": rounded_s(RUNNER_GUARD_S),
            "clip_lag_tolerance_s": rounded_s(CLIP_LAG_TOLERANCE_S),
            "max_lag_error_s": rounded_s(MAX_LAG_ERROR_S),
            "min_audio_s": rounded_s(MIN_AUDIO_S),
            "max_codec_trim_s": rounded_s(MAX_CODEC_TRIM_S),
        },
        "clips": [],
    }

    try:
        clips = load_manifest(args.manifest)
        if not os.path.isdir(args.stems):
            raise InputError("stems directory does not exist: %s" % args.stems)
        if args.clips_dir and not os.path.isdir(args.clips_dir):
            raise InputError("clips directory does not exist: %s" % args.clips_dir)
        stems = find_stems(args.stems)
        if not stems:
            expected = ", ".join(
                "%s_audio.%s" % (role, extension)
                for role in ("host", "guest")
                for extension in STEM_EXTENSIONS
            )
            raise InputError(
                "no speaker stems found under %s; expected one of: %s"
                % (args.stems, expected)
            )
    except InputError as exc:
        receipt["status"] = "UNUSABLE"
        receipt["why"] = str(exc)
        receipt["summary"] = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "unusable": 0,
        }
        print("UNUSABLE: %s" % exc)
        if args.json_out:
            try:
                write_json(args.json_out, receipt)
            except InputError as write_exc:
                print("UNUSABLE: %s" % write_exc)
        return 2

    manifest_dir = os.path.dirname(os.path.abspath(args.manifest))
    for position, entry in enumerate(clips, 1):
        receipt["clips"].append(
            measure_clip(
                entry, position, stems, manifest_dir, args.clips_dir
            )
        )

    passed = sum(row["status"] == "PASS" for row in receipt["clips"])
    failed = sum(row["status"] == "FAIL" for row in receipt["clips"])
    unusable = sum(row["status"] == "UNUSABLE" for row in receipt["clips"])
    receipt["summary"] = {
        "total": len(receipt["clips"]),
        "passed": passed,
        "failed": failed,
        "unusable": unusable,
    }

    if unusable:
        receipt["status"] = "UNUSABLE"
        exit_code = 2
        print(
            "UNUSABLE: %d/%d clips could not be measured; %d passed and %d "
            "measured violation(s)."
            % (unusable, len(receipt["clips"]), passed, failed)
        )
    elif failed:
        receipt["status"] = "FAIL"
        exit_code = 1
        print(
            "FAIL: %d/%d clips violate the claimed source mapping; %d passed."
            % (failed, len(receipt["clips"]), passed)
        )
    else:
        receipt["status"] = "PASS"
        exit_code = 0
        print(
            "PASS: all %d clips and all declared variants locate in their "
            "claimed source windows." % passed
        )

    if args.json_out:
        try:
            write_json(args.json_out, receipt)
        except InputError as exc:
            print("UNUSABLE: %s" % exc)
            return 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
