from __future__ import annotations

import json
from pathlib import Path

import yaml

from departments.social.runtime import guards


def _write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def _item(body_path: Path, *, item_id: str = "item-1") -> dict:
    return {
        "item_id": item_id,
        "source_type": "podcast_backcatalog",
        "title": "Durable operating systems",
        "url": f"https://example.invalid/content/{item_id}",
        "published_at": "2024-01-01T00:00:00+00:00",
        "body_path": str(body_path),
        "last_resurfaced_at": None,
        "prior_engagement": {"score": 4.25},
    }


def _manifest(item: dict, body: str) -> dict:
    return {
        "version": "v1",
        "assembled_at": "2026-07-28T12:00:00+00:00",
        "item": item,
        "body_text": body,
        "brand": {"voice": "plain and specific"},
        "offer": {"name": "Example Assessment"},
        "complete": True,
        "missing": [],
    }


def test_resolve_allows_one_canonical_match_and_blocks_unknown_surface(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("Evergreen source.", encoding="utf-8")
    item = _item(body)
    item_path = _write_json(tmp_path / "item.json", item)
    index_path = _write_json(tmp_path / "index.json", {"items": [item]})

    resolved = guards.resolve(item_path, index_path, "linkedin_mybcat")
    assert resolved["status"] == "resolved"
    assert resolved["item"] == {**item, "prior_engagement": {"score": 4.25}}

    out = tmp_path / "blocked.json"
    rc = guards.main(
        [
            "resolve",
            "--state-dir",
            str(tmp_path / "state"),
            "--item",
            str(item_path),
            "--index",
            str(index_path),
            "--surface",
            "made_up_surface",
            "--out",
            str(out),
        ]
    )
    assert rc == 2
    assert json.loads(out.read_text())["status"] == "blocked"
    assert (tmp_path / "state" / "quarantine" / "item-1.json").exists()


def test_resolve_malformed_input_fails_closed(tmp_path):
    item_path = tmp_path / "item.json"
    item_path.write_text("{broken", encoding="utf-8")
    index_path = _write_json(tmp_path / "index.json", [])
    out = tmp_path / "out.json"

    rc = guards.main(
        [
            "resolve",
            "--state-dir",
            str(tmp_path / "state"),
            "--item",
            str(item_path),
            "--index",
            str(index_path),
            "--surface",
            "linkedin_mybcat",
            "--out",
            str(out),
        ]
    )

    assert rc == 2
    assert json.loads(out.read_text())["status"] == "blocked"


def test_eligibility_allows_approved_evergreen_item_and_structural_cta(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text(
        "Guest: Example Speaker\nA durable lesson for operators.", encoding="utf-8"
    )
    item_path = _write_json(tmp_path / "item.json", _item(body))
    suppression = _write_jsonl(tmp_path / "suppression.jsonl", [])
    approvals = tmp_path / "approvals.yaml"
    approvals.write_text(
        yaml.safe_dump({"approved_names": ["Example Speaker"]}), encoding="utf-8"
    )

    result = guards.eligibility(
        item_path,
        suppression,
        approvals_path=approvals,
        cta_url="https://example.invalid/book",
    )

    assert result["status"] == "eligible"


def test_eligibility_blocks_suppression_time_anchor_unapproved_and_bad_cta(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("Evergreen.", encoding="utf-8")
    item_path = _write_json(tmp_path / "item.json", _item(body))
    suppression = _write_jsonl(
        tmp_path / "suppression.jsonl", [{"item_id": "item-1", "reason": "retracted"}]
    )
    try:
        guards.eligibility(item_path, suppression)
    except guards.GateBlocked as exc:
        assert "suppressed" in str(exc)
    else:
        raise AssertionError("suppressed item was allowed")

    _write_jsonl(suppression, [])
    body.write_text("Join us live tomorrow.", encoding="utf-8")
    try:
        guards.eligibility(item_path, suppression)
    except guards.GateBlocked as exc:
        assert "time-anchored" in str(exc)
    else:
        raise AssertionError("time-anchored item was allowed")

    body.write_text("Client: Example Client\nEvergreen.", encoding="utf-8")
    try:
        guards.eligibility(item_path, suppression)
    except guards.GateBlocked as exc:
        assert "approvals" in str(exc)
    else:
        raise AssertionError("unapproved client was allowed")

    body.write_text("Evergreen.", encoding="utf-8")
    try:
        guards.eligibility(item_path, suppression, cta_url="not-a-url")
    except guards.GateBlocked as exc:
        assert "cta_url" in str(exc)
    else:
        raise AssertionError("malformed CTA was allowed")


def test_privacy_redacts_sensitive_shapes_and_blocklist_before_model(tmp_path):
    body_path = tmp_path / "body.txt"
    body_path.write_text("source", encoding="utf-8")
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        _manifest(
            _item(body_path),
            (
                "Contact fake.person@example.invalid or 202-555-0100. "
                "DOB: 01/02/1980. Member ID: FAKE-12345. "
                "Example Practice supplied the case."
            ),
        ),
    )
    blocklist = tmp_path / "blocklist.yaml"
    blocklist.write_text(
        yaml.safe_dump({"practices": ["Example Practice"]}), encoding="utf-8"
    )

    result = guards.privacy(manifest_path, blocklist)

    assert result["sanitized"] is True
    assert result["redactions"] == 5
    assert "example.invalid" not in result["body_text"]
    assert "Example Practice" not in result["body_text"]
    assert result["body_text"].count("[REDACTED]") == 5


def test_privacy_blocks_unredactable_structural_hit_and_incomplete_manifest(tmp_path):
    body_path = tmp_path / "body.txt"
    body_path.write_text("source", encoding="utf-8")
    item = _item(body_path)
    item["url"] = "https://example.invalid/fake.person@example.invalid"
    manifest_path = _write_json(
        tmp_path / "manifest.json", _manifest(item, "Evergreen.")
    )
    blocklist = tmp_path / "blocklist.yaml"
    blocklist.write_text("blocked_tokens: []\n", encoding="utf-8")

    try:
        guards.privacy(manifest_path, blocklist)
    except guards.GateBlocked as exc:
        assert "structural field" in str(exc)
    else:
        raise AssertionError("structural sensitive value was redacted and allowed")

    manifest = _manifest(_item(body_path), "Evergreen.")
    manifest["complete"] = False
    _write_json(manifest_path, manifest)
    try:
        guards.privacy(manifest_path, blocklist)
    except guards.GateBlocked as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete manifest was allowed")


def test_kill_trips_from_independent_sensor_and_marker_blocks_next_run(tmp_path):
    charter = tmp_path / "charter.yaml"
    charter.write_text(
        yaml.safe_dump(
            {"kill_if": [{"reputation_event": "platform strike or ban"}]}
        ),
        encoding="utf-8",
    )
    observations = _write_jsonl(
        tmp_path / "observations.jsonl",
        [
            {
                "metric": "platform_strike",
                "value": True,
                "source": "platform_sensor",
                "ts": "2026-07-28T12:00:00+00:00",
            }
        ],
    )
    state = tmp_path / "state"
    out = tmp_path / "kill.json"

    rc = guards.main(
        [
            "kill",
            "--state-dir",
            str(state),
            "--observations",
            str(observations),
            "--charter",
            str(charter),
            "--out",
            str(out),
        ]
    )
    assert rc == 2
    assert (state / "KILLED").exists()

    _write_jsonl(observations, [])
    out2 = tmp_path / "kill-again.json"
    rc2 = guards.main(
        [
            "kill",
            "--state-dir",
            str(state),
            "--observations",
            str(observations),
            "--charter",
            str(charter),
            "--out",
            str(out2),
        ]
    )
    assert rc2 == 2
    assert "KILLED marker" in json.loads(out2.read_text())["reason"]


def test_breaker_is_per_surface_and_trips_on_failure_streak(tmp_path):
    observations = _write_jsonl(
        tmp_path / "observations.jsonl",
        [
            {
                "metric": "delivery_failure",
                "value": True,
                "surface": "linkedin_mybcat",
                "source": "zernio_status_sensor",
                "ts": f"2026-07-28T12:0{minute}:00+00:00",
            }
            for minute in range(3)
        ],
    )
    state = tmp_path / "state"
    out = tmp_path / "breaker.json"
    rc = guards.main(
        [
            "breaker",
            "--state-dir",
            str(state),
            "--observations",
            str(observations),
            "--surface",
            "linkedin_mybcat",
            "--out",
            str(out),
        ]
    )
    assert rc == 2
    assert (state / "BREAKER_linkedin_mybcat").exists()
    assert not (state / "BREAKER_facebook_mybcat").exists()

    clear = guards.breaker(state, observations, "facebook_mybcat")
    assert clear["status"] == "clear"
