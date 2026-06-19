"""Tests for the pydantic config schema."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from caldrith.config.schema import (
    RepositorySettings,
    RestrictedRepos,
    SafeSettingsConfig,
    config_json_schema,
)


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


def test_deferred_tiers_accepted_but_unused() -> None:
    # A config exercising deferred seams must still validate.
    cfg = SafeSettingsConfig.model_validate(
        {
            "repository": {"allow_auto_merge": True},
            "labels": [{"name": "bug", "color": "ff0000"}],
            "branches": [{"name": "main"}],
            "rulesets": [],
        }
    )
    assert cfg.repository is not None
    assert cfg.labels == [{"name": "bug", "color": "ff0000"}]


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
    # restrictions/required_signatures are deferred — typos and unsupported keys fail loudly.
    with pytest.raises(ValidationError):
        SafeSettingsConfig.model_validate(
            {"branches": [{"name": "main", "protection": {"restrictions": {}}}]}
        )


def test_json_schema_available() -> None:
    schema = config_json_schema()
    assert "properties" in schema
    assert "repository" in schema["properties"]
