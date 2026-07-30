"""Build the SG-REPUBLISH back-catalog index from declared sources."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET


LOG = logging.getLogger(__name__)
REQUIRED = {
    "item_id",
    "source_type",
    "title",
    "url",
    "published_at",
    "body_path",
    "last_resurfaced_at",
    "prior_engagement",
}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _missing(out: Path, reason: str) -> int:
    LOG.error(reason)
    _write(out, {"status": "missing", "reason": reason})
    return 3


def _iso(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("published_at is empty")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _text(item: ET.Element, *names: str) -> str:
    for name in names:
        node = item.find(name)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _rss_rows(source: str) -> list[dict]:
    if source.startswith(("http://", "https://")):
        with urlopen(source, timeout=20) as response:  # nosec: declared read source
            root = ET.fromstring(response.read())
    else:
        root = ET.parse(source).getroot()
    rows = []
    for item in root.findall(".//item"):
        url = _text(item, "link")
        item_id = _text(item, "guid") or url
        body_path = _text(item, "body_path", "bodyPath")
        img_el = item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}image")
        thumbnail_url = img_el.get("href", "") if img_el is not None else ""
        row = {
            "item_id": item_id,
            "source_type": "podcast",
            "title": _text(item, "title"),
            "url": url,
            "thumbnail_url": thumbnail_url,
            "published_at": _iso(_text(item, "pubDate", "published_at")),
            "body_path": body_path,
            "last_resurfaced_at": None,
            "prior_engagement": {"score": 0.0},
        }
        rows.append(row)
    return rows


def _json_rows(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("items")
    if not isinstance(value, list):
        raise ValueError("items source must be a JSON list or an object with items[]")
    return value


def _index_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _validate(row: dict) -> dict:
    if not isinstance(row, dict) or not REQUIRED.issubset(row):
        raise ValueError(f"content_item missing fields: {sorted(REQUIRED - set(row or {}))}")
    for field in ("item_id", "source_type", "title", "url", "published_at", "body_path"):
        if not isinstance(row[field], str):
            raise ValueError(f"content_item.{field} must be a string")
    if "thumbnail_url" in row and not isinstance(row["thumbnail_url"], str):
        row["thumbnail_url"] = ""
    if not row["item_id"].strip() or not row["title"].strip() or not row["url"].strip():
        raise ValueError("content_item item_id, title, and url must be non-empty")
    row["published_at"] = _iso(row["published_at"])
    if row["last_resurfaced_at"] is not None:
        row["last_resurfaced_at"] = _iso(str(row["last_resurfaced_at"]))
    engagement = row["prior_engagement"]
    if not isinstance(engagement, dict) or isinstance(engagement.get("score"), bool):
        raise ValueError("content_item.prior_engagement.score must be numeric")
    engagement["score"] = float(engagement["score"])
    return row


def run(*, rss: list[str], items: list[Path], index: Path | None, out: Path) -> int:
    if not rss and not items:
        return _missing(out, "no inventory source supplied")
    try:
        existing = {_validate(row)["item_id"]: row for row in _index_rows(index)} if index else {}
        incoming: list[dict] = []
        for source in rss:
            source_rows = _rss_rows(source)
            if not source_rows:
                return _missing(out, f"RSS source contained zero items: {source}")
            incoming.extend(source_rows)
        for source in items:
            source_rows = _json_rows(source)
            if not source_rows:
                return _missing(out, f"items source contained zero items: {source}")
            incoming.extend(source_rows)
        if not incoming:
            return _missing(out, "inventory source contained zero items")
        merged = dict(existing)
        for raw in incoming:
            row = _validate(dict(raw))
            old = existing.get(row["item_id"])
            if old:
                row["last_resurfaced_at"] = old["last_resurfaced_at"]
                row["prior_engagement"] = old["prior_engagement"]
                if "thumbnail_url" in old and "thumbnail_url" not in row:
                    row["thumbnail_url"] = old["thumbnail_url"]
            merged[row["item_id"]] = row
        rows = [_validate(row) for _, row in sorted(merged.items())]
        if not rows:
            return _missing(out, "inventory index contained zero valid rows")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, TypeError, ET.ParseError, json.JSONDecodeError) as exc:
        return _missing(out, f"inventory source unreadable or invalid: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rss", action="append", default=[])
    parser.add_argument("--items", type=Path, action="append", default=[])
    parser.add_argument("--index", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run(rss=args.rss, items=args.items, index=args.index, out=args.out))


if __name__ == "__main__":
    main()
