import pytest

from departments.podcast.runtime import compare_charter


def _observation(sensor, status):
    return {
        "ts": "2026-08-05T00:00:00+00:00",
        "sensor": sensor,
        "subject": "test-subject",
        "status": status,
        "evidence": "test-evidence",
    }


def test_hopper_unknown_classifies_as_high_severity_candidate():
    candidate = compare_charter.compare_observations(
        [_observation("hopper", "unknown")], {}
    )[0]

    assert candidate["failure_class"] == "hopper_blind"
    assert candidate["severity"] == "high"
    assert candidate["one_question"] == (
        "Hopper/publish-reliability evidence is unavailable — what broke the "
        "publish schedule source or the publish-day verifier?"
    )


def test_funnel_alarm_classifies_as_high_severity_candidate():
    candidate = compare_charter.compare_observations(
        [_observation("funnel", "alarm")], {}
    )[0]

    assert candidate["failure_class"] == "funnel_behind"
    assert candidate["severity"] == "high"
    assert candidate["what_it_means"] == (
        "The guest pipeline fell behind its daily numbers, so future episodes "
        "are at risk of running dry."
    )


def test_expectation_unknown_maps_to_expectation_blind():
    rows = [{
        "ts": "2026-08-05T12:00:00+00:00", "sensor": "expectation",
        "subject": "expectation-none", "status": "unknown",
        "evidence": "/tmp/manifests",
        "detail": "no expectation manifests found (fail closed)", "metrics": {},
    }]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == ["expectation_blind"]
    assert candidates[0]["severity"] == "high"
    assert candidates[0]["what_it_means"]


def test_expectation_alarm_maps_to_expectation_delta():
    rows = [{
        "ts": "2026-08-05T12:00:00+00:00", "sensor": "expectation",
        "subject": "expectation-daily", "status": "alarm",
        "evidence": "/tmp/receipt.json",
        "detail": "2 expectation delta(s)",
        "metrics": {"counts": {"ok": 1}, "deltas": []},
    }]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == ["expectation_delta"]
    assert candidates[0]["severity"] == "high"


def test_runmanifest_alarm_maps_to_missing_steps():
    rows = [_observation("runmanifest", "alarm")]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == [
        "runmanifest_missing_steps"
    ]
    assert candidates[0]["severity"] == "high"


def test_runmanifest_unknown_maps_to_unverified():
    rows = [_observation("runmanifest", "unknown")]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == ["runmanifest_unverified"]
    assert candidates[0]["severity"] == "med"


def test_floors_alarm_maps_to_floors_attention():
    rows = [_observation("floors", "alarm")]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == ["floors_attention"]
    assert candidates[0]["severity"] == "med"


def test_floors_unknown_maps_to_floors_unconfigured():
    rows = [_observation("floors", "unknown")]
    candidates = compare_charter.compare_observations(rows, {})
    assert [c["failure_class"] for c in candidates] == ["floors_unconfigured"]
    assert candidates[0]["severity"] == "med"


def test_truly_unknown_sensor_status_pair_still_raises():
    with pytest.raises(
        ValueError,
        match="no charter comparison transition for sensor='unmapped', status='broken'",
    ):
        compare_charter.compare_observations(
            [_observation("unmapped", "broken")], {}
        )


@pytest.mark.parametrize(("sensor", "status"), compare_charter.FAILURE_CLASSES)
def test_every_declared_failure_transition_classifies(sensor, status):
    candidates = compare_charter.compare_observations(
        [_observation(sensor, status)], {}
    )

    assert len(candidates) == 1


def test_every_failure_class_has_plain_english_meaning_and_need():
    failure_classes = {
        failure_class
        for failure_class, _severity in compare_charter.FAILURE_CLASSES.values()
    } | {
        failure_class
        for failure_class, _severity in compare_charter.FAILURE_HINT_CLASSES.values()
    }

    assert failure_classes == set(compare_charter.MEANINGS)
    for failure_class in failure_classes:
        meaning = compare_charter.MEANINGS[failure_class]
        assert meaning["what_it_means"].strip()
        assert meaning["what_it_needs"].strip()
        assert not {
            "sensor",
            "verifier",
            "fingerprint",
            "observation",
        } & set(meaning["what_it_means"].lower().split())
