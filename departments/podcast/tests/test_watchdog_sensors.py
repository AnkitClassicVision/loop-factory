import json
from datetime import date
from pathlib import Path

import pytest

from departments.podcast.runtime import manifest_sensor, pipeline_sensor, publish_verifier


DAILY_SCRIPT = Path(__file__).parents[1] / "runtime" / "podcast_daily.sh"


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_charter(path, target=2):
    path.write_text(f"""
department: podcast
owner: ankit
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  outcome_additional:
    - metric: pipeline_guests
      target: {target}
""", encoding="utf-8")


def read_rows(state):
    return [json.loads(line) for line in (state / "observations.jsonl").read_text().splitlines()]


def test_pipeline_independent_sources_and_auditable_fields(tmp_path):
    sources, state = tmp_path / "sources", tmp_path / "state"
    sources.mkdir()
    charter = tmp_path / "charter.yaml"
    write_charter(charter, 1)
    write_json(sources / "calendar.json", [
        {"guest": "Ada", "email": "ada@example.test", "event_type": "podcast recording",
         "start_iso": "2026-07-24T15:00:00Z"},
    ])
    write_json(sources / "hubspot_contacts.json", [
        {"email": "ada@example.test", "podcast_status": "scheduled", "name": "Ada"},
    ])
    first = pipeline_sensor.run(state, sources, charter)
    assert first["status"] == "ok"
    assert first["metrics"]["count"] == 1
    fields = first["metrics"]["counted_guests"][0]["source_fields"]
    assert fields["calendar.json"]["event_type"] == "podcast recording"
    assert fields["hubspot_contacts.json"]["podcast_status"] == "scheduled"

    # Unrelated local/self-report artifacts cannot affect the independent count.
    write_json(sources / "loop_receipt.json", {"pipeline_guests": 99})
    assert pipeline_sensor.run(state, sources, charter)["metrics"]["count"] == 1
    write_json(sources / "hubspot_contacts.json", [])
    assert pipeline_sensor.run(state, sources, charter)["metrics"]["count"] == 0


def test_pipeline_below_target_fails(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(sources / "calendar.json", [])
    write_json(sources / "hubspot_contacts.json", [])
    charter = tmp_path / "charter.yaml"
    write_charter(charter, 6)
    assert pipeline_sensor.run(tmp_path / "state", sources, charter)["status"] == "fail"


def test_pipeline_name_only_join_is_unknown_and_not_counted(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(
        sources / "calendar.json",
        [{"guest": "Shared Name", "event_type": "podcast recording"}],
    )
    write_json(
        sources / "hubspot_contacts.json",
        [
            {
                "name": "Shared Name",
                "email": "guest@example.test",
                "podcast_status": "scheduled",
            }
        ],
    )
    charter = tmp_path / "charter.yaml"
    write_charter(charter, 1)

    observation = pipeline_sensor.run(tmp_path / "state", sources, charter)

    assert observation["status"] == "unknown"
    assert observation["metrics"]["count"] == 0
    assert observation["detail"] == "unresolved identity — suppress + review"


def test_pipeline_duplicate_hubspot_email_is_unknown(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(
        sources / "calendar.json",
        [
            {
                "guest": "Ada",
                "email": "dup@example.test",
                "event_type": "podcast recording",
            }
        ],
    )
    write_json(
        sources / "hubspot_contacts.json",
        [
            {
                "name": "Ada A",
                "email": "dup@example.test",
                "podcast_status": "scheduled",
            },
            {
                "name": "Ada B",
                "email": "dup@example.test",
                "podcast_status": "scheduled",
            },
        ],
    )
    charter = tmp_path / "charter.yaml"
    write_charter(charter, 1)

    observation = pipeline_sensor.run(tmp_path / "state", sources, charter)

    assert observation["status"] == "unknown"
    assert observation["metrics"]["count"] == 0
    assert observation["metrics"]["identity_issue_counts"][
        "duplicate_hubspot_email"
    ] == 1


def test_pipeline_duplicate_name_without_distinct_emails_is_unknown(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(
        sources / "calendar.json",
        [{"guest": "Shared Name", "event_type": "podcast recording"}],
    )
    write_json(
        sources / "hubspot_contacts.json",
        [
            {"name": "Shared Name", "podcast_status": "scheduled"},
            {"name": "Shared Name", "podcast_status": "scheduled"},
        ],
    )
    charter = tmp_path / "charter.yaml"
    write_charter(charter, 1)

    observation = pipeline_sensor.run(tmp_path / "state", sources, charter)

    assert observation["status"] == "unknown"
    assert observation["metrics"]["count"] == 0
    assert observation["metrics"]["identity_issue_counts"][
        "duplicate_display_name_without_distinct_emails"
    ] == 1


def test_missing_source_is_unknown_not_crash(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    charter = tmp_path / "charter.yaml"
    write_charter(charter)
    observation = pipeline_sensor.run(tmp_path / "state", sources, charter)
    assert observation["status"] == "unknown"
    assert "calendar.json" in observation["detail"]


def test_publish_missing_social_receipt_fails(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(sources / "publish_schedule.json", {"episodes": [{
        "episode_id": "ep-7", "publish_date": "2026-07-22",
        "expected": {"rss_url": "https://feed.test/ep-7", "youtube_url": "https://yt.test/7",
                     "social_receipts": ["linkedin-7", "x-7"]},
    }]})
    (sources / "rss.xml").write_text("<rss><item><guid>ep-7</guid></item></rss>", encoding="utf-8")
    write_json(sources / "youtube_status.json", [{"episode_id": "ep-7", "status": "public"}])
    write_json(sources / "social_receipts.json", [{"receipt_id": "linkedin-7"}])
    obs = publish_verifier.run(tmp_path / "state", sources, date(2026, 7, 22))[0]
    assert obs["status"] == "fail"
    assert obs["metrics"]["missing_social_receipts"] == ["x-7"]


@pytest.mark.parametrize(
    ("field", "value", "missing_label"),
    [
        ("rss_url", "", "expected.rss_url"),
        ("youtube_url", None, "expected.youtube_url"),
        ("social_receipts", [], "expected.social_receipts"),
    ],
)
def test_publish_empty_expected_artifact_fails(
    tmp_path, field, value, missing_label
):
    sources = tmp_path / "sources"
    sources.mkdir()
    expected = {
        "rss_url": "https://feed.test/ep-7",
        "youtube_url": "https://yt.test/7",
        "social_receipts": ["linkedin-7"],
    }
    expected[field] = value
    write_json(
        sources / "publish_schedule.json",
        {
            "episodes": [
                {
                    "episode_id": "ep-7",
                    "publish_date": "2026-07-22",
                    "expected": expected,
                }
            ]
        },
    )
    (sources / "rss.xml").write_text(
        "<rss><item><guid>ep-7</guid><link>https://feed.test/ep-7</link></item></rss>",
        encoding="utf-8",
    )
    write_json(
        sources / "youtube_status.json",
        [
            {
                "episode_id": "ep-7",
                "youtube_url": "https://yt.test/7",
                "status": "public",
            }
        ],
    )
    write_json(
        sources / "social_receipts.json", [{"receipt_id": "linkedin-7"}]
    )

    observation = publish_verifier.run(
        tmp_path / "state", sources, date(2026, 7, 22)
    )[0]

    assert observation["status"] == "fail"
    assert missing_label in observation["detail"]
    assert missing_label in observation["metrics"]["missing_expectations"]


def test_daily_chain_runs_all_independent_sensors_before_compare():
    lines = DAILY_SCRIPT.read_text(encoding="utf-8").splitlines()
    runtime_lines = {
        name: next(
            index for index, line in enumerate(lines) if f"runtime/{name}.py" in line
        )
        for name in (
            "sense_estate",
            "pipeline_sensor",
            "publish_verifier",
            "manifest_sensor",
            "compare_charter",
        )
    }

    assert list(runtime_lines.values()) == sorted(runtime_lines.values())
    for name in ("pipeline_sensor", "publish_verifier", "manifest_sensor"):
        line = lines[runtime_lines[name]]
        assert "factory/launch.py" in line
        assert "--shadow" in line
        assert '--sources "${SOURCES}"' in line


def test_manifest_completeness_and_fallback_flags(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    write_json(sources / "guest_manifests.json", {"guests": [{
        "guest": "Ada", "email": "ada@example.test", "episode_id": "ep-7",
        "publish_date": "2026-08-22", "fields": {
            "headshot": {"present": True, "source": "hubspot"},
            "links": {"present": True, "source": "hubspot"},
            "bio": {"present": False, "source": "hubspot", "fallback_available": True},
            "promo_assets": {"present": False, "source": "hubspot", "fallback_available": False},
        },
    }]})
    obs = manifest_sensor.run(tmp_path / "state", sources, date(2026, 7, 22))[0]
    assert obs["status"] == "warn"
    assert obs["metrics"]["completeness_pct"] == 50.0
    assert obs["metrics"]["missing"] == ["bio", "promo_assets"]
    assert obs["metrics"]["fallback_available"] == {"bio": True, "promo_assets": False}


def test_duplicate_email_is_unknown_and_suppressed(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    guest = {"email": "dup@example.test", "fields": {}}
    write_json(sources / "guest_manifests.json", {"guests": [guest, guest]})
    observations = manifest_sensor.run(tmp_path / "state", sources)
    assert [row["status"] for row in observations] == ["unknown", "unknown"]
    assert all(row["detail"] == "suppress + review" for row in observations)
    assert len(read_rows(tmp_path / "state")) == 2
