"""Tests for the pydantic config schema."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from caldrith.config.schema import (
    CodeSecurityConfiguration,
    RepositorySettings,
    RestrictedRepos,
    SafeSettingsConfig,
    SubOrg,
    config_json_schema,
)


def test_code_security_configuration_validates() -> None:
    CodeSecurityConfiguration(
        name="Baseline",
        dependency_graph_autosubmit_action="enabled",
        dependabot_delegated_alert_dismissal="enabled",
        enforcement="enforced",
        apply_to="all_repos",
        default_for_new_repos="all",
    )
    with pytest.raises(ValidationError):
        CodeSecurityConfiguration(name="x", dependabot_alerts="on")  # not enabled|disabled|not_set
    with pytest.raises(ValidationError):
        CodeSecurityConfiguration(name="x", apply_to="some_repos")  # only all_repos
    with pytest.raises(ValidationError):
        CodeSecurityConfiguration(name="x", default_for_new_repos="everything")


def test_suborg_visibility_parses_and_validates() -> None:
    SubOrg(name="public-only", visibility=["public"])  # ok
    SubOrg(name="restricted", visibility=["private", "internal"])  # ok
    SubOrg(name="by-name", repos=["svc-*"])  # visibility optional
    with pytest.raises(ValidationError):
        SubOrg(name="typo", visibility=["pubic"])  # unknown visibility rejected


def test_three_required_fields_parse() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {
            "repository": {
                "allow_auto_merge": True,
                "delete_branch_on_merge": False,
                "allow_update_branch": True,
            }
        }
    )
    assert cfg.repository is not None
    assert cfg.repository.allow_auto_merge is True
    assert cfg.repository.delete_branch_on_merge is False
    assert cfg.repository.allow_update_branch is True


def test_exclude_unset_only_keeps_declared_fields() -> None:
    repo = RepositorySettings(allow_auto_merge=True)
    dumped = repo.model_dump(exclude_unset=True)
    assert dumped == {"allow_auto_merge": True}


def test_unknown_repository_key_rejected() -> None:
    with pytest.raises(ValidationError):
        RepositorySettings.model_validate({"not_a_real_field": True})


def test_many_tiers_parse_into_typed_models() -> None:
    # A config exercising several tiers validates into typed models.
    cfg = SafeSettingsConfig.model_validate(
        {
            "repository": {"allow_auto_merge": True},
            "labels": [{"name": "bug", "color": "ff0000"}],
            "branches": [{"name": "main"}],
            "rulesets": [],
        }
    )
    assert cfg.repository is not None
    assert cfg.labels is not None
    assert cfg.labels[0].name == "bug"
    assert cfg.labels[0].color == "ff0000"


def test_empty_config_valid() -> None:
    cfg = SafeSettingsConfig.model_validate({})
    assert cfg.repository is None


def test_yaml_round_trip() -> None:
    raw = """
repository:
  allow_auto_merge: true
  delete_branch_on_merge: true
  allow_update_branch: false
  has_issues: true
"""
    cfg = SafeSettingsConfig.model_validate(yaml.safe_load(raw))
    assert cfg.repository is not None
    assert cfg.repository.has_issues is True


def test_restricted_repos_list_form() -> None:
    cfg = SafeSettingsConfig.model_validate({"restrictedRepos": ["legacy-*", "tmp"]})
    assert cfg.restricted_repos == ["legacy-*", "tmp"]


def test_restricted_repos_object_form() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {"restrictedRepos": {"include": ["svc-*"], "exclude": ["svc-old"]}}
    )
    assert isinstance(cfg.restricted_repos, RestrictedRepos)
    assert cfg.restricted_repos.include == ["svc-*"]
    assert cfg.restricted_repos.exclude == ["svc-old"]


def test_restricted_repos_absent_is_none() -> None:
    cfg = SafeSettingsConfig.model_validate({"repository": {"allow_auto_merge": True}})
    assert cfg.restricted_repos is None


def test_repository_security_parses_camelcase() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {
            "repository": {
                "security": {
                    "enableVulnerabilityAlerts": True,
                    "enableAutomatedSecurityFixes": True,
                    "enablePrivateVulnerabilityReporting": False,
                }
            }
        }
    )
    assert cfg.repository is not None
    assert cfg.repository.security is not None
    assert cfg.repository.security.enable_vulnerability_alerts is True
    assert cfg.repository.security.enable_automated_security_fixes is True
    assert cfg.repository.security.enable_private_vulnerability_reporting is False


def test_branches_protection_parses() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {
            "branches": [
                {
                    "name": "main",
                    "protection": {
                        "enforce_admins": True,
                        "required_status_checks": {"strict": True, "contexts": ["ci/build"]},
                        "required_pull_request_reviews": {"required_approving_review_count": 2},
                    },
                },
                {"name": "release", "protection": None},
            ]
        }
    )
    assert cfg.branches is not None
    assert cfg.branches[0].name == "main"
    assert cfg.branches[0].protection is not None
    assert cfg.branches[0].protection.enforce_admins is True
    assert cfg.branches[0].protection.required_status_checks is not None
    assert cfg.branches[0].protection.required_status_checks.contexts == ["ci/build"]
    assert cfg.branches[1].protection is None  # remove-protection entry


def test_unknown_protection_key_rejected() -> None:
    # Typos and genuinely unsupported keys fail loudly (extra="forbid").
    with pytest.raises(ValidationError):
        SafeSettingsConfig.model_validate(
            {"branches": [{"name": "main", "protection": {"not_a_real_field": True}}]}
        )


def test_restrictions_and_signatures_parse() -> None:
    # restrictions + required_signatures are now first-class protection fields.
    cfg = SafeSettingsConfig.model_validate(
        {
            "branches": [
                {
                    "name": "main",
                    "protection": {
                        "required_signatures": True,
                        "lock_branch": True,
                        "restrictions": {"users": ["octocat"], "teams": ["core"]},
                    },
                }
            ]
        }
    )
    assert cfg.branches is not None
    protection = cfg.branches[0].protection
    assert protection is not None
    assert protection.required_signatures is True
    assert protection.lock_branch is True
    assert protection.restrictions is not None
    assert protection.restrictions.users == ["octocat"]
    assert protection.restrictions.apps == []  # default empty


def test_empty_required_pull_request_reviews_rejected() -> None:
    # `required_pull_request_reviews: {}` would otherwise silently mean "reviews off"
    # after canonicalisation — surface it as an error at parse time instead.
    with pytest.raises(ValidationError):
        SafeSettingsConfig.model_validate(
            {"branches": [{"name": "main", "protection": {"required_pull_request_reviews": {}}}]}
        )


def test_empty_required_status_checks_rejected() -> None:
    with pytest.raises(ValidationError):
        SafeSettingsConfig.model_validate(
            {"branches": [{"name": "main", "protection": {"required_status_checks": {}}}]}
        )


def test_rulesets_parse() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {
            "rulesets": [
                {
                    "name": "Chargate required",
                    "enforcement": "active",
                    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": "chargate / chargate"}]
                            },
                        }
                    ],
                    "bypass_actors": [
                        {"actor_id": 2134967, "actor_type": "Integration", "bypass_mode": "always"}
                    ],
                }
            ]
        }
    )
    assert cfg.rulesets is not None
    assert cfg.rulesets[0].name == "Chargate required"
    assert cfg.rulesets[0].target == "branch"  # default
    assert cfg.rulesets[0].rules[0]["type"] == "required_status_checks"
    assert cfg.rulesets[0].bypass_actors is not None
    assert cfg.rulesets[0].bypass_actors[0].actor_id == 2134967


def test_files_parse() -> None:
    cfg = SafeSettingsConfig.model_validate(
        {
            "files": [
                {"path": ".github/workflows/security.yml", "content": "name: Security\n"},
                {"path": ".github/workflows/release.yaml", "content": "x", "create_only": True},
            ]
        }
    )
    assert cfg.files is not None
    assert cfg.files[0].path == ".github/workflows/security.yml"
    assert cfg.files[0].create_only is False  # default
    assert cfg.files[1].create_only is True


def test_json_schema_available() -> None:
    schema = config_json_schema()
    assert "properties" in schema
    assert "repository" in schema["properties"]
