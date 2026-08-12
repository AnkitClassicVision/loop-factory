from __future__ import annotations

import copy
import json

import pytest

from factory.authority import AuthorityMapError, load, validate


DEPARTMENT = "sales"


def _map():
    return {
        "schema": "authority-map/v1",
        "department": DEPARTMENT,
        "actions": [
            {
                "action": "observe", "owner": "factory-owner", "actor": "factory_supervisor",
                "authority": "observe", "proof": "signed_observation", "external_effect": False,
                "approval_required": False,
            },
            {
                "action": "plan", "owner": "worker-owner", "actor": "direct_worker",
                "authority": "draft", "proof": "release_bound_proposal", "external_effect": False,
                "approval_required": False,
            },
            {
                "action": "approve", "owner": "human-owner", "actor": "human_gate",
                "authority": "approve", "proof": "signed_human_decision", "external_effect": False,
                "approval_required": False,
            },
            {
                "action": "execute", "owner": "executor-owner", "actor": "dedicated_executor",
                "authority": "execute", "proof": "target_readback", "external_effect": True,
                "approval_required": True,
            },
            {
                "action": "verify", "owner": "verifier-owner", "actor": "independent_verifier",
                "authority": "verify", "proof": "target_readback", "external_effect": False,
                "approval_required": False,
            },
        ],
    }


def test_authority_map_requires_one_boundary_correct_owner_and_proof_per_action(tmp_path):
    path = tmp_path / "authority-map.json"
    path.write_text(json.dumps(_map()), encoding="utf-8")

    value = load(path, department=DEPARTMENT)

    assert [item["action"] for item in value["actions"]] == [
        "observe", "plan", "approve", "execute", "verify"
    ]
    assert next(item for item in value["actions"] if item["action"] == "execute")["approval_required"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["actions"].pop(),
        lambda value: value["actions"].__setitem__(1, copy.deepcopy(value["actions"][0])),
        lambda value: value["actions"][0].__setitem__("actor", "dedicated_executor"),
        lambda value: value["actions"][3].__setitem__("approval_required", False),
        lambda value: value["actions"][3].__setitem__("proof", "self_report"),
        lambda value: value["actions"][0].__setitem__("owner", ""),
    ],
)
def test_ambiguous_or_unsafe_authority_maps_fail_closed(mutate):
    value = _map()
    mutate(value)

    with pytest.raises(AuthorityMapError):
        validate(value, department=DEPARTMENT)
