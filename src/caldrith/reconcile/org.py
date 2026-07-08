"""Reconcile organization-scoped settings (applied once per Organization install).

Unlike the repository tiers (run per repo by :func:`caldrith.reconcile.runner.run_reconcile`),
organization settings apply to the account as a whole, so they have their own entrypoint,
:func:`run_org_reconcile`, invoked once per installation. It reconciles:

- **settings** — the ``orgs.update`` scalar fields (member privileges, security defaults,
  profile), diffed generically like the repository block;
- **actions** — org Actions policy + default workflow token permissions;
- **interaction_limits** — org-wide interaction limit;
- **custom_property_definitions** — the org's custom-property *schema*;
- **rulesets** — org rulesets (reusing the repo ruleset create/update + drift logic).

On a non-Organization account the tier is a graceful no-op (the ``organization`` block is
only meaningful for orgs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from githubkit import GitHub

from caldrith.audit.logging import bind_context, get_logger
from caldrith.config.diff import compare_deep
from caldrith.config.loader import load_admin_config
from caldrith.config.schema import (
    CodeSecurityConfiguration,
    InteractionLimits,
    OrgActions,
    OrganizationSettings,
    Ruleset,
)
from caldrith.github_json import response_json
from caldrith.reconcile.base import TierResult
from caldrith.reconcile.planner import account_type
from caldrith.reconcile.ruleset import _has_drift, _to_body
from caldrith.settings import AppConfig, get_config

_log = get_logger(__name__)

# Sub-tiers reconciled via their own endpoints, excluded from the orgs.update diff.
_NESTED_FIELDS = {
    "actions",
    "interaction_limits",
    "custom_property_definitions",
    "rulesets",
    "code_security_configuration",
}


@dataclass
class OrgSummary:
    """Aggregate outcome of an organization reconcile."""

    owner: str
    dry_run: bool
    results: list[TierResult] = field(default_factory=list)

    @property
    def changed(self) -> list[TierResult]:
        return [r for r in self.results if r.changed]

    @property
    def applied(self) -> list[TierResult]:
        return [r for r in self.results if r.applied]

    @property
    def any_changed(self) -> bool:
        return any(r.changed for r in self.results)


async def _reconcile_settings(
    client: GitHub, owner: str, org: OrganizationSettings, *, dry_run: bool
) -> TierResult:
    """Diff + apply the ``orgs.update`` scalar fields."""
    desired = org.model_dump(exclude_unset=True, exclude_none=True, exclude=_NESTED_FIELDS)
    result = TierResult(tier="organization", scope=owner)
    if not desired:
        return result
    live = response_json(await client.rest.orgs.async_get(org=owner))
    diff = compare_deep(actual=live, desired=desired)
    if diff.has_changes:
        result.changed = True
        result.notes = [f"{k} -> {v!r}" for k, v in diff.changed_payload().items()]
        if not dry_run:
            await client.rest.orgs.async_update(org=owner, **diff.changed_payload())
            result.applied = True
    return result


async def _reconcile_actions(
    client: GitHub, owner: str, actions: OrgActions, *, dry_run: bool
) -> TierResult:
    """Reconcile org Actions policy + default workflow token permissions."""
    api = client.rest.actions
    result = TierResult(tier="org_actions", scope=owner)

    if actions.enabled_repositories is not None or actions.allowed_actions is not None:
        current = response_json(
            await api.async_get_github_actions_permissions_organization(org=owner)
        )
        repos_drift = (
            actions.enabled_repositories is not None
            and current.get("enabled_repositories") != actions.enabled_repositories
        )
        allowed_drift = (
            actions.allowed_actions is not None
            and current.get("allowed_actions") != actions.allowed_actions
        )
        if repos_drift or allowed_drift:
            result.changed = True
            result.notes.append("set org actions permissions")
            if not dry_run:
                want_repos = (
                    actions.enabled_repositories
                    if actions.enabled_repositories is not None
                    else current.get("enabled_repositories")
                )
                body: dict[str, Any] = {"enabled_repositories": want_repos}
                want_allowed = (
                    actions.allowed_actions
                    if actions.allowed_actions is not None
                    else current.get("allowed_actions")
                )
                if want_repos != "none" and want_allowed:
                    body["allowed_actions"] = want_allowed
                await api.async_set_github_actions_permissions_organization(org=owner, data=body)

    if (
        actions.default_workflow_permissions is not None
        or actions.can_approve_pull_request_reviews is not None
    ):
        current = response_json(
            await api.async_get_github_actions_default_workflow_permissions_organization(org=owner)
        )
        dwp_drift = (
            actions.default_workflow_permissions is not None
            and current.get("default_workflow_permissions") != actions.default_workflow_permissions
        )
        cap_drift = (
            actions.can_approve_pull_request_reviews is not None
            and current.get("can_approve_pull_request_reviews")
            != actions.can_approve_pull_request_reviews
        )
        if dwp_drift or cap_drift:
            result.changed = True
            result.notes.append("set org default workflow permissions")
            if not dry_run:
                await api.async_set_github_actions_default_workflow_permissions_organization(
                    org=owner,
                    data={
                        "default_workflow_permissions": (
                            actions.default_workflow_permissions
                            if actions.default_workflow_permissions is not None
                            else current.get("default_workflow_permissions")
                        ),
                        "can_approve_pull_request_reviews": (
                            actions.can_approve_pull_request_reviews
                            if actions.can_approve_pull_request_reviews is not None
                            else current.get("can_approve_pull_request_reviews")
                        ),
                    },
                )
    result.applied = result.changed and not dry_run
    return result


async def _reconcile_interaction_limits(
    client: GitHub, owner: str, limits: InteractionLimits, *, dry_run: bool
) -> TierResult:
    """Reconcile the org-wide interaction limit (set/remove on drift of ``limit``)."""
    interactions = client.rest.interactions
    result = TierResult(tier="org_interaction_limits", scope=owner)
    live = response_json(await interactions.async_get_restrictions_for_org(org=owner))
    current_limit = live.get("limit") if isinstance(live, dict) else None

    if limits.limit is None:
        if current_limit:
            result.changed = True
            result.notes.append("remove org interaction limit")
            if not dry_run:
                await interactions.async_remove_restrictions_for_org(org=owner)
                result.applied = True
        return result

    if current_limit != limits.limit:
        result.changed = True
        result.notes.append(f"set org interaction limit: {limits.limit}")
        if not dry_run:
            body: dict[str, Any] = {"limit": limits.limit}
            if limits.expiry is not None:
                body["expiry"] = limits.expiry
            await interactions.async_set_restrictions_for_org(org=owner, data=body)
            result.applied = True
    return result


async def _reconcile_custom_property_definitions(
    client: GitHub, owner: str, definitions: list[Any], *, dry_run: bool
) -> TierResult:
    """Upsert the org's custom-property definitions (by ``property_name``)."""
    orgs = client.rest.orgs
    result = TierResult(tier="org_custom_properties", scope=owner)
    live_list = (
        response_json(
            await orgs.async_custom_properties_for_repos_get_organization_definitions(org=owner)
        )
        or []
    )
    live = {item["property_name"]: item for item in live_list}

    changed_defs: list[dict[str, Any]] = []
    for definition in definitions:
        desired = definition.model_dump(exclude_none=True)
        current = live.get(definition.property_name)
        if current is None or not _has_definition_match(desired, current):
            changed_defs.append(desired)
            result.notes.append(f"set property definition: {definition.property_name}")

    if changed_defs:
        result.changed = True
        if not dry_run:
            await orgs.async_custom_properties_for_repos_create_or_update_organization_definitions(
                org=owner, data={"properties": changed_defs}
            )
            result.applied = True
    return result


def _has_definition_match(desired: dict[str, Any], live: dict[str, Any]) -> bool:
    """True if every declared definition field matches the live definition."""
    for key, value in desired.items():
        live_value = live.get(key)
        if isinstance(value, list) and isinstance(live_value, list):
            if sorted(value) != sorted(live_value):
                return False
        elif live_value != value:
            return False
    return True


async def _reconcile_rulesets(
    client: GitHub, owner: str, rulesets: list[Ruleset], *, dry_run: bool
) -> TierResult:
    """Create/update org rulesets (by ``name``; reuses the repo ruleset drift logic)."""
    repos = client.rest.repos
    result = TierResult(tier="org_rulesets", scope=owner)
    summaries = response_json(await repos.async_get_org_rulesets(org=owner)) or []
    existing = {s["name"]: s for s in summaries}

    for desired in rulesets:
        body = _to_body(desired)
        current = existing.get(desired.name)
        if current is None:
            result.changed = True
            result.notes.append(f"create:{desired.name}")
            if not dry_run:
                await repos.async_create_org_ruleset(org=owner, data=body)  # type: ignore[arg-type]
        else:
            full = response_json(
                await repos.async_get_org_ruleset(org=owner, ruleset_id=current["id"])
            )
            if _has_drift(desired, body, full):
                result.changed = True
                result.notes.append(f"update:{desired.name}")
                if not dry_run:
                    await repos.async_update_org_ruleset(
                        org=owner,
                        ruleset_id=current["id"],
                        data=body,  # type: ignore[arg-type]
                    )
    result.applied = result.changed and not dry_run
    return result


async def _reconcile_code_security_config(
    client: GitHub, owner: str, cfg: CodeSecurityConfiguration, *, dry_run: bool
) -> TierResult:
    """Create/update the org code-security configuration (by ``name``) and apply it.

    The configuration body is every declared field except the caldrith-only ``apply_to`` /
    ``default_for_new_repos``. After a create or update (and only then, to avoid kicking an
    attach job on every no-op reconcile) the configuration is attached to all repos
    (``apply_to: all_repos``) and/or set as the default for new repos.
    """
    api = client.rest.code_security
    result = TierResult(tier="org_code_security_configuration", scope=owner)
    body = cfg.model_dump(exclude_none=True, exclude={"apply_to", "default_for_new_repos"})

    configs = response_json(await api.async_get_configurations_for_org(org=owner)) or []
    existing = next((c for c in configs if c.get("name") == cfg.name), None)

    config_id = existing.get("id") if existing else None
    touched = False
    if existing is None:
        result.changed = True
        result.notes.append(f"create:{cfg.name}")
        if not dry_run:
            created = response_json(
                await api.async_create_configuration(org=owner, data=body)  # type: ignore[arg-type]
            )
            config_id = created.get("id")
        touched = True
    elif any(existing.get(key) != value for key, value in body.items() if key != "name"):
        result.changed = True
        result.notes.append(f"update:{cfg.name}")
        if not dry_run:
            await api.async_update_configuration(
                org=owner,
                configuration_id=config_id,  # type: ignore[arg-type]
                data=body,  # type: ignore[arg-type]
            )
        touched = True

    if touched and cfg.apply_to == "all_repos":
        result.notes.append("attach: all repos")
        if not dry_run and config_id is not None:
            await api.async_attach_configuration(
                org=owner, configuration_id=config_id, data={"scope": "all"}
            )
    if touched and cfg.default_for_new_repos is not None:
        result.notes.append(f"default for new repos: {cfg.default_for_new_repos}")
        if not dry_run and config_id is not None:
            await api.async_set_configuration_as_default(
                org=owner,
                configuration_id=config_id,
                data={"default_for_new_repos": cfg.default_for_new_repos},
            )
    result.applied = result.changed and not dry_run
    return result


async def run_org_reconcile(
    client: GitHub,
    installation_id: int,
    owner: str,
    *,
    dry_run: bool = False,
    config: AppConfig | None = None,
) -> OrgSummary:
    """Reconcile ``owner``'s organization-scoped settings against the admin config."""
    cfg = config or get_config()
    log = bind_context(_log, installation_id=installation_id)
    settings_config = await load_admin_config(
        client,
        owner=owner,
        admin_repo=cfg.admin_repo,
        config_path=cfg.config_path,
        settings_file=cfg.settings_file_path,
    )
    org = settings_config.organization
    if org is None:
        return OrgSummary(owner, dry_run)

    if await account_type(client, installation_id) != "Organization":
        log.info("reconcile_org.not_an_org", owner=owner)
        return OrgSummary(owner, dry_run)

    results: list[TierResult] = [await _reconcile_settings(client, owner, org, dry_run=dry_run)]
    if org.actions is not None:
        results.append(await _reconcile_actions(client, owner, org.actions, dry_run=dry_run))
    if org.interaction_limits is not None:
        results.append(
            await _reconcile_interaction_limits(
                client, owner, org.interaction_limits, dry_run=dry_run
            )
        )
    if org.custom_property_definitions is not None:
        results.append(
            await _reconcile_custom_property_definitions(
                client, owner, org.custom_property_definitions, dry_run=dry_run
            )
        )
    if org.rulesets:
        results.append(await _reconcile_rulesets(client, owner, org.rulesets, dry_run=dry_run))
    if org.code_security_configuration is not None:
        results.append(
            await _reconcile_code_security_config(
                client, owner, org.code_security_configuration, dry_run=dry_run
            )
        )

    for result in results:
        if result.changed:
            log.info(
                "reconcile_org.tier",
                owner=owner,
                tier=result.tier,
                applied=result.applied,
                dry_run=dry_run,
            )
    return OrgSummary(owner, dry_run, results)
