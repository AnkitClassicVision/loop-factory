#!/usr/bin/env python3
"""Certify that promo clips visibly frame a person in the intended aspect."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# Half the sampled frames must show a face so room-only crops cannot pass.
DEFAULT_MIN_FACE_COVERAGE = 0.50
# A 0.28 offset leaves the median face center inside the central 44% of the frame.
DEFAULT_MAX_CENTER_OFFSET = 0.28
# A face below 10% of frame height is too small to make the person the subject.
DEFAULT_MIN_FACE_HEIGHT = 0.10
# Nine samples cover the clip without making this release gate unnecessarily slow.
DEFAULT_SAMPLES = 9
# A 1.05 pyramid step improves recall while retaining stable Haar detections.
FACE_SCALE_FACTOR = 1.05
# Four agreeing Haar neighbors reject weak, wall-texture false positives.
FACE_MIN_NEIGHBORS = 4
# Thirty pixels is the smallest useful Haar search window after video decode.
FACE_MIN_PIXELS = 30
# Sixty seconds prevents a corrupt probe or fixture encode from hanging the gate.
SUBPROCESS_TIMEOUT_SECONDS = 60
# Two seconds provides enough frames for all nine hermetic self-test samples.
SELF_TEST_DURATION_SECONDS = 2
# Nine fixture frames per second guarantees distinct positions for nine samples.
SELF_TEST_FPS = 9

VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}
PORTRAIT_WORDS = {
    "9x16",
    "portrait",
    "reel",
    "reels",
    "short",
    "shorts",
    "tiktok",
    "vertical",
}
LANDSCAPE_WORDS = {
    "16x9",
    "horizontal",
    "landscape",
    "widescreen",
}
MANIFEST_LIST_KEYS = ("clips", "videos", "files", "items", "entries")
MANIFEST_PATH_KEYS = (
    "path",
    "file",
    "filename",
    "video",
    "video_path",
    "clip",
    "clip_path",
    "output",
    "output_path",
)
MANIFEST_ORIENTATION_KEYS = (
    "orientation",
    "expected_orientation",
    "aspect",
    "aspect_ratio",
    "format",
    "format_name",
    "target_format",
)
FACE_COVERAGE_WHY = (
    "a promo clip with no face in most frames is framed on the wrong subject"
)


class UnusableInputError(Exception):
    """The requested certification cannot produce trustworthy measurements."""


@dataclass(frozen=True)
class ClipSpec:
    path: Path
    expected_orientation: str | None
    orientation_source: str | None


@dataclass(frozen=True)
class FaceMeasurements:
    width: int
    height: int
    sampled_frames: int
    face_frames: int
    coverage: float
    median_center: float | None
    center_offset: float | None
    median_face_height: float | None


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    measurements: dict[str, Any]
    threshold: str
    why: str


@dataclass(frozen=True)
class ClipResult:
    spec: ClipSpec
    status: str
    checks: tuple[CheckResult, ...]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Certify face coverage, centering, size, and clip aspect."
    )
    parser.add_argument("--clips-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--min-face-coverage",
        type=float,
        default=DEFAULT_MIN_FACE_COVERAGE,
    )
    parser.add_argument(
        "--max-center-offset",
        type=float,
        default=DEFAULT_MAX_CENTER_OFFSET,
    )
    parser.add_argument(
        "--min-face-height",
        type=float,
        default=DEFAULT_MIN_FACE_HEIGHT,
    )
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--json", dest="json_output", type=Path, metavar="OUT")
    parser.add_argument("--self-test", action="store_true")
    return parser


def validate_thresholds(args: argparse.Namespace) -> None:
    values = (
        ("--min-face-coverage", args.min_face_coverage, 0.0, 1.0),
        ("--max-center-offset", args.max_center_offset, 0.0, 0.5),
        ("--min-face-height", args.min_face_height, 0.0, 1.0),
    )
    for name, value, lower, upper in values:
        if not math.isfinite(value) or not lower <= value <= upper:
            raise UnusableInputError(
                f"{name} must be a finite number from {lower:g} to {upper:g}"
            )
    if args.samples < 1:
        raise UnusableInputError("--samples must be at least 1")


def load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        raise UnusableInputError(
            f"cv2 missing; face framing is UNMEASURABLE ({exc})"
        ) from exc
    try:
        import numpy as np
    except (ImportError, OSError) as exc:
        raise UnusableInputError(
            f"numpy missing; face framing is UNMEASURABLE ({exc})"
        ) from exc
    return cv2, np


def load_detector(cv2: Any) -> Any:
    try:
        cascade_root = Path(cv2.data.haarcascades)
        cascade_path = cascade_root / "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(str(cascade_path))
    except Exception as exc:
        raise UnusableInputError(
            f"OpenCV frontal-face cascade is UNMEASURABLE ({exc})"
        ) from exc
    if not cascade_path.is_file() or detector.empty():
        raise UnusableInputError(
            f"OpenCV frontal-face cascade is UNMEASURABLE at {cascade_path}"
        )
    return detector


def normalize_orientation(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        return None
    text = text.replace(":", "x").replace("/", "x")
    if text in PORTRAIT_WORDS:
        return "portrait"
    if text in LANDSCAPE_WORDS:
        return "landscape"
    return None


def orientation_from_name(path: Path) -> tuple[str | None, str | None]:
    stem = path.stem.lower().replace("9:16", "9x16").replace("16:9", "16x9")
    words = set(filter(None, re.split(r"[^a-z0-9]+", stem)))
    portrait = bool(words & PORTRAIT_WORDS)
    landscape = bool(words & LANDSCAPE_WORDS)
    if portrait and landscape:
        raise UnusableInputError(
            f"clip name declares both portrait and landscape: {path.name}"
        )
    if portrait:
        return "portrait", "filename"
    if landscape:
        return "landscape", "filename"
    return None, None


def declared_orientation(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in MANIFEST_ORIENTATION_KEYS:
        if key not in entry:
            continue
        orientation = normalize_orientation(entry[key])
        if orientation is not None:
            return orientation, f"manifest:{key}"
    vertical = entry.get("vertical")
    if isinstance(vertical, bool):
        return (
            ("portrait", "manifest:vertical")
            if vertical
            else ("landscape", "manifest:vertical")
        )
    return None, None


def manifest_entries(
    value: Any,
    inherited_orientation: str | None = None,
    inherited_source: str | None = None,
) -> Iterable[tuple[str, str | None, str | None]]:
    if isinstance(value, str):
        yield value, inherited_orientation, inherited_source
        return
    if isinstance(value, list):
        for item in value:
            yield from manifest_entries(
                item,
                inherited_orientation,
                inherited_source,
            )
        return
    if not isinstance(value, dict):
        raise UnusableInputError(
            "manifest entries must be paths, objects, or lists"
        )

    orientation, source = declared_orientation(value)
    orientation = orientation or inherited_orientation
    source = source or inherited_source

    direct_paths = [
        value[key]
        for key in MANIFEST_PATH_KEYS
        if isinstance(value.get(key), str)
    ]
    if direct_paths:
        for raw_path in direct_paths:
            yield raw_path, orientation, source
        return

    special_paths: list[tuple[str, str, str]] = []
    for key in ("vertical_path", "portrait_path"):
        if isinstance(value.get(key), str):
            special_paths.append((value[key], "portrait", f"manifest:{key}"))
    for key in ("landscape_path", "horizontal_path"):
        if isinstance(value.get(key), str):
            special_paths.append((value[key], "landscape", f"manifest:{key}"))
    if special_paths:
        yield from special_paths
        return

    outputs = value.get("outputs")
    if isinstance(outputs, dict):
        for key, item in outputs.items():
            if not isinstance(item, (str, list, dict)):
                continue
            keyed_orientation = normalize_orientation(key) or orientation
            keyed_source = (
                f"manifest:outputs.{key}"
                if normalize_orientation(key)
                else source
            )
            yield from manifest_entries(
                item,
                keyed_orientation,
                keyed_source,
            )

    found_container = False
    for key in MANIFEST_LIST_KEYS:
        if key in value:
            found_container = True
            yield from manifest_entries(value[key], orientation, source)
    if not direct_paths and not found_container and not outputs:
        known_path_key = any(
            key in value
            for key in (
                *MANIFEST_PATH_KEYS,
                "vertical_path",
                "portrait_path",
                "landscape_path",
                "horizontal_path",
            )
        )
        if known_path_key:
            raise UnusableInputError("manifest path field must contain a string")
        raise UnusableInputError(
            "manifest object contains no supported clip entries"
        )


def resolve_manifest_path(
    raw_path: str,
    clips_dir: Path,
    manifest_path: Path,
) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    candidates = (
        clips_dir / candidate,
        manifest_path.parent / candidate,
    )
    for resolved in candidates:
        if resolved.is_file():
            return resolved.resolve()
    return candidates[0].resolve()


def discover_clips(clips_dir: Path, manifest_path: Path | None) -> list[ClipSpec]:
    if not clips_dir.is_dir():
        raise UnusableInputError(f"clips directory is not readable: {clips_dir}")
    specs: list[ClipSpec] = []
    if manifest_path is None:
        paths = sorted(
            path.resolve()
            for path in clips_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        for path in paths:
            orientation, source = orientation_from_name(path)
            specs.append(ClipSpec(path, orientation, source))
    else:
        if not manifest_path.is_file():
            raise UnusableInputError(
                f"manifest is not a readable file: {manifest_path}"
            )
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise UnusableInputError(
                f"cannot read manifest {manifest_path}: {exc}"
            ) from exc
        try:
            data = json.loads(manifest_text)
        except json.JSONDecodeError:
            lines = [
                line.strip()
                for line in manifest_text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if not lines:
                raise UnusableInputError(
                    f"manifest is neither JSON nor a non-empty path list: "
                    f"{manifest_path}"
                )
            data = lines
        for raw_path, orientation, source in manifest_entries(data):
            path = resolve_manifest_path(raw_path, clips_dir, manifest_path)
            if not path.is_file():
                raise UnusableInputError(
                    f"manifest clip is not a readable file: {path}"
                )
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise UnusableInputError(
                    f"manifest entry is not a supported video: {path}"
                )
            if orientation is None:
                orientation, source = orientation_from_name(path)
            specs.append(ClipSpec(path, orientation, source))

    deduplicated: dict[Path, ClipSpec] = {}
    for spec in specs:
        previous = deduplicated.get(spec.path)
        if previous is None:
            deduplicated[spec.path] = spec
            continue
        if (
            previous.expected_orientation
            and spec.expected_orientation
            and previous.expected_orientation != spec.expected_orientation
        ):
            raise UnusableInputError(
                f"conflicting orientations declared for {spec.path}"
            )
        if previous.expected_orientation is None and spec.expected_orientation:
            deduplicated[spec.path] = spec
    if not deduplicated:
        raise UnusableInputError(f"no clips found in {clips_dir}")
    return list(deduplicated.values())


def probe_video(path: Path) -> tuple[int, int, float | None]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise UnusableInputError("ffprobe is required but was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise UnusableInputError(f"ffprobe timed out for {path}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        reason = detail[-1] if detail else "unknown ffprobe error"
        raise UnusableInputError(f"unreadable video {path}: {reason}")
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        stream = streams[0]
        width = int(stream["width"])
        height = int(stream["height"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UnusableInputError(
            f"unreadable video {path}: no usable video stream"
        ) from exc
    if width < 1 or height < 1:
        raise UnusableInputError(f"unreadable video {path}: invalid dimensions")
    duration_value = stream.get("duration")
    if duration_value in (None, "N/A"):
        duration_value = (payload.get("format") or {}).get("duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError):
        duration = None
    if duration is not None and (
        not math.isfinite(duration) or duration <= 0.0
    ):
        duration = None
    return width, height, duration


def evenly_spaced_positions(
    frame_count: int,
    duration: float | None,
    samples: int,
    np: Any,
) -> tuple[str, list[float]]:
    if frame_count > 0:
        distinct_samples = min(samples, frame_count)
        return (
            "frames",
            [
                float(value)
                for value in np.linspace(
                    0,
                    frame_count - 1,
                    distinct_samples,
                )
            ],
        )
    if duration is not None:
        margin = duration / (2.0 * samples)
        return (
            "milliseconds",
            [
                float(value * 1000.0)
                for value in np.linspace(margin, duration - margin, samples)
            ],
        )
    raise UnusableInputError(
        "video has neither a measurable frame count nor duration"
    )


def detect_largest_face(detector: Any, frame: Any, cv2: Any) -> Any | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    minimum = max(FACE_MIN_PIXELS, int(min(frame.shape[:2]) * 0.03))
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=FACE_SCALE_FACTOR,
        minNeighbors=FACE_MIN_NEIGHBORS,
        minSize=(minimum, minimum),
    )
    if len(faces) == 0:
        return None
    return max(
        faces,
        key=lambda face: int(face[2]) * int(face[3]),
    )


def measure_clip(
    path: Path,
    samples: int,
    detector: Any,
    cv2: Any,
    np: Any,
) -> FaceMeasurements:
    probed_width, probed_height, duration = probe_video(path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise UnusableInputError(f"unreadable video {path}: OpenCV cannot open it")
    try:
        frame_count_raw = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        frame_count = (
            int(round(frame_count_raw))
            if math.isfinite(frame_count_raw) and frame_count_raw >= 1
            else 0
        )
        position_mode, positions = evenly_spaced_positions(
            frame_count,
            duration,
            samples,
            np,
        )
        centers: list[float] = []
        heights: list[float] = []
        actual_width: int | None = None
        actual_height: int | None = None
        sampled_frames = len(positions)
        for index, position in enumerate(positions):
            if position_mode == "frames":
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(round(position)))
            else:
                capture.set(cv2.CAP_PROP_POS_MSEC, position)
            ok, frame = capture.read()
            if not ok or frame is None or frame.size == 0:
                raise UnusableInputError(
                    f"unreadable video {path}: cannot decode sample "
                    f"{index + 1}/{sampled_frames}"
                )
            frame_height, frame_width = frame.shape[:2]
            if frame_width < 1 or frame_height < 1:
                raise UnusableInputError(
                    f"unreadable video {path}: decoded an empty frame"
                )
            if actual_width is None:
                actual_width = frame_width
                actual_height = frame_height
            elif (actual_width, actual_height) != (frame_width, frame_height):
                raise UnusableInputError(
                    f"unreadable video {path}: dimensions change between samples"
                )
            face = detect_largest_face(detector, frame, cv2)
            if face is None:
                continue
            x, _y, face_width, face_height = map(float, face)
            centers.append((x + face_width / 2.0) / frame_width)
            heights.append(face_height / frame_height)
    finally:
        capture.release()

    if actual_width is None or actual_height is None:
        raise UnusableInputError(f"unreadable video {path}: no decoded frames")
    probed_dimensions = (probed_width, probed_height)
    decoded_dimensions = (actual_width, actual_height)
    if (
        decoded_dimensions != probed_dimensions
        and decoded_dimensions != tuple(reversed(probed_dimensions))
    ):
        raise UnusableInputError(
            f"unreadable video {path}: ffprobe reports "
            f"{probed_width}x{probed_height}, decoded "
            f"{actual_width}x{actual_height}"
        )
    face_frames = len(centers)
    median_center = float(np.median(centers)) if centers else None
    median_height = float(np.median(heights)) if heights else None
    offset = (
        abs(median_center - 0.5)
        if median_center is not None
        else None
    )
    return FaceMeasurements(
        width=actual_width,
        height=actual_height,
        sampled_frames=sampled_frames,
        face_frames=face_frames,
        coverage=face_frames / sampled_frames,
        median_center=median_center,
        center_offset=offset,
        median_face_height=median_height,
    )


def observed_orientation(width: int, height: int) -> str:
    if height > width:
        return "portrait"
    if width > height:
        return "landscape"
    return "square"


def evaluate_checks(
    spec: ClipSpec,
    measurement: FaceMeasurements,
    min_face_coverage: float,
    max_center_offset: float,
    min_face_height: float,
) -> tuple[CheckResult, ...]:
    coverage_status = (
        "PASS"
        if measurement.coverage >= min_face_coverage
        else "FAIL"
    )
    coverage = CheckResult(
        name="FACE_COVERAGE",
        status=coverage_status,
        measurements={
            "face_frames": measurement.face_frames,
            "sampled_frames": measurement.sampled_frames,
            "coverage": measurement.coverage,
        },
        threshold=f">={min_face_coverage:.3f}",
        why=FACE_COVERAGE_WHY,
    )

    if measurement.median_center is None:
        centering_status = "N/A"
        centering_why = "no detected face exists to center"
    else:
        centering_status = (
            "PASS"
            if measurement.center_offset <= max_center_offset
            else "FAIL"
        )
        centering_why = (
            "a subject jammed against an edge is not safely framed"
        )
    centering = CheckResult(
        name="FACE_CENTERING",
        status=centering_status,
        measurements={
            "median_position": measurement.median_center,
            "offset": measurement.center_offset,
        },
        threshold=f"<={max_center_offset:.3f}",
        why=centering_why,
    )

    if measurement.median_face_height is None:
        size_status = "N/A"
        size_why = "no detected face exists to size"
    else:
        size_status = (
            "PASS"
            if measurement.median_face_height >= min_face_height
            else "FAIL"
        )
        size_why = "a tiny face means the crop framed the room, not the person"
    size = CheckResult(
        name="FACE_SIZE",
        status=size_status,
        measurements={
            "median_face_height": measurement.median_face_height,
        },
        threshold=f">={min_face_height:.3f}",
        why=size_why,
    )

    actual_orientation = observed_orientation(
        measurement.width,
        measurement.height,
    )
    aspect_status = (
        "FAIL"
        if (
            spec.expected_orientation is not None
            and actual_orientation != spec.expected_orientation
        )
        else "PASS"
    )
    if spec.expected_orientation is None:
        aspect_why = "no portrait or landscape target is named or declared"
    else:
        aspect_why = (
            f"a {spec.expected_orientation} target must have "
            f"{'height greater than width' if spec.expected_orientation == 'portrait' else 'width greater than height'}"
        )
    aspect = CheckResult(
        name="ASPECT",
        status=aspect_status,
        measurements={
            "width": measurement.width,
            "height": measurement.height,
            "observed": actual_orientation,
            "expected": spec.expected_orientation,
            "declaration_source": spec.orientation_source,
        },
        threshold=(
            spec.expected_orientation
            if spec.expected_orientation is not None
            else "report-only"
        ),
        why=aspect_why,
    )
    return coverage, centering, size, aspect


def evaluate_clip(
    spec: ClipSpec,
    args: argparse.Namespace,
    detector: Any,
    cv2: Any,
    np: Any,
) -> ClipResult:
    measurement = measure_clip(
        spec.path,
        args.samples,
        detector,
        cv2,
        np,
    )
    checks = evaluate_checks(
        spec,
        measurement,
        args.min_face_coverage,
        args.max_center_offset,
        args.min_face_height,
    )
    status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    return ClipResult(spec=spec, status=status, checks=checks)


def number(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def print_clip_result(result: ClipResult) -> None:
    clip = str(result.spec.path)
    for check in result.checks:
        measured = " ".join(
            f"{key}={number(value)}"
            for key, value in check.measurements.items()
        )
        print(
            f"{check.status} clip={clip} check={check.name} "
            f"{measured} threshold={check.threshold} WHY={check.why}"
        )


def check_to_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "name": check.name,
        "status": check.status,
        "measurements": check.measurements,
        "threshold": check.threshold,
        "why": check.why,
    }


def result_to_dict(result: ClipResult) -> dict[str, Any]:
    return {
        "clip": str(result.spec.path),
        "expected_orientation": result.spec.expected_orientation,
        "orientation_source": result.spec.orientation_source,
        "status": result.status,
        "checks": [check_to_dict(check) for check in result.checks],
    }


def write_json_report(
    output: Path,
    status: str,
    results: list[ClipResult],
    unusable: list[dict[str, str]],
) -> None:
    payload = {
        "status": status,
        "summary": {
            "clips": len(results) + len(unusable),
            "passed": sum(result.status == "PASS" for result in results),
            "failed": sum(result.status == "FAIL" for result in results),
            "unusable": len(unusable),
        },
        "clips": [result_to_dict(result) for result in results],
        "unusable_inputs": unusable,
    }
    try:
        output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise UnusableInputError(
            f"cannot write JSON report {output}: {exc}"
        ) from exc


def run_certification(
    specs: list[ClipSpec],
    args: argparse.Namespace,
    detector: Any,
    cv2: Any,
    np: Any,
) -> tuple[int, list[ClipResult], list[dict[str, str]]]:
    results: list[ClipResult] = []
    unusable: list[dict[str, str]] = []
    for spec in specs:
        try:
            result = evaluate_clip(spec, args, detector, cv2, np)
        except UnusableInputError as exc:
            message = str(exc)
            unusable.append({"clip": str(spec.path), "reason": message})
            print(f"UNMEASURABLE clip={spec.path} reason={message}")
            continue
        results.append(result)
        print_clip_result(result)
    failed = sum(result.status == "FAIL" for result in results)
    passed = sum(result.status == "PASS" for result in results)
    if unusable:
        status = "UNMEASURABLE"
        exit_code = 2
    elif failed:
        status = "FAIL"
        exit_code = 1
    else:
        status = "PASS"
        exit_code = 0
    print(
        f"SUMMARY status={status} clips={len(specs)} "
        f"passed={passed} failed={failed} unusable={len(unusable)}"
    )
    return exit_code, results, unusable


def textured_wall(width: int, height: int, cv2: Any, np: Any) -> Any:
    y, x = np.indices((height, width))
    texture = (
        202.0
        + 8.0 * np.sin(x / 43.0)
        + 5.0 * np.cos(y / 57.0)
        + 3.0 * np.sin((x + y) / 19.0)
    )
    wall = np.empty((height, width, 3), dtype=np.uint8)
    wall[:, :, 0] = np.clip(texture - 18.0, 0, 255).astype(np.uint8)
    wall[:, :, 1] = np.clip(texture - 7.0, 0, 255).astype(np.uint8)
    wall[:, :, 2] = np.clip(texture + 7.0, 0, 255).astype(np.uint8)
    for row, wall_y in enumerate(range(0, height, 160)):
        cv2.line(wall, (0, wall_y), (width, wall_y), (165, 176, 186), 3)
        offset = 90 if row % 2 else 0
        for wall_x in range(offset, width, 220):
            cv2.line(
                wall,
                (wall_x, wall_y),
                (wall_x, min(height - 1, wall_y + 160)),
                (174, 184, 193),
                2,
            )
    return wall


def draw_face_candidate(variant: int, cv2: Any, np: Any) -> Any:
    image = np.full((640, 640, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (250, 500), (390, 639), (155, 175, 190), -1)
    cv2.ellipse(
        image,
        (320, 350),
        (205, 250),
        0,
        0,
        360,
        (175 + variant * 2, 195 + variant, 210),
        -1,
    )
    cv2.ellipse(image, (320, 350), (205, 250), 0, 0, 360, (45, 45, 45), 8)
    cv2.ellipse(image, (320, 225), (190, 125), 0, 180, 360, (35, 35, 35), -1)
    cv2.ellipse(image, (160, 335), (26, 70), 0, 0, 360, (165, 185, 200), -1)
    cv2.ellipse(image, (480, 335), (26, 70), 0, 0, 360, (165, 185, 200), -1)
    for eye_x in (245, 395):
        cv2.ellipse(
            image,
            (eye_x, 325),
            (58, 38),
            0,
            0,
            360,
            (135, 145, 155),
            -1,
        )
        cv2.ellipse(
            image,
            (eye_x, 327),
            (35, 18),
            0,
            0,
            360,
            (245, 245, 245),
            -1,
        )
        cv2.circle(image, (eye_x, 328), 12 + variant, (25, 25, 25), -1)
        cv2.circle(image, (eye_x - 3, 324), 3, (245, 245, 245), -1)
        cv2.line(
            image,
            (eye_x - 45, 285),
            (eye_x + 45, 280),
            (45, 45, 45),
            8,
        )
    nose = np.array([[320, 340], [282, 430], [350, 430]], dtype=np.int32)
    cv2.fillConvexPoly(image, nose, (150, 165, 180))
    cv2.line(image, (320, 345), (303, 418), (70, 70, 70), 6)
    cv2.ellipse(image, (320, 426), (38, 16), 0, 0, 180, (60, 60, 60), 5)
    cv2.line(image, (320, 438), (320, 457), (80, 80, 80), 4)
    cv2.ellipse(image, (320, 485), (72, 30), 0, 0, 180, (50, 50, 50), 8)
    cv2.ellipse(
        image,
        (320, 482),
        (50, 12),
        0,
        0,
        180,
        (245, 245, 245),
        -1,
    )
    return image


def verified_synthetic_face(
    detector: Any,
    cv2: Any,
    np: Any,
) -> Any:
    for attempt in range(8):
        face = draw_face_candidate(attempt, cv2, np)
        detection = detect_largest_face(detector, face, cv2)
        count = 0 if detection is None else 1
        largest = (
            "none"
            if detection is None
            else ",".join(str(int(value)) for value in detection)
        )
        print(
            f"SELF_TEST fixture=synthetic_face attempt={attempt + 1} "
            f"detections={count} largest={largest} "
            f"status={'DETECTED' if detection is not None else 'NOT_DETECTED'}"
        )
        if detection is not None:
            return face
    raise UnusableInputError(
        "procedural green fixture was not detected by the Haar cascade"
    )


def paste_image(destination: Any, source: Any, left: int, top: int) -> None:
    destination_height, destination_width = destination.shape[:2]
    source_height, source_width = source.shape[:2]
    destination_left = max(0, left)
    destination_top = max(0, top)
    destination_right = min(destination_width, left + source_width)
    destination_bottom = min(destination_height, top + source_height)
    if (
        destination_left >= destination_right
        or destination_top >= destination_bottom
    ):
        return
    source_left = destination_left - left
    source_top = destination_top - top
    source_right = source_left + destination_right - destination_left
    source_bottom = source_top + destination_bottom - destination_top
    destination[
        destination_top:destination_bottom,
        destination_left:destination_right,
    ] = source[source_top:source_bottom, source_left:source_right]


def encode_fixture(frame: Any, output: Path, cv2: Any) -> None:
    image_path = output.with_suffix(".png")
    if not cv2.imwrite(str(image_path), frame):
        raise UnusableInputError(f"cannot write self-test image {image_path}")
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(SELF_TEST_FPS),
        "-i",
        str(image_path),
        "-t",
        str(SELF_TEST_DURATION_SECONDS),
        "-c:v",
        "mpeg4",
        "-q:v",
        "2",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise UnusableInputError("ffmpeg is required but was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise UnusableInputError(
            f"ffmpeg timed out creating {output.name}"
        ) from exc
    if completed.returncode != 0:
        raise UnusableInputError(
            f"ffmpeg could not create {output.name}: "
            f"{completed.stderr.strip()}"
        )


def check_by_name(result: ClipResult, name: str) -> CheckResult:
    return next(check for check in result.checks if check.name == name)


def run_self_test(cv2: Any, np: Any) -> int:
    detector = load_detector(cv2)
    face = verified_synthetic_face(detector, cv2, np)
    with tempfile.TemporaryDirectory(prefix="clip-framing-self-test-") as raw:
        root = Path(raw)
        portrait_wall = textured_wall(1080, 1920, cv2, np)
        good_frame = portrait_wall.copy()
        paste_image(good_frame, face, 220, 500)
        no_face_frame = portrait_wall.copy()
        edge_frame = portrait_wall.copy()
        edge_face = cv2.resize(
            face,
            None,
            fx=0.90,
            fy=0.90,
            interpolation=cv2.INTER_AREA,
        )
        edge_face = edge_face[:, 60:]
        paste_image(edge_frame, edge_face, 0, 500)
        landscape_frame = textured_wall(1920, 1080, cv2, np)
        paste_image(landscape_frame, face, 640, 100)

        fixture_frames = {
            "good.mp4": good_frame,
            "bad_no_face.mp4": no_face_frame,
            "bad_edge.mp4": edge_frame,
            "bad_aspect.mp4": landscape_frame,
        }
        for filename, frame in fixture_frames.items():
            encode_fixture(frame, root / filename, cv2)

        specs = [
            ClipSpec(root / filename, "portrait", "self-test declaration")
            for filename in fixture_frames
        ]
        args = argparse.Namespace(
            samples=DEFAULT_SAMPLES,
            min_face_coverage=DEFAULT_MIN_FACE_COVERAGE,
            max_center_offset=DEFAULT_MAX_CENTER_OFFSET,
            min_face_height=DEFAULT_MIN_FACE_HEIGHT,
        )
        exit_code, results, unusable = run_certification(
            specs,
            args,
            detector,
            cv2,
            np,
        )
        by_name = {result.spec.path.name: result for result in results}
        assertions = {
            "green_is_green": (
                by_name.get("good.mp4") is not None
                and by_name["good.mp4"].status == "PASS"
            ),
            "no_face_fails_coverage": (
                by_name.get("bad_no_face.mp4") is not None
                and check_by_name(
                    by_name["bad_no_face.mp4"],
                    "FACE_COVERAGE",
                ).status
                == "FAIL"
            ),
            "edge_fails_centering": (
                by_name.get("bad_edge.mp4") is not None
                and check_by_name(
                    by_name["bad_edge.mp4"],
                    "FACE_CENTERING",
                ).status
                == "FAIL"
            ),
            "wrong_shape_fails_aspect": (
                by_name.get("bad_aspect.mp4") is not None
                and check_by_name(
                    by_name["bad_aspect.mp4"],
                    "ASPECT",
                ).status
                == "FAIL"
            ),
        }
        for name, passed in assertions.items():
            print(
                f"SELF_TEST assertion={name} "
                f"status={'PASS' if passed else 'FAIL'}"
            )
        passed = (
            not unusable
            and len(results) == 4
            and all(assertions.values())
            and exit_code == 1
        )
        print(
            f"SELF_TEST SUMMARY status={'PASS' if passed else 'FAIL'} "
            f"assertions={sum(assertions.values())}/{len(assertions)}"
        )
        return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_thresholds(args)
        if args.self_test:
            forbidden = (
                args.clips_dir is not None
                or args.manifest is not None
                or args.json_output is not None
            )
            if forbidden:
                raise UnusableInputError(
                    "--self-test cannot be combined with input or JSON options"
                )
            cv2, np = load_dependencies()
            return run_self_test(cv2, np)
        if args.clips_dir is None:
            parser.error("--clips-dir is required unless --self-test is used")
        cv2, np = load_dependencies()
        detector = load_detector(cv2)
        specs = discover_clips(args.clips_dir, args.manifest)
        exit_code, results, unusable = run_certification(
            specs,
            args,
            detector,
            cv2,
            np,
        )
        if args.json_output is not None:
            report_status = (
                "UNMEASURABLE"
                if exit_code == 2
                else "FAIL"
                if exit_code == 1
                else "PASS"
            )
            write_json_report(
                args.json_output,
                report_status,
                results,
                unusable,
            )
        return exit_code
    except UnusableInputError as exc:
        print(f"UNMEASURABLE reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
