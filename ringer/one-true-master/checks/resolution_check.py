#!/usr/bin/env python3
"""Certify declared resolution and measured picture detail in a final video."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Sequence

import numpy as np


# Five probes resist isolated soft frames without making certification too slow.
DEFAULT_PROBE_COUNT = 5
# One probe is the minimum that can form a median distribution.
MIN_PROBE_COUNT = 1
# A 0.75 floor tolerates ordinary encode/content variance while catching material loss.
DEFAULT_MIN_DETAIL_RATIO = 0.75
# Retention above 0.92 means a half-size round trip removed too little claimed-resolution detail.
UPSCALE_RETENTION_MAX = 0.92
# The first 10% is excluded so intros and title bumpers do not dominate the sample.
PROBE_WINDOW_START_FRACTION = 0.10
# Sampling 80% of each file excludes both head and tail bumpers.
PROBE_WINDOW_FRACTION = 0.80
# A twofold round trip directly tests the shipped 540p-to-1080p failure shape.
ROUNDTRIP_SCALE_FACTOR = 2
# A decoded scale target must retain at least one pixel in each dimension.
MIN_FRAME_DIMENSION = 1
# Two pixels per axis are required to compute both first differences.
MIN_DETAIL_DIMENSION = 2
# Scores at or below this epsilon cannot support a meaningful ratio.
MIN_MEASURABLE_DETAIL = 1.0e-9
# Sixty seconds bounds a stuck media probe while allowing accurate seeks in large files.
SUBPROCESS_TIMEOUT_SECONDS = 60
# Diagnostic tails stay bounded when ffmpeg reports a damaged input.
MAX_DIAGNOSTIC_CHARS = 2_000
# A nearby input seek recovers isolated timestamp holes without hiding broad damage.
PROBE_RETRY_OFFSET_SECONDS = 0.100
# Keep retry seeks just inside the resolved media interval.
PROBE_END_GUARD_SECONDS = 0.001

SELF_TEST_DURATION_SECONDS = 2.0
SELF_TEST_FPS = 5
SELF_TEST_NOISE_STRENGTH = 18
SELF_TEST_NOISE_SEED = 12_345


class UnusableInputError(RuntimeError):
    """The requested certification cannot produce trustworthy measurements."""


@dataclass(frozen=True)
class MediaInfo:
    path: str
    width: int
    height: int
    duration_s: float
    stream_index: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class ProbeMeasurement:
    timestamp_s: float
    detail: float


@dataclass(frozen=True)
class SourceComparison:
    source: str
    comparison_width_final: int
    comparison_width_source: int
    comparison_height: int
    final_probes: list[ProbeMeasurement]
    source_probes: list[ProbeMeasurement]
    final_median_detail: float
    source_median_detail: float
    ratio: float


class FrameDecoder:
    """Decode gray frames with a small cache shared by self-test cases."""

    def __init__(self, ffmpeg: str) -> None:
        self.ffmpeg = ffmpeg
        self._cache: dict[
            tuple[str, float, int, int, tuple[int, int] | None], np.ndarray
        ] = {}

    def gray_frame(
        self,
        media: MediaInfo,
        timestamp_s: float,
        width: int,
        height: int,
        *,
        roundtrip_phase: tuple[int, int] | None = None,
    ) -> np.ndarray:
        key = (media.path, round(timestamp_s, 6), width, height, roundtrip_phase)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if roundtrip_phase is not None:
            phase_x, phase_y = roundtrip_phase
            half_width = max(MIN_FRAME_DIMENSION, width // ROUNDTRIP_SCALE_FACTOR)
            half_height = max(MIN_FRAME_DIMENSION, height // ROUNDTRIP_SCALE_FACTOR)
            phase_filter = ""
            if phase_x or phase_y:
                phase_filter = (
                    f"crop={width - phase_x}:{height - phase_y}:"
                    f"{phase_x}:{phase_y},"
                    f"pad={width}:{height}:0:0:black,"
                )
            video_filter = phase_filter + (
                f"scale={half_width}:{half_height}:flags=neighbor,"
                f"scale={width}:{height}:flags=lanczos,format=gray"
            )
        else:
            video_filter = f"scale={width}:{height}:flags=lanczos,format=gray"

        expected_bytes = width * height
        latest_seek_s = max(
            0.0,
            media.duration_s - PROBE_END_GUARD_SECONDS,
        )
        requested_seek_s = min(max(0.0, timestamp_s), latest_seek_s)
        candidate_seeks = [
            requested_seek_s,
            min(latest_seek_s, requested_seek_s + PROBE_RETRY_OFFSET_SECONDS),
            max(0.0, requested_seek_s - PROBE_RETRY_OFFSET_SECONDS),
        ]
        unique_seeks: list[float] = []
        for seek_s in candidate_seeks:
            if not any(abs(seek_s - prior) < 1.0e-9 for prior in unique_seeks):
                unique_seeks.append(seek_s)

        failures = []
        for seek_s in unique_seeks:
            # Input seeking is deliberate: VFR/durationless WebM files may not
            # expose a reliable container timebase for output-side seeking.
            command = [
                self.ffmpeg,
                "-v",
                "error",
                "-ss",
                f"{seek_s:.6f}",
                "-i",
                media.path,
                "-map",
                f"0:{media.stream_index}",
                "-frames:v",
                "1",
                "-vf",
                video_filter,
                "-an",
                "-sn",
                "-dn",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            ]
            try:
                result = run_command(
                    command,
                    label=f"decode frame from {media.path}",
                )
            except UnusableInputError as exc:
                failures.append(f"{seek_s:.3f}s: {exc}")
                continue
            if len(result.stdout) != expected_bytes:
                failures.append(
                    f"{seek_s:.3f}s: decoded {len(result.stdout)} bytes; "
                    f"expected {expected_bytes}"
                )
                continue

            frame = np.frombuffer(result.stdout, dtype=np.uint8).reshape(
                (height, width)
            )
            if abs(seek_s - requested_seek_s) > 1.0e-9:
                print(
                    f"WARNING: probe in {media.path} at "
                    f"{requested_seek_s:.3f}s used nearby decodable seek "
                    f"{seek_s:.3f}s",
                    file=sys.stderr,
                )
            self._cache[key] = frame
            return frame

        raise UnusableInputError(
            f"no decodable frame in {media.path} near {requested_seek_s:.3f}s "
            f"after {len(unique_seeks)} seek attempt(s): "
            f"{' | '.join(failures)}"
        )


def diagnostic_tail(stderr: bytes | str) -> str:
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = stderr
    text = text.strip()
    return text[-MAX_DIAGNOSTIC_CHARS:] if text else "no diagnostic output"


def run_command(
    command: Sequence[str],
    *,
    label: str,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=text,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise UnusableInputError(f"{command[0]} required") from exc
    except subprocess.TimeoutExpired as exc:
        raise UnusableInputError(
            f"{label} timed out after {SUBPROCESS_TIMEOUT_SECONDS}s"
        ) from exc

    if result.returncode != 0:
        raise UnusableInputError(
            f"{label} failed (exit {result.returncode}): "
            f"{diagnostic_tail(result.stderr)}"
        )
    return result


def require_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise UnusableInputError("ffmpeg and ffprobe required")
    return ffmpeg, ffprobe


def positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0.0 else None


def positive_rate(value: Any) -> float | None:
    if not isinstance(value, str):
        return positive_float(value)
    numerator, separator, denominator = value.partition("/")
    if not separator:
        return positive_float(value)
    parsed_numerator = positive_float(numerator)
    parsed_denominator = positive_float(denominator)
    if parsed_numerator is None or parsed_denominator is None:
        return None
    return positive_float(parsed_numerator / parsed_denominator)


def validate_local_file(path_value: str, label: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        raise UnusableInputError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise UnusableInputError(f"{label} is not a regular file: {path}")
    if not os.access(path, os.R_OK):
        raise UnusableInputError(f"{label} is not readable: {path}")
    return path


def _duration_from_metadata(
    path: Path,
    ffprobe: str,
    stream_index: int | None,
) -> tuple[float | None, str | None, str]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=index,codec_type,duration,disposition",
        "-i",
        str(path),
    ]
    try:
        result = run_command(
            command,
            label=f"duration metadata probe for {path}",
            text=True,
        )
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, None, "ffprobe returned invalid JSON"
    except UnusableInputError as exc:
        return None, None, str(exc)

    video_streams = [
        stream
        for stream in payload.get("streams", [])
        if stream.get("codec_type") == "video"
        and stream.get("disposition", {}).get("attached_pic") != 1
    ]
    if stream_index is not None:
        selected_streams = [
            stream
            for stream in video_streams
            if stream.get("index") == stream_index
        ]
    else:
        selected_streams = video_streams[:1]

    for stream in selected_streams:
        duration_s = positive_float(stream.get("duration"))
        if duration_s is not None:
            return duration_s, "a_stream_metadata", "positive stream duration"

    duration_s = positive_float(payload.get("format", {}).get("duration"))
    if duration_s is not None:
        return duration_s, "a_format_metadata", "positive format duration"
    return None, None, "stream and format duration were missing or non-positive"


def _duration_from_last_packet(
    path: Path,
    ffprobe: str,
) -> tuple[float | None, str]:
    """Stream packet CSV and retain only the last valid timestamp and duration."""
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time,duration_time",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        raise UnusableInputError(f"{ffprobe} required")

    last_pts_s: float | None = None
    last_duration_s: float | None = None
    timed_out = False
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + SUBPROCESS_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0.0:
                timed_out = True
                process.kill()
                break
            events = selector.select(timeout=min(1.0, remaining_s))
            if not events:
                if process.poll() is not None:
                    break
                continue
            for key, _mask in events:
                line = key.fileobj.readline()
                if line == "":
                    selector.unregister(key.fileobj)
                    continue
                try:
                    fields = next(csv.reader([line]))
                except (csv.Error, StopIteration):
                    continue
                if not fields:
                    continue
                pts_s = positive_float(fields[0])
                if pts_s is None:
                    try:
                        parsed_pts = float(fields[0])
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(parsed_pts) or parsed_pts < 0.0:
                        continue
                    pts_s = parsed_pts
                last_pts_s = pts_s
                last_duration_s = (
                    positive_float(fields[1]) if len(fields) > 1 else None
                )
        try:
            return_code = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            return_code = process.wait()
    finally:
        selector.close()
        process.stdout.close()
        stderr_file.seek(0)
        stderr_text = stderr_file.read()
        stderr_file.close()

    if timed_out:
        return None, (
            f"packet timestamp probe timed out after "
            f"{SUBPROCESS_TIMEOUT_SECONDS}s"
        )
    if return_code != 0:
        return None, (
            f"packet timestamp probe failed (exit {return_code}): "
            f"{diagnostic_tail(stderr_text)}"
        )
    if last_pts_s is None:
        return None, "no finite non-negative packet pts_time was found"
    duration_s = positive_float(last_pts_s + (last_duration_s or 0.0))
    if duration_s is None:
        return None, "last packet timestamp did not produce a positive duration"
    suffix = (
        " plus duration_time"
        if last_duration_s is not None
        else " (duration_time unavailable)"
    )
    return duration_s, f"last pts_time={last_pts_s:.6f}{suffix}"


def _duration_from_decoded_frame_count(
    path: Path,
    ffprobe: str,
) -> tuple[float | None, str]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_read_frames,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = run_command(
            command,
            label=f"decoded frame-count duration probe for {path}",
            text=True,
        )
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "ffprobe returned invalid JSON"
    except UnusableInputError as exc:
        return None, str(exc)

    streams = payload.get("streams", [])
    if not streams:
        return None, "no video stream returned a decoded frame count"
    stream = streams[0]
    frame_count = positive_float(stream.get("nb_read_frames"))
    frame_rate = positive_rate(stream.get("avg_frame_rate"))
    if frame_count is None or frame_rate is None:
        return None, "decoded frame count and average frame rate were not both usable"
    duration_s = positive_float(frame_count / frame_rate)
    if duration_s is None:
        return None, "decoded frame count divided by average frame rate was invalid"
    return (
        duration_s,
        f"nb_read_frames={int(frame_count)} avg_frame_rate={frame_rate:.6f}",
    )


def resolve_duration_s(
    path: str | Path,
    ffprobe: str | None = None,
    *,
    stream_index: int | None = None,
    rung_out: list[str] | None = None,
) -> float:
    """Resolve duration through metadata, packet timestamps, then decoded frames."""
    media_path = Path(path)
    active_ffprobe = ffprobe or shutil.which("ffprobe")
    if active_ffprobe is None:
        raise UnusableInputError("ffprobe required")

    attempts: list[str] = []
    duration_s, rung, detail = _duration_from_metadata(
        media_path,
        active_ffprobe,
        stream_index,
    )
    attempts.append(f"(a) format/stream metadata: {detail}")
    if duration_s is not None and rung is not None:
        if rung_out is not None:
            rung_out.append(rung)
        print(
            f"DURATION: {media_path} resolved to {duration_s:.6f}s via "
            f"rung (a) {rung.removeprefix('a_')}"
        )
        return duration_s

    duration_s, detail = _duration_from_last_packet(media_path, active_ffprobe)
    attempts.append(f"(b) last packet pts_time: {detail}")
    if duration_s is not None:
        if rung_out is not None:
            rung_out.append("b_packet_pts")
        print(
            f"DURATION: {media_path} resolved to {duration_s:.6f}s via "
            f"rung (b) last packet pts_time"
        )
        return duration_s

    duration_s, detail = _duration_from_decoded_frame_count(
        media_path,
        active_ffprobe,
    )
    attempts.append(f"(c) decoded frame count / average frame rate: {detail}")
    if duration_s is not None:
        if rung_out is not None:
            rung_out.append("c_decoded_frames")
        print(
            f"DURATION: {media_path} resolved to {duration_s:.6f}s via "
            f"rung (c) decoded frame count / average frame rate"
        )
        return duration_s

    raise UnusableInputError(
        f"could not resolve a positive finite video duration for {media_path}; "
        f"WHY: {'; '.join(attempts)}"
    )


def probe_media(path: Path, ffprobe: str) -> MediaInfo:
    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,duration,disposition",
        "-i",
        str(path),
    ]
    result = run_command(command, label=f"ffprobe {path}", text=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise UnusableInputError(f"ffprobe returned invalid JSON for {path}") from exc

    video_streams = []
    for stream in payload.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        if stream.get("disposition", {}).get("attached_pic") == 1:
            continue
        try:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            index = int(stream["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if width >= MIN_DETAIL_DIMENSION and height >= MIN_DETAIL_DIMENSION:
            video_streams.append((stream, index, width, height))

    if not video_streams:
        raise UnusableInputError(f"no usable video stream in {path}")

    default_streams = [
        item for item in video_streams if item[0].get("disposition", {}).get("default") == 1
    ]
    stream, index, width, height = (default_streams or video_streams)[0]
    duration_s = resolve_duration_s(
        path,
        ffprobe,
        stream_index=index,
    )

    return MediaInfo(
        path=str(path),
        width=width,
        height=height,
        duration_s=duration_s,
        stream_index=index,
    )


def probe_timestamps(duration_s: float, count: int) -> list[float]:
    """Return bin-centered, evenly spaced timestamps inside each file's middle 80%."""
    step = PROBE_WINDOW_FRACTION / count
    return [
        duration_s * (PROBE_WINDOW_START_FRACTION + (index + 0.5) * step)
        for index in range(count)
    ]


def scaled_width(width: int, height: int, target_height: int) -> int:
    """Preserve native pixel aspect while ensuring a positive comparison width."""
    return max(1, int(round(width * target_height / height)))


def detail_score(frame: np.ndarray) -> float:
    """Mean absolute horizontal difference plus mean absolute vertical difference."""
    pixels = frame.astype(np.int16, copy=False)
    horizontal = float(np.mean(np.abs(np.diff(pixels, axis=1))))
    vertical = float(np.mean(np.abs(np.diff(pixels, axis=0))))
    return horizontal + vertical


def measure_probes(
    decoder: FrameDecoder,
    media: MediaInfo,
    timestamps: Sequence[float],
    width: int,
    height: int,
    *,
    roundtrip_phase: tuple[int, int] | None = None,
) -> list[ProbeMeasurement]:
    measurements = []
    failures = []
    for timestamp_s in timestamps:
        try:
            frame = decoder.gray_frame(
                media,
                timestamp_s,
                width,
                height,
                roundtrip_phase=roundtrip_phase,
            )
        except UnusableInputError as exc:
            failures.append((timestamp_s, str(exc)))
            print(
                f"WARNING: degraded probe for {media.path} at "
                f"{timestamp_s:.3f}s: {exc}",
                file=sys.stderr,
            )
            continue
        measurements.append(
            ProbeMeasurement(
                timestamp_s=timestamp_s,
                detail=detail_score(frame),
            )
        )
    if len(failures) * 2 > len(timestamps):
        failure_summary = " | ".join(
            f"{timestamp_s:.3f}s: {reason}"
            for timestamp_s, reason in failures
        )
        raise UnusableInputError(
            f"more than half the probes failed to decode for {media.path}: "
            f"{len(failures)}/{len(timestamps)}; WHY: {failure_summary}"
        )
    return measurements


def median_detail(measurements: Sequence[ProbeMeasurement]) -> float:
    return float(median(item.detail for item in measurements))


def compare_source_distributions(
    decoder: FrameDecoder,
    final: MediaInfo,
    sources: Sequence[MediaInfo],
    probe_count: int,
) -> list[SourceComparison]:
    comparisons = []
    final_timestamps = probe_timestamps(final.duration_s, probe_count)

    for source in sources:
        comparison_height = min(final.height, source.height)
        final_width = scaled_width(final.width, final.height, comparison_height)
        source_width = scaled_width(source.width, source.height, comparison_height)
        final_probes = measure_probes(
            decoder,
            final,
            final_timestamps,
            final_width,
            comparison_height,
        )
        source_probes = measure_probes(
            decoder,
            source,
            probe_timestamps(source.duration_s, probe_count),
            source_width,
            comparison_height,
        )
        final_median = median_detail(final_probes)
        source_median = median_detail(source_probes)
        if source_median <= MIN_MEASURABLE_DETAIL:
            raise UnusableInputError(
                f"source has no measurable detail in its probe distribution: {source.path}"
            )
        comparisons.append(
            SourceComparison(
                source=source.path,
                comparison_width_final=final_width,
                comparison_width_source=source_width,
                comparison_height=comparison_height,
                final_probes=final_probes,
                source_probes=source_probes,
                final_median_detail=final_median,
                source_median_detail=source_median,
                ratio=final_median / source_median,
            )
        )

    return comparisons


def check_declared_resolution(
    final: MediaInfo,
    sources: Sequence[MediaInfo],
) -> dict[str, Any]:
    width_source = max(sources, key=lambda item: (item.width, item.pixels))
    height_source = max(sources, key=lambda item: (item.height, item.pixels))
    required_width = width_source.width
    required_height = height_source.height
    width_margin = final.width - required_width
    height_margin = final.height - required_height
    passed = width_margin >= 0 and height_margin >= 0
    return {
        "name": "DECLARED_RESOLUTION",
        "status": "PASS" if passed else "FAIL",
        "final_width": final.width,
        "final_height": final.height,
        "required_source_width": required_width,
        "required_source_height": required_height,
        "width_source": width_source.path,
        "height_source": height_source.path,
        "width_margin_px": width_margin,
        "height_margin_px": height_margin,
        "why": (
            "The final must not advertise fewer pixels in either dimension than "
            "the largest corresponding dimension across the captured source cameras."
        ),
    }


def check_real_detail(
    comparisons: Sequence[SourceComparison],
    min_detail_ratio: float,
) -> dict[str, Any]:
    # The highest ratio is the least lossy available distribution match.
    selected = max(comparisons, key=lambda item: item.ratio)
    passed = selected.ratio >= min_detail_ratio
    return {
        "name": "REAL_DETAIL",
        "status": "PASS" if passed else "FAIL",
        "matching_method": (
            "Distribution comparison, not frame-pair comparison: each file is "
            "sampled at its own evenly spaced timestamps in its own middle 80%; "
            "with multiple sources, the highest final/source median ratio is the "
            "best available match."
        ),
        "selected_source": selected.source,
        "comparison_height": selected.comparison_height,
        "comparison_width_final": selected.comparison_width_final,
        "comparison_width_source": selected.comparison_width_source,
        "final_median_detail": selected.final_median_detail,
        "source_median_detail": selected.source_median_detail,
        "ratio": selected.ratio,
        "minimum_ratio": min_detail_ratio,
        "margin": selected.ratio - min_detail_ratio,
        "final_probes": [asdict(item) for item in selected.final_probes],
        "source_probes": [asdict(item) for item in selected.source_probes],
        "candidates": [
            {
                "source": item.source,
                "comparison_height": item.comparison_height,
                "comparison_width_final": item.comparison_width_final,
                "comparison_width_source": item.comparison_width_source,
                "final_median_detail": item.final_median_detail,
                "source_median_detail": item.source_median_detail,
                "ratio": item.ratio,
            }
            for item in comparisons
        ],
        "why": (
            "The median first-difference ratio measures retained picture detail "
            "after both files are scaled to a fair common height."
        ),
    }


def check_upscale_fingerprint(
    decoder: FrameDecoder,
    final: MediaInfo,
    probe_count: int,
) -> dict[str, Any]:
    timestamps = probe_timestamps(final.duration_s, probe_count)
    before = measure_probes(
        decoder,
        final,
        timestamps,
        final.width,
        final.height,
    )
    before_median = median_detail(before)
    if before_median <= MIN_MEASURABLE_DETAIL:
        raise UnusableInputError(
            "final has no measurable detail, so upscale retention is undefined"
        )
    phase_results = []
    for phase in ((0, 0), (1, 0), (0, 1), (1, 1)):
        after_for_phase = measure_probes(
            decoder,
            final,
            timestamps,
            final.width,
            final.height,
            roundtrip_phase=phase,
        )
        phase_results.append((phase, after_for_phase, median_detail(after_for_phase)))
    selected_phase, after, after_median = max(
        phase_results,
        key=lambda item: item[2],
    )
    retention = after_median / before_median
    passed = retention <= UPSCALE_RETENTION_MAX
    return {
        "name": "UPSCALE_FINGERPRINT",
        "status": "PASS" if passed else "FAIL",
        "method": (
            "Source-independent final-frame round trip; it uses no cross-file "
            "timestamp or frame matching."
        ),
        "before_median_detail": before_median,
        "after_median_detail": after_median,
        "retention_ratio": retention,
        "maximum_retention": UPSCALE_RETENTION_MAX,
        "margin": UPSCALE_RETENTION_MAX - retention,
        "selected_sampling_phase": list(selected_phase),
        "phase_candidates": [
            {
                "phase": list(phase),
                "after_median_detail": phase_median,
                "retention_ratio": phase_median / before_median,
            }
            for phase, _phase_probes, phase_median in phase_results
        ],
        "before_probes": [asdict(item) for item in before],
        "after_probes": [asdict(item) for item in after],
        "why": (
            f"Retention above {UPSCALE_RETENTION_MAX:.2f} means halving and "
            f"restoring the frame removed less than "
            f"{(1.0 - UPSCALE_RETENTION_MAX) * 100:.0f}% of its detail, a "
            "fingerprint of picture already limited to roughly half the claimed "
            "linear resolution. The maximum across all four 2x2 sampling "
            "origins prevents a scaler phase shift from hiding that lattice."
        ),
    }


def evaluate(
    final_path: str,
    source_paths: Sequence[str],
    min_detail_ratio: float,
    probe_count: int,
    *,
    ffmpeg: str,
    ffprobe: str,
    decoder: FrameDecoder | None = None,
) -> dict[str, Any]:
    final_file = validate_local_file(final_path, "--final")
    source_files = [
        validate_local_file(path, f"--sources[{index}]")
        for index, path in enumerate(source_paths)
    ]
    final = probe_media(final_file, ffprobe)
    sources = [probe_media(path, ffprobe) for path in source_files]
    active_decoder = decoder or FrameDecoder(ffmpeg)

    declared = check_declared_resolution(final, sources)
    comparisons = compare_source_distributions(
        active_decoder,
        final,
        sources,
        probe_count,
    )
    real_detail = check_real_detail(comparisons, min_detail_ratio)
    upscale = check_upscale_fingerprint(active_decoder, final, probe_count)
    checks = [declared, real_detail, upscale]
    passed = all(check["status"] == "PASS" for check in checks)

    return {
        "status": "PASS" if passed else "FAIL",
        "exit_code": 0 if passed else 1,
        "final": asdict(final),
        "sources": [asdict(source) for source in sources],
        "probe_count": probe_count,
        "probe_window": {
            "start_fraction": PROBE_WINDOW_START_FRACTION,
            "span_fraction": PROBE_WINDOW_FRACTION,
            "placement": "evenly spaced bin centers",
        },
        "thresholds": {
            "min_detail_ratio": min_detail_ratio,
            "upscale_retention_max": UPSCALE_RETENTION_MAX,
        },
        "checks": checks,
    }


def format_probes(probes: Sequence[dict[str, Any]]) -> str:
    return ", ".join(
        f"t={item['timestamp_s']:.3f}s detail={item['detail']:.6f}"
        for item in probes
    )


def render_report(report: dict[str, Any]) -> None:
    for check in report["checks"]:
        name = check["name"]
        status = check["status"]
        if name == "DECLARED_RESOLUTION":
            print(
                f"{status} {name}: final={check['final_width']}x{check['final_height']}; "
                f"source_envelope={check['required_source_width']}x"
                f"{check['required_source_height']}; "
                f"margin={check['width_margin_px']:+d}px width, "
                f"{check['height_margin_px']:+d}px height; "
                f"width_source={check['width_source']}; "
                f"height_source={check['height_source']}"
            )
        elif name == "REAL_DETAIL":
            print(f"MATCHING: {check['matching_method']}")
            print(
                f"{status} {name}: final_median={check['final_median_detail']:.6f}; "
                f"source_median={check['source_median_detail']:.6f}; "
                f"ratio={check['ratio']:.6f}; "
                f"required>={check['minimum_ratio']:.6f}; "
                f"margin={check['margin']:+.6f}; source={check['selected_source']}; "
                f"comparison={check['comparison_width_final']}x"
                f"{check['comparison_height']} final vs "
                f"{check['comparison_width_source']}x"
                f"{check['comparison_height']} source"
            )
            print(f"  FINAL_PROBES: {format_probes(check['final_probes'])}")
            print(f"  SOURCE_PROBES: {format_probes(check['source_probes'])}")
            if len(check["candidates"]) > 1:
                for candidate in check["candidates"]:
                    print(
                        "  SOURCE_CANDIDATE: "
                        f"{candidate['source']} "
                        f"final_median={candidate['final_median_detail']:.6f} "
                        f"source_median={candidate['source_median_detail']:.6f} "
                        f"ratio={candidate['ratio']:.6f} "
                        f"height={candidate['comparison_height']}"
                    )
        elif name == "UPSCALE_FINGERPRINT":
            print(f"METHOD: {check['method']}")
            print(
                f"{status} {name}: before_median={check['before_median_detail']:.6f}; "
                f"after_median={check['after_median_detail']:.6f}; "
                f"retention={check['retention_ratio']:.6f}; "
                f"required<={check['maximum_retention']:.6f}; "
                f"margin={check['margin']:+.6f}; "
                f"sampling_phase={check['selected_sampling_phase']}"
            )
            print(f"  BEFORE_PROBES: {format_probes(check['before_probes'])}")
            print(f"  AFTER_PROBES: {format_probes(check['after_probes'])}")
            for phase in check["phase_candidates"]:
                print(
                    "  PHASE_CANDIDATE: "
                    f"phase={phase['phase']} "
                    f"after_median={phase['after_median_detail']:.6f} "
                    f"retention={phase['retention_ratio']:.6f}"
                )
        print(f"  WHY: {check['why']}")

    print(f"{report['status']} RESOLUTION_CHECK")


def write_json_report(report: dict[str, Any], output_path: str) -> None:
    path = Path(output_path).expanduser().resolve()
    if path.exists() and not path.is_file():
        raise UnusableInputError(f"--json is not a regular file path: {path}")
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise UnusableInputError(f"cannot write --json output {path}: {exc}") from exc


def synthesize_self_test_media(ffmpeg: str, directory: Path) -> dict[str, Path]:
    paths = {
        "source": directory / "source.mkv",
        "good": directory / "good.mkv",
        "bad": directory / "bad.mkv",
        "low": directory / "low.mkv",
        "durationless": directory / "durationless-source.webm",
        "corrupt": directory / "corrupt-input.webm",
    }
    pattern = (
        f"testsrc2=size=1920x1080:rate={SELF_TEST_FPS}:"
        f"duration={SELF_TEST_DURATION_SECONDS},"
        f"noise=alls={SELF_TEST_NOISE_STRENGTH}:allf=t+u:"
        f"all_seed={SELF_TEST_NOISE_SEED}"
    )
    synthesize = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        pattern,
        "-an",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        "-pix_fmt",
        "yuv420p",
        str(paths["source"]),
    ]
    run_command(synthesize, label="synthesize detailed self-test source")

    transforms = {
        "good": "scale=1920:1080:flags=lanczos,setsar=1",
        "bad": (
            "scale=960:540:flags=area,"
            "scale=1920:1080:flags=lanczos,setsar=1"
        ),
        "low": "scale=1280:720:flags=area,setsar=1",
    }
    for name, video_filter in transforms.items():
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(paths["source"]),
            "-map",
            "0:v:0",
            "-vf",
            video_filter,
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(paths[name]),
        ]
        run_command(command, label=f"synthesize {name} self-test final")

    durationless = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        str(paths["source"]),
        "-map",
        "0:v:0",
        "-vf",
        "scale=640:360:flags=area,setsar=1",
        "-an",
        "-c:v",
        "libvpx",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "webm",
        "-live",
        "1",
        str(paths["durationless"]),
    ]
    run_command(
        durationless,
        label="synthesize genuinely durationless VP8/WebM self-test source",
    )
    paths["corrupt"].write_bytes(b"not a media container\n")
    return paths


def check_by_name(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(check for check in report["checks"] if check["name"] == name)


def run_self_test() -> int:
    try:
        ffmpeg, ffprobe = require_tools()
        with tempfile.TemporaryDirectory(prefix="resolution-check-self-test-") as tmp:
            paths = synthesize_self_test_media(ffmpeg, Path(tmp))
            decoder = FrameDecoder(ffmpeg)
            reports = {}
            for case in ("good", "bad", "low"):
                print(f"SELF-TEST CASE {case.upper()}")
                report = evaluate(
                    str(paths[case]),
                    [str(paths["source"])],
                    DEFAULT_MIN_DETAIL_RATIO,
                    DEFAULT_PROBE_COUNT,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    decoder=decoder,
                )
                reports[case] = report
                render_report(report)

            duration_metadata = run_command(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_entries",
                    "format=duration:stream=duration",
                    str(paths["durationless"]),
                ],
                label="verify durationless self-test fixture metadata",
                text=True,
            )
            try:
                duration_payload = json.loads(duration_metadata.stdout)
            except json.JSONDecodeError as exc:
                raise UnusableInputError(
                    "durationless fixture metadata verification returned invalid JSON"
                ) from exc
            stream_duration_value: Any = "N/A"
            streams = duration_payload.get("streams", [])
            if streams:
                stream_duration_value = streams[0].get("duration", "N/A")
            format_duration_value = duration_payload.get("format", {}).get(
                "duration",
                "N/A",
            )
            metadata_absent = (
                positive_float(stream_duration_value) is None
                and positive_float(format_duration_value) is None
            )
            print(
                "SELF-TEST DURATIONLESS METADATA VERIFICATION: "
                f"stream_duration={stream_duration_value} "
                f"format_duration={format_duration_value} "
                f"genuinely_N/A={metadata_absent}"
            )

            duration_rungs: list[str] = []
            recovered_duration_s = resolve_duration_s(
                paths["durationless"],
                ffprobe,
                rung_out=duration_rungs,
            )
            duration_error_fraction = (
                abs(recovered_duration_s - SELF_TEST_DURATION_SECONDS)
                / SELF_TEST_DURATION_SECONDS
            )
            packet_duration_ok = (
                metadata_absent
                and duration_rungs == ["b_packet_pts"]
                and duration_error_fraction <= 0.10
            )
            print(
                "SELF-TEST DURATIONLESS RESOLUTION: "
                f"true={SELF_TEST_DURATION_SECONDS:.6f}s "
                f"resolved={recovered_duration_s:.6f}s "
                f"relative_error={duration_error_fraction:.6f} "
                f"rung={duration_rungs[-1] if duration_rungs else 'none'} "
                f"packet_fallback_verified={packet_duration_ok}"
            )

            print("SELF-TEST CASE DURATIONLESS-SOURCE")
            durationless_report = evaluate(
                str(paths["good"]),
                [str(paths["durationless"])],
                DEFAULT_MIN_DETAIL_RATIO,
                DEFAULT_PROBE_COUNT,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                decoder=decoder,
            )
            reports["durationless"] = durationless_report
            render_report(durationless_report)
            durationless_ok = (
                packet_duration_ok
                and durationless_report["exit_code"] == 0
                and all(
                    check["status"] == "PASS"
                    for check in durationless_report["checks"]
                )
            )

            print("SELF-TEST CASE EXIT-CODE")
            corrupt_result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--final",
                    str(paths["corrupt"]),
                    "--sources",
                    str(paths["source"]),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
            corrupt_exit_ok = corrupt_result.returncode == 2
            print(
                "SELF-TEST EXIT-CODE VERIFICATION: "
                f"expected_exit=2 actual_exit={corrupt_result.returncode} "
                f"error_reported={'ERROR:' in corrupt_result.stderr} "
                f"unusable_input_exit_verified={corrupt_exit_ok}"
            )
            if corrupt_result.stderr.strip():
                print(
                    "  CORRUPT_INPUT_WHY: "
                    f"{diagnostic_tail(corrupt_result.stderr)}"
                )

            good_checks = reports["good"]["checks"]
            good_ok = (
                reports["good"]["exit_code"] == 0
                and all(check["status"] == "PASS" for check in good_checks)
            )
            bad_detail = check_by_name(reports["bad"], "REAL_DETAIL")
            bad_upscale = check_by_name(reports["bad"], "UPSCALE_FINGERPRINT")
            bad_ok = (
                reports["bad"]["exit_code"] == 1
                and (
                    bad_detail["status"] == "FAIL"
                    or bad_upscale["status"] == "FAIL"
                )
            )
            low_declared = check_by_name(reports["low"], "DECLARED_RESOLUTION")
            low_ok = (
                reports["low"]["exit_code"] == 1
                and low_declared["status"] == "FAIL"
            )

            print(
                "SELF-TEST ASSERT GOOD: "
                f"expected_exit=0 actual_exit={reports['good']['exit_code']} "
                f"all_three_pass={good_ok}"
            )
            print(
                "SELF-TEST ASSERT BAD: "
                f"expected_exit=1 actual_exit={reports['bad']['exit_code']} "
                f"real_detail_margin={bad_detail['margin']:+.6f} "
                f"upscale_margin={bad_upscale['margin']:+.6f} "
                f"quality_failure_detected={bad_ok}"
            )
            print(
                "SELF-TEST ASSERT BELOW-SOURCE: "
                f"expected_exit=1 actual_exit={reports['low']['exit_code']} "
                f"width_margin={low_declared['width_margin_px']:+d}px "
                f"height_margin={low_declared['height_margin_px']:+d}px "
                f"declared_failure_detected={low_ok}"
            )
            print(
                "SELF-TEST ASSERT DURATIONLESS-SOURCE: "
                f"expected_exit=0 actual_exit={durationless_report['exit_code']} "
                f"packet_fallback_verified={packet_duration_ok} "
                f"all_three_pass={durationless_ok}"
            )
            print(
                "SELF-TEST ASSERT EXIT-CODE: "
                f"expected_exit=2 actual_exit={corrupt_result.returncode} "
                f"unusable_input_exit_verified={corrupt_exit_ok}"
            )

            if (
                good_ok
                and bad_ok
                and low_ok
                and durationless_ok
                and corrupt_exit_ok
            ):
                print("PASS SELF-TEST")
                return 0
            print("FAIL SELF-TEST")
            return 1
    except UnusableInputError as exc:
        print(f"ERROR SELF-TEST: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Certify declared resolution, real detail, and the absence of an "
            "upscale fingerprint."
        )
    )
    parser.add_argument("--final", help="final video to certify")
    parser.add_argument(
        "--sources",
        nargs="+",
        help="one or more source camera videos",
    )
    parser.add_argument(
        "--min-detail-ratio",
        type=float,
        default=DEFAULT_MIN_DETAIL_RATIO,
        help=f"minimum final/source median detail ratio (default: {DEFAULT_MIN_DETAIL_RATIO})",
    )
    parser.add_argument(
        "--probe-count",
        type=int,
        default=DEFAULT_PROBE_COUNT,
        help=f"probes per file in its middle 80%% (default: {DEFAULT_PROBE_COUNT})",
    )
    parser.add_argument("--json", metavar="OUT", help="also write the full report as JSON")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run hermetic ffmpeg-lavfi green and red cases",
    )
    return parser


def validate_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.self_test:
        if args.final is not None or args.sources is not None or args.json is not None:
            parser.error("--self-test cannot be combined with --final, --sources, or --json")
        return
    if args.final is None:
        parser.error("--final is required unless --self-test is used")
    if not args.sources:
        parser.error("--sources requires at least one path unless --self-test is used")
    if not math.isfinite(args.min_detail_ratio) or args.min_detail_ratio <= 0.0:
        parser.error("--min-detail-ratio must be a positive finite number")
    if args.probe_count < MIN_PROBE_COUNT:
        parser.error(f"--probe-count must be at least {MIN_PROBE_COUNT}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_arguments(parser, args)
    if args.self_test:
        return run_self_test()

    try:
        ffmpeg, ffprobe = require_tools()
        report = evaluate(
            args.final,
            args.sources,
            args.min_detail_ratio,
            args.probe_count,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        if args.json is not None:
            input_paths = {
                Path(args.final).expanduser().resolve(),
                *(Path(path).expanduser().resolve() for path in args.sources),
            }
            json_path = Path(args.json).expanduser().resolve()
            if json_path in input_paths:
                raise UnusableInputError("--json must not overwrite an input media file")
            write_json_report(report, args.json)
        render_report(report)
        return int(report["exit_code"])
    except UnusableInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
