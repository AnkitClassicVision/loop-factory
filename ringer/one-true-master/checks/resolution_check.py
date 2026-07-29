#!/usr/bin/env python3
"""Certify that a final master preserves the resolution captured by its cameras."""

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
# Same-framing re-encodes should retain nearly all source detail. A controlled
# 960x540-to-1920x1080 round trip lost about 13.5% on the real episode.
DEFAULT_MIN_DETAIL_RATIO = 0.85
# Kept only to show what the retired absolute fingerprint would have concluded.
LEGACY_UPSCALE_RETENTION_MAX = 0.92
DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_CANVAS_HEIGHT = 1080
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
class AlignedDetailProbe:
    ancestor_timestamp_s: float
    final_timestamp_s: float
    ancestor_detail: float
    final_detail: float
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


def validate_stem_directory(path_value: str) -> list[Path]:
    directory = Path(path_value).expanduser().resolve()
    if not directory.exists():
        raise UnusableInputError(f"--stems does not exist: {directory}")
    if not directory.is_dir():
        raise UnusableInputError(f"--stems is not a directory: {directory}")
    try:
        stems = sorted(
            (path.resolve() for path in directory.iterdir() if path.is_file()),
            key=lambda path: (path.name.casefold(), str(path)),
        )
    except OSError as exc:
        raise UnusableInputError(f"cannot read --stems directory {directory}: {exc}") from exc
    if not stems:
        raise UnusableInputError(f"--stems contains no regular files: {directory}")
    for index, stem in enumerate(stems):
        if not os.access(stem, os.R_OK):
            raise UnusableInputError(f"--stems[{index}] is not readable: {stem}")
    # A real stems directory holds audio stems (.flac/.wav) beside the video
    # stems; probing an audio file as video aborted the whole gate on the first
    # live run (2026-07-29). Inside a DIRECTORY, skip files carrying no video
    # stream and say which were skipped. Strictness is preserved where it
    # matters: an explicitly named file with no video stream still raises,
    # because the caller asserted it was a video.
    video_stems, skipped = [], []
    for stem in stems:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(stem)],
                capture_output=True, text=True, timeout=60)
            has_video = probe.returncode == 0 and probe.stdout.strip().split(",")[0].isdigit()
        except (OSError, subprocess.SubprocessError):
            has_video = False
        if has_video:
            video_stems.append(stem)
        else:
            skipped.append(stem.name)
    if skipped:
        print("STEM_DIRECTORY_FILTER skipped_non_video=%s" % ",".join(sorted(skipped)),
              flush=True)
    if not video_stems:
        raise UnusableInputError(
            f"--stems contains no files with a video stream: {directory}")
    return video_stems


def parse_canvas(value: str) -> tuple[int, int]:
    width_text, separator, height_text = value.lower().partition("x")
    if not separator:
        raise argparse.ArgumentTypeError("--canvas must use WIDTHxHEIGHT")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--canvas must use integer WIDTHxHEIGHT") from exc
    if width < MIN_DETAIL_DIMENSION or height < MIN_DETAIL_DIMENSION:
        raise argparse.ArgumentTypeError(
            f"--canvas dimensions must each be at least {MIN_DETAIL_DIMENSION}"
        )
    return width, height


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


def check_stem_resolution(
    stems: Sequence[MediaInfo] | None,
    sources: Sequence[MediaInfo],
    canvas: tuple[int, int],
) -> dict[str, Any]:
    if stems is None:
        return {
            "name": "STEM_RESOLUTION",
            "status": "SKIPPED",
            "why": (
                "--stems was not supplied, so the primary deterministic check "
                "could not inspect per-speaker intermediate dimensions."
            ),
        }

    canvas_width, canvas_height = canvas
    comparisons = []
    for index, (stem, source) in enumerate(zip(stems, sources)):
        required_width = min(source.width, canvas_width)
        required_height = min(source.height, canvas_height)
        width_margin = stem.width - required_width
        height_margin = stem.height - required_height
        pair_passed = width_margin >= 0 and height_margin >= 0
        comparisons.append(
            {
                "index": index,
                "stem": stem.path,
                "source": source.path,
                "stem_width": stem.width,
                "stem_height": stem.height,
                "source_width": source.width,
                "source_height": source.height,
                "required_width": required_width,
                "required_height": required_height,
                "width_margin_px": width_margin,
                "height_margin_px": height_margin,
                "status": "PASS" if pair_passed else "FAIL",
            }
        )

    unmatched_stems = [stem.path for stem in stems[len(comparisons) :]]
    unmatched_sources = [source.path for source in sources[len(comparisons) :]]
    counts_match = len(stems) == len(sources)
    passed = counts_match and all(
        comparison["status"] == "PASS" for comparison in comparisons
    )
    return {
        "name": "STEM_RESOLUTION",
        "status": "PASS" if passed else "FAIL",
        "matching_method": (
            "Stem files and source camera tracks are independently sorted by "
            "filename, then paired by zero-based index."
        ),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "stem_count": len(stems),
        "source_count": len(sources),
        "counts_match": counts_match,
        "comparisons": comparisons,
        "unmatched_stems": unmatched_stems,
        "unmatched_sources": unmatched_sources,
        "why": (
            "Each stem must be at least min(its index-matched camera dimension, "
            "the delivery canvas) on both axes. Pair names are printed so an "
            "incorrect index match is visible."
        ),
    }


def measure_aligned_detail(
    decoder: FrameDecoder,
    final: MediaInfo,
    ancestor: MediaInfo,
    probe_count: int,
    head_offset_s: float,
) -> tuple[list[AlignedDetailProbe], int, int]:
    ancestor_span_in_final_s = final.duration_s - head_offset_s
    if ancestor_span_in_final_s <= 0.0:
        raise UnusableInputError(
            f"--head-offset-s {head_offset_s:.6f} leaves no aligned final duration"
        )
    aligned_duration_s = min(ancestor.duration_s, ancestor_span_in_final_s)
    timestamps = probe_timestamps(aligned_duration_s, probe_count)
    comparison_height = min(final.height, ancestor.height)
    final_width = scaled_width(final.width, final.height, comparison_height)
    ancestor_width = scaled_width(
        ancestor.width,
        ancestor.height,
        comparison_height,
    )
    measurements = []
    failures = []
    for ancestor_timestamp_s in timestamps:
        final_timestamp_s = head_offset_s + ancestor_timestamp_s
        try:
            final_frame = decoder.gray_frame(
                final,
                final_timestamp_s,
                final_width,
                comparison_height,
            )
            ancestor_frame = decoder.gray_frame(
                ancestor,
                ancestor_timestamp_s,
                ancestor_width,
                comparison_height,
            )
            final_detail = detail_score(final_frame)
            ancestor_detail = detail_score(ancestor_frame)
            if ancestor_detail <= MIN_MEASURABLE_DETAIL:
                raise UnusableInputError(
                    "ancestor frame has no measurable first-difference detail"
                )
        except UnusableInputError as exc:
            failures.append((ancestor_timestamp_s, final_timestamp_s, str(exc)))
            print(
                f"WARNING: degraded aligned probe ancestor={ancestor_timestamp_s:.3f}s "
                f"final={final_timestamp_s:.3f}s: {exc}",
                file=sys.stderr,
            )
            continue
        measurements.append(
            AlignedDetailProbe(
                ancestor_timestamp_s=ancestor_timestamp_s,
                final_timestamp_s=final_timestamp_s,
                ancestor_detail=ancestor_detail,
                final_detail=final_detail,
                ratio=final_detail / ancestor_detail,
            )
        )
    if len(failures) * 2 > len(timestamps):
        failure_summary = " | ".join(
            f"ancestor={ancestor_s:.3f}s final={final_s:.3f}s: {reason}"
            for ancestor_s, final_s, reason in failures
        )
        raise UnusableInputError(
            f"more than half the aligned probes were unusable: "
            f"{len(failures)}/{len(timestamps)}; WHY: {failure_summary}"
        )
    return measurements, final_width, ancestor_width


def check_same_framing_detail(
    decoder: FrameDecoder,
    final: MediaInfo,
    ancestor: MediaInfo | None,
    probe_count: int,
    head_offset_s: float,
    min_detail_ratio: float,
) -> dict[str, Any]:
    if ancestor is None:
        return {
            "name": "SAME_FRAMING_DETAIL",
            "status": "SKIPPED",
            "why": (
                "--ancestor was not supplied, so no direct same-framing "
                "final/ancestor comparison was possible."
            ),
        }

    measurements, final_width, ancestor_width = measure_aligned_detail(
        decoder,
        final,
        ancestor,
        probe_count,
        head_offset_s,
    )
    ratio = float(median(item.ratio for item in measurements))
    passed = ratio >= min_detail_ratio
    return {
        "name": "SAME_FRAMING_DETAIL",
        "status": "PASS" if passed else "FAIL",
        "matching_method": (
            "Each ancestor probe is paired to final time "
            "head_offset_s + ancestor_time; the median is taken over paired "
            "per-frame final/ancestor detail ratios."
        ),
        "ancestor": ancestor.path,
        "head_offset_s": head_offset_s,
        "comparison_height": min(final.height, ancestor.height),
        "comparison_width_final": final_width,
        "comparison_width_ancestor": ancestor_width,
        "ratio": ratio,
        "minimum_ratio": min_detail_ratio,
        "margin": ratio - min_detail_ratio,
        "aligned_probes": [asdict(item) for item in measurements],
        "why": (
            "Only a direct ancestor with identical framing supports a meaningful "
            "detail-retention gate; unrelated camera/composite framings do not."
        ),
    }


def measure_upscale_retention_advisory(
    decoder: FrameDecoder,
    media: MediaInfo,
    probe_count: int,
) -> dict[str, Any]:
    timestamps = probe_timestamps(media.duration_s, probe_count)
    before = measure_probes(
        decoder,
        media,
        timestamps,
        media.width,
        media.height,
    )
    before_median = median_detail(before)
    if before_median <= MIN_MEASURABLE_DETAIL:
        raise UnusableInputError("media has no measurable detail")
    phase_results = []
    for phase in ((0, 0), (1, 0), (0, 1), (1, 1)):
        after_for_phase = measure_probes(
            decoder,
            media,
            timestamps,
            media.width,
            media.height,
            roundtrip_phase=phase,
        )
        phase_results.append((phase, median_detail(after_for_phase)))
    selected_phase, after_median = max(phase_results, key=lambda item: item[1])
    retention = after_median / before_median
    return {
        "media": media.path,
        "width": media.width,
        "height": media.height,
        "measurement_status": "MEASURED",
        "before_median_detail": before_median,
        "after_median_detail": after_median,
        "retention_ratio": retention,
        "selected_sampling_phase": list(selected_phase),
        "legacy_threshold": LEGACY_UPSCALE_RETENTION_MAX,
        "legacy_would_fail": retention > LEGACY_UPSCALE_RETENTION_MAX,
    }


def upscale_fingerprint_advisory(
    decoder: FrameDecoder,
    final: MediaInfo,
    sources: Sequence[MediaInfo],
    probe_count: int,
) -> dict[str, Any]:
    measurements = []
    for role, media in [
        ("final", final),
        *((f"source[{index}]", source) for index, source in enumerate(sources)),
    ]:
        try:
            measurement = measure_upscale_retention_advisory(
                decoder,
                media,
                probe_count,
            )
        except UnusableInputError as exc:
            measurement = {
                "media": media.path,
                "width": media.width,
                "height": media.height,
                "measurement_status": "UNAVAILABLE",
                "why_unavailable": str(exc),
            }
        measurement["role"] = role
        measurements.append(measurement)

    # This can never gate. On the real episode, untouched raw camera footage
    # scored 1.000-1.008 retention, above the retired 0.92 failure threshold.
    # Native webcam softness therefore looks identical to prior upscaling.
    return {
        "name": "UPSCALE_FINGERPRINT_ADVISORY",
        "status": "INFO",
        "measurements": measurements,
        "why": (
            "Advisory only, never a FAIL: real raw-camera footage measured "
            "1.000-1.008 retention because native webcam softness contains "
            "little fine detail for a half-size round trip to remove."
        ),
    }


def evaluate(
    final_path: str,
    source_paths: Sequence[str],
    min_detail_ratio: float,
    probe_count: int,
    *,
    stems_path: str | None,
    ancestor_path: str | None,
    canvas: tuple[int, int],
    head_offset_s: float,
    ffmpeg: str,
    ffprobe: str,
    decoder: FrameDecoder | None = None,
) -> dict[str, Any]:
    final_file = validate_local_file(final_path, "--final")
    source_files = sorted(
        (
            validate_local_file(path, f"--sources[{index}]")
            for index, path in enumerate(source_paths)
        ),
        key=lambda path: (path.name.casefold(), str(path)),
    )
    stem_files = (
        validate_stem_directory(stems_path) if stems_path is not None else None
    )
    ancestor_file = (
        validate_local_file(ancestor_path, "--ancestor")
        if ancestor_path is not None
        else None
    )
    final = probe_media(final_file, ffprobe)
    sources = [probe_media(path, ffprobe) for path in source_files]
    stems = (
        [probe_media(path, ffprobe) for path in stem_files]
        if stem_files is not None
        else None
    )
    ancestor = (
        probe_media(ancestor_file, ffprobe) if ancestor_file is not None else None
    )
    active_decoder = decoder or FrameDecoder(ffmpeg)

    stem_resolution = check_stem_resolution(stems, sources, canvas)
    declared = check_declared_resolution(final, sources)
    same_framing = check_same_framing_detail(
        active_decoder,
        final,
        ancestor,
        probe_count,
        head_offset_s,
        min_detail_ratio,
    )
    advisory = upscale_fingerprint_advisory(
        active_decoder,
        final,
        sources,
        probe_count,
    )
    checks = [stem_resolution, declared, same_framing, advisory]
    passed = not any(check["status"] == "FAIL" for check in checks)

    return {
        "status": "PASS" if passed else "FAIL",
        "exit_code": 0 if passed else 1,
        "final": asdict(final),
        "sources": [asdict(source) for source in sources],
        "stems": (
            [asdict(stem) for stem in stems] if stems is not None else None
        ),
        "ancestor": asdict(ancestor) if ancestor is not None else None,
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "head_offset_s": head_offset_s,
        "probe_count": probe_count,
        "probe_window": {
            "start_fraction": PROBE_WINDOW_START_FRACTION,
            "span_fraction": PROBE_WINDOW_FRACTION,
            "placement": "evenly spaced bin centers",
        },
        "thresholds": {
            "min_detail_ratio": min_detail_ratio,
            "legacy_upscale_retention_max_advisory_only": (
                LEGACY_UPSCALE_RETENTION_MAX
            ),
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
        if name == "STEM_RESOLUTION":
            if status == "SKIPPED":
                print(f"{status} {name}")
            else:
                print(f"MATCHING: {check['matching_method']}")
                print(
                    f"{status} {name}: stems={check['stem_count']} "
                    f"sources={check['source_count']} "
                    f"counts_match={check['counts_match']}; "
                    f"canvas={check['canvas_width']}x{check['canvas_height']}"
                )
                for comparison in check["comparisons"]:
                    print(
                        f"  {comparison['status']} STEM_SOURCE_PAIR"
                        f"[{comparison['index']}]: "
                        f"stem={comparison['stem']} "
                        f"{comparison['stem_width']}x{comparison['stem_height']}; "
                        f"source={comparison['source']} "
                        f"{comparison['source_width']}x"
                        f"{comparison['source_height']}; "
                        f"required>={comparison['required_width']}x"
                        f"{comparison['required_height']}; "
                        f"margin={comparison['width_margin_px']:+d}px width, "
                        f"{comparison['height_margin_px']:+d}px height"
                    )
                for path in check["unmatched_stems"]:
                    print(f"  FAIL UNMATCHED_STEM: {path}")
                for path in check["unmatched_sources"]:
                    print(f"  FAIL UNMATCHED_SOURCE: {path}")
        elif name == "DECLARED_RESOLUTION":
            print(
                f"{status} {name}: final={check['final_width']}x{check['final_height']}; "
                f"source_envelope={check['required_source_width']}x"
                f"{check['required_source_height']}; "
                f"margin={check['width_margin_px']:+d}px width, "
                f"{check['height_margin_px']:+d}px height; "
                f"width_source={check['width_source']}; "
                f"height_source={check['height_source']}"
            )
        elif name == "SAME_FRAMING_DETAIL":
            if status == "SKIPPED":
                print(f"{status} {name}")
                print(f"  WHY: {check['why']}")
                continue
            print(f"MATCHING: {check['matching_method']}")
            print(
                f"{status} {name}: "
                f"ratio={check['ratio']:.6f}; "
                f"required>={check['minimum_ratio']:.6f}; "
                f"margin={check['margin']:+.6f}; "
                f"ancestor={check['ancestor']}; "
                f"head_offset={check['head_offset_s']:.6f}s; "
                f"comparison={check['comparison_width_final']}x"
                f"{check['comparison_height']} final vs "
                f"{check['comparison_width_ancestor']}x"
                f"{check['comparison_height']} ancestor"
            )
            for probe in check["aligned_probes"]:
                print(
                    "  ALIGNED_PROBE: "
                    f"ancestor_t={probe['ancestor_timestamp_s']:.3f}s "
                    f"final_t={probe['final_timestamp_s']:.3f}s "
                    f"ancestor_detail={probe['ancestor_detail']:.6f} "
                    f"final_detail={probe['final_detail']:.6f} "
                    f"ratio={probe['ratio']:.6f}"
                )
        elif name == "UPSCALE_FINGERPRINT_ADVISORY":
            print(
                f"{status} {name}: advisory_only=True; "
                "never_changes_verdict=True"
            )
            for measurement in check["measurements"]:
                if measurement["measurement_status"] == "MEASURED":
                    print(
                        f"  INFO {measurement['role']}: "
                        f"media={measurement['media']} "
                        f"resolution={measurement['width']}x"
                        f"{measurement['height']} "
                        f"before_median={measurement['before_median_detail']:.6f} "
                        f"after_median={measurement['after_median_detail']:.6f} "
                        f"retention={measurement['retention_ratio']:.6f} "
                        f"legacy_threshold={measurement['legacy_threshold']:.6f} "
                        f"legacy_would_fail={measurement['legacy_would_fail']} "
                        f"phase={measurement['selected_sampling_phase']}"
                    )
                else:
                    print(
                        f"  INFO {measurement['role']}: "
                        f"media={measurement['media']} "
                        f"resolution={measurement['width']}x"
                        f"{measurement['height']} "
                        f"retention=UNAVAILABLE "
                        f"WHY={measurement['why_unavailable']}"
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
        "source_720": directory / "source-720.mkv",
        "good": directory / "good-final.mkv",
        "roundtrip": directory / "roundtrip-final.mkv",
        "low": directory / "low-final.mkv",
        "soft_source": directory / "soft-source.mkv",
        "soft_final": directory / "soft-final.mkv",
        "durationless": directory / "durationless-source.webm",
        "corrupt": directory / "corrupt-input.webm",
        "stems_good": directory / "stems-good",
        "stems_bad": directory / "stems-bad",
        "stems_720": directory / "stems-720",
        "stems_soft": directory / "stems-soft",
    }
    for key in ("stems_good", "stems_bad", "stems_720", "stems_soft"):
        paths[key].mkdir()
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
        "roundtrip": (
            "scale=960:540:flags=area,"
            "scale=1920:1080:flags=lanczos,setsar=1"
        ),
        "low": "scale=1280:720:flags=area,setsar=1",
        "source_720": "scale=1280:720:flags=area,setsar=1",
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

    stem_transforms = {
        paths["stems_good"] / "speaker-01.mkv": (
            str(paths["source"]),
            "scale=1920:1080:flags=lanczos,setsar=1",
        ),
        paths["stems_bad"] / "speaker-01.mkv": (
            str(paths["source"]),
            "scale=960:540:flags=area,setsar=1",
        ),
        paths["stems_720"] / "speaker-01.mkv": (
            str(paths["source_720"]),
            "scale=1920:1080:flags=lanczos,setsar=1",
        ),
    }
    for output_path, (input_path, video_filter) in stem_transforms.items():
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            input_path,
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
            str(output_path),
        ]
        run_command(command, label=f"synthesize self-test stem {output_path.name}")

    soft_source = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        str(paths["source"]),
        "-map",
        "0:v:0",
        "-vf",
        "gblur=sigma=18:steps=3,setsar=1",
        "-an",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        "-pix_fmt",
        "yuv420p",
        str(paths["soft_source"]),
    ]
    run_command(soft_source, label="synthesize deliberately soft 1080p source")
    for output_path in (
        paths["soft_final"],
        paths["stems_soft"] / "speaker-01.mkv",
    ):
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(paths["soft_source"]),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        run_command(command, label=f"synthesize correct soft case {output_path.name}")

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
            canvas = (DEFAULT_CANVAS_WIDTH, DEFAULT_CANVAS_HEIGHT)

            case_inputs = {
                "stem-good": {
                    "final": paths["good"],
                    "sources": [paths["source"]],
                    "stems": paths["stems_good"],
                    "ancestor": paths["source"],
                },
                "stem-red-960x540": {
                    "final": paths["good"],
                    "sources": [paths["source"]],
                    "stems": paths["stems_bad"],
                    "ancestor": paths["source"],
                },
                "small-camera-legitimate-upscale": {
                    "final": paths["good"],
                    "sources": [paths["source_720"]],
                    "stems": paths["stems_720"],
                    "ancestor": None,
                },
                "same-framing-roundtrip-red": {
                    "final": paths["roundtrip"],
                    "sources": [paths["source"]],
                    "stems": paths["stems_good"],
                    "ancestor": paths["source"],
                },
                "soft-source": {
                    "final": paths["soft_final"],
                    "sources": [paths["soft_source"]],
                    "stems": paths["stems_soft"],
                    "ancestor": paths["soft_source"],
                },
                "below-source": {
                    "final": paths["low"],
                    "sources": [paths["source"]],
                    "stems": None,
                    "ancestor": None,
                },
            }
            reports = {}
            for case, inputs in case_inputs.items():
                print(f"SELF-TEST CASE {case.upper()}")
                report = evaluate(
                    str(inputs["final"]),
                    [str(path) for path in inputs["sources"]],
                    DEFAULT_MIN_DETAIL_RATIO,
                    DEFAULT_PROBE_COUNT,
                    stems_path=(
                        str(inputs["stems"]) if inputs["stems"] is not None else None
                    ),
                    ancestor_path=(
                        str(inputs["ancestor"])
                        if inputs["ancestor"] is not None
                        else None
                    ),
                    canvas=canvas,
                    head_offset_s=0.0,
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
                stems_path=None,
                ancestor_path=None,
                canvas=canvas,
                head_offset_s=0.0,
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
                    check["status"] != "FAIL"
                    for check in durationless_report["checks"]
                )
            )

            print("SELF-TEST CASE STEM-RED-CLI-EXIT")
            stem_red_result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--final",
                    str(paths["good"]),
                    "--sources",
                    str(paths["source"]),
                    "--stems",
                    str(paths["stems_bad"]),
                    "--probe-count",
                    "1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
            stem_red_exit_ok = stem_red_result.returncode == 1
            print(
                "SELF-TEST STEM-RED CLI EXIT VERIFICATION: "
                f"expected_exit=1 actual_exit={stem_red_result.returncode} "
                f"structural_fail_printed="
                f"{'FAIL STEM_RESOLUTION' in stem_red_result.stdout} "
                f"exit_verified={stem_red_exit_ok}"
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

            stem_good = check_by_name(
                reports["stem-good"],
                "STEM_RESOLUTION",
            )
            same_good = check_by_name(
                reports["stem-good"],
                "SAME_FRAMING_DETAIL",
            )
            good_ok = (
                reports["stem-good"]["exit_code"] == 0
                and stem_good["status"] == "PASS"
                and same_good["status"] == "PASS"
            )

            stem_bad = check_by_name(
                reports["stem-red-960x540"],
                "STEM_RESOLUTION",
            )
            bad_pair = stem_bad["comparisons"][0]
            stem_bad_ok = (
                reports["stem-red-960x540"]["exit_code"] == 1
                and stem_bad["status"] == "FAIL"
                and bad_pair["stem_width"] == 960
                and bad_pair["stem_height"] == 540
                and bad_pair["required_width"] == 1920
                and bad_pair["required_height"] == 1080
            )

            small_stem = check_by_name(
                reports["small-camera-legitimate-upscale"],
                "STEM_RESOLUTION",
            )
            small_pair = small_stem["comparisons"][0]
            small_camera_ok = (
                reports["small-camera-legitimate-upscale"]["exit_code"] == 0
                and small_stem["status"] == "PASS"
                and small_pair["source_width"] == 1280
                and small_pair["source_height"] == 720
                and small_pair["stem_width"] == 1920
                and small_pair["stem_height"] == 1080
            )

            roundtrip_detail = check_by_name(
                reports["same-framing-roundtrip-red"],
                "SAME_FRAMING_DETAIL",
            )
            roundtrip_ok = (
                reports["same-framing-roundtrip-red"]["exit_code"] == 1
                and roundtrip_detail["status"] == "FAIL"
                and roundtrip_detail["ratio"] < DEFAULT_MIN_DETAIL_RATIO
            )

            soft_detail = check_by_name(
                reports["soft-source"],
                "SAME_FRAMING_DETAIL",
            )
            soft_advisory = check_by_name(
                reports["soft-source"],
                "UPSCALE_FINGERPRINT_ADVISORY",
            )
            soft_retentions = [
                measurement["retention_ratio"]
                for measurement in soft_advisory["measurements"]
                if measurement["measurement_status"] == "MEASURED"
            ]
            soft_old_false_red = (
                bool(soft_retentions)
                and max(soft_retentions) > LEGACY_UPSCALE_RETENTION_MAX
            )
            soft_ok = (
                reports["soft-source"]["exit_code"] == 0
                and soft_detail["status"] == "PASS"
                and soft_advisory["status"] == "INFO"
                and soft_old_false_red
            )

            low_declared = check_by_name(
                reports["below-source"],
                "DECLARED_RESOLUTION",
            )
            low_ok = (
                reports["below-source"]["exit_code"] == 1
                and low_declared["status"] == "FAIL"
            )

            print(
                "SELF-TEST ASSERT STEM-GOOD: "
                f"expected_exit=0 actual_exit={reports['stem-good']['exit_code']} "
                f"stem={stem_good['comparisons'][0]['stem_width']}x"
                f"{stem_good['comparisons'][0]['stem_height']} "
                f"required={stem_good['comparisons'][0]['required_width']}x"
                f"{stem_good['comparisons'][0]['required_height']} "
                f"same_framing_ratio={same_good['ratio']:.6f} "
                f"verified={good_ok}"
            )
            print(
                "SELF-TEST ASSERT STEM-RED-960x540: "
                f"expected_exit=1 "
                f"actual_exit={reports['stem-red-960x540']['exit_code']} "
                f"stem={bad_pair['stem_width']}x{bad_pair['stem_height']} "
                f"required={bad_pair['required_width']}x"
                f"{bad_pair['required_height']} "
                f"margin={bad_pair['width_margin_px']:+d}px width, "
                f"{bad_pair['height_margin_px']:+d}px height "
                f"structural_failure_verified={stem_bad_ok}"
            )
            print(
                "SELF-TEST ASSERT SMALL-CAMERA: "
                f"expected_exit=0 "
                f"actual_exit="
                f"{reports['small-camera-legitimate-upscale']['exit_code']} "
                f"source={small_pair['source_width']}x{small_pair['source_height']} "
                f"stem={small_pair['stem_width']}x{small_pair['stem_height']} "
                f"legitimate_upscale_verified={small_camera_ok}"
            )
            print(
                "SELF-TEST ASSERT SAME-FRAMING-ROUNDTRIP: "
                f"expected_exit=1 "
                f"actual_exit="
                f"{reports['same-framing-roundtrip-red']['exit_code']} "
                f"median_ratio={roundtrip_detail['ratio']:.6f} "
                f"required>={DEFAULT_MIN_DETAIL_RATIO:.6f} "
                f"detail_failure_verified={roundtrip_ok}"
            )
            print(
                "SELF-TEST ASSERT SOFT-SOURCE: "
                f"expected_exit=0 actual_exit={reports['soft-source']['exit_code']} "
                f"same_framing_ratio={soft_detail['ratio']:.6f} "
                f"advisory_retentions="
                f"{','.join(f'{value:.6f}' for value in soft_retentions)} "
                f"legacy_threshold={LEGACY_UPSCALE_RETENTION_MAX:.6f} "
                f"old_false_red_observed={soft_old_false_red} "
                f"false_red_removed={soft_ok}"
            )
            print(
                "SELF-TEST ASSERT BELOW-SOURCE: "
                f"expected_exit=1 actual_exit={reports['below-source']['exit_code']} "
                f"width_margin={low_declared['width_margin_px']:+d}px "
                f"height_margin={low_declared['height_margin_px']:+d}px "
                f"declared_failure_detected={low_ok}"
            )
            print(
                "SELF-TEST ASSERT DURATIONLESS-SOURCE: "
                f"expected_exit=0 actual_exit={durationless_report['exit_code']} "
                f"packet_fallback_verified={packet_duration_ok} "
                f"nonfailing_checks={durationless_ok}"
            )
            print(
                "SELF-TEST ASSERT STEM-RED-CLI-EXIT: "
                f"expected_exit=1 actual_exit={stem_red_result.returncode} "
                f"verified={stem_red_exit_ok}"
            )
            print(
                "SELF-TEST ASSERT EXIT-CODE: "
                f"expected_exit=2 actual_exit={corrupt_result.returncode} "
                f"unusable_input_exit_verified={corrupt_exit_ok}"
            )

            if (
                good_ok
                and stem_bad_ok
                and small_camera_ok
                and roundtrip_ok
                and soft_ok
                and low_ok
                and durationless_ok
                and stem_red_exit_ok
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
            "Certify stem dimensions and same-framing detail retention in a "
            "finished video master."
        )
    )
    parser.add_argument("--final", help="final video to certify")
    parser.add_argument(
        "--sources",
        nargs="+",
        help="one or more source camera videos",
    )
    parser.add_argument(
        "--stems",
        metavar="DIR",
        help=(
            "directory of per-speaker video stems; sorted and paired by index "
            "with sorted --sources"
        ),
    )
    parser.add_argument(
        "--ancestor",
        help="direct same-framing ancestor of --final",
    )
    parser.add_argument(
        "--canvas",
        type=parse_canvas,
        default=(DEFAULT_CANVAS_WIDTH, DEFAULT_CANVAS_HEIGHT),
        metavar="WIDTHxHEIGHT",
        help=(
            "delivery canvas used by STEM_RESOLUTION "
            f"(default: {DEFAULT_CANVAS_WIDTH}x{DEFAULT_CANVAS_HEIGHT})"
        ),
    )
    parser.add_argument(
        "--head-offset-s",
        type=float,
        default=0.0,
        help=(
            "seconds added to each ancestor timestamp to align the final "
            "(default: 0.0)"
        ),
    )
    parser.add_argument(
        "--min-detail-ratio",
        type=float,
        default=DEFAULT_MIN_DETAIL_RATIO,
        help=(
            "minimum median paired final/ancestor detail ratio "
            f"(default: {DEFAULT_MIN_DETAIL_RATIO})"
        ),
    )
    parser.add_argument(
        "--probe-count",
        type=int,
        default=DEFAULT_PROBE_COUNT,
        help=(
            "aligned probes in the common middle 80%% "
            f"(default: {DEFAULT_PROBE_COUNT})"
        ),
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
        incompatible = (
            args.final is not None
            or args.sources is not None
            or args.stems is not None
            or args.ancestor is not None
            or args.json is not None
            or args.canvas != (DEFAULT_CANVAS_WIDTH, DEFAULT_CANVAS_HEIGHT)
            or args.head_offset_s != 0.0
            or args.min_detail_ratio != DEFAULT_MIN_DETAIL_RATIO
            or args.probe_count != DEFAULT_PROBE_COUNT
        )
        if incompatible:
            parser.error("--self-test cannot be combined with certification options")
        return
    if args.final is None:
        parser.error("--final is required unless --self-test is used")
    if not args.sources:
        parser.error("--sources requires at least one path unless --self-test is used")
    if not math.isfinite(args.min_detail_ratio) or args.min_detail_ratio <= 0.0:
        parser.error("--min-detail-ratio must be a positive finite number")
    if not math.isfinite(args.head_offset_s) or args.head_offset_s < 0.0:
        parser.error("--head-offset-s must be a finite non-negative number")
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
            stems_path=args.stems,
            ancestor_path=args.ancestor,
            canvas=args.canvas,
            head_offset_s=args.head_offset_s,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        if args.json is not None:
            input_paths = {
                Path(args.final).expanduser().resolve(),
                *(Path(path).expanduser().resolve() for path in args.sources),
            }
            if args.ancestor is not None:
                input_paths.add(Path(args.ancestor).expanduser().resolve())
            if report["stems"] is not None:
                input_paths.update(
                    Path(stem["path"]).resolve() for stem in report["stems"]
                )
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
