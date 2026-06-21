"""Tests for the repository rulesets applier."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import Ruleset, RulesetBypassActor
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.ruleset import (
    RulesetApplier,
    _has_unexpected_bypass_actors,
    _subset_match,
)

_RULESETS = "https://api.github.com/repos/acme/widget/rulesets"


def _desired() -> Ruleset:
    return Ruleset(
        name="Chargate required",
        target="branch",
        enforcement="active",
        conditions={"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "chargate / chargate"}],
                    "strict_required_status_checks_policy": False,
                },
            }
        ],
        bypass_actors=[
            RulesetBypassActor(actor_id=2134967, actor_type="Integration", bypass_mode="always")
        ],
    )


def _live_full(ruleset_id: int = 1, **overrides: Any) -> dict[str, Any]:
    """A GET-ruleset response: matches _desired() + server-added fields/normalised params."""
    base: dict[str, Any] = {
        "id": ruleset_id,
        "name": "Chargate required",
        "target": "branch",
        "enforcement": "active",
        "source_type": "Repository",
        "source": "acme/widget",
        "node_id": "RR_x",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "chargate / chargate", "integration_id": 999}
                    ],
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                },
            }
        ],
        "bypass_actors": [
            {"actor_id": 2134967, "actor_type": "Integration", "bypass_mode": "always"}
        ],
        "_links": {"self": {"href": "https://api.github.com/..."}},
    }
    base.update(overrides)
    return base


# --- _subset_match: the idempotency core ---


def test_subset_match_ignores_extra_actual_keys() -> None:
    assert _subset_match({"a": 1}, {"a": 1, "b": 2}) is True
    assert _subset_match({"a": 1}, {"a": 2}) is False
    assert _subset_match({"a": 1}, {"b": 2}) is False  # missing key


def test_subset_match_lists_order_insensitive_and_subset_items() -> None:
    assert _subset_match([{"x": 1}], [{"x": 1, "y": 2}]) is True
    assert _subset_match([{"x": 1}, {"x": 2}], [{"x": 2}, {"x": 1}]) is True
    assert _subset_match([{"x": 3}], [{"x": 1}]) is False


@respx.mock
async def test_creates_when_absent() -> None:
    respx.get(_RULESETS).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_RULESETS).mock(return_value=httpx.Response(201, json=_live_full()))

    async with GitHub("token") as client:
        result = await RulesetApplier(client).apply(TargetRepo("acme", "widget"), [_desired()])

    assert create.called
    body = json.loads(create.calls.last.request.content)
    assert body["name"] == "Chargate required"
    assert body["bypass_actors"][0]["actor_id"] == 2134967
    assert result.changed_fields == ["create:Chargate required"]
    assert result.applied is True


@respx.mock
async def test_noop_when_matching() -> None:
    respx.get(_RULESETS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 7, "name": "Chargate required", "source_type": "Repository"}]
        )
    )
    respx.get(f"{_RULESETS}/7").mock(
        return_value=httpx.Response(200, json=_live_full(ruleset_id=7))
    )
    put = respx.put(f"{_RULESETS}/7").mock(
        return_value=httpx.Response(200, json=_live_full(ruleset_id=7))
    )

    async with GitHub("token") as client:
        result = await RulesetApplier(client).apply(TargetRepo("acme", "widget"), [_desired()])

    assert not put.called  # subset match -> idempotent (server extras ignored)
    assert result.changed is False


@respx.mock
async def test_updates_when_drifted() -> None:
    drifted = _live_full(ruleset_id=7, enforcement="disabled")
    respx.get(_RULESETS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 7, "name": "Chargate required", "source_type": "Repository"}]
        )
    )
    respx.get(f"{_RULESETS}/7").mock(return_value=httpx.Response(200, json=drifted))
    put = respx.put(f"{_RULESETS}/7").mock(
        return_value=httpx.Response(200, json=_live_full(ruleset_id=7))
    )

    async with GitHub("token") as client:
        result = await RulesetApplier(client).apply(TargetRepo("acme", "widget"), [_desired()])

    assert put.called
    assert result.changed_fields == ["update:Chargate required"]


@respx.mock
async def test_ignores_org_inherited_ruleset() -> None:
    # A same-named ORG ruleset must not be mistaken for the repo's -> create a repo one.
    respx.get(_RULESETS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Chargate required", "source_type": "Organization"}]
        )
    )
    create = respx.post(_RULESETS).mock(return_value=httpx.Response(201, json=_live_full()))

    async with GitHub("token") as client:
        result = await RulesetApplier(client).apply(TargetRepo("acme", "widget"), [_desired()])

    assert create.called
    assert result.changed_fields == ["create:Chargate required"]


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_RULESETS).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_RULESETS).mock(return_value=httpx.Response(201, json=_live_full()))

    async with GitHub("token") as client:
        result = await RulesetApplier(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [_desired()]
        )

    assert not create.called
    assert result.changed is True and result.applied is False


def test_unexpected_bypass_actor_detection() -> None:
    desired = _desired()  # declares the diatreme Integration actor
    declared_only = {"bypass_actors": [{"actor_id": 2134967, "actor_type": "Integration"}]}
    assert _has_unexpected_bypass_actors(desired, declared_only) is False
    with_extra = {
        "bypass_actors": [
            {"actor_id": 2134967, "actor_type": "Integration"},
            {"actor_id": 9, "actor_type": "Team"},  # manually added escape hatch
        ]
    }
    assert _has_unexpected_bypass_actors(desired, with_extra) is True
    # A config that does not declare bypass_actors does not manage them -> never flags.
    unmanaged = Ruleset(name="x", rules=[])
    assert _has_unexpected_bypass_actors(unmanaged, with_extra) is False


@respx.mock
async def test_extra_bypass_actor_reverted_as_drift() -> None:
    # Live ruleset subset-matches desired but carries an EXTRA bypass actor (a silent
    # escape hatch around the required check). Must be detected as drift and reverted.
    sneaky = _live_full(ruleset_id=7)
    sneaky["bypass_actors"] = [
        {"actor_id": 2134967, "actor_type": "Integration", "bypass_mode": "always"},
        {"actor_id": 5, "actor_type": "OrganizationAdmin", "bypass_mode": "always"},
    ]
    respx.get(_RULESETS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 7, "name": "Chargate required", "source_type": "Repository"}]
        )
    )
    respx.get(f"{_RULESETS}/7").mock(return_value=httpx.Response(200, json=sneaky))
    put = respx.put(f"{_RULESETS}/7").mock(
        return_value=httpx.Response(200, json=_live_full(ruleset_id=7))
    )

    async with GitHub("token") as client:
        result = await RulesetApplier(client).apply(TargetRepo("acme", "widget"), [_desired()])

    assert put.called  # extra bypass actor reverted
    assert result.changed_fields == ["update:Chargate required"]
