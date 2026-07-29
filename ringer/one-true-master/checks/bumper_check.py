#!/usr/bin/env python3
"""Standalone certification checker for podcast intro and outro bumpers."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


# 8 kHz retains speech/music timing while keeping exhaustive FFT correlation cheap.
SAMPLE_RATE_HZ = 8_000
# Five seconds searches beyond each expected slot without reaching far into the body.
SLOT_SEARCH_PADDING_S = 5.0
# Peaks within 0.5 seconds are one alignment lobe, not independent alternatives.
DISTANT_PEAK_EXCLUSION_S = 0.5
# A five-second guard keeps the middle uniqueness window well clear of both slots.
MIDDLE_SLOT_GUARD_S = 5.0
# The uniqueness window gets five seconds beyond the longer bumper for useful context.
MIDDLE_WINDOW_PADDING_S = 5.0
# An interior copy at half the presence threshold is strong enough to reject uniqueness.
UNIQUENESS_THRESHOLD_FACTOR = 0.5
# 0.60 accepts codec damage while rejecting unrelated episode audio.
DEFAULT_MIN_CORR = 0.60
# 0.75 seconds covers AAC priming but still rejects a bumper in the wrong slot.
DEFAULT_LAG_TOLERANCE_S = 0.75
# 0.75 LU is the owner's approved deviation from each asset's supplied target.
DEFAULT_LEVEL_TOLERANCE_LU = 0.75
# Two samples are the minimum needed to define nonzero centered signal energy.
MIN_AUDIO_SAMPLES = 2
# Float64 tiny is the fail-closed boundary for effectively silent correlation inputs.
MIN_CORRELATION_ENERGY = np.finfo(np.float64).tiny
# One thousand stderr characters preserve a useful ffmpeg failure without log flooding.
COMMAND_ERROR_TAIL_CHARS = 1_000

# Five seconds keeps the global-energy regression above the production threshold.
SELF_TEST_BUMPER_DURATION_S = 5.0
# Twenty seconds leaves a guarded middle window between the two synthetic slots.
SELF_TEST_BODY_DURATION_S = 20.0
# -19 LUFS represents the approved per-asset bumper target in the regression.
SELF_TEST_TARGET_LUFS = -19.0
# -16 LUFS represents the body baseline that the checker must reject for bumpers.
SELF_TEST_WRONG_TARGET_LUFS = -16.0
# Two raw AAC encoding passes expose the proven roughly 43 ms decoder displacement.
SELF_TEST_EXPECTED_PRIMING_S = 0.043
# Thirty milliseconds allows ffmpeg-version variation while still proving real delay.
SELF_TEST_PRIMING_TOLERANCE_S = 0.030


class CheckerError(RuntimeError):
    """An input or local tool failure that makes certification unusable."""


class OneLineArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures inside the check/status/WHY output contract."""

    def error(self, message: str) -> None:
        print(
            f"CLI FAIL measured=unavailable WHY {json.dumps(one_line(message))}",
            file=sys.stderr,
        )
        raise SystemExit(2)


@dataclass(frozen=True)
class MediaInputs:
    final: Path
    intro: Path
    outro: Path
    final_duration_s: float
    intro_duration_s: float
    outro_duration_s: float


@dataclass(frozen=True)
class CorrelationPeak:
    r: float
    abs_r: float
    lag_samples: int
    second_distant_abs_r: float
    distant_margin: float


@dataclass
class CheckResult:
    check: str
    status: str
    metrics: dict[str, Any]
    why: str | None = None

    def line(self) -> str:
        fields = [self.check, self.status]
        fields.extend(
            f"{name}={format_metric(value)}"
            for name, value in self.metrics.items()
        )
        if self.why:
            fields.extend(("WHY", json.dumps(one_line(self.why))))
        return " ".join(fields)


def one_line(value: object) -> str:
    return " ".join(str(value).splitlines())


def format_metric(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return f"{number:.4f}" if math.isfinite(number) else str(number)
    return json.dumps(one_line(value))


def emit(result: CheckResult) -> None:
    print(result.line())


def run_command(
    command: Sequence[str],
    *,
    purpose: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CheckerError(
            f"{purpose}: executable not found: {command[0]}"
        ) from exc
    except OSError as exc:
        raise CheckerError(
            f"{purpose}: could not start {command[0]}: {exc}"
        ) from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CheckerError(
            f"{purpose}: {command[0]} exited {completed.returncode}: "
            f"{one_line(stderr[-COMMAND_ERROR_TAIL_CHARS:])}"
        )
    return completed


def require_readable_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise CheckerError(
            f"{label} is missing or is not a regular file: {path}"
        )
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise CheckerError(f"{label} is unreadable: {path}: {exc}") from exc


def probe_duration(path: Path, label: str) -> float:
    completed = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            os.fspath(path),
        ],
        purpose=f"probe {label}",
    )
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        duration_s = float(raw)
    except ValueError as exc:
        raise CheckerError(
            f"{label} has no usable duration: {raw!r}"
        ) from exc
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise CheckerError(
            f"{label} duration must be positive and finite, got {duration_s!r}"
        )
    return duration_s


def load_inputs(final: Path, intro: Path, outro: Path) -> MediaInputs:
    for path, label in (
        (final, "final"),
        (intro, "intro bumper"),
        (outro, "outro bumper"),
    ):
        require_readable_file(path, label)
    return MediaInputs(
        final=final,
        intro=intro,
        outro=outro,
        final_duration_s=probe_duration(final, "final"),
        intro_duration_s=probe_duration(intro, "intro bumper"),
        outro_duration_s=probe_duration(outro, "outro bumper"),
    )


def decode_mono_f32(
    path: Path,
    *,
    label: str,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> np.ndarray:
    trim = f"atrim=start={max(0.0, start_s):.9f}"
    if duration_s is not None:
        trim += f":duration={max(0.0, duration_s):.9f}"
    completed = run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            os.fspath(path),
            "-vn",
            "-af",
            f"{trim},asetpts=PTS-STARTPTS,aresample={SAMPLE_RATE_HZ}",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-f",
            "f32le",
            "pipe:1",
        ],
        purpose=f"decode {label}",
    )
    samples = np.frombuffer(completed.stdout, dtype="<f4").astype(
        np.float64,
        copy=False,
    )
    if samples.size < MIN_AUDIO_SAMPLES:
        raise CheckerError(
            f"{label} decoded to {samples.size} samples; "
            f"at least {MIN_AUDIO_SAMPLES} are required"
        )
    if not np.all(np.isfinite(samples)):
        raise CheckerError(f"{label} decoded to non-finite samples")
    return samples


def full_lag_fft_ncc(
    search: np.ndarray,
    template: np.ndarray,
) -> CorrelationPeak:
    """Return the global-energy normalized peak over every linear lag.

    Lag zero aligns the first template sample with the first search sample;
    positive lags move the template start later in the search.  Centering and
    normalization use each complete input vector, matching a conventional
    full-lag FFT normalized cross-correlation.  Global normalization keeps
    short edge overlaps from acquiring an artificial |r| near one.
    """
    if search.ndim != 1 or template.ndim != 1:
        raise CheckerError("correlation inputs must be mono vectors")
    if search.size < template.size:
        raise CheckerError(
            "correlation search window is shorter than its bumper template"
        )
    if template.size < MIN_AUDIO_SAMPLES:
        raise CheckerError("bumper template is too short for correlation")

    centered_search = search - float(np.mean(search))
    centered_template = template - float(np.mean(template))
    search_energy = float(np.dot(centered_search, centered_search))
    template_energy = float(np.dot(centered_template, centered_template))
    if (
        not math.isfinite(search_energy)
        or search_energy <= MIN_CORRELATION_ENERGY
    ):
        raise CheckerError("correlation search has zero or invalid energy")
    if (
        not math.isfinite(template_energy)
        or template_energy <= MIN_CORRELATION_ENERGY
    ):
        raise CheckerError("bumper template has zero or invalid energy")

    linear_size = search.size + template.size - 1
    fft_size = 1 << (linear_size - 1).bit_length()
    cross_correlation = np.fft.irfft(
        np.fft.rfft(centered_search, fft_size)
        * np.fft.rfft(centered_template[::-1], fft_size),
        fft_size,
    )[:linear_size]

    # Convolution index k corresponds to start lag k - (template_size - 1).
    # This complete linear axis includes the edge-overlap branch that the old
    # 0..(search_size-template_size) slice accidentally discarded.
    lags = np.arange(
        -(template.size - 1),
        search.size,
        dtype=np.int64,
    )
    denominator = math.sqrt(search_energy * template_energy)
    correlations = cross_correlation / denominator
    correlations = np.clip(correlations, -1.0, 1.0)
    absolute = np.abs(correlations)

    best_index = int(np.argmax(absolute))
    best_lag = int(lags[best_index])
    best_abs_r = float(absolute[best_index])
    best_r = float(correlations[best_index])

    exclusion_samples = int(
        round(DISTANT_PEAK_EXCLUSION_S * SAMPLE_RATE_HZ)
    )
    distant_mask = np.abs(lags - best_lag) > exclusion_samples
    second_abs_r = (
        float(np.max(absolute[distant_mask]))
        if np.any(distant_mask)
        else 0.0
    )
    return CorrelationPeak(
        r=best_r,
        abs_r=best_abs_r,
        lag_samples=best_lag,
        second_distant_abs_r=second_abs_r,
        distant_margin=best_abs_r - second_abs_r,
    )


def presence_result(
    name: str,
    peak: CorrelationPeak,
    *,
    search_start_s: float,
    expected_start_s: float,
    min_corr: float,
    lag_tolerance_s: float,
) -> CheckResult:
    implied_start_s = search_start_s + peak.lag_samples / SAMPLE_RATE_HZ
    offset_s = implied_start_s - expected_start_s
    correlation_pass = peak.abs_r >= min_corr
    slot_pass = abs(offset_s) <= lag_tolerance_s
    reasons: list[str] = []
    if not correlation_pass:
        reasons.append(
            f"peak |r| {peak.abs_r:.4f} is below minimum {min_corr:.4f}"
        )
    if not slot_pass:
        reasons.append(
            f"implied offset {offset_s:+.4f}s exceeds lag tolerance "
            f"{lag_tolerance_s:.4f}s"
        )
    return CheckResult(
        check=f"{name}_PRESENCE",
        status="PASS" if correlation_pass and slot_pass else "FAIL",
        metrics={
            "r": peak.r,
            "abs_r": peak.abs_r,
            "implied_start_s": implied_start_s,
            "expected_start_s": expected_start_s,
            "offset_s": offset_s,
            "second_distant_abs_r": peak.second_distant_abs_r,
            "distant_margin": peak.distant_margin,
            "min_corr": min_corr,
            "lag_tolerance_s": lag_tolerance_s,
        },
        why="; ".join(reasons) or None,
    )


def unavailable_presence_result(
    name: str,
    *,
    search_samples: int,
    template_samples: int,
    min_corr: float,
    lag_tolerance_s: float,
) -> CheckResult:
    return CheckResult(
        check=f"{name}_PRESENCE",
        status="FAIL",
        metrics={
            "r": 0.0,
            "abs_r": 0.0,
            "offset_s": "unavailable",
            "second_distant_abs_r": 0.0,
            "distant_margin": 0.0,
            "search_samples": search_samples,
            "template_samples": template_samples,
            "min_corr": min_corr,
            "lag_tolerance_s": lag_tolerance_s,
        },
        why=(
            f"search window has {search_samples} samples, fewer than the "
            f"{template_samples}-sample bumper"
        ),
    )


def uniqueness_result(
    name: str,
    peak: CorrelationPeak | None,
    *,
    threshold: float,
    middle_start_s: float,
    middle_duration_s: float,
    unavailable_reason: str | None = None,
) -> CheckResult:
    if peak is None:
        return CheckResult(
            check=f"{name}_UNIQUENESS",
            status="FAIL",
            metrics={
                "peak_r": "unavailable",
                "peak_abs_r": "unavailable",
                "threshold": threshold,
                "middle_start_s": middle_start_s,
                "middle_duration_s": middle_duration_s,
            },
            why=unavailable_reason or "middle correlation is unavailable",
        )
    leaked = peak.abs_r > threshold
    return CheckResult(
        check=f"{name}_UNIQUENESS",
        status="FAIL" if leaked else "PASS",
        metrics={
            "peak_r": peak.r,
            "peak_abs_r": peak.abs_r,
            "threshold": threshold,
            "middle_start_s": middle_start_s,
            "middle_duration_s": middle_duration_s,
            "second_distant_abs_r": peak.second_distant_abs_r,
            "distant_margin": peak.distant_margin,
        },
        why=(
            f"interior peak |r| {peak.abs_r:.4f} exceeds leak threshold "
            f"{threshold:.4f}"
            if leaked
            else None
        ),
    )


LOUDNORM_JSON_RE = re.compile(
    r"\{[^{}]*\"input_i\"[^{}]*\}",
    flags=re.DOTALL,
)


def measure_integrated_lufs(
    path: Path,
    *,
    start_s: float,
    duration_s: float,
    label: str,
) -> float:
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            os.fspath(path),
            "-vn",
            "-af",
            (
                f"atrim=start={max(0.0, start_s):.9f}:"
                f"duration={max(0.0, duration_s):.9f},"
                "asetpts=PTS-STARTPTS,loudnorm=print_format=json"
            ),
            "-f",
            "null",
            "-",
        ],
        purpose=f"measure {label} loudness",
    )
    stderr = completed.stderr.decode("utf-8", errors="replace")
    matches = LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise CheckerError(f"{label} loudnorm output did not contain JSON")
    try:
        measured_lufs = float(json.loads(matches[-1])["input_i"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CheckerError(
            f"{label} loudnorm JSON has no usable input_i"
        ) from exc
    if not math.isfinite(measured_lufs):
        raise CheckerError(
            f"{label} measured loudness is not finite: {measured_lufs!r}"
        )
    return measured_lufs


def level_result(
    name: str,
    measured_lufs: float,
    expected_lufs: float | None,
    tolerance_lu: float,
    *,
    span_start_s: float,
    span_duration_s: float,
) -> CheckResult:
    metrics: dict[str, Any] = {
        "measured_lufs": measured_lufs,
        "expected_lufs": (
            expected_lufs if expected_lufs is not None else "not_supplied"
        ),
        "tolerance_lu": tolerance_lu,
        "span_start_s": span_start_s,
        "span_duration_s": span_duration_s,
    }
    if expected_lufs is None:
        return CheckResult(
            check=f"{name}_LEVEL",
            status="SKIP",
            metrics=metrics,
        )

    delta_lu = measured_lufs - expected_lufs
    metrics["delta_lu"] = delta_lu
    passed = abs(delta_lu) <= tolerance_lu
    return CheckResult(
        check=f"{name}_LEVEL",
        status="PASS" if passed else "FAIL",
        metrics=metrics,
        why=(
            f"absolute target error {abs(delta_lu):.4f} LU exceeds tolerance "
            f"{tolerance_lu:.4f} LU"
            if not passed
            else None
        ),
    )


def correlate_presence(
    name: str,
    search: np.ndarray,
    template: np.ndarray,
    *,
    search_start_s: float,
    expected_start_s: float,
    min_corr: float,
    lag_tolerance_s: float,
) -> tuple[CheckResult, CorrelationPeak | None]:
    if search.size < template.size:
        return (
            unavailable_presence_result(
                name,
                search_samples=search.size,
                template_samples=template.size,
                min_corr=min_corr,
                lag_tolerance_s=lag_tolerance_s,
            ),
            None,
        )
    peak = full_lag_fft_ncc(search, template)
    return (
        presence_result(
            name,
            peak,
            search_start_s=search_start_s,
            expected_start_s=expected_start_s,
            min_corr=min_corr,
            lag_tolerance_s=lag_tolerance_s,
        ),
        peak,
    )


def run_checks(
    media: MediaInputs,
    *,
    expected_intro_lufs: float | None,
    expected_outro_lufs: float | None,
    lag_tolerance_s: float,
    min_corr: float,
    tolerance_lu: float,
) -> list[CheckResult]:
    intro_template = decode_mono_f32(media.intro, label="intro bumper")
    outro_template = decode_mono_f32(media.outro, label="outro bumper")

    intro_search_start_s = 0.0
    intro_search_duration_s = min(
        media.final_duration_s,
        media.intro_duration_s + SLOT_SEARCH_PADDING_S,
    )
    outro_search_duration_s = min(
        media.final_duration_s,
        media.outro_duration_s + SLOT_SEARCH_PADDING_S,
    )
    outro_search_start_s = max(
        0.0,
        media.final_duration_s - outro_search_duration_s,
    )
    intro_search = decode_mono_f32(
        media.final,
        label="final intro search window",
        start_s=intro_search_start_s,
        duration_s=intro_search_duration_s,
    )
    outro_search = decode_mono_f32(
        media.final,
        label="final outro search window",
        start_s=outro_search_start_s,
        duration_s=outro_search_duration_s,
    )

    intro_expected_start_s = 0.0
    outro_expected_start_s = (
        media.final_duration_s - media.outro_duration_s
    )
    intro_presence, intro_peak = correlate_presence(
        "INTRO",
        intro_search,
        intro_template,
        search_start_s=intro_search_start_s,
        expected_start_s=intro_expected_start_s,
        min_corr=min_corr,
        lag_tolerance_s=lag_tolerance_s,
    )
    outro_presence, outro_peak = correlate_presence(
        "OUTRO",
        outro_search,
        outro_template,
        search_start_s=outro_search_start_s,
        expected_start_s=outro_expected_start_s,
        min_corr=min_corr,
        lag_tolerance_s=lag_tolerance_s,
    )

    middle_safe_start_s = media.intro_duration_s + MIDDLE_SLOT_GUARD_S
    middle_safe_end_s = (
        media.final_duration_s
        - media.outro_duration_s
        - MIDDLE_SLOT_GUARD_S
    )
    middle_safe_duration_s = max(
        0.0,
        middle_safe_end_s - middle_safe_start_s,
    )
    desired_middle_duration_s = (
        max(media.intro_duration_s, media.outro_duration_s)
        + MIDDLE_WINDOW_PADDING_S
    )
    middle_duration_s = min(
        middle_safe_duration_s,
        desired_middle_duration_s,
    )
    middle_start_s = (
        middle_safe_start_s
        + max(0.0, middle_safe_duration_s - middle_duration_s) / 2.0
    )
    uniqueness_threshold = UNIQUENESS_THRESHOLD_FACTOR * min_corr

    if middle_duration_s <= 0.0:
        reason = (
            "no middle window remains after "
            f"{MIDDLE_SLOT_GUARD_S:.4f}s guards around both bumper slots"
        )
        intro_uniqueness = uniqueness_result(
            "INTRO",
            None,
            threshold=uniqueness_threshold,
            middle_start_s=middle_start_s,
            middle_duration_s=middle_duration_s,
            unavailable_reason=reason,
        )
        outro_uniqueness = uniqueness_result(
            "OUTRO",
            None,
            threshold=uniqueness_threshold,
            middle_start_s=middle_start_s,
            middle_duration_s=middle_duration_s,
            unavailable_reason=reason,
        )
    else:
        middle = decode_mono_f32(
            media.final,
            label="final middle uniqueness window",
            start_s=middle_start_s,
            duration_s=middle_duration_s,
        )
        intro_middle_peak = (
            full_lag_fft_ncc(middle, intro_template)
            if middle.size >= intro_template.size
            else None
        )
        outro_middle_peak = (
            full_lag_fft_ncc(middle, outro_template)
            if middle.size >= outro_template.size
            else None
        )
        intro_uniqueness = uniqueness_result(
            "INTRO",
            intro_middle_peak,
            threshold=uniqueness_threshold,
            middle_start_s=middle_start_s,
            middle_duration_s=middle_duration_s,
            unavailable_reason=(
                f"middle window has {middle.size} samples, fewer than the "
                f"{intro_template.size}-sample intro bumper"
                if intro_middle_peak is None
                else None
            ),
        )
        outro_uniqueness = uniqueness_result(
            "OUTRO",
            outro_middle_peak,
            threshold=uniqueness_threshold,
            middle_start_s=middle_start_s,
            middle_duration_s=middle_duration_s,
            unavailable_reason=(
                f"middle window has {middle.size} samples, fewer than the "
                f"{outro_template.size}-sample outro bumper"
                if outro_middle_peak is None
                else None
            ),
        )

    intro_detected_start_s = (
        intro_search_start_s + intro_peak.lag_samples / SAMPLE_RATE_HZ
        if intro_peak is not None and intro_presence.status == "PASS"
        else intro_expected_start_s
    )
    outro_detected_start_s = (
        outro_search_start_s + outro_peak.lag_samples / SAMPLE_RATE_HZ
        if outro_peak is not None and outro_presence.status == "PASS"
        else outro_expected_start_s
    )
    intro_level_start_s = max(0.0, intro_detected_start_s)
    outro_level_start_s = max(0.0, outro_detected_start_s)
    intro_measured_lufs = measure_integrated_lufs(
        media.final,
        start_s=intro_level_start_s,
        duration_s=media.intro_duration_s,
        label="intro span",
    )
    outro_measured_lufs = measure_integrated_lufs(
        media.final,
        start_s=outro_level_start_s,
        duration_s=media.outro_duration_s,
        label="outro span",
    )
    intro_level = level_result(
        "INTRO",
        intro_measured_lufs,
        expected_intro_lufs,
        tolerance_lu,
        span_start_s=intro_level_start_s,
        span_duration_s=media.intro_duration_s,
    )
    outro_level = level_result(
        "OUTRO",
        outro_measured_lufs,
        expected_outro_lufs,
        tolerance_lu,
        span_start_s=outro_level_start_s,
        span_duration_s=media.outro_duration_s,
    )

    return [
        intro_presence,
        outro_presence,
        intro_uniqueness,
        outro_uniqueness,
        intro_level,
        outro_level,
    ]


def write_json_report(
    output_path: Path,
    *,
    media: MediaInputs,
    results: list[CheckResult],
    parameters: dict[str, Any],
    exit_code: int,
) -> None:
    payload = {
        "exit_code": exit_code,
        "all_required_checks_pass": exit_code == 0,
        "inputs": {
            "final": os.fspath(media.final),
            "intro": os.fspath(media.intro),
            "outro": os.fspath(media.outro),
            "final_duration_s": media.final_duration_s,
            "intro_duration_s": media.intro_duration_s,
            "outro_duration_s": media.outro_duration_s,
        },
        "parameters": parameters,
        "checks": [asdict(result) for result in results],
    }
    try:
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise CheckerError(
            f"could not write JSON report {output_path}: {exc}"
        ) from exc


def generate_signature(
    output_path: Path,
    *,
    frequency_a: int,
    frequency_b: int,
    modulation_hz: int,
    noise_seed: int,
) -> None:
    duration_s = SELF_TEST_BUMPER_DURATION_S
    run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"sine=frequency={frequency_a}:sample_rate=48000:"
                f"duration={duration_s}"
            ),
            "-f",
            "lavfi",
            "-i",
            (
                f"sine=frequency={frequency_b}:sample_rate=48000:"
                f"duration={duration_s}"
            ),
            "-f",
            "lavfi",
            "-i",
            (
                "anoisesrc=color=pink:amplitude=0.30:sample_rate=48000:"
                f"duration={duration_s}:seed={noise_seed}"
            ),
            "-f",
            "lavfi",
            "-i",
            (
                f"aevalsrc=0.5+0.5*sin(2*PI*{modulation_hz}*t):"
                f"s=48000:d={duration_s}"
            ),
            "-filter_complex",
            (
                "[2:a][3:a]amultiply[am_noise];"
                "[0:a][1:a][am_noise]"
                "amix=inputs=3:weights=0.38 0.24 0.16:normalize=0,"
                "afade=t=in:st=0:d=0.02,"
                f"afade=t=out:st={duration_s - 0.02:.3f}:d=0.02,"
                f"loudnorm=I={SELF_TEST_TARGET_LUFS}:TP=-2:LRA=7,"
                "aresample=48000[out]"
            ),
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            os.fspath(output_path),
        ],
        purpose=f"generate {output_path.name}",
    )


def generate_body(
    output_path: Path,
    *,
    target_lufs: float = SELF_TEST_WRONG_TARGET_LUFS,
) -> None:
    run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                "anoisesrc=color=white:amplitude=0.20:sample_rate=48000:"
                f"duration={SELF_TEST_BODY_DURATION_S}:seed=404"
            ),
            "-af",
            (
                "highpass=f=180,lowpass=f=2800,"
                f"loudnorm=I={target_lufs}:TP=-2:LRA=7,aresample=48000"
            ),
            "-c:a",
            "pcm_s16le",
            os.fspath(output_path),
        ],
        purpose="generate body",
    )


def required_checks_exit_code(results: Sequence[CheckResult]) -> int:
    """Return failure when any emitted required check is a failure."""
    return 1 if any(result.status == "FAIL" for result in results) else 0


def build_aac_final(
    output_path: Path,
    *,
    intro: Path,
    body: Path,
    outro: Path | None,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="bumper-check-aac-stage-",
        dir=output_path.parent,
    ) as raw_stage:
        first_pass = Path(raw_stage) / "first-pass.aac"
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            os.fspath(intro),
            "-i",
            os.fspath(body),
        ]
        labels = ["[0:a]", "[1:a]"]
        if outro is not None:
            command.extend(("-i", os.fspath(outro)))
            labels.append("[2:a]")
        command.extend(
            (
                "-filter_complex",
                (
                    f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1,"
                    "aresample=48000[out]"
                ),
                "-map",
                "[out]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                os.fspath(first_pass),
            )
        )
        run_command(command, purpose=f"build {output_path.name} first AAC pass")
        run_command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                os.fspath(first_pass),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                os.fspath(output_path),
            ],
            purpose=f"build {output_path.name} second AAC pass",
        )


def numeric_metric(result: CheckResult, name: str) -> float:
    value = result.metrics.get(name)
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        raise CheckerError(f"{result.check} metric {name} is unavailable")
    return float(value)


def self_test_result(
    name: str,
    passed: bool,
    metrics: dict[str, Any],
    failure_why: str,
) -> bool:
    emit(
        CheckResult(
            check=name,
            status="PASS" if passed else "FAIL",
            metrics=metrics,
            why=None if passed else failure_why,
        )
    )
    return passed


def run_self_test() -> int:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path is None or ffprobe_path is None:
        emit(
            CheckResult(
                check="SELF_TEST_TOOLS",
                status="FAIL",
                metrics={
                    "ffmpeg": ffmpeg_path or "missing",
                    "ffprobe": ffprobe_path or "missing",
                },
                why="ffmpeg and ffprobe are required",
            )
        )
        return 2

    outcomes: list[bool] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix="bumper-check-self-test-"
        ) as raw_directory:
            directory = Path(raw_directory)
            intro = directory / "intro.wav"
            outro = directory / "outro.wav"
            wrong_outro = directory / "wrong-outro.wav"
            body = directory / "body.wav"
            quiet_body = directory / "quiet-body.wav"
            matched_final = directory / "matched.aac"
            offset_final = directory / "offset.m4a"
            wrong_final = directory / "wrong.aac"
            missing_final = directory / "missing.aac"

            generate_signature(
                intro,
                frequency_a=431,
                frequency_b=947,
                modulation_hz=3,
                noise_seed=101,
            )
            generate_signature(
                outro,
                frequency_a=613,
                frequency_b=1289,
                modulation_hz=5,
                noise_seed=202,
            )
            generate_signature(
                wrong_outro,
                frequency_a=787,
                frequency_b=1543,
                modulation_hz=7,
                noise_seed=303,
            )
            generate_body(body)
            generate_body(quiet_body, target_lufs=-50.0)
            build_aac_final(
                matched_final,
                intro=intro,
                body=body,
                outro=outro,
            )
            build_aac_final(
                offset_final,
                intro=intro,
                body=quiet_body,
                outro=outro,
            )
            build_aac_final(
                wrong_final,
                intro=intro,
                body=body,
                outro=wrong_outro,
            )
            build_aac_final(
                missing_final,
                intro=intro,
                body=body,
                outro=None,
            )

            common = {
                "expected_intro_lufs": SELF_TEST_TARGET_LUFS,
                "expected_outro_lufs": SELF_TEST_TARGET_LUFS,
                "lag_tolerance_s": DEFAULT_LAG_TOLERANCE_S,
                "min_corr": DEFAULT_MIN_CORR,
                "tolerance_lu": DEFAULT_LEVEL_TOLERANCE_LU,
            }
            matched_media = load_inputs(matched_final, intro, outro)
            matched = run_checks(matched_media, **common)
            matched_by_name = {result.check: result for result in matched}
            matched_required = (
                "INTRO_PRESENCE",
                "OUTRO_PRESENCE",
                "INTRO_UNIQUENESS",
                "OUTRO_UNIQUENESS",
            )
            matched_pass = all(
                matched_by_name[name].status == "PASS"
                for name in matched_required
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_MATCHED",
                    matched_pass,
                    {
                        "intro_abs_r": numeric_metric(
                            matched_by_name["INTRO_PRESENCE"],
                            "abs_r",
                        ),
                        "outro_abs_r": numeric_metric(
                            matched_by_name["OUTRO_PRESENCE"],
                            "abs_r",
                        ),
                        "intro_middle_abs_r": numeric_metric(
                            matched_by_name["INTRO_UNIQUENESS"],
                            "peak_abs_r",
                        ),
                        "outro_middle_abs_r": numeric_metric(
                            matched_by_name["OUTRO_UNIQUENESS"],
                            "peak_abs_r",
                        ),
                    },
                    "matched final did not pass presence and uniqueness",
                )
            )

            # This fixture proves the changed full-lag branch: its decoded
            # peak lies beyond the old complete-placement ceiling at +5 s.
            offset_media = load_inputs(offset_final, intro, outro)
            offset = run_checks(offset_media, **common)
            offset_presence = next(
                result
                for result in offset
                if result.check == "OUTRO_PRESENCE"
            )
            offset_search_duration_s = min(
                offset_media.final_duration_s,
                offset_media.outro_duration_s + SLOT_SEARCH_PADDING_S,
            )
            offset_search_start_s = max(
                0.0,
                offset_media.final_duration_s - offset_search_duration_s,
            )
            offset_search = decode_mono_f32(
                offset_media.final,
                label="offset final outro search window",
                start_s=offset_search_start_s,
                duration_s=offset_search_duration_s,
            )
            offset_template = decode_mono_f32(
                offset_media.outro,
                label="offset outro bumper",
            )
            offset_peak = full_lag_fft_ncc(offset_search, offset_template)
            old_complete_max_lag_samples = (
                offset_search.size - offset_template.size
            )
            recovered_lag_s = offset_peak.lag_samples / SAMPLE_RATE_HZ
            old_complete_max_lag_s = (
                old_complete_max_lag_samples / SAMPLE_RATE_HZ
            )
            offset_expected_start_s = (
                offset_media.final_duration_s - offset_media.outro_duration_s
            )
            offset_implied_start_s = (
                offset_search_start_s + recovered_lag_s
            )
            offset_start_error_s = (
                offset_implied_start_s - offset_expected_start_s
            )
            offset_proved = (
                offset_presence.status == "PASS"
                and offset_peak.abs_r >= 0.9
                and abs(offset_start_error_s) <= 0.100
                and offset_peak.lag_samples > old_complete_max_lag_samples
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_OFFSET_BUMPER",
                    offset_proved,
                    {
                        "recovered_lag_s": recovered_lag_s,
                        "old_complete_max_lag_s":
                            old_complete_max_lag_s,
                        "outro_abs_r": offset_peak.abs_r,
                        "implied_start_s": offset_implied_start_s,
                        "expected_start_s": offset_expected_start_s,
                        "start_error_s": offset_start_error_s,
                    },
                    (
                        "offset outro was not recovered beyond the old "
                        "complete-placement lag ceiling with |r| >= 0.9 "
                        "and start error <= 100 ms"
                    ),
                )
            )

            wrong = run_checks(
                load_inputs(wrong_final, intro, outro),
                **common,
            )
            wrong_presence = next(
                result
                for result in wrong
                if result.check == "OUTRO_PRESENCE"
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_WRONG_OUTRO",
                    wrong_presence.status == "FAIL",
                    {
                        "checker_status": wrong_presence.status,
                        "outro_abs_r": numeric_metric(
                            wrong_presence,
                            "abs_r",
                        ),
                    },
                    "wrong outro did not fail OUTRO_PRESENCE",
                )
            )

            captured_main_output = io.StringIO()
            with contextlib.redirect_stdout(captured_main_output):
                wrong_main_exit_code = main(
                    [
                        "--final",
                        os.fspath(wrong_final),
                        "--intro",
                        os.fspath(intro),
                        "--outro",
                        os.fspath(outro),
                    ]
                )
            captured_lines = captured_main_output.getvalue().splitlines()
            outro_fail_lines = sum(
                line.startswith("OUTRO_PRESENCE FAIL")
                for line in captured_lines
            )
            exit_code_proved = (
                wrong_main_exit_code == 1 and outro_fail_lines == 1
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_EXIT_CODE",
                    exit_code_proved,
                    {
                        "main_return_code": wrong_main_exit_code,
                        "outro_presence_fail_lines": outro_fail_lines,
                        "captured_output_lines": len(captured_lines),
                    },
                    (
                        "in-process main did not return 1 for exactly one "
                        "captured OUTRO_PRESENCE FAIL line"
                    ),
                )
            )

            missing = run_checks(
                load_inputs(missing_final, intro, outro),
                **common,
            )
            missing_presence = next(
                result
                for result in missing
                if result.check == "OUTRO_PRESENCE"
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_MISSING_OUTRO",
                    missing_presence.status == "FAIL",
                    {
                        "checker_status": missing_presence.status,
                        "outro_abs_r": numeric_metric(
                            missing_presence,
                            "abs_r",
                        ),
                    },
                    "missing outro did not fail OUTRO_PRESENCE",
                )
            )

            measured_lufs = numeric_metric(
                matched_by_name["INTRO_LEVEL"],
                "measured_lufs",
            )
            level_green = level_result(
                "INTRO",
                measured_lufs,
                SELF_TEST_TARGET_LUFS,
                DEFAULT_LEVEL_TOLERANCE_LU,
                span_start_s=0.0,
                span_duration_s=SELF_TEST_BUMPER_DURATION_S,
            )
            level_red = level_result(
                "INTRO",
                measured_lufs,
                SELF_TEST_WRONG_TARGET_LUFS,
                DEFAULT_LEVEL_TOLERANCE_LU,
                span_start_s=0.0,
                span_duration_s=SELF_TEST_BUMPER_DURATION_S,
            )
            level_proved = (
                level_green.status == "PASS"
                and level_red.status == "FAIL"
                and abs(measured_lufs - SELF_TEST_TARGET_LUFS)
                <= DEFAULT_LEVEL_TOLERANCE_LU
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_LEVEL_TARGET",
                    level_proved,
                    {
                        "measured_lufs": measured_lufs,
                        "green_expected_lufs": SELF_TEST_TARGET_LUFS,
                        "green_delta_lu": (
                            measured_lufs - SELF_TEST_TARGET_LUFS
                        ),
                        "red_expected_lufs": SELF_TEST_WRONG_TARGET_LUFS,
                        "red_delta_lu": (
                            measured_lufs - SELF_TEST_WRONG_TARGET_LUFS
                        ),
                        "tolerance_lu": DEFAULT_LEVEL_TOLERANCE_LU,
                    },
                    (
                        "-19 LUFS did not pass or the rejected -16 LUFS "
                        "baseline did not fail"
                    ),
                )
            )

            intro_presence = matched_by_name["INTRO_PRESENCE"]
            priming_offset_s = numeric_metric(
                intro_presence,
                "offset_s",
            )
            priming_proved = (
                intro_presence.status == "PASS"
                and abs(priming_offset_s) <= DEFAULT_LAG_TOLERANCE_S
                and abs(
                    priming_offset_s - SELF_TEST_EXPECTED_PRIMING_S
                )
                <= SELF_TEST_PRIMING_TOLERANCE_S
            )
            outcomes.append(
                self_test_result(
                    "SELF_TEST_AAC_PRIMING",
                    priming_proved,
                    {
                        "offset_s": priming_offset_s,
                        "expected_priming_s": SELF_TEST_EXPECTED_PRIMING_S,
                        "observation_tolerance_s":
                            SELF_TEST_PRIMING_TOLERANCE_S,
                        "lag_tolerance_s": DEFAULT_LAG_TOLERANCE_S,
                        "intro_abs_r": numeric_metric(
                            intro_presence,
                            "abs_r",
                        ),
                    },
                    (
                        "the roughly 43 ms AAC priming displacement did not "
                        "pass presence at the default lag tolerance"
                    ),
                )
            )
    except CheckerError as exc:
        emit(
            CheckResult(
                check="SELF_TEST_RUNTIME",
                status="FAIL",
                metrics={"measured": "unavailable"},
                why=str(exc),
            )
        )
        return 2

    return 0 if all(outcomes) else 1


def validate_cli_values(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    if args.self_test:
        conflicts = any(
            value is not None
            for value in (
                args.final,
                args.intro,
                args.outro,
                args.expected_intro_lufs,
                args.expected_outro_lufs,
                args.json_output,
            )
        )
        if conflicts:
            parser.error(
                "--self-test cannot be combined with media or JSON arguments"
            )
        return

    missing = [
        option
        for option, value in (
            ("--final", args.final),
            ("--intro", args.intro),
            ("--outro", args.outro),
        )
        if value is None
    ]
    if missing:
        parser.error(f"required arguments missing: {', '.join(missing)}")
    if (
        args.expected_intro_lufs is not None
        and not math.isfinite(args.expected_intro_lufs)
    ):
        parser.error("--expected-intro-lufs must be finite")
    if (
        args.expected_outro_lufs is not None
        and not math.isfinite(args.expected_outro_lufs)
    ):
        parser.error("--expected-outro-lufs must be finite")
    if (
        not math.isfinite(args.lag_tolerance_s)
        or args.lag_tolerance_s < 0.0
    ):
        parser.error("--lag-tolerance-s must be finite and non-negative")
    if not math.isfinite(args.min_corr) or not 0.0 < args.min_corr <= 1.0:
        parser.error("--min-corr must be finite and in (0, 1]")
    if not math.isfinite(args.tolerance_lu) or args.tolerance_lu < 0.0:
        parser.error("--tolerance-lu must be finite and non-negative")


def build_parser() -> argparse.ArgumentParser:
    parser = OneLineArgumentParser(
        description=(
            "Certify podcast bumper presence, uniqueness, and target loudness."
        )
    )
    parser.add_argument("--final", type=Path)
    parser.add_argument("--intro", type=Path)
    parser.add_argument("--outro", type=Path)
    parser.add_argument("--expected-intro-lufs", type=float)
    parser.add_argument("--expected-outro-lufs", type=float)
    parser.add_argument(
        "--lag-tolerance-s",
        type=float,
        default=DEFAULT_LAG_TOLERANCE_S,
    )
    parser.add_argument(
        "--min-corr",
        type=float,
        default=DEFAULT_MIN_CORR,
    )
    parser.add_argument(
        "--tolerance-lu",
        type=float,
        default=DEFAULT_LEVEL_TOLERANCE_LU,
    )
    parser.add_argument("--json", dest="json_output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_cli_values(args, parser)
    if args.self_test:
        return run_self_test()

    assert args.final is not None
    assert args.intro is not None
    assert args.outro is not None
    try:
        media = load_inputs(args.final, args.intro, args.outro)
        results = run_checks(
            media,
            expected_intro_lufs=args.expected_intro_lufs,
            expected_outro_lufs=args.expected_outro_lufs,
            lag_tolerance_s=args.lag_tolerance_s,
            min_corr=args.min_corr,
            tolerance_lu=args.tolerance_lu,
        )
    except CheckerError as exc:
        emit(
            CheckResult(
                check="INPUTS",
                status="FAIL",
                metrics={
                    "final": os.fspath(args.final),
                    "intro": os.fspath(args.intro),
                    "outro": os.fspath(args.outro),
                },
                why=str(exc),
            )
        )
        return 2

    for result in results:
        emit(result)
    exit_code = required_checks_exit_code(results)

    if args.json_output is not None:
        try:
            write_json_report(
                args.json_output,
                media=media,
                results=results,
                parameters={
                    "expected_intro_lufs": args.expected_intro_lufs,
                    "expected_outro_lufs": args.expected_outro_lufs,
                    "lag_tolerance_s": args.lag_tolerance_s,
                    "min_corr": args.min_corr,
                    "tolerance_lu": args.tolerance_lu,
                    "sample_rate_hz": SAMPLE_RATE_HZ,
                },
                exit_code=exit_code,
            )
        except CheckerError as exc:
            emit(
                CheckResult(
                    check="JSON_OUTPUT",
                    status="FAIL",
                    metrics={"path": os.fspath(args.json_output)},
                    why=str(exc),
                )
            )
            return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
