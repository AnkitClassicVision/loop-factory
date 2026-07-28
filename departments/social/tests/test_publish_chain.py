from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from departments.social.runtime import delivery_verify
from departments.social.runtime import dispatch
from departments.social.runtime import kernel_bridge


def _draft() -> dict:
    return {
        "surface": "linkedin_mybcat",
        "body": "A durable operating lesson with a sourced claim.",
        "cta_url": "https://example.invalid/book",
        "sources": [
            {
                "claim": "The source contains this operating lesson.",
                "source": "https://example.invalid/source",
            }
        ],
        "engine": "codex_oauth",
        "round": 1,
    }


def _qa() -> dict:
    return {"pass": True, "defects": [], "engine": "claude_subscription"}


def _fake_command(tmp_path: Path, payload: dict, marker: Path) -> Path:
    command = tmp_path / "fake-zernio"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('called', encoding='utf-8')\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return command


def test_shadow_dispatch_uses_kernel_simulates_zero_and_never_calls_zernio(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "obviously-fake-test-key")
    state = tmp_path / "state"
    draft = _draft()
    token = kernel_bridge.request_dispatch_token(state, draft)
    marker = tmp_path / "zernio-called"
    fake = _fake_command(tmp_path, {"id": "should-not-run"}, marker)

    receipt = dispatch.dispatch(
        state,
        draft,
        _qa(),
        token,
        zernio_cmd=str(fake),
    )

    assert receipt["simulated"] is True
    assert receipt["delivered_count"] == 0
    assert not marker.exists()
    assert (state / "kernel" / "dispatch_sink.jsonl").exists()
    verification = delivery_verify.verify(state, receipt, zernio_cmd=str(fake))
    assert verification["verified"] is True
    assert verification["platform_post_id"] != receipt["post_ref"]
    assert not marker.exists()


def test_shadow_refuses_explicit_live_even_with_promoted_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "obviously-fake-test-key")
    state = tmp_path / "state"
    draft = _draft()
    token = kernel_bridge.request_dispatch_token(state, draft)
    marker = tmp_path / "zernio-called"
    fake = _fake_command(tmp_path, {"id": "must-not-run"}, marker)

    with pytest.raises(dispatch.DispatchBlocked, match="shadow charter"):
        dispatch.dispatch(
            state,
            draft,
            _qa(),
            token,
            promoted_flag=True,
            delivery_mode="live",
            zernio_cmd=str(fake),
        )

    assert not marker.exists()


def test_dispatch_refuses_failed_qa_kill_and_surface_breaker(tmp_path, monkeypatch):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "obviously-fake-test-key")
    draft = _draft()

    state_qa = tmp_path / "qa-state"
    token_qa = kernel_bridge.request_dispatch_token(state_qa, draft)
    with pytest.raises(dispatch.DispatchBlocked, match="did not pass"):
        dispatch.dispatch(
            state_qa,
            draft,
            {"pass": False, "defects": [{"code": "voice", "detail": "flat"}], "engine": "qa"},
            token_qa,
        )

    state_kill = tmp_path / "kill-state"
    token_kill = kernel_bridge.request_dispatch_token(state_kill, draft)
    (state_kill / "KILLED").write_text("{}", encoding="utf-8")
    with pytest.raises(dispatch.DispatchBlocked, match="KILLED"):
        dispatch.dispatch(state_kill, draft, _qa(), token_kill)

    state_breaker = tmp_path / "breaker-state"
    token_breaker = kernel_bridge.request_dispatch_token(state_breaker, draft)
    (state_breaker / "BREAKER_linkedin_mybcat").write_text("{}", encoding="utf-8")
    with pytest.raises(dispatch.DispatchBlocked, match="circuit breaker"):
        dispatch.dispatch(state_breaker, draft, _qa(), token_breaker)


def test_delivery_verify_rejects_scheduler_echo_and_accepts_platform_id(tmp_path):
    receipt = {
        "post_ref": "scheduler-ref-1",
        "surface": "linkedin_mybcat",
        "delivered_count": 1,
        "simulated": False,
        "ts": "2026-07-28T12:00:00+00:00",
    }
    marker = tmp_path / "zernio-called"
    liar = _fake_command(
        tmp_path,
        {
            "post": {
                "id": "scheduler-ref-1",
                "platformPostId": "scheduler-ref-1",
                "status": "published",
            }
        },
        marker,
    )
    with pytest.raises(delivery_verify.VerificationBlocked, match="scheduler echo"):
        delivery_verify.verify(tmp_path, receipt, zernio_cmd=str(liar))
    assert marker.exists()

    marker.unlink()
    confirmer = _fake_command(
        tmp_path,
        {
            "post": {
                "id": "scheduler-ref-1",
                "platformPostId": "platform-confirmed-9",
                "status": "published",
            }
        },
        marker,
    )
    verified = delivery_verify.verify(tmp_path, receipt, zernio_cmd=str(confirmer))
    assert verified == {
        "post_ref": "scheduler-ref-1",
        "platform_post_id": "platform-confirmed-9",
        "status": "published",
        "verified": True,
        "ts": verified["ts"],
    }


def test_simulate_verifier_rejects_lying_sink_echo(tmp_path):
    receipt = {
        "post_ref": "sim-dispatch-1",
        "surface": "linkedin_mybcat",
        "delivered_count": 0,
        "simulated": True,
        "ts": "2026-07-28T12:00:00+00:00",
    }
    sink = tmp_path / "simulate.jsonl"
    sink.write_text(
        json.dumps(
            {
                "post_ref": "sim-dispatch-1",
                "platform_post_id": "sim-dispatch-1",
                "surface": "linkedin_mybcat",
                "status": "simulated",
                "source": "simulate_sink",
                "ts": "2026-07-28T12:00:01+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(delivery_verify.VerificationBlocked, match="scheduler echo"):
        delivery_verify.verify(tmp_path, receipt, simulate_sink=sink)
