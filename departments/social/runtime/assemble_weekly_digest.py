"""SG-SENSE N4 — assemble the weekly digest owed to the owner (C16, C18).

Lists every PLATFORM-VERIFIED post with its link, an engagement summary, and
the quarantine count. Sanitized: only counts/IDs ever render, never DM or
comment bodies — this node reads a fixed whitelist of fields out of each
input row and never dumps a row verbatim, so an accidentally-included body
field can never leak into the digest. Metrics whose charter target is still
TBD_MEASURE_IN_SHADOW render as a shadow baseline, not a pass/fail. When the
durable memory backend is not yet wired (charter memory.backends == []), the
digest surfaces that seam explicitly instead of silently dropping it (C18).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.charter_loader import CharterError, load_charter  # noqa: E402

logger = logging.getLogger("assemble_weekly_digest")

DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "social" / "state"
DEFAULT_CHARTER_PATH = REPO_ROOT / "departments" / "social" / "charter.yaml"

METRIC_LABELS = {
    "budget_near": "Budget near ceiling",
    "cap_near": "Weekly posting cap near",
    "delivery_verified_gap": "Delivery verification gap",
    "discovery_calls_booked": "Discovery calls booked",
    "discovery_calls_booked_by_source": "Discovery calls booked by source",
    "dollars_used": "Dollars used",
    "engagement_rate": "Engagement rate",
    "engagement_rate_per_surface": "Engagement rate per surface",
    "faux_work_signal": "Faux-work signal",
    "gaming_signal": "Gaming signal",
    "icaregrow_webinar_registrations": "icaregrow webinar registrations",
    "impressions": "Impressions",
    "likes": "Likes",
    "model_calls_used": "Model calls used",
    "platform_verified_delivery_pct": "Platform-verified delivery percent",
    "podcast_dept_post_engagement": "Podcast department post engagement",
    "posts_per_week_all_surfaces": "Posts per week across all surfaces",
    "quarantine_backlog_items": "Quarantine backlog items",
    "worker_minutes_used": "Worker minutes used",
}


class DigestInputError(RuntimeError):
    """Raised when a required digest input is missing, unreadable, or malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_list(path: str | Path, label: str) -> list[Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise DigestInputError(f"{label} not found: {file_path}")
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DigestInputError(f"{label} unreadable: {file_path}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DigestInputError(f"{label} is malformed JSON: {file_path}: {exc}") from exc
    if not isinstance(value, list):
        raise DigestInputError(f"{label} must be a JSON list: {file_path}")
    return value


def load_observations(path: str | Path) -> list[dict[str, Any]]:
    obs_path = Path(path)
    if not obs_path.exists():
        raise DigestInputError(f"observations not found: {obs_path}")
    try:
        lines = obs_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DigestInputError(f"observations unreadable: {obs_path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DigestInputError(f"observations malformed at {obs_path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise DigestInputError(f"observations malformed at {obs_path}:{line_number}: row is not an object")
        rows.append(value)
    return rows


def quarantine_count(state_dir: str | Path) -> int:
    qdir = Path(state_dir) / "quarantine"
    if not qdir.is_dir():
        return 0
    return len(list(qdir.glob("*.json")))


def post_lookup(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Only lift a fixed, known-safe set of fields per post — never the raw row."""
    lookup: dict[str, dict[str, Any]] = {}
    for row in observations:
        if row.get("source") != "zernio":
            continue
        post_ref = row.get("post_ref")
        if not post_ref:
            continue
        entry = lookup.setdefault(post_ref, {"surface": None, "url": None, "platform_post_id": None, "metrics": {}})
        if row.get("surface"):
            entry["surface"] = row["surface"]
        if row.get("url"):
            entry["url"] = row["url"]
        if row.get("platform_post_id"):
            entry["platform_post_id"] = row["platform_post_id"]
        metric = row.get("metric")
        value = row.get("value")
        if (
            metric in METRIC_LABELS
            and metric != "platform_verified"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            entry["metrics"][metric] = value
    return lookup


def dept_wide_metric_rows(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest row per metric among rows that are not tied to a single post."""
    values: dict[str, dict[str, Any]] = {}
    for row in observations:
        if row.get("post_ref"):
            continue
        metric = row.get("metric")
        if metric not in METRIC_LABELS:
            continue
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        values[metric] = row
    return values


def unrecognized_metric_count(observations: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in observations
        if row.get("metric") not in METRIC_LABELS
        and row.get("metric") != "platform_verified"
    )


def outcome_targets(charter: dict[str, Any] | None) -> dict[str, Any]:
    if not charter:
        return {}
    setpoints = charter.get("setpoints") or {}
    targets: dict[str, Any] = {}
    operational = setpoints.get("operational") or {}
    if operational.get("metric"):
        targets[operational["metric"]] = operational.get("target")
    outcome = setpoints.get("outcome") or {}
    if outcome.get("metric"):
        targets[outcome["metric"]] = outcome.get("target")
    for row in setpoints.get("outcome_additional") or []:
        if isinstance(row, dict) and row.get("metric"):
            targets[row["metric"]] = row.get("target")
    return targets


def tbd_metrics(charter: dict[str, Any] | None) -> set[str]:
    targets = outcome_targets(charter)
    return {metric for metric, target in targets.items() if target == "TBD_MEASURE_IN_SHADOW"}


def render_markdown(
    *,
    now: str,
    verified_posts: list[dict[str, Any]],
    lookup: dict[str, dict[str, Any]],
    quarantine_total: int,
    dept_metrics: dict[str, dict[str, Any]],
    tbd: set[str],
    targets: dict[str, Any],
    memory_backend_state: str,
    unrecognized_rows: int,
) -> str:
    lines: list[str] = ["# Weekly Social Sensing Digest", "", f"Generated: {now}", ""]

    lines.append(f"## Verified Posts ({len(verified_posts)})")
    if not verified_posts:
        lines.append("- none verified this cycle")
    else:
        for post in sorted(verified_posts, key=lambda p: str(p.get("post_ref"))):
            post_ref = post.get("post_ref")
            info = lookup.get(post_ref, {})
            surface = info.get("surface") or "unknown surface"
            link = info.get("url") or "link unavailable"
            platform_post_id = post.get("platform_post_id") or info.get("platform_post_id") or "unknown"
            metrics = info.get("metrics") or {}
            metrics_str = ", ".join(
                f"{METRIC_LABELS[k]}={v}" for k, v in sorted(metrics.items())
            ) or "no engagement metrics pulled yet"
            lines.append(
                f"- `{post_ref}` ({surface}) — link: {link} — platform_post_id: `{platform_post_id}` — engagement: {metrics_str}"
            )
    lines.append("")

    lines.append("## Quarantine")
    lines.append(f"{quarantine_total} item(s) pending owner review this cycle.")
    lines.append("")

    lines.append("## Outcome & Anti-Gaming Metrics")
    if not dept_metrics:
        lines.append("- no department-wide metric rows observed this cycle")
    else:
        for metric in sorted(dept_metrics):
            row = dept_metrics[metric]
            value = row["value"]
            label = METRIC_LABELS[metric]
            if metric in tbd:
                lines.append(f"- {label}: baseline (shadow) — observed {value}")
                continue
            target = targets.get(metric)
            if target is not None:
                lines.append(f"- {label}: {value} (target: {target})")
            else:
                lines.append(f"- {label}: {value}")
    if unrecognized_rows:
        lines.append(f"- {unrecognized_rows} unrecognized rows")
    lines.append("")

    if memory_backend_state != "wired":
        lines.append("## Memory Backend")
        lines.append(
            "UNWIRED MEMORY SEAM: durable company-scoped digests (OB_company / MyBCAT Hub) "
            "are not yet wired; this digest is accumulating locally only "
            "(departments/social/state). See charter memory.backends (C18)."
        )
        lines.append("")

    return "\n".join(lines) + "\n"


def write_missing(out_path: str | Path, reason: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": "missing", "reason": reason, "source": "assemble_weekly_digest", "ts": _now()}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Assemble the SG-SENSE weekly digest")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--out", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--verified-posts", required=True)
    parser.add_argument("--memory-backend-state", default="unwired", choices=["wired", "unwired"])
    parser.add_argument("--charter", default=str(DEFAULT_CHARTER_PATH))
    args = parser.parse_args()

    try:
        observations = load_observations(args.observations)
        verified_posts_raw = load_json_list(args.verified_posts, "verified-posts")
    except DigestInputError as exc:
        logger.error("digest input unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    try:
        charter = load_charter(args.charter, expect_department="social")
    except CharterError as exc:
        logger.error("charter unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    verified_posts = [post for post in verified_posts_raw if isinstance(post, dict) and post.get("verified") is True]
    lookup = post_lookup(observations)
    dept_metrics = dept_wide_metric_rows(observations)
    targets = outcome_targets(charter)
    tbd = tbd_metrics(charter)

    markdown = render_markdown(
        now=_now(),
        verified_posts=verified_posts,
        lookup=lookup,
        quarantine_total=quarantine_count(args.state_dir),
        dept_metrics=dept_metrics,
        tbd=tbd,
        targets=targets,
        memory_backend_state=args.memory_backend_state,
        unrecognized_rows=unrecognized_metric_count(observations),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    logger.info("assembled digest: %d verified posts, %d dept-wide metrics", len(verified_posts), len(dept_metrics))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
