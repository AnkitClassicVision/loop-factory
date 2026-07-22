"""Regression tests for the v0.1 cross-model review findings (Codex + Gemini).

Each test pins a fix from the 2026-07-21 dual review: fail-closed model
attestation, real-time frequency windows, strict approval semantics, scaffold
input validation, graph impl containment, and applied capability confinement.
"""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LS = _load("lock_service", "kernel/lock_service.py")
R = _load("receipts", "kernel/receipts.py")
RB = _load("read_broker", "kernel/gateways/read_broker.py")
FREQ = _load("frequency", "kernel/gateways/frequency.py")
HIL = _load("human_in_the_loop", "factory/human_in_the_loop.py")
SC = _load("scaffold", "factory/scaffold.py")
G = _load("graphs", "factory/graphs.py")
LAUNCH = _load("launch", "factory/launch.py")


class _Clock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        self.t += 1.0
        return self.t


def _svc(tmp_path, clock=None):
    return LS.LockService(
        R.LocalSigner(key="test-key"),
        budget_ledger=tmp_path / "b.jsonl",
        freq_ledger=tmp_path / "f.jsonl",
        nonce_ledger=tmp_path / "n.jsonl",
        clock=clock or _Clock(),
    )


# --- Codex P0 #2: model receipts require a sanitation attestation ---------- #

def test_unsanitized_model_request_is_denied(tmp_path):
    ls = _svc(tmp_path)
    with pytest.raises(LS.model.GatewayDenied):
        ls.request_model("raw prompt with who knows what")
    # attested request succeeds and the receipt binds sanitized=True
    issued = ls.request_model("sanitized bundle", sanitized=True)
    assert issued["receipt"]


# --- Codex #11 / Gemini #2: frequency windows are DAYS, not seconds -------- #

def test_frequency_person_cap_spans_days_not_seconds(tmp_path):
    f = FREQ.FrequencyService(tmp_path / "f.jsonl")
    day = 86400.0
    for i in range(3):
        f.reserve_slot("p1", "o1", now=i * day)          # 3 touches over 3 days
    with pytest.raises(FREQ.FrequencyDenied):
        f.reserve_slot("p1", "o1", now=10 * day)          # 4th within 30 days
    # outside the 30-day window the slot frees up again
    assert f.reserve_slot("p1", "o1", now=40 * day)


def test_frequency_org_cap_spans_days(tmp_path):
    f = FREQ.FrequencyService(tmp_path / "f.jsonl")
    day = 86400.0
    f.reserve_slot("p1", "o1", now=0.0)
    with pytest.raises(FREQ.FrequencyDenied):
        f.reserve_slot("p2", "o1", now=3 * day)           # 2nd human in org <7d
    assert f.reserve_slot("p2", "o1", now=8 * day)        # ok after the window


# --- Gemini #1: raw-field deny list is casefolded --------------------------- #

def test_raw_field_deny_is_case_insensitive():
    with pytest.raises(RB.RawReadDenied):
        RB.read("contact", {"Transcript": "hello"}, ["Transcript"])


# --- Codex P0 #1: approval semantics ---------------------------------------- #

def _hil_queue(tmp_path, status="pending_approval"):
    q = tmp_path / "queue.jsonl"
    row = {"contact_id": "c1", "draft": "d", "status": status,
           "queued_at": "2026-07-21T09:00:00Z", "decision_id": "dep-1"}
    q.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return q


def test_prefix_verdict_is_not_an_approval(tmp_path):
    q = _hil_queue(tmp_path)
    out = HIL.apply(q, "dep-1", verdict="APPROVE_ALL")
    assert out["applied"] is False


def test_replayed_decision_is_a_noop(tmp_path):
    q = _hil_queue(tmp_path)
    assert HIL.apply(q, "dep-1", verdict="APPROVE")["applied"] is True
    replay = HIL.apply(q, "dep-1", verdict="APPROVE")
    assert replay["applied"] is False


def test_hook_failure_is_recorded_never_silent(tmp_path):
    q = _hil_queue(tmp_path)

    def boom(_):
        raise RuntimeError("connector down")

    out = HIL.apply(q, "dep-1", verdict="APPROVE", on_approved=boom)
    assert out["status"] == "approved_hook_failed"
    saved = json.loads(q.read_text(encoding="utf-8").splitlines()[0])
    assert saved["status"] == "approved_hook_failed"
    assert "connector down" in saved["hook"]


# --- Codex #18: scaffold input validation ----------------------------------- #

def test_scaffold_rejects_hostile_names(tmp_path):
    for bad in ("../escape", "a b", "Name'; rm -rf /", "UPPER", ""):
        with pytest.raises(ValueError):
            SC.scaffold_department(bad, root=tmp_path)


def test_scaffold_refuses_to_overwrite_a_charter(tmp_path):
    SC.scaffold_department("demo", root=tmp_path)
    with pytest.raises(FileExistsError):
        SC.scaffold_department("demo", root=tmp_path)


def test_scaffold_persists_registry_partition(tmp_path):
    (tmp_path / "estate" / "registry.d").mkdir(parents=True)
    out = SC.scaffold_department("demo", root=tmp_path)
    reg = tmp_path / "estate" / "registry.d" / "demo.yaml"
    assert out["registry_file"] == str(reg)
    assert "id: \"demo\"" in reg.read_text(encoding="utf-8") or "id: demo" in reg.read_text(encoding="utf-8")


# --- Codex #8: impl paths cannot escape the department ----------------------- #

def test_impl_path_escape_fails_traceability(tmp_path):
    dept = tmp_path / "departments" / "t"
    (dept / "runtime").mkdir(parents=True)
    data = {"subgraphs": [{"id": "S", "concept_refs": ["C1"],
                           "nodes": [{"id": "N1", "impl": "../../../etc/passwd"}]}]}
    (dept / "subgraphs.json").write_text(json.dumps(data), encoding="utf-8")
    fails = G.check_traceability(dept)
    assert any("escapes the department directory" in f for f in fails)


# --- Codex #17: capability confinement is actually applied ------------------- #

def test_launch_env_is_credential_free():
    dirty = {"PATH": "/usr/bin", "HOME": "/home/x", "AWS_SECRET_ACCESS_KEY": "z",
             "OE_KERNEL_SIGNING_KEY": "sign", "HUBSPOT_TOKEN": "t"}
    env = LAUNCH.build_env(dirty)
    assert env["PATH"] == "/usr/bin"
    for leaked in ("AWS_SECRET_ACCESS_KEY", "OE_KERNEL_SIGNING_KEY", "HUBSPOT_TOKEN"):
        assert leaked not in env
    assert env["OE_KERNEL_ONLY"] == "1" and env["PLACEHOLDER_MODE"] == "1"


# --- kernel/bridge fails closed without a signing key ------------------------ #

def test_bridge_fails_closed_without_signing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OE_KERNEL_SIGNING_KEY", raising=False)
    BR = _load("bridge", "kernel/bridge.py")
    with pytest.raises(ValueError):
        BR.load_kernel(tmp_path)
