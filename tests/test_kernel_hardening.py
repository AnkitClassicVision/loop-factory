"""Regression tests for the GLM adversarial review's P0/P1 findings.

Each test is a failing scenario the reviewer described; they must all pass after
the hardening. Source: kernel-v1 GLM security review, 2026-07-21.
"""
import importlib.util
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
CAP = _load("capabilities", "kernel/capabilities.py")
RB = _load("read_broker", "kernel/gateways/read_broker.py")


class _Clock:
    """A controllable server clock injected at construction (never per-call)."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _svc(tmp_path, clock=None):
    return LS.LockService(
        R.LocalSigner(key="k"),
        budget_ledger=tmp_path / "b.jsonl",
        freq_ledger=tmp_path / "f.jsonl",
        nonce_ledger=tmp_path / "n.jsonl",
        clock=clock or _Clock(),
    )


# --- P0 #1: caller cannot control the clock ------------------------------- #

def test_p0_1_expiration_uses_server_clock(tmp_path):
    clock = _Clock(1000.0)
    ls = _svc(tmp_path, clock=clock)
    issued = ls.request_send("x@y.co", "subj", "body", person="p1", org="o1")
    # advance the SERVER clock past the ttl; the department has no now knob
    clock.t = 1000.0 + LS.MAX_TTL_S + 10
    with pytest.raises(LS.dispatch.GatewayDenied):
        ls.send("x@y.co", "subj", "body", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s.jsonl")


# --- P0 #2: revocation is enforced at use --------------------------------- #

def test_p0_2_revoked_receipt_is_denied(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "subj", "body", person="p1", org="o1")
    ls.revoke(issued["nonce"])
    with pytest.raises(LS.dispatch.GatewayDenied):
        ls.send("x@y.co", "subj", "body", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s.jsonl")


# --- P0 #3: the frequency gate is enforced inside issuance ---------------- #

def test_p0_3_frequency_enforced_in_send_issuance(tmp_path):
    ls = _svc(tmp_path)
    for _ in range(3):
        ls.request_send("x@y.co", "s", "b", person="p1", org="o1")
    with pytest.raises(LS.freq_mod.FrequencyDenied):
        ls.request_send("x@y.co", "s", "b", person="p1", org="o1")  # 4th in window


def test_p0_3_budget_enforced_in_model_issuance(tmp_path):
    ls = LS.LockService(
        R.LocalSigner(key="k"),
        budget_ceilings={"model_calls": 2},  # tiny ceiling; 80% review flag at >=1.6
        budget_ledger=tmp_path / "b.jsonl",
        freq_ledger=tmp_path / "f.jsonl",
        nonce_ledger=tmp_path / "n.jsonl",
        clock=_Clock(),
    )
    ls.request_model("p1", sanitized=True)  # 1 model_call reserved, under ceiling
    # the next reservation crosses the ceiling/review threshold -> no receipt
    with pytest.raises((LS.budget_mod.BudgetExceeded, LS.budget_mod.BudgetReviewRequired)):
        ls.request_model("p2", sanitized=True)


# --- P0 #4: single-use + revocation survive a restart --------------------- #

def test_p0_4_consumed_nonce_durable_across_restart(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "subj", "body", person="p1", org="o1")
    ls.send("x@y.co", "subj", "body", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s.jsonl")
    # simulate a process restart: a fresh service over the SAME ledgers
    ls2 = _svc(tmp_path)
    with pytest.raises(LS.dispatch.GatewayDenied):  # already consumed -> replay denied
        ls2.send("x@y.co", "subj", "body", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s2.jsonl")


def test_p0_4_revocation_durable_across_restart(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "subj", "body", person="p1", org="o1")
    ls.revoke(issued["nonce"])
    ls2 = _svc(tmp_path)  # restart
    with pytest.raises(LS.dispatch.GatewayDenied):
        ls2.send("x@y.co", "subj", "body", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s.jsonl")


# --- P1-N1: durable ledger is crash-safe (fsync + torn-line tolerance) ---- #

def test_p1n1_torn_ledger_line_does_not_brick_startup(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "s", "b", person="p1", org="o1")
    ls.send("x@y.co", "s", "b", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s.jsonl")
    # append a torn/partial line (a crashed write) to the consumed ledger
    consumed = tmp_path / "n.consumed.jsonl"
    with consumed.open("a", encoding="utf-8") as fh:
        fh.write('{"nonce": "torn')  # no closing brace, no newline
    # a fresh service over the torn ledger must still construct (fail-safe),
    # and the cleanly-consumed nonce is still remembered (replay still denied)
    ls2 = _svc(tmp_path)
    with pytest.raises(LS.dispatch.GatewayDenied):
        ls2.send("x@y.co", "s", "b", issued["receipt"], slot=issued["slot"], sink=tmp_path / "s2.jsonl")


# --- P1 #5: credential env is an allowlist -------------------------------- #

def test_p1_5_department_env_is_allowlist(tmp_path):
    dirty = {"PATH": "/usr/bin", "HOME": "/home/dep", "FOO_BEARER": "x",
             "SESSION_ID": "y", "RANDOM_VAR": "z"}
    clean = CAP.department_env(dirty)
    assert clean["PATH"] == "/usr/bin" and clean["HOME"] == "/home/dep"
    # allowlist: anything not explicitly allowed is dropped, not just known creds
    for dropped in ("FOO_BEARER", "SESSION_ID", "RANDOM_VAR"):
        assert dropped not in clean


# --- P1 #6: ttl is capped server-side ------------------------------------- #

def test_p1_6_ttl_capped(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "s", "b", person="p1", org="o1", ttl_s=10**18)
    # the effective exp cannot exceed clock + MAX_TTL_S
    # decode the receipt payload to check exp
    import base64, json
    payload_b64 = issued["receipt"].rsplit(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert payload["exp"] <= ls._now() + LS.MAX_TTL_S + 0.001


# --- P1 #7: subject is bound ---------------------------------------------- #

def test_p1_7_subject_is_bound(tmp_path):
    ls = _svc(tmp_path)
    issued = ls.request_send("x@y.co", "Re: your appointment", "body", person="p1", org="o1")
    # swapping the subject after issuance must be denied
    with pytest.raises(LS.dispatch.GatewayDenied):
        ls.send("x@y.co", "URGENT verify your account", "body", issued["receipt"],
                slot=issued["slot"], sink=tmp_path / "s.jsonl")


# --- P1 #8: non-string values are redacted/quarantined -------------------- #

def test_p1_8_non_string_values_not_returned_raw():
    record = {"custom": [{"note": "patient x@y.co called 615-555-1212"}]}
    out = RB.read("contact", record, ["custom"])
    assert out["custom"] == {"_quarantined": True}
