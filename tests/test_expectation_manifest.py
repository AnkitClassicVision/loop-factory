"""Expectation manifest reconciler: declared-expectation vs reality contract.

The defining cases come from the 2026-08-04 incidents: a recording finished in
a cloud with no job (snapshot_member), a research stage stamped done with an
empty payload (json_field), and a step quiet inside its deadline that must NOT
alarm yet (pending). Deny-by-default is load-bearing: a missing snapshot key
is an error, never an empty-and-therefore-healthy list.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory import expectation_manifest as em

NOW = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)


def write_manifest(tmp_path, body: str) -> Path:
    path = tmp_path / "m.yaml"
    path.write_text(body, encoding="utf-8")
    return path


RECORDING_MANIFEST = """\
schema: expectation-manifest/v1
process: recording-intake
instances:
  source: snapshot
  snapshot: finished
steps:
  - id: job-enqueued
    deadline_minutes: 30
    expect:
      - kind: snapshot_member
        snapshot: jobs
    authorized_skip:
      glob: "skips/{id}-job-*.json"
    heal: escalate
"""


def snap(finished_age_minutes: float, job_ids):
    anchor = (NOW - timedelta(minutes=finished_age_minutes)).isoformat()
    return {"finished": [{"id": "rec-1", "anchor_ts": anchor}], "jobs": list(job_ids)}


def reconcile(tmp_path, manifest_body, snapshots, now=NOW):
    manifest = em.load_manifest(write_manifest(tmp_path, manifest_body))
    return em.reconcile(manifest, tmp_path, snapshots, now)


def test_present_job_is_ok(tmp_path):
    receipt = reconcile(tmp_path, RECORDING_MANIFEST, snap(60, ["rec-1"]))
    assert receipt["counts"] == {"ok": 1, "pending": 0, "authorized_skips": 0, "deltas": 0}


def test_missing_job_past_deadline_is_a_delta(tmp_path):
    receipt = reconcile(tmp_path, RECORDING_MANIFEST, snap(60, []))
    assert receipt["counts"]["deltas"] == 1
    delta = receipt["deltas"][0]
    assert delta["step"] == "job-enqueued" and delta["status"] == "missing"
    assert delta["heal"] == "escalate"


def test_missing_job_inside_deadline_is_pending_not_delta(tmp_path):
    receipt = reconcile(tmp_path, RECORDING_MANIFEST, snap(10, []))
    assert receipt["counts"]["deltas"] == 0
    assert receipt["counts"]["pending"] == 1


def test_authorized_skip_receipt_silences_the_delta(tmp_path):
    (tmp_path / "skips").mkdir()
    (tmp_path / "skips" / "rec-1-job-owner.json").write_text("{}", encoding="utf-8")
    receipt = reconcile(tmp_path, RECORDING_MANIFEST, snap(60, []))
    assert receipt["counts"]["deltas"] == 0
    assert receipt["counts"]["authorized_skips"] == 1


def test_missing_snapshot_key_fails_closed(tmp_path):
    with pytest.raises(em.ManifestError, match="fail closed"):
        reconcile(tmp_path, RECORDING_MANIFEST, {"finished": [
            {"id": "rec-1", "anchor_ts": NOW.isoformat()}]})


RESEARCH_MANIFEST = """\
schema: expectation-manifest/v1
process: prep-intake
instances:
  source: glob
  glob: "episodes/*/episode.json"
  id_from: parent_dir
steps:
  - id: researched-means-research
    deadline_minutes: 0
    expect:
      - kind: json_field
        file: "episodes/{id}/episode.json"
        pointer: "guests/0/research"
        non_empty: true
      - kind: artifact
        glob: "episodes/{id}/content/prep-doc.html"
"""


def test_mike_case_empty_research_with_stamped_stage_is_a_delta(tmp_path):
    ep = tmp_path / "episodes" / "2026-08-04-mike" / "content"
    ep.mkdir(parents=True)
    (ep / "prep-doc.html").write_text("doc", encoding="utf-8")
    (ep.parent / "episode.json").write_text(
        json.dumps({"stage": "prep-call-booked", "guests": [{"research": {}}]}),
        encoding="utf-8")
    manifest = em.load_manifest(write_manifest(tmp_path, RESEARCH_MANIFEST))
    receipt = em.reconcile(manifest, tmp_path, {}, NOW)
    assert receipt["counts"]["deltas"] == 1
    assert "research" in receipt["deltas"][0]["unmet"][0]


def test_real_research_passes(tmp_path):
    ep = tmp_path / "episodes" / "2026-08-04-good" / "content"
    ep.mkdir(parents=True)
    (ep / "prep-doc.html").write_text("doc", encoding="utf-8")
    (ep.parent / "episode.json").write_text(
        json.dumps({"guests": [{"research": {"bio": "real"}}]}), encoding="utf-8")
    manifest = em.load_manifest(write_manifest(tmp_path, RESEARCH_MANIFEST))
    receipt = em.reconcile(manifest, tmp_path, {}, NOW)
    assert receipt["counts"] == {"ok": 1, "pending": 0, "authorized_skips": 0, "deltas": 0}


def test_bad_schema_and_kinds_fail_closed(tmp_path):
    with pytest.raises(em.ManifestError, match="schema"):
        em.load_manifest(write_manifest(tmp_path, "schema: nope/v9\nprocess: x\n"))
    bad_kind = RECORDING_MANIFEST.replace("snapshot_member", "vibes")
    with pytest.raises(em.ManifestError, match="expect.kind"):
        em.load_manifest(write_manifest(tmp_path, bad_kind))


def test_cli_exit_codes(tmp_path, capsys):
    manifest_path = write_manifest(tmp_path, RECORDING_MANIFEST)
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap(60, [])), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    rc = em.main(["--manifest", str(manifest_path), "--root", str(tmp_path),
                  "--snapshots", str(snap_path), "--now", NOW.isoformat(),
                  "--receipt", str(receipt_path)])
    assert rc == 3  # RED: delta present must be a nonzero, alarming exit
    assert json.loads(receipt_path.read_text())["counts"]["deltas"] == 1
    snap_path.write_text(json.dumps(snap(60, ["rec-1"])), encoding="utf-8")
    assert em.main(["--manifest", str(manifest_path), "--root", str(tmp_path),
                    "--snapshots", str(snap_path), "--now", NOW.isoformat()]) == 0
    assert em.main(["--manifest", str(manifest_path), "--root", str(tmp_path),
                    "--now", NOW.isoformat()]) == 2  # snapshots absent: fail closed
