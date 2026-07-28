"""SG-SENSE N1 — pull engagement analytics for every tracked post via Zernio.

Read-only sensing (C16, C20): pulls metrics for ALL posts on tracked surfaces
regardless of author (this department, the podcast department, or manual
posts) — the caller never filters by author, matching cap_scope
all_authors_via_zernio_count. A broken/absent feed is reported as missing,
never fabricated as zero (charter C13, exceptions.metrics_feed_outage).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ZERNIO_CMD = ["zernio", "analytics", "--json"]

logger = logging.getLogger("pull_zernio_analytics")


class FeedError(RuntimeError):
    """Raised when the Zernio feed is unreachable, times out, or is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_feed_text(text: str, origin: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FeedError(f"feed output is malformed JSON ({origin}): {exc}") from exc
    if not isinstance(value, list):
        raise FeedError(f"feed output must be a JSON list of posts ({origin})")
    return value


def load_fake_feed(path: str | Path) -> list[dict[str, Any]]:
    fake_path = Path(path)
    if not fake_path.exists():
        raise FeedError(f"fake feed not found: {fake_path}")
    try:
        text = fake_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FeedError(f"fake feed unreadable: {fake_path}: {exc}") from exc
    return _parse_feed_text(text, str(fake_path))


def run_zernio(cmd: list[str], timeout: float) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise FeedError(f"zernio command not found: {cmd!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FeedError(f"zernio command timed out after {timeout}s: {cmd!r}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"zernio exited {completed.returncode}"
        raise FeedError(f"zernio command failed: {detail[:240]}")
    return _parse_feed_text(completed.stdout, "zernio stdout")


def build_rows(
    posts: list[Any], now: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn raw feed posts into observation rows. Ambiguous items are quarantined,
    never guessed (charter C13, exceptions.ambiguous_item)."""
    rows: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for idx, post in enumerate(posts):
        if not isinstance(post, dict):
            quarantined.append({"item_id": f"index-{idx}", "reason": "post entry is not an object"})
            continue
        post_ref = post.get("post_ref")
        surface = post.get("surface")
        if not post_ref or not surface:
            quarantined.append(
                {
                    "item_id": str(post_ref or f"index-{idx}"),
                    "reason": "missing post_ref or surface; ambiguous item",
                }
            )
            continue
        metrics = post.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        base = {
            "source": "zernio",
            "ts": now,
            "post_ref": post_ref,
            "surface": surface,
            "platform_post_id": post.get("platform_post_id"),
            "url": post.get("url"),
            "author_dept": post.get("author_dept"),
        }
        for metric_name, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            rows.append({**base, "metric": str(metric_name), "value": float(value)})
        verified = post.get("platform_verified", True)
        rows.append({**base, "metric": "platform_verified", "value": 1.0 if verified else 0.0})
    return rows, quarantined


def write_quarantine(state_dir: str | Path, quarantined: list[dict[str, Any]]) -> None:
    if not quarantined:
        return
    qdir = Path(state_dir) / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    for item in quarantined:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(item["item_id"]))
        path = qdir / f"{safe_id}.json"
        path.write_text(
            json.dumps(
                {**item, "node": "pull_zernio_analytics", "ts": _now()},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def write_rows(out_path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_missing(out_path: str | Path, reason: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"status": "missing", "reason": reason, "source": "zernio", "ts": _now()},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Pull Zernio analytics for all tracked posts")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--zernio-cmd", nargs="+", default=list(DEFAULT_ZERNIO_CMD))
    parser.add_argument("--fake-feed", default=None, help="offline/test mode: read posts from this JSON file")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        if args.fake_feed:
            posts = load_fake_feed(args.fake_feed)
        else:
            posts = run_zernio(args.zernio_cmd, args.timeout)
    except FeedError as exc:
        logger.error("zernio feed unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    now = _now()
    rows, quarantined = build_rows(posts, now)
    write_quarantine(args.state_dir, quarantined)
    write_rows(args.out, rows)
    logger.info(
        "pulled %d observation rows from %d posts (%d quarantined)",
        len(rows), len(posts), len(quarantined),
    )
    raise SystemExit(0)


if __name__ == "__main__":
    main()
