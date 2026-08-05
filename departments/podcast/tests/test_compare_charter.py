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
