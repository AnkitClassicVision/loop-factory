"""Factory-standard self-heal ladder (P3, finishing B5).

A deterministic heal state machine every department shares. Bounded at every
level, escalation-only (a level never lowers except on a clean success reset),
minimum observation window between transitions, terminal `parked` state, and an
oscillation detector for a node that keeps flapping up and back. Heal may NEVER
modify an immutable safety invariant. Spec §8.

Levels: L1 task retry -> L2 Ringer heal (read-only review swarm then a separate
fix swarm) -> L4 process change card (graph edit through F2-F4, human review).
L3 (node auto-heal) is Track B and not built here. L5 (estate heal) belongs to
the estate manager.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


# charter.immutable_safety_invariants.heal_may_not_modify (mirror; source of
# truth is departments/<dept>/charter.yaml).
IMMUTABLE_INVARIANTS = frozenset({
    "delivery_floor", "send_authorization", "eligibility_allowlist",
    "frequency_policy", "privacy_floor", "kill_controller", "circuit_breaker",
    "promotion_contract", "budget_ceilings", "identity_resolution",
    "metric_definitions", "gateway_mode", "autonomy_state",
})

MAX_L1 = 2   # task retries before escalating to L2
MAX_L2 = 2   # Ringer heal rounds before escalating to L4
MAX_L4 = 2   # change-card rounds before parking
MAX_FLAPS = 4   # up-then-reset cycles before parking a node as oscillating


class ImmutableHealError(RuntimeError):
    """A heal action tried to modify a charter safety floor."""


def assert_heal_target_allowed(target: str) -> None:
    if target in IMMUTABLE_INVARIANTS:
        raise ImmutableHealError(f"heal may not modify immutable invariant: {target}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class HealLadder:
    """Persistent per-node heal state. `record_failure` returns the next heal
    action; `record_success` resets a node (counting toward flap detection)."""

    def __init__(self, state_path=None, min_observation_s: float = 0.0):
        self.state_path = Path(state_path) if state_path else None
        self.min_observation_s = min_observation_s
        self._nodes: dict[str, dict] = {}
        if self.state_path and self.state_path.exists():
            try:
                self._nodes = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._nodes = {}

    # --- persistence --------------------------------------------------------- #

    def _save(self) -> None:
        if self.state_path is not None:
            _atomic_write(self.state_path, json.dumps(self._nodes, indent=2) + "\n")

    def _node(self, node: str) -> dict:
        return self._nodes.setdefault(node, {
            "level": "L1", "attempts": 0, "flaps": 0,
            "last_transition_at": None, "level_started_at": None, "status": "active",
        })

    # --- API ----------------------------------------------------------------- #

    def record_success(self, node: str, now: float) -> dict:
        st = self._node(node)
        # a success that follows an escalation past L1 is a flap (it climbed then
        # reset without stabilizing) — counted toward the oscillation detector.
        if st["status"] == "active" and st["level"] != "L1":
            st["flaps"] = st.get("flaps", 0) + 1
        st["level"] = "L1"
        st["attempts"] = 0
        st["last_transition_at"] = now
        st["level_started_at"] = None
        self._save()
        return {"act": "clear", "node": node, "level": "L1"}

    def record_failure(self, node: str, kind: str, now: float) -> dict:
        st = self._node(node)

        if st["status"] == "parked":
            return {"act": "park", "level": st["level"], "terminal": True, "node": node,
                    "reason": st.get("park_reason", "parked")}

        # oscillation: a node that keeps flapping never stabilizes -> park it.
        if st.get("flaps", 0) >= MAX_FLAPS:
            return self._park(node, st, now, reason="oscillation")

        if st.get("level_started_at") is None:
            st["level_started_at"] = now
        st["attempts"] += 1
        level = st["level"]
        cap = {"L1": MAX_L1, "L2": MAX_L2, "L4": MAX_L4}[level]

        if st["attempts"] < cap:
            self._save()
            return {"act": _ACT_FOR_LEVEL[level], "level": level, "node": node,
                    "attempt": st["attempts"]}

        # cap reached at this level -> escalate, but only after the node has been
        # observed at this level for at least the minimum window (avoids a
        # too-fast climb on a transient burst).
        started = st.get("level_started_at")
        if started is not None and (now - started) < self.min_observation_s:
            self._save()
            return {"act": "wait", "level": level, "node": node,
                    "reason": "min_observation_window"}

        nxt = _NEXT_LEVEL.get(level)
        if nxt is None:  # exhausted L4 -> park (terminal)
            return self._park(node, st, now, reason="l4_exhausted")
        st["level"] = nxt
        st["attempts"] = 0
        st["last_transition_at"] = now
        st["level_started_at"] = now
        self._save()
        return {"act": _ACT_FOR_LEVEL[nxt], "level": nxt, "node": node, "escalated": True}

    def _park(self, node: str, st: dict, now: float, reason: str) -> dict:
        st["status"] = "parked"
        st["park_reason"] = reason
        st["last_transition_at"] = now
        self._save()
        return {"act": "park", "level": st["level"], "terminal": True, "node": node,
                "reason": reason}


_ACT_FOR_LEVEL = {"L1": "retry", "L2": "ringer_heal", "L4": "change_card"}
_NEXT_LEVEL = {"L1": "L2", "L2": "L4", "L4": None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-heal ladder: record a node failure and print the next heal action")
    parser.add_argument("--state", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--kind", default="task_error")
    parser.add_argument("--now", type=float, required=True)
    parser.add_argument("--success", action="store_true")
    args = parser.parse_args()
    lad = HealLadder(state_path=args.state)
    result = lad.record_success(args.node, args.now) if args.success else lad.record_failure(args.node, args.kind, args.now)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
