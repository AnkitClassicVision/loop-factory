from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _counts(count: int = 0) -> dict[str, int]:
    return {"linkedin_mybcat": count}


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


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
        surface_counts=_counts(),
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
            surface_counts=_counts(),
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
            surface_counts=_counts(),
        )

    state_kill = tmp_path / "kill-state"
    token_kill = kernel_bridge.request_dispatch_token(state_kill, draft)
    (state_kill / "KILLED").write_text("{}", encoding="utf-8")
    with pytest.raises(dispatch.DispatchBlocked, match="KILLED"):
        dispatch.dispatch(
            state_kill, draft, _qa(), token_kill, surface_counts=_counts()
        )

    state_breaker = tmp_path / "breaker-state"
    token_breaker = kernel_bridge.request_dispatch_token(state_breaker, draft)
    (state_breaker / "BREAKER_linkedin_mybcat").write_text("{}", encoding="utf-8")
    with pytest.raises(dispatch.DispatchBlocked, match="circuit breaker"):
        dispatch.dispatch(
            state_breaker, draft, _qa(), token_breaker, surface_counts=_counts()
        )


def test_require_shadow_refuses_draft_only_live(monkeypatch):
    monkeypatch.setattr(kernel_bridge, "autonomy_state", lambda: "draft_only")

    with pytest.raises(RuntimeError, match="draft_only"):
        kernel_bridge.require_shadow(live=True)


def test_all_author_surface_cap_yields_without_zernio_attempt(tmp_path):
    state = tmp_path / "state"
    marker = tmp_path / "zernio-called"
    fake = _fake_command(tmp_path, {"id": "must-not-run"}, marker)
    draft_path = _write_json(tmp_path / "draft.json", _draft())
    qa_path = _write_json(tmp_path / "qa.json", _qa())
    token_path = _write_json(
        tmp_path / "token.json", {"receipt": "unused-at-cap", "slot": "unused-at-cap"}
    )
    counts_path = _write_json(tmp_path / "counts.json", _counts(5))
    out = tmp_path / "dispatch.json"

    rc = dispatch.main(
        [
            "--state-dir",
            str(state),
            "--draft",
            str(draft_path),
            "--qa-report",
            str(qa_path),
            "--token",
            str(token_path),
            "--surface-counts",
            str(counts_path),
            "--zernio-cmd",
            str(fake),
            "--out",
            str(out),
        ]
    )

    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0
    assert receipt["status"] == "yielded"
    assert receipt["surface_count"] == 5
    assert receipt["cap"] == kernel_bridge.surface_daily_cap("linkedin_mybcat") == 5
    assert kernel_bridge.surface_daily_cap("x_mybcat") == 8
    assert receipt["delivered_count"] == 0
    assert not marker.exists()
    assert not (state / "kernel" / "dispatch_sink.jsonl").exists()


def test_shadow_cli_missing_surface_counts_refuses_and_writes_receipt(tmp_path):
    marker = tmp_path / "zernio-called"
    fake = _fake_command(tmp_path, {"id": "must-not-run"}, marker)
    out = tmp_path / "dispatch.json"

    rc = dispatch.main(
        [
            "--state-dir",
            str(tmp_path / "state"),
            "--draft",
            str(_write_json(tmp_path / "draft.json", _draft())),
            "--qa-report",
            str(_write_json(tmp_path / "qa.json", _qa())),
            "--token",
            str(
                _write_json(
                    tmp_path / "token.json",
                    {"receipt": "unused", "slot": "unused"},
                )
            ),
            "--zernio-cmd",
            str(fake),
            "--out",
            str(out),
        ]
    )

    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 2
    assert receipt["status"] == "blocked"
    assert "surface_counts is required" in receipt["reason"]
    assert not marker.exists()


def test_marker_created_after_token_mint_blocks_same_dispatch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "obviously-fake-test-key")
    state = tmp_path / "state"
    draft = _draft()
    token = kernel_bridge.request_dispatch_token(state, draft)
    marker = state / "BREAKER_linkedin_mybcat"
    gateway_called = False

    class FakeKernel:
        def send(self, *args, **kwargs):
            nonlocal gateway_called
            gateway_called = True
            return {"mode": "shadow", "delivered": False}

    def trip_before_gateway(_state_dir):
        marker.write_text("{}", encoding="utf-8")
        return FakeKernel()

    monkeypatch.setattr(kernel_bridge, "get_kernel", trip_before_gateway)

    with pytest.raises(dispatch.DispatchBlocked, match="circuit breaker"):
        dispatch.dispatch(
            state,
            draft,
            _qa(),
            token,
            surface_counts=_counts(),
        )

    assert marker.exists()
    assert gateway_called is False


def _fake_daily_python(
    tmp_path: Path,
    observations: Path,
    *,
    mode: str,
) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "python3"
    wrapper.write_text(
        f"""#!{sys.executable}
import json
import subprocess
import sys
from pathlib import Path

REAL = {sys.executable!r}
OBSERVATIONS = Path({str(observations)!r})
MODE = {mode!r}
args = sys.argv[1:]
target = Path(args[0]).name if args and args[0] != "-" else "-"

run_real_guard = (
    target == "guards.py"
    and len(args) > 1
    and args[1] in {{"kill", "breaker"}}
)
if target == "-" or run_real_guard:
    completed = subprocess.run([REAL, *args], check=False)
    if target == "guards.py" and MODE == "corrupt_receipt":
        out = Path(args[args.index("--out") + 1])
        if out.name == "S6-kill.json":
            out.write_text("{{corrupt", encoding="utf-8")
    raise SystemExit(completed.returncode)

out = Path(args[args.index("--out") + 1])
payload = (
    {{"pass": True, "defects": [], "engine": "fake"}}
    if target == "qa_post.py"
    else {{"status": "ok", "node": target}}
)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload) + "\\n", encoding="utf-8")

if target == "select_candidate.py" and MODE == "trip_kill":
    row = {{
        "metric": "platform_strike",
        "value": True,
        "surface": "linkedin_mybcat",
        "source": "platform_sensor",
        "ts": "2026-07-28T12:00:00+00:00",
    }}
    with OBSERVATIONS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\\n")
if target == "select_candidate.py" and MODE == "trip_breaker":
    with OBSERVATIONS.open("a", encoding="utf-8") as handle:
        for minute in range(3):
            row = {{
                "metric": "delivery_failure",
                "value": True,
                "surface": "linkedin_mybcat",
                "source": "zernio_status_sensor",
                "ts": f"2026-07-28T12:0{{minute}}:00+00:00",
            }}
            handle.write(json.dumps(row) + "\\n")
if target == "dispatch.py":
    Path({str(tmp_path / "dispatch-invoked")!r}).write_text("called", encoding="utf-8")
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return fake_bin


def _run_daily(
    tmp_path: Path, monkeypatch, *, mode: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    state = tmp_path / "state"
    state.mkdir()
    observations = state / "observations.jsonl"
    observations.write_text("", encoding="utf-8")
    _write_json(state / "surface_counts.json", _counts())
    fake_bin = _fake_daily_python(tmp_path, observations, mode=mode)
    monkeypatch.setenv("SOCIAL_STATE_DIR", str(state))
    monkeypatch.setenv("SOCIAL_OBSERVATIONS", str(observations))
    monkeypatch.setenv(
        "PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
    )
    script = Path(__file__).parents[1] / "runtime" / "social_daily.sh"
    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, state


@pytest.mark.parametrize(
    ("mode", "marker_name", "guard_receipt"),
    [
        ("trip_kill", "KILLED", "S6-kill-pre-dispatch.json"),
        (
            "trip_breaker",
            "BREAKER_linkedin_mybcat",
            "S7-breaker-pre-dispatch.json",
        ),
    ],
)
def test_mid_run_observation_trips_pre_dispatch_guard_and_blocks_that_run(
    tmp_path, monkeypatch, mode, marker_name, guard_receipt
):
    completed, state = _run_daily(tmp_path, monkeypatch, mode=mode)
    run_dir = next((state / "receipts").iterdir())

    assert completed.returncode == 2
    assert (state / marker_name).exists()
    assert (run_dir / guard_receipt).exists()
    assert not (run_dir / "N6-dispatch.json").exists()
    assert not (tmp_path / "dispatch-invoked").exists()


def test_daily_chain_stops_on_corrupt_receipt_and_records_incident(
    tmp_path, monkeypatch
):
    completed, state = _run_daily(tmp_path, monkeypatch, mode="corrupt_receipt")
    run_dir = next((state / "receipts").iterdir())
    incidents = json.loads(
        (state / "incident_candidates.json").read_text(encoding="utf-8")
    )

    assert completed.returncode == 2
    assert "invalid receipt: S6-kill" in completed.stderr
    assert incidents[-1]["failure_class"] == "invalid_receipt"
    assert incidents[-1]["subject"] == "S6-kill"
    assert (run_dir / "S6-kill.json").exists()
    assert not (run_dir / "S7-breaker.json").exists()
    assert not (run_dir / "N1-inventory.json").exists()


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
