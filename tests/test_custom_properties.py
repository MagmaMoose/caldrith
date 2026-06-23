"""Tests for the custom-properties tier (declared-only, idempotent set-on-drift)."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.custom_properties import reconcile
from caldrith.reconcile.planner import TargetRepo

_VALUES = "https://api.github.com/repos/acme/widget/properties/values"


def _cfg(**custom_properties: object) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"custom_properties": custom_properties})


def _live(*pairs: tuple[str, object]) -> list[dict]:
    return [{"property_name": name, "value": value} for name, value in pairs]


@respx.mock
async def test_change_sends_only_drifted_props() -> None:
    # live env=stage (drift) + tags already matching (order differs but equal) + an
    # undeclared prop that must be left untouched.
    respx.get(_VALUES).mock(
        return_value=httpx.Response(
            200,
            json=_live(("env", "stage"), ("tags", ["b", "a"]), ("undeclared", "keep")),
        )
    )
    patch = respx.patch(_VALUES).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client, TargetRepo("acme", "widget"), _cfg(env="prod", tags=["a", "b"])
        )

    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    # only the drifted "env" prop is sent — "tags" converged, "undeclared" unmanaged.
    assert body == {"properties": [{"property_name": "env", "value": "prod"}]}
    (result,) = results
    assert result.tier == "custom_properties"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_converged_no_patch_order_insensitive() -> None:
    # both declared props match; the list is given in a different order.
    respx.get(_VALUES).mock(
        return_value=httpx.Response(200, json=_live(("env", "prod"), ("tags", ["b", "a"])))
    )
    patch = respx.patch(_VALUES).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client, TargetRepo("acme", "widget"), _cfg(env="prod", tags=["a", "b"])
        )

    assert not patch.called
    (result,) = results
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_patches() -> None:
    respx.get(_VALUES).mock(return_value=httpx.Response(200, json=_live(("env", "stage"))))
    patch = respx.patch(_VALUES).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client, TargetRepo("acme", "widget"), _cfg(env="prod"), dry_run=True
        )

    assert not patch.called
    (result,) = results
    assert result.changed is True
    assert result.applied is False
