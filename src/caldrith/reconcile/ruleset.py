"""Reconcile repository rulesets from settings.yml.

Caldrith creates the declared rulesets on each managed repo (matched by ``name``) and
updates them on drift. GitHub's ruleset ``GET`` echoes server-added defaults and
metadata (ids, ``_links``, normalised rule parameters), so idempotency uses a **subset
match**: every field the config declares must be present-and-equal in the live ruleset;
extra server fields are ignored. Rulesets are **not pruned** — removing one from the
config does not delete it (a deliberately safe default; deletes are manual).

Only **repo-level** rulesets are considered (org-inherited rulesets — ``source_type ==
"Organization"`` — are left to the org and never edited here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from githubkit import GitHub

from caldrith.config.schema import Ruleset
from caldrith.github_json import response_json
from caldrith.reconcile.planner import TargetRepo


@dataclass
class RulesetResult:
    """Outcome of reconciling a repo's rulesets."""

    repo: str
    changed_fields: list[str] = field(default_factory=list)  # e.g. "create:Name" / "update:Name"
    applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changed_fields)


def _subset_match(desired: Any, actual: Any) -> bool:
    """True if every value in ``desired`` is present-and-equal in ``actual`` (recursive).

    Dicts: every desired key must match (extra actual keys ignored). Lists: every desired
    element must match some distinct actual element (order-insensitive). Scalars: ``==``.
    """
    if isinstance(desired, dict):
        if not isinstance(actual, dict):
            return False
        return all(_subset_match(value, actual.get(key)) for key, value in desired.items())
    if isinstance(desired, list):
        if not isinstance(actual, list):
            return False
        used = [False] * len(actual)
        for want in desired:
            for index, have in enumerate(actual):
                if not used[index] and _subset_match(want, have):
                    used[index] = True
                    break
            else:
                return False
        return True
    return desired == actual


def _to_body(ruleset: Ruleset) -> dict[str, Any]:
    """Build the rulesets API create/update body from the config model."""
    body: dict[str, Any] = {
        "name": ruleset.name,
        "target": ruleset.target,
        "enforcement": ruleset.enforcement,
        "rules": ruleset.rules,
    }
    if ruleset.conditions is not None:
        body["conditions"] = ruleset.conditions
    if ruleset.bypass_actors is not None:
        body["bypass_actors"] = [a.model_dump(exclude_none=True) for a in ruleset.bypass_actors]
    return body


class RulesetApplier:
    """Reconciles a single repo's rulesets against the declared set."""

    def __init__(self, client: GitHub, *, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    async def _repo_rulesets(self, target: TargetRepo) -> dict[str, dict[str, Any]]:
        """Name -> summary for the repo's own (non-inherited) rulesets."""
        response = await self._client.rest.repos.async_get_repo_rulesets(
            owner=target.owner, repo=target.name
        )
        out: dict[str, dict[str, Any]] = {}
        for summary in response_json(response) or []:
            if summary.get("source_type") == "Repository":
                out[summary["name"]] = summary
        return out

    async def _full(self, target: TargetRepo, ruleset_id: int) -> dict[str, Any]:
        response = await self._client.rest.repos.async_get_repo_ruleset(
            owner=target.owner, repo=target.name, ruleset_id=ruleset_id
        )
        return response_json(response)

    async def apply(self, target: TargetRepo, rulesets: list[Ruleset]) -> RulesetResult:
        """Create/update the declared rulesets on ``target`` (unless dry-run)."""
        result = RulesetResult(repo=target.full_name)
        existing = await self._repo_rulesets(target)

        for desired in rulesets:
            body = _to_body(desired)
            current = existing.get(desired.name)
            if current is None:
                if not self._dry_run:
                    await self._client.rest.repos.async_create_repo_ruleset(
                        owner=target.owner,
                        repo=target.name,
                        data=body,  # type: ignore[arg-type]
                    )
                result.changed_fields.append(f"create:{desired.name}")
            elif not _subset_match(body, await self._full(target, current["id"])):
                if not self._dry_run:
                    await self._client.rest.repos.async_update_repo_ruleset(
                        owner=target.owner,
                        repo=target.name,
                        ruleset_id=current["id"],
                        data=body,  # type: ignore[arg-type]
                    )
                result.changed_fields.append(f"update:{desired.name}")

        result.applied = bool(result.changed_fields) and not self._dry_run
        return result
