"""Self-contained tests for the podcast runtime kernel bridge.

Pins the three guarantees the bridge exists to hold:
  * get_kernel wires load_kernel with the charter's weekly budget ceilings.
  * require_shadow refuses a live request while the charter is still 'shadow'
    (belt-and-suspenders for the shadow-first rule, AGENTS.md #1).
  * podcast_daily.sh stays shadow-only (valid bash syntax, no live flag).
"""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from departments.podcast.runtime import heal_apply

ROOT = Path(__file__).resolve().parents[3]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KB = _load("podcast_kernel_bridge",
           "departments/podcast/runtime/kernel_bridge.py")
SCRIPT = ROOT / "departments" / "podcast" / "runtime" / "podcast_daily.sh"


def _synth_charter(autonomy_state="shadow", ceilings=None):
    """A charter-shaped dict sufficient for charter_loader.thresholds() and
    autonomy_state() without touching disk."""
    return {
        "department": "podcast",
        "owner": "podcast-owner",
        "autonomy_state": autonomy_state,
        "immutable_safety_invariants": {"heal_may_not_modify": ["autonomy_state"]},
        "budget": {"weekly_ceilings": ceilings or {
            "model_calls": 7, "dollars": 3, "worker_minutes": 9}},
    }


def _fake_bridge(monkeypatch, captured, sentinel):
    """Install a fake bridge module so load_kernel captures its kwargs."""
    def fake_load_kernel(state_dir, *, signer=None, budget_ceilings=None):
        captured["state_dir"] = state_dir
        captured["budget_ceilings"] = budget_ceilings
        captured["signer"] = signer
        return sentinel
    monkeypatch.setattr(KB, "_bridge",
                        lambda: SimpleNamespace(load_kernel=fake_load_kernel))


# --------------------------------------------------------------------------- #
# get_kernel wires the charter's weekly ceilings into load_kernel
# --------------------------------------------------------------------------- #

def test_get_kernel_passes_charter_ceilings_to_load_kernel(tmp_path, monkeypatch):
    ceilings = {"model_calls": 5, "dollars": 2, "worker_minutes": 11}
    monkeypatch.setattr(KB, "_load_charter",
                        lambda: _synth_charter(ceilings=ceilings))

    captured = {}
    sentinel = object()
    _fake_bridge(monkeypatch, captured, sentinel)

    got = KB.get_kernel(tmp_path)

    assert got is sentinel
    assert captured["state_dir"] == tmp_path          # state_dir forwarded
    assert captured["signer"] is None                  # default; trusted ctx supplies it
    assert captured["budget_ceilings"] is not None     # never an open-ended budget
    # charter weekly ceilings flow straight through (charter wins over defaults)
    assert captured["budget_ceilings"] == ceilings


def test_get_kernel_defaults_fill_missing_charter_ceilings(tmp_path, monkeypatch):
    # a charter that sets only one ceiling: factory defaults fill the rest
    monkeypatch.setattr(KB, "_load_charter",
                        lambda: _synth_charter(ceilings={"dollars": 2}))
    captured = {}
    _fake_bridge(monkeypatch, captured, sentinel="kernel")
    KB.get_kernel(tmp_path)
    assert captured["budget_ceilings"]["dollars"] == 2          # charter wins
    assert "model_calls" in captured["budget_ceilings"]         # default filled


# --------------------------------------------------------------------------- #
# require_shadow
# --------------------------------------------------------------------------- #

def test_require_shadow_raises_when_charter_shadow_and_live(monkeypatch):
    monkeypatch.setattr(KB, "_load_charter",
                        lambda: _synth_charter(autonomy_state="shadow"))
    with pytest.raises(RuntimeError):
        KB.require_shadow(live=True)


def test_require_shadow_noop_when_not_live(monkeypatch):
    # no live request -> never raises, and must not even load the charter
    monkeypatch.setattr(KB, "_load_charter",
                        lambda: pytest.fail("charter must not load when not live"))
    KB.require_shadow(live=False)
    KB.require_shadow()  # default is also a safe no-op


def test_require_shadow_allows_live_once_promoted(monkeypatch):
    monkeypatch.setattr(KB, "_load_charter",
                        lambda: _synth_charter(autonomy_state="gated_live"))
    # not shadow anymore -> a live request is permitted (no raise)
    KB.require_shadow(live=True)


def test_live_heal_path_refuses_shadow_charter_before_execution(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    incident = {
        "fingerprint": "fp-live-shadow",
        "failure_class": "timer_failed",
        "state": "open",
        "evidence": ["systemd://podcast.timer"],
    }
    (state_dir / "incidents.json").write_text(
        json.dumps({incident["fingerprint"]: incident}), encoding="utf-8"
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("shadow charter must refuse before subprocess execution")

    monkeypatch.setattr(heal_apply.subprocess, "run", forbidden)
    receipt = heal_apply.apply_heal(
        state_dir,
        incident["fingerprint"],
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
    )

    assert receipt["result"] == "refused"
    assert "autonomy_state is 'shadow'" in receipt["detail"]
    assert not (state_dir / "heal_attempts.json").exists()


# --------------------------------------------------------------------------- #
# podcast_daily.sh stays shadow-only
# --------------------------------------------------------------------------- #

def test_podcast_daily_sh_is_syntactically_valid_and_shadow_only():
    text = SCRIPT.read_text(encoding="utf-8")
    # shadow-only: the live flag must never appear, even in comments
    assert "--live" not in text
    # valid bash syntax (parse-only, no execution)
    rc = subprocess.run(["bash", "-n", str(SCRIPT)]).returncode
    assert rc == 0, "podcast_daily.sh failed bash -n syntax check"
