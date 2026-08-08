from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "outbox_push.py"


def _sender(
    tmp_path: Path,
    name: str,
    *,
    output: str = "",
    exit_code: int = 0,
) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"p = pathlib.Path({str(tmp_path / (name + '.jsonl'))!r})\n"
        "with p.open('a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write({output!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def _config(
    tmp_path: Path,
    watch: Path,
    ping: Path,
    card: Path,
    *,
    buzz: Path | None = None,
    card_enabled: bool = True,
    ledger_file: Path | None = None,
) -> Path:
    senders = {
        "ping": [sys.executable, str(ping), "{text}"],
        "card": [sys.executable, str(card), "{title}", "{body}"],
        "card_enabled": card_enabled,
    }
    if buzz is not None:
        senders["buzz"] = [
            sys.executable,
            str(buzz),
            "{card}",
            "{department}",
            "{kind}",
            "{text}",
        ]
    value = {
        "cursor_file": str(tmp_path / "cursor.json"),
        "watches": [
            {"path": str(watch), "department": "buzz-dept", "kind": "escalation"}
        ],
        "senders": senders,
    }
    if ledger_file is not None:
        value["ledger_file"] = str(ledger_file)
    elif buzz is not None:
        value["ledger_file"] = str(tmp_path / "ledger.jsonl")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _run(config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--once"],
        text=True,
        capture_output=True,
        check=False,
    )


def _calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ordered_sender(tmp_path: Path, name: str, event: str, *, output: str = "", exit_code: int = 0) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(tmp_path / 'events.txt')!r})\n"
        f"with p.open('a') as handle: handle.write({event!r} + '\\n')\n"
        f"sys.stdout.write({output!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("invalid_buzz", [[], [""], "not-an-argv-list"])
def test_buzz_is_optional_but_validated_when_present(tmp_path, invalid_buzz):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Optional buzz"}) + "\n", encoding="utf-8")
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "card", output='{"identifier":"ANK-1"}\n')
    config = _config(tmp_path, watch, ping, card)

    assert _run(config).returncode == 0

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["senders"]["buzz"] = invalid_buzz
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = _run(config)
    assert result.returncode == 2
    assert "senders.buzz" in result.stderr


def test_buzz_runs_with_card_and_existing_placeholders(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"question": "Owner decision?"}) + "\n", encoding="utf-8")
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "card", output='created\n{"identifier":"ANK-42"}\n')
    buzz = _sender(tmp_path, "buzz")
    ledger = tmp_path / "ledger.jsonl"

    result = _run(_config(tmp_path, watch, ping, card, buzz=buzz, ledger_file=ledger))

    assert result.returncode == 0
    assert _calls(tmp_path / "buzz.jsonl") == [
        [
            "ANK-42",
            "buzz-dept",
            "escalation",
            "Department: buzz-dept\nKind: escalation\nOwner decision?",
        ]
    ]
    assert (tmp_path / "card.jsonl").stat().st_mtime_ns <= (
        tmp_path / "buzz.jsonl"
    ).stat().st_mtime_ns


@pytest.mark.parametrize("case", ["card-failed", "no-identifier", "card-disabled"])
def test_buzz_does_not_run_without_a_successful_durable_card(tmp_path, case):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": case}) + "\n", encoding="utf-8")
    ping = _sender(tmp_path, "ping")
    output = '{"url":"https://example.test/no-id"}\n'
    exit_code = 1 if case == "card-failed" else 0
    if case == "card-failed":
        output = '{"identifier":"ANK-NOT-CREATED"}\n'
    card = _sender(tmp_path, "card", output=output, exit_code=exit_code)
    buzz = _sender(tmp_path, "buzz")

    result = _run(
        _config(
            tmp_path,
            watch,
            ping,
            card,
            buzz=buzz,
            card_enabled=case != "card-disabled",
        )
    )

    assert _calls(tmp_path / "buzz.jsonl") == []
    if case == "card-failed":
        # 2026-08-05 contract: an undelivered card is never consumed — the row
        # stays for the next tick's in-order retry and the run exits 3 loudly.
        assert result.returncode == 3
        cursor = tmp_path / "cursor.json"
        state = (
            json.loads(cursor.read_text(encoding="utf-8")).get(str(watch))
            if cursor.exists()
            else None
        )
        assert not state or state["offset_lines"] == 0
    elif case == "no-identifier":
        assert result.returncode == 3
        cursor = tmp_path / "cursor.json"
        assert not cursor.exists() or json.loads(cursor.read_text(encoding="utf-8"))[
            str(watch)
        ]["offset_lines"] == 0
    else:
        assert result.returncode == 0
        assert json.loads((tmp_path / "cursor.json").read_text(encoding="utf-8"))[
            str(watch)
        ]["offset_lines"] == 1


def test_buzz_failure_warns_but_does_not_fail_or_stop_tick(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(
        json.dumps({"eli5": "first"}) + "\n" + json.dumps({"eli5": "second"}) + "\n",
        encoding="utf-8",
    )
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "card", output='{"identifier":"ANK-7"}\n')
    buzz = _sender(tmp_path, "buzz", exit_code=7)
    ledger = tmp_path / "ledger.jsonl"

    result = _run(_config(tmp_path, watch, ping, card, buzz=buzz, ledger_file=ledger))

    assert result.returncode == 0
    assert "warning" in result.stderr.lower()
    assert "buzz" in result.stderr.lower()
    assert _calls(tmp_path / "ping.jsonl") == []
    assert len(_calls(tmp_path / "card.jsonl")) == 2
    assert len(_calls(tmp_path / "buzz.jsonl")) == 2
    assert json.loads((tmp_path / "cursor.json").read_text(encoding="utf-8"))[
        str(watch)
    ]["offset_lines"] == 2


def test_last_card_json_object_is_shared_by_buzz_and_ledger(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Use latest card"}) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _sender(
        tmp_path,
        "card",
        output=(
            '{"identifier":"ANK-OLD","url":"https://example.test/old"}\n'
            'status\n{"identifier":"ANK-NEW","url":"https://example.test/new"}\n'
        ),
    )
    buzz = _sender(tmp_path, "buzz")

    result = _run(
        _config(tmp_path, watch, ping, card, buzz=buzz, ledger_file=ledger)
    )

    assert result.returncode == 0
    assert _calls(tmp_path / "buzz.jsonl")[0][0] == "ANK-NEW"
    ledger_row = json.loads(ledger.read_text(encoding="utf-8"))
    assert ledger_row["card_identifier"] == "ANK-NEW"
    assert ledger_row["card_url"] == "https://example.test/new"


def test_bound_path_creates_card_and_ledger_before_buzz_without_ping(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Bound delivery"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "broken_ping", exit_code=9)
    card = _ordered_sender(
        tmp_path, "ordered_card", "card", output='{"identifier":"ANK-88"}\n'
    )
    buzz = _ordered_sender(tmp_path, "ordered_buzz", "buzz")

    result = _run(_config(tmp_path, watch, ping, card, buzz=buzz, ledger_file=ledger))

    assert result.returncode == 0
    assert not (tmp_path / "broken_ping.jsonl").exists()
    assert (tmp_path / "events.txt").read_text().splitlines() == ["card", "buzz"]
    assert json.loads(ledger.read_text())["card_identifier"] == "ANK-88"


@pytest.mark.parametrize("output", ["", "{}\n", '{"identifier":""}\n', '{"identifier":"   "}\n'])
def test_bound_path_fails_closed_without_usable_card_identifier(tmp_path, output):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Needs a card"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "unusable_card", output=output)
    buzz = _sender(tmp_path, "buzz")

    result = _run(_config(tmp_path, watch, ping, card, buzz=buzz, ledger_file=ledger))

    assert result.returncode == 3
    assert _calls(tmp_path / "ping.jsonl") == []
    assert _calls(tmp_path / "buzz.jsonl") == []
    assert not ledger.exists()
    cursor = tmp_path / "cursor.json"
    assert not cursor.exists() or json.loads(cursor.read_text())[str(watch)]["offset_lines"] == 0


def test_bound_card_failure_emits_neither_ping_nor_buzz(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Card must exist"}) + "\n")
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "failed_card", exit_code=1)
    buzz = _sender(tmp_path, "buzz")

    result = _run(_config(tmp_path, watch, ping, card, buzz=buzz))

    assert result.returncode == 3
    assert _calls(tmp_path / "ping.jsonl") == []
    assert _calls(tmp_path / "buzz.jsonl") == []


def test_bound_path_without_ledger_fails_before_buzz(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Must be tracked"}) + "\n")
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "card", output='{"identifier":"ANK-91"}\n')
    buzz = _sender(tmp_path, "buzz")

    config = _config(tmp_path, watch, ping, card, buzz=buzz)
    data = yaml.safe_load(config.read_text())
    data.pop("ledger_file")
    config.write_text(yaml.safe_dump(data))
    result = _run(config)

    assert result.returncode == 2
    assert "ledger_file is required when senders.buzz is configured" in result.stderr
    assert _calls(tmp_path / "card.jsonl") == []
    assert _calls(tmp_path / "ping.jsonl") == []
    assert _calls(tmp_path / "buzz.jsonl") == []


def test_no_buzz_preserves_legacy_ping_then_card_path(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Legacy delivery"}) + "\n")
    ping = _ordered_sender(tmp_path, "legacy_ping", "ping")
    card = _ordered_sender(
        tmp_path, "legacy_card", "card", output='{"identifier":"ANK-90"}\n'
    )

    result = _run(_config(tmp_path, watch, ping, card))

    assert result.returncode == 0
    assert (tmp_path / "events.txt").read_text().splitlines() == ["ping", "card"]
