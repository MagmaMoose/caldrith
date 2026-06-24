"""Per-installation GitHub client factory.

Uses githubkit's :class:`AppInstallationAuthStrategy`, which mints (and refreshes)
installation access tokens from the App's id + private key. A fresh
:class:`~githubkit.GitHub` client is returned for every installation so tokens are
never shared across tenants.

Verified against githubkit 0.16.0:
  - ``AppInstallationAuthStrategy(app_id, private_key, installation_id, ...)``
  - ``GitHub(auth, *, base_url=...)``
"""

from __future__ import annotations

from githubkit import AppAuthStrategy, AppInstallationAuthStrategy, GitHub

from caldrith.settings import AppConfig, get_config


class GitHubClientFactory:
    """Builds per-installation githubkit clients from App credentials."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or get_config()

    def for_installation(
        self,
        installation_id: int,
        base_url: str | None = None,
    ) -> GitHub[AppInstallationAuthStrategy]:
        """Return a new client authenticated as ``installation_id``.

        Args:
            installation_id: The GitHub App installation id.
            base_url: REST API base URL; defaults to the configured
                ``GITHUB_API_URL`` (so GHES is a config change, not a code change).
        """
        auth = AppInstallationAuthStrategy(
            app_id=self._config.app_id,
            private_key=self._config.private_key,
            installation_id=installation_id,
        )
        return GitHub(auth, base_url=base_url or self._config.github_api_url)

    def for_app(self, base_url: str | None = None) -> GitHub[AppAuthStrategy]:
        """Return a client authenticated as the App itself (a JWT, no installation).

        Used to enumerate installations (``apps.list_installations`` /
        ``apps.get_org_installation``) — the endpoints that take an app JWT, not an
        installation token. Tokens are never shared across calls.
        """
        auth = AppAuthStrategy(app_id=self._config.app_id, private_key=self._config.private_key)
        return GitHub(auth, base_url=base_url or self._config.github_api_url)
