"""Tests for the SG-SENSE lane (read-only sensing): pull_zernio_analytics,
pull_call_joins, compare_charter, assemble_weekly_digest.

Every node is exercised as a standalone CLI subprocess (matching the runtime
interface contract) with fakes for every external dependency — no network,
no real zernio/calendar system. Fixtures use obviously-fake data only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"

PULL_ZERNIO = RUNTIME_DIR / "pull_zernio_analytics.py"
PULL_CALL_JOINS = RUNTIME_DIR / "pull_call_joins.py"
COMPARE_CHARTER = RUNTIME_DIR / "compare_charter.py"
ASSEMBLE_DIGEST = RUNTIME_DIR / "assemble_weekly_digest.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run_node(script: Path, args: list[str], timeout: float = 30.0):
    import subprocess

    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def write_charter(
    path: Path,
    *,
    weekly_touch_ceiling: float = 100,
    pace_ceiling_near_frac: float = 0.9,
    faux_work_touch_floor: float = 10,
    budget_near_frac: float = 0.8,
    outcome_target: str | float = "TBD_MEASURE_IN_SHADOW",
    platform_verified_delivery_pct_target: float = 100,
    model_calls_ceiling: float = 500,
) -> Path:
    path.write_text(
        f"""
department: social
owner: ankit
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state, cross_author_cap_policy, freshness_gate]
setpoints:
  operational:
    metric: posts_per_week_all_surfaces
    target: 100
  outcome:
    metric: discovery_calls_booked
    target: {outcome_target!r}
    sensor: independent_calendar_hubspot_join
  outcome_additional:
    - {{metric: engagement_rate_per_surface, target: TBD_MEASURE_IN_SHADOW}}
    - {{metric: platform_verified_delivery_pct, target: {platform_verified_delivery_pct_target}}}
thresholds:
  weekly_touch_ceiling: {weekly_touch_ceiling}
  pace_ceiling_near_frac: {pace_ceiling_near_frac}
  faux_work_touch_floor: {faux_work_touch_floor}
  budget_near_frac: {budget_near_frac}
budget:
  weekly_ceilings:
    model_calls: {model_calls_ceiling}
    dollars: 0
    worker_minutes: 1200
""",
        encoding="utf-8",
    )
    return path


def write_zernio_rows(path: Path, post_refs: list[str], *, surface: str = "linkedin_mybcat") -> None:
    now = "2026-07-27T12:00:00+00:00"
    rows = []
    for ref in post_refs:
        rows.append(
            {
                "metric": "platform_verified",
                "value": 1.0,
                "source": "zernio",
                "ts": now,
                "post_ref": ref,
                "surface": surface,
            }
        )
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# pull_zernio_analytics
# ---------------------------------------------------------------------------

def test_pull_zernio_analytics_fake_feed_success(tmp_path):
    feed = tmp_path / "feed.json"
    write_json(
        feed,
        [
            {
                "post_ref": "p1",
                "surface": "linkedin_mybcat",
                "platform_post_id": "li_123",
                "url": "https://linkedin.example.test/p1",
                "author_dept": "social",
                "metrics": {"impressions": 120, "likes": 5, "engagement_rate": 0.041},
                "platform_verified": True,
            },
            {
                "post_ref": "p2",
                "surface": "youtube_podcast",
                "platform_post_id": "yt_456",
                "url": "https://youtube.example.test/p2",
                "author_dept": "podcast",
                "metrics": {"impressions": 300, "likes": 12},
                "platform_verified": True,
            },
        ],
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "obs.jsonl"
    result = run_node(
        PULL_ZERNIO,
        ["--state-dir", str(state_dir), "--out", str(out), "--fake-feed", str(feed)],
    )
    assert result.returncode == 0, result.stderr
    rows = read_jsonl(out)
    assert any(r["metric"] == "impressions" and r["post_ref"] == "p1" and r["value"] == 120 for r in rows)
    assert any(r["metric"] == "platform_verified" and r["post_ref"] == "p1" and r["value"] == 1.0 for r in rows)
    # ALL posts pulled regardless of author (C16/C20) — no filtering by author_dept.
    assert any(r["post_ref"] == "p2" and r["author_dept"] == "podcast" for r in rows)


def test_pull_zernio_analytics_missing_feed_exits3_no_fabrication(tmp_path):
    state_dir = tmp_path / "state"
    out = tmp_path / "obs.jsonl"
    missing_feed = tmp_path / "does-not-exist.json"
    result = run_node(
        PULL_ZERNIO,
        ["--state-dir", str(state_dir), "--out", str(out), "--fake-feed", str(missing_feed)],
    )
    assert result.returncode == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "missing"
    assert "reason" in payload and payload["reason"]
    # never fabricate metrics or zeros in place of missing data
    assert "value" not in payload
    assert "metric" not in payload


def test_pull_zernio_analytics_fake_cmd_success(tmp_path):
    fake_cmd = tmp_path / "fake_zernio.py"
    fake_cmd.write_text(
        "import json, sys\n"
        "print(json.dumps([{\"post_ref\": \"p9\", \"surface\": \"x_mybcat\", "
        "\"metrics\": {\"impressions\": 42}, \"platform_verified\": True}]))\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "obs.jsonl"
    result = run_node(
        PULL_ZERNIO,
        [
            "--state-dir", str(state_dir),
            "--out", str(out),
            "--zernio-cmd", sys.executable, str(fake_cmd),
        ],
    )
    assert result.returncode == 0, result.stderr
    rows = read_jsonl(out)
    assert any(r["metric"] == "impressions" and r["post_ref"] == "p9" and r["value"] == 42 for r in rows)


def test_pull_zernio_analytics_fake_cmd_failure_exits3(tmp_path):
    fake_cmd = tmp_path / "fake_zernio_fail.py"
    fake_cmd.write_text(
        "import sys\nsys.stderr.write('synthetic zernio outage')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "obs.jsonl"
    result = run_node(
        PULL_ZERNIO,
        [
            "--state-dir", str(state_dir),
            "--out", str(out),
            "--zernio-cmd", sys.executable, str(fake_cmd),
        ],
    )
    assert result.returncode == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "missing"


def test_pull_zernio_analytics_quarantines_ambiguous_item(tmp_path):
    feed = tmp_path / "feed.json"
    write_json(
        feed,
        [
            {"post_ref": "", "surface": "linkedin_mybcat", "metrics": {"impressions": 1}},
            {"post_ref": "p1", "surface": "linkedin_mybcat", "metrics": {"impressions": 5}},
        ],
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "obs.jsonl"
    result = run_node(
        PULL_ZERNIO,
        ["--state-dir", str(state_dir), "--out", str(out), "--fake-feed", str(feed)],
    )
    assert result.returncode == 0, result.stderr
    quarantine_files = list((state_dir / "quarantine").glob("*.json"))
    assert len(quarantine_files) == 1
    rows = read_jsonl(out)
    assert all(r.get("post_ref") != "" for r in rows)


# ---------------------------------------------------------------------------
# pull_call_joins
# ---------------------------------------------------------------------------

def test_pull_call_joins_counts_total_and_by_source(tmp_path):
    export = tmp_path / "calendar.json"
    write_json(
        export,
        [
            {"event_id": "e1", "start": "2026-07-20T10:00:00Z", "source_tag": "linkedin_mybcat", "contact_ref": "contact-001"},
            {"event_id": "e2", "start": "2026-07-21T10:00:00Z", "source_tag": "linkedin_mybcat", "contact_ref": "contact-002"},
            {"event_id": "e3", "start": "2026-07-22T10:00:00Z", "source_tag": "facebook_mybcat", "contact_ref": "contact-003"},
        ],
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "calls.jsonl"
    result = run_node(
        PULL_CALL_JOINS,
        ["--state-dir", str(state_dir), "--out", str(out), "--calendar-export", str(export)],
    )
    assert result.returncode == 0, result.stderr
    rows = read_jsonl(out)
    total_row = next(r for r in rows if r["metric"] == "discovery_calls_booked")
    assert total_row["value"] == 3
    by_source = {r["source_tag"]: r["value"] for r in rows if r["metric"] == "discovery_calls_booked_by_source"}
    assert by_source == {"linkedin_mybcat": 2, "facebook_mybcat": 1}


def test_pull_call_joins_missing_export_exits3_no_fabrication(tmp_path):
    state_dir = tmp_path / "state"
    out = tmp_path / "calls.jsonl"
    missing_export = tmp_path / "does-not-exist.json"
    result = run_node(
        PULL_CALL_JOINS,
        ["--state-dir", str(state_dir), "--out", str(out), "--calendar-export", str(missing_export)],
    )
    assert result.returncode == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "missing"
    assert "value" not in payload


def test_pull_call_joins_ambiguous_row_quarantined_and_excluded(tmp_path):
    export = tmp_path / "calendar.json"
    write_json(
        export,
        [
            {"event_id": "e1", "start": "2026-07-20T10:00:00Z", "source_tag": "linkedin_mybcat", "contact_ref": "contact-001"},
            {"event_id": "e2", "start": "2026-07-21T10:00:00Z", "source_tag": "linkedin_mybcat", "contact_ref": ""},
        ],
    )
    state_dir = tmp_path / "state"
    out = tmp_path / "calls.jsonl"
    result = run_node(
        PULL_CALL_JOINS,
        ["--state-dir", str(state_dir), "--out", str(out), "--calendar-export", str(export)],
    )
    assert result.returncode == 0, result.stderr
    rows = read_jsonl(out)
    total_row = next(r for r in rows if r["metric"] == "discovery_calls_booked")
    assert total_row["value"] == 1
    quarantine_files = list((state_dir / "quarantine").glob("*.json"))
    assert len(quarantine_files) == 1


# ---------------------------------------------------------------------------
# compare_charter
# ---------------------------------------------------------------------------

def test_compare_charter_thresholds_are_charter_driven(tmp_path):
    observations = tmp_path / "obs.jsonl"
    write_zernio_rows(observations, ["p1", "p2", "p3", "p4", "p5"])

    charter_tight = write_charter(
        tmp_path / "charter_tight.yaml", weekly_touch_ceiling=5, pace_ceiling_near_frac=0.9, faux_work_touch_floor=3
    )
    out_tight = tmp_path / "signals_tight.jsonl"
    result_tight = run_node(
        COMPARE_CHARTER,
        ["--state-dir", str(tmp_path / "state"), "--out", str(out_tight), "--observations", str(observations), "--charter", str(charter_tight)],
    )
    assert result_tight.returncode == 0, result_tight.stderr
    signals_tight = {r["metric"]: r["value"] for r in read_jsonl(out_tight)}
    assert signals_tight["cap_near"] is True
    assert signals_tight["faux_work_signal"] is False

    charter_loose = write_charter(
        tmp_path / "charter_loose.yaml", weekly_touch_ceiling=100, pace_ceiling_near_frac=0.9, faux_work_touch_floor=10
    )
    out_loose = tmp_path / "signals_loose.jsonl"
    result_loose = run_node(
        COMPARE_CHARTER,
        ["--state-dir", str(tmp_path / "state"), "--out", str(out_loose), "--observations", str(observations), "--charter", str(charter_loose)],
    )
    assert result_loose.returncode == 0, result_loose.stderr
    signals_loose = {r["metric"]: r["value"] for r in read_jsonl(out_loose)}
    assert signals_loose["cap_near"] is False
    assert signals_loose["faux_work_signal"] is True


def test_compare_charter_missing_observations_exits3(tmp_path):
    charter = write_charter(tmp_path / "charter.yaml")
    out = tmp_path / "signals.jsonl"
    missing_obs = tmp_path / "does-not-exist.jsonl"
    result = run_node(
        COMPARE_CHARTER,
        ["--state-dir", str(tmp_path / "state"), "--out", str(out), "--observations", str(missing_obs), "--charter", str(charter)],
    )
    assert result.returncode == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "missing"


def test_compare_charter_gaming_signal_from_self_reported_mismatch(tmp_path):
    observations = tmp_path / "obs.jsonl"
    now = "2026-07-27T12:00:00+00:00"
    write_json_rows = [
        {"metric": "engagement_rate", "value": 0.02, "source": "zernio", "ts": now, "post_ref": "p1", "surface": "linkedin_mybcat"},
    ]
    observations.write_text("\n".join(json.dumps(r) for r in write_json_rows) + "\n", encoding="utf-8")

    self_reported = tmp_path / "self_reported.jsonl"
    self_reported_rows = [
        {"metric": "engagement_rate", "value": 0.5, "source": "dept_self_report", "ts": now, "post_ref": "p1", "surface": "linkedin_mybcat"},
    ]
    self_reported.write_text("\n".join(json.dumps(r) for r in self_reported_rows) + "\n", encoding="utf-8")

    charter = write_charter(tmp_path / "charter.yaml")
    out = tmp_path / "signals.jsonl"
    result = run_node(
        COMPARE_CHARTER,
        [
            "--state-dir", str(tmp_path / "state"),
            "--out", str(out),
            "--observations", str(observations),
            "--self-reported", str(self_reported),
            "--charter", str(charter),
        ],
    )
    assert result.returncode == 0, result.stderr
    signals = {r["metric"]: r for r in read_jsonl(out)}
    assert signals["gaming_signal"]["value"] is True
    assert signals["gaming_signal"]["evidence"]["mismatches"]


# ---------------------------------------------------------------------------
# assemble_weekly_digest
# ---------------------------------------------------------------------------

def test_assemble_weekly_digest_links_seam_and_tbd_and_no_dm_leak(tmp_path):
    now = "2026-07-27T12:00:00+00:00"
    observations = tmp_path / "obs.jsonl"
    rows = [
        {
            "metric": "impressions", "value": 88, "source": "zernio", "ts": now,
            "post_ref": "p1", "surface": "linkedin_mybcat",
            "platform_post_id": "li_777", "url": "https://linkedin.example.test/p1",
            "dm_body": "SECRET_DM_SHOULD_NOT_LEAK",
        },
        {
            "metric": "platform_verified", "value": 1.0, "source": "zernio", "ts": now,
            "post_ref": "p1", "surface": "linkedin_mybcat",
        },
        {"metric": "discovery_calls_booked", "value": 4.0, "source": "calendar_export_join", "ts": now},
    ]
    observations.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    verified_posts = tmp_path / "verified.json"
    write_json(
        verified_posts,
        [
            {"post_ref": "p1", "platform_post_id": "li_777", "status": "confirmed", "verified": True, "ts": now},
            {"post_ref": "p2", "platform_post_id": "li_999", "status": "failed", "verified": False, "ts": now},
        ],
    )

    charter = write_charter(tmp_path / "charter.yaml", outcome_target="TBD_MEASURE_IN_SHADOW")

    state_dir = tmp_path / "state"
    (state_dir / "quarantine").mkdir(parents=True)
    (state_dir / "quarantine" / "item1.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "digest.md"
    result = run_node(
        ASSEMBLE_DIGEST,
        [
            "--state-dir", str(state_dir),
            "--out", str(out),
            "--observations", str(observations),
            "--verified-posts", str(verified_posts),
            "--memory-backend-state", "unwired",
            "--charter", str(charter),
        ],
    )
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")

    # every platform-verified post appears with its link
    assert "p1" in text
    assert "https://linkedin.example.test/p1" in text
    assert "li_777" in text
    # the non-verified post is excluded
    assert "p2" not in text
    assert "li_999" not in text
    # quarantine count surfaced
    assert "1 item(s) pending owner review" in text
    # TBD_MEASURE_IN_SHADOW metric renders as a shadow baseline, not a failure
    assert "discovery_calls_booked: baseline (shadow)" in text
    # unwired memory seam notice present (charter C18)
    assert "UNWIRED MEMORY SEAM" in text
    # sanitized: DM body never leaks even though it was present on the input row
    assert "SECRET_DM_SHOULD_NOT_LEAK" not in text


def test_assemble_weekly_digest_wired_backend_omits_seam_notice(tmp_path):
    now = "2026-07-27T12:00:00+00:00"
    observations = tmp_path / "obs.jsonl"
    observations.write_text("", encoding="utf-8")
    verified_posts = tmp_path / "verified.json"
    write_json(verified_posts, [])
    charter = write_charter(tmp_path / "charter.yaml")
    state_dir = tmp_path / "state"
    out = tmp_path / "digest.md"
    result = run_node(
        ASSEMBLE_DIGEST,
        [
            "--state-dir", str(state_dir),
            "--out", str(out),
            "--observations", str(observations),
            "--verified-posts", str(verified_posts),
            "--memory-backend-state", "wired",
            "--charter", str(charter),
        ],
    )
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "UNWIRED MEMORY SEAM" not in text
    assert "none verified this cycle" in text


def test_assemble_weekly_digest_missing_verified_posts_exits3(tmp_path):
    observations = tmp_path / "obs.jsonl"
    observations.write_text("", encoding="utf-8")
    missing_verified = tmp_path / "does-not-exist.json"
    out = tmp_path / "digest.md"
    result = run_node(
        ASSEMBLE_DIGEST,
        [
            "--state-dir", str(tmp_path / "state"),
            "--out", str(out),
            "--observations", str(observations),
            "--verified-posts", str(missing_verified),
        ],
    )
    assert result.returncode == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "missing"
