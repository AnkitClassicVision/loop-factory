#!/usr/bin/env python3
"""Verify that existing podcast artifacts do not predate their dependencies.

This is deliberately a freshness receipt, not a completeness check. Missing
downstream artifacts are reported as ABSENT. A missing raw source set or a DAG
with no measurable edges is unusable input, so neither can produce a pass.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


MTIME_TOLERANCE_S = 0.0  # Freshness is exact: descendants must be at least as new as inputs.


PathRecord = Tuple[Path, float]
Edge = Tuple[PathRecord, PathRecord]


def format_seconds(value: float) -> str:
    """Render every timestamp or duration with exactly three decimals."""
    return f"{value:.3f}"


def display_path(path: Path, episode: Path) -> str:
    """Prefer stable episode-relative evidence paths."""
    try:
        return path.relative_to(episode).as_posix()
    except ValueError:
        return str(path)


def collect_files(
    episode: Path,
    pattern: str,
    label: str,
    absences: List[Dict[str, str]],
    errors: List[str],
) -> List[PathRecord]:
    """Collect regular files for one DAG node pattern and record no-evidence."""
    try:
        candidates = sorted(episode.glob(pattern), key=lambda item: item.as_posix())
    except OSError as exc:
        errors.append(f"cannot enumerate {label}: {exc}")
        return []

    records: List[PathRecord] = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            records.append((path, path.stat().st_mtime))
        except OSError as exc:
            errors.append(f"cannot read mtime for {display_path(path, episode)}: {exc}")

    if not records:
        absences.append({"artifact": label, "pattern": pattern})
        print(f"ABSENT: {label} ({pattern})")
    return records


def cross_edges(
    ancestors: Sequence[PathRecord],
    descendants: Sequence[PathRecord],
) -> Iterable[Edge]:
    """Yield every concrete dependency edge between two populated DAG nodes."""
    for ancestor in ancestors:
        for descendant in descendants:
            yield ancestor, descendant


def write_json_receipt(path: str, receipt: Dict[str, object]) -> None:
    """Write only the explicitly requested JSON receipt."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove that every existing podcast artifact is at least as new "
            "as each existing input declared by the episode dependency DAG."
        )
    )
    parser.add_argument("--episode", required=True, help="episode directory")
    parser.add_argument("--json", dest="json_out", help="optional JSON receipt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    episode = Path(args.episode).expanduser()

    if not episode.exists():
        print(f"UNUSABLE: episode directory does not exist: {episode}")
        return 2
    if not episode.is_dir():
        print(f"UNUSABLE: --episode is not a directory: {episode}")
        return 2

    try:
        episode = episode.resolve(strict=True)
    except OSError as exc:
        print(f"UNUSABLE: cannot resolve episode directory {episode}: {exc}")
        return 2

    absences: List[Dict[str, str]] = []
    errors: List[str] = []

    raw = collect_files(
        episode, "raw/*.webm", "raw webm sources", absences, errors
    )
    timeline = collect_files(
        episode, "processed/timeline.json", "processed timeline", absences, errors
    )
    stems = collect_files(
        episode, "processed/stems/*", "processed stems", absences, errors
    )
    composites = collect_files(
        episode, "processed/composite*.mp4", "processed composites", absences, errors
    )
    angle = collect_files(
        episode,
        "processed/angle_switched.mp4",
        "angle-switched render",
        absences,
        errors,
    )
    boundaries = collect_files(
        episode,
        "processed/angle_switched.mp4.boundaries.json",
        "angle-switch boundaries",
        absences,
        errors,
    )
    final_mp4 = collect_files(
        episode, "final/episode.mp4", "final episode mp4", absences, errors
    )
    final_mp3 = collect_files(
        episode, "final/episode.mp3", "final episode mp3", absences, errors
    )
    clips = collect_files(episode, "clips/*", "clip artifacts", absences, errors)
    transcript = collect_files(
        episode,
        "processed/transcript.json",
        "processed transcript",
        absences,
        errors,
    )
    content = collect_files(
        episode,
        "content/episode_content.json",
        "episode content",
        absences,
        errors,
    )

    finals = final_mp4 + final_mp3
    switched_outputs = angle + boundaries

    edges: List[Edge] = []
    edges.extend(cross_edges(raw, timeline))
    edges.extend(cross_edges(raw, stems))
    edges.extend(cross_edges(timeline, composites))
    edges.extend(cross_edges(stems, composites))
    edges.extend(cross_edges(composites, switched_outputs))
    edges.extend(cross_edges(switched_outputs, finals))
    edges.extend(cross_edges(finals, clips))
    edges.extend(cross_edges(stems, transcript))
    edges.extend(cross_edges(transcript, content))

    checks: List[Dict[str, object]] = []
    stale_count = 0
    for ancestor, descendant in edges:
        ancestor_path, ancestor_mtime = ancestor
        descendant_path, descendant_mtime = descendant
        ancestor_name = display_path(ancestor_path, episode)
        descendant_name = display_path(descendant_path, episode)
        delta_s = descendant_mtime - ancestor_mtime
        stale = delta_s < -MTIME_TOLERANCE_S

        check: Dict[str, object] = {
            "status": "stale" if stale else "fresh",
            "descendant": descendant_name,
            "descendant_mtime_s": format_seconds(descendant_mtime),
            "input": ancestor_name,
            "input_mtime_s": format_seconds(ancestor_mtime),
            "delta_s": format_seconds(delta_s),
        }

        if stale:
            stale_count += 1
            why = (
                f"descendant is {format_seconds(abs(delta_s))} seconds older "
                "than this input"
            )
            check["why"] = why
            print(
                f"STALE: {descendant_name} ({format_seconds(descendant_mtime)}) "
                f"predates its input {ancestor_name} "
                f"({format_seconds(ancestor_mtime)}); WHY: {why}"
            )
        else:
            print(
                f"FRESH: {descendant_name} ({format_seconds(descendant_mtime)}) "
                f"is not older than its input {ancestor_name} "
                f"({format_seconds(ancestor_mtime)})"
            )
        checks.append(check)

    if errors:
        for error in errors:
            print(f"UNUSABLE: {error}")

    raw_missing = not raw
    no_evidence = not checks
    if stale_count:
        exit_code = 1
        status = "fail"
        summary = (
            f"FAIL: {stale_count} stale edge(s) among {len(checks)} measured; "
            f"{len(absences)} artifact group(s) absent."
        )
    elif errors or raw_missing or no_evidence:
        exit_code = 2
        status = "unusable"
        reasons = []
        if raw_missing:
            reasons.append("no raw/*.webm source files")
        if no_evidence:
            reasons.append("no dependency edges could be measured")
        if errors:
            reasons.append(f"{len(errors)} filesystem read error(s)")
        summary = (
            f"UNUSABLE: {'; '.join(reasons)}. "
            "No evidence is not a freshness pass."
        )
    else:
        exit_code = 0
        status = "pass"
        summary = (
            f"PASS: {len(checks)} dependency edge(s) measured; none stale; "
            f"{len(absences)} optional artifact group(s) absent."
        )

    print(summary)

    receipt: Dict[str, object] = {
        "episode": str(episode),
        "status": status,
        "exit_code": exit_code,
        "checks": checks,
        "absent": absences,
        "errors": errors,
        "summary": {
            "measured_edges": len(checks),
            "stale_edges": stale_count,
            "absent_groups": len(absences),
        },
    }
    if args.json_out:
        try:
            write_json_receipt(args.json_out, receipt)
        except (OSError, TypeError, ValueError) as exc:
            print(f"UNUSABLE: cannot write JSON receipt {args.json_out}: {exc}")
            return 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
