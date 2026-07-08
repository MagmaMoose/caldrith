# CHANGELOG

<!-- version list -->

## v1.13.1 (2026-07-01)

### Bug Fixes

- **files**: Name per-base managed branches as siblings, not nested paths
  ([#40](https://github.com/MagmaMoose/caldrith/pull/40),
  [`fc65edd`](https://github.com/MagmaMoose/caldrith/commit/fc65edd6c3eba79beac74beab1dbbfb3f6e8dd5b))

### Chores

- Update caldrith image
  ([`c045b3a`](https://github.com/MagmaMoose/caldrith/commit/c045b3a80c44b42a5dfdea0ffba3a32bc7ce0428))


## v1.13.0 (2026-07-01)

### Chores

- Update caldrith image
  ([`a941e35`](https://github.com/MagmaMoose/caldrith/commit/a941e359c024761164912772dcb8e5bac76b7a8b))

### Features

- **files**: Provision managed files into multiple base branches
  ([#39](https://github.com/MagmaMoose/caldrith/pull/39),
  [`1ae0987`](https://github.com/MagmaMoose/caldrith/commit/1ae0987e6f1f6854ff9c99827fe19b3e6222001c))


## v1.12.2 (2026-07-01)

### Bug Fixes

- **k8s**: Make manual-trigger secret opt-in ([#38](https://github.com/MagmaMoose/caldrith/pull/38),
  [`3b016c9`](https://github.com/MagmaMoose/caldrith/commit/3b016c924c43168bb1ad3309b3086b6cbc51223a))

### Build System

- **deps**: Bump the github-actions group across 1 directory with 2 updates
  ([#34](https://github.com/MagmaMoose/caldrith/pull/34),
  [`62a71c0`](https://github.com/MagmaMoose/caldrith/commit/62a71c06ee31345ef8474dc815cec3205fcad287))

### Chores

- Update caldrith image
  ([`cd2f478`](https://github.com/MagmaMoose/caldrith/commit/cd2f478a39dfd755bcf6cc398564cf25ac228331))


## v1.12.1 (2026-07-01)

### Bug Fixes

- **worker,api**: Isolate ARQ on a Caldrith-specific queue
  ([#32](https://github.com/MagmaMoose/caldrith/pull/32),
  [`bcbc8a3`](https://github.com/MagmaMoose/caldrith/commit/bcbc8a33acefcafe2a5e21297764dc145312f0c5))

### Chores

- Update caldrith image
  ([`5df73ea`](https://github.com/MagmaMoose/caldrith/commit/5df73eaaa369a5b2e57c41959c0e836267cf92fb))


## v1.12.0 (2026-06-27)

### Chores

- Update caldrith image
  ([`18f8477`](https://github.com/MagmaMoose/caldrith/commit/18f8477a8a91b169496cf4e886b5016b2b1a9c08))

### Features

- **webhooks**: Re-base admin repo's open PRs on settings change
  ([#37](https://github.com/MagmaMoose/caldrith/pull/37),
  [`8b27985`](https://github.com/MagmaMoose/caldrith/commit/8b27985bde03d2363886af7fb72102dcc9ef904d))


## v1.11.2 (2026-06-27)

### Bug Fixes

- **ci**: Build multi-arch (amd64+arm64) images
  ([#35](https://github.com/MagmaMoose/caldrith/pull/35),
  [`aed6a18`](https://github.com/MagmaMoose/caldrith/commit/aed6a18978700e8b19d779c63412cb0093463ef7))

### Chores

- Update caldrith image
  ([`e396cd8`](https://github.com/MagmaMoose/caldrith/commit/e396cd81b4cb90bdd2d6d7372a38a44eaa421260))


## v1.11.1 (2026-06-24)

### Bug Fixes

- **k8s**: Suppress KICS false positive on ExternalSecret secretKey
  ([#31](https://github.com/MagmaMoose/caldrith/pull/31),
  [`8b9fc53`](https://github.com/MagmaMoose/caldrith/commit/8b9fc534052a83b036d8a2da1eef07c9a41bace0))

### Chores

- Update caldrith image
  ([`519df47`](https://github.com/MagmaMoose/caldrith/commit/519df47f8c6fd71d75745d59745701e4f6a95817))

- **k8s**: Enable hourly reconcile cron + wire manual-trigger token
  ([#31](https://github.com/MagmaMoose/caldrith/pull/31),
  [`8b9fc53`](https://github.com/MagmaMoose/caldrith/commit/8b9fc534052a83b036d8a2da1eef07c9a41bace0))


## v1.11.0 (2026-06-24)

### Bug Fixes

- **reconcile**: Paginate apps.list_installations + audit-log auth fails
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))

- **tests**: Import CodeScanningDefaultSetup in test_schema.py
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))

- **worker**: Use hour axis when RECONCILE_CRON_MINUTES >= 60
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))

### Chores

- Update caldrith image
  ([`65a1dc9`](https://github.com/MagmaMoose/caldrith/commit/65a1dc9deffe1cb1498e519e35a7582e451c04ad))

### Features

- **api,worker**: Manual /reconcile endpoint + periodic re-reconcile cron
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))

- **api,worker**: Manual reconcile endpoint + periodic re-reconcile cron
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))

### Refactoring

- **tests**: Single import style for caldrith.api.app
  ([#30](https://github.com/MagmaMoose/caldrith/pull/30),
  [`5a195e5`](https://github.com/MagmaMoose/caldrith/commit/5a195e55910f7cbad0a78bfbb34762fb93c75592))


## v1.10.0 (2026-06-24)

### Chores

- Update caldrith image
  ([`3af9acd`](https://github.com/MagmaMoose/caldrith/commit/3af9acd37a47a4afa10e4b0e0cd6037e3475215a))

### Code Style

- Ruff format files.py + test_files.py ([#28](https://github.com/MagmaMoose/caldrith/pull/28),
  [`a483e71`](https://github.com/MagmaMoose/caldrith/commit/a483e71a69fb6a5b723dfae356bfb3229a312398))

### Features

- **org**: Organization code-security configuration tier
  ([#28](https://github.com/MagmaMoose/caldrith/pull/28),
  [`a483e71`](https://github.com/MagmaMoose/caldrith/commit/a483e71a69fb6a5b723dfae356bfb3229a312398))


## v1.9.0 (2026-06-24)

### Chores

- Update caldrith image
  ([`df86988`](https://github.com/MagmaMoose/caldrith/commit/df86988921a2325e0967ef3d3aae142e17065518))

### Features

- **files**: Upgrade_only — never downgrade a bot-bumped SHA-pinned version
  ([#29](https://github.com/MagmaMoose/caldrith/pull/29),
  [`cf4a733`](https://github.com/MagmaMoose/caldrith/commit/cf4a73316d51fefebc840b92de58d7e1e209f9a9))


## v1.8.0 (2026-06-24)

### Chores

- Provision required workflows (caldrith) ([#27](https://github.com/MagmaMoose/caldrith/pull/27),
  [`c8fb484`](https://github.com/MagmaMoose/caldrith/commit/c8fb484c4d8faa9f5f5d928b3e02b51b23e5ba6c))

- Update caldrith image
  ([`606be5a`](https://github.com/MagmaMoose/caldrith/commit/606be5a90340ddc883d4da09f62fc85cf8f7e6ec))

### Code Style

- Ruff format files.py and test_files.py ([#26](https://github.com/MagmaMoose/caldrith/pull/26),
  [`6dce776`](https://github.com/MagmaMoose/caldrith/commit/6dce7766a702f810076e321051b20ab980449a19))

### Features

- **code-scanning**: CodeQL default-setup tier + maximal public-hardening guide
  ([#26](https://github.com/MagmaMoose/caldrith/pull/26),
  [`6dce776`](https://github.com/MagmaMoose/caldrith/commit/6dce7766a702f810076e321051b20ab980449a19))

- **code-scanning**: Tier to enable CodeQL default setup
  ([#26](https://github.com/MagmaMoose/caldrith/pull/26),
  [`6dce776`](https://github.com/MagmaMoose/caldrith/commit/6dce7766a702f810076e321051b20ab980449a19))


## v1.7.0 (2026-06-23)

### Chores

- Gitignore .idea/ and .vscode/ (IDE config) ([#25](https://github.com/MagmaMoose/caldrith/pull/25),
  [`c829d02`](https://github.com/MagmaMoose/caldrith/commit/c829d0266c89812741dc9d7ab9f35c80f6a4969b))

### Documentation

- **configuration**: Document Code Quality & Code Coverage ruleset rules
  ([#24](https://github.com/MagmaMoose/caldrith/pull/24),
  [`8c81345`](https://github.com/MagmaMoose/caldrith/commit/8c8134516b6d3dea961dab3430034e04b24ee9ed))

- **configuration**: Expand the rulesets reference
  ([#24](https://github.com/MagmaMoose/caldrith/pull/24),
  [`8c81345`](https://github.com/MagmaMoose/caldrith/commit/8c8134516b6d3dea961dab3430034e04b24ee9ed))

- **configuration**: Flag plan-gated (paid) settings
  ([#24](https://github.com/MagmaMoose/caldrith/pull/24),
  [`8c81345`](https://github.com/MagmaMoose/caldrith/commit/8c8134516b6d3dea961dab3430034e04b24ee9ed))

### Features

- **overlay**: Scope overlays by repo visibility
  ([#25](https://github.com/MagmaMoose/caldrith/pull/25),
  [`c829d02`](https://github.com/MagmaMoose/caldrith/commit/c829d0266c89812741dc9d7ab9f35c80f6a4969b))

- **overlay**: Scope overlays by repo visibility (e.g. paid features on public repos only)
  ([#25](https://github.com/MagmaMoose/caldrith/pull/25),
  [`c829d02`](https://github.com/MagmaMoose/caldrith/commit/c829d0266c89812741dc9d7ab9f35c80f6a4969b))


## v1.6.1 (2026-06-23)

### Bug Fixes

- **files**: Author managed-file commits via signed createCommitOnBranch
  ([#22](https://github.com/MagmaMoose/caldrith/pull/22),
  [`0f53403`](https://github.com/MagmaMoose/caldrith/commit/0f534039c92bb632256a504ff8c3338f54697472))

### Chores

- Provision required workflows (caldrith) ([#18](https://github.com/MagmaMoose/caldrith/pull/18),
  [`bd1e066`](https://github.com/MagmaMoose/caldrith/commit/bd1e06626503a97371cecde8d43e2d9ebf7bf572))

- Update caldrith image
  ([`0fa4b5e`](https://github.com/MagmaMoose/caldrith/commit/0fa4b5edd56ee6935bb66d41317714fdf8353b4d))

### Documentation

- **configuration**: Add a Key|Default|Purpose reference table for every setting
  ([#21](https://github.com/MagmaMoose/caldrith/pull/21),
  [`8177a14`](https://github.com/MagmaMoose/caldrith/commit/8177a143a46c082bbc6c943abfa2ee5d79f12c73))

- **configuration**: Flag plan-gated (paid) settings
  ([#23](https://github.com/MagmaMoose/caldrith/pull/23),
  [`e2b2b02`](https://github.com/MagmaMoose/caldrith/commit/e2b2b024d8026ce467fa3cbcaa21bf36e6112cee))


## v1.6.0 (2026-06-23)

### Chores

- Update caldrith image
  ([`b2c638d`](https://github.com/MagmaMoose/caldrith/commit/b2c638d67afa36cb105307dc8053c47c63ab98cb))

### Features

- **files**: Prune orphaned managed files from the provisioning PR
  ([#20](https://github.com/MagmaMoose/caldrith/pull/20),
  [`cc986c0`](https://github.com/MagmaMoose/caldrith/commit/cc986c0fc9e69f11b2fb2aa617160040b0b4d6ab))


## v1.5.1 (2026-06-23)

### Bug Fixes

- Make file-provisioning and ruleset tiers fleet-safe
  ([#19](https://github.com/MagmaMoose/caldrith/pull/19),
  [`7eaace0`](https://github.com/MagmaMoose/caldrith/commit/7eaace0b5a41dc93925e0bdd0b53efde9a90ef81))

- Update branch reference for managed files provisioning
  ([#19](https://github.com/MagmaMoose/caldrith/pull/19),
  [`7eaace0`](https://github.com/MagmaMoose/caldrith/commit/7eaace0b5a41dc93925e0bdd0b53efde9a90ef81))

### Chores

- Update caldrith image
  ([`f30e763`](https://github.com/MagmaMoose/caldrith/commit/f30e763c8032b4c4343867c26d83f8b95a0c43a5))


## v1.5.0 (2026-06-23)

### Bug Fixes

- Avoid 422 when a label rename target already exists
  ([#17](https://github.com/MagmaMoose/caldrith/pull/17),
  [`923a017`](https://github.com/MagmaMoose/caldrith/commit/923a017dbb9ce44d5b719000ec5267ca46df7b25))

- Establish branch protection before enabling required_signatures
  ([#17](https://github.com/MagmaMoose/caldrith/pull/17),
  [`923a017`](https://github.com/MagmaMoose/caldrith/commit/923a017dbb9ce44d5b719000ec5267ca46df7b25))

- Gate secret prune on the declared store, not the global flag
  ([#17](https://github.com/MagmaMoose/caldrith/pull/17),
  [`923a017`](https://github.com/MagmaMoose/caldrith/commit/923a017dbb9ce44d5b719000ec5267ca46df7b25))

### Chores

- Update caldrith image
  ([`0e93e8f`](https://github.com/MagmaMoose/caldrith/commit/0e93e8f125bcc98b53fdff713c53a6421ae97a88))

### Features

- Enforce the full GitHub configuration surface
  ([#17](https://github.com/MagmaMoose/caldrith/pull/17),
  [`923a017`](https://github.com/MagmaMoose/caldrith/commit/923a017dbb9ce44d5b719000ec5267ca46df7b25))


## v1.4.2 (2026-06-21)

### Bug Fixes

- Make file-provisioning and ruleset tiers fleet-safe
  ([#16](https://github.com/MagmaMoose/caldrith/pull/16),
  [`98ae292`](https://github.com/MagmaMoose/caldrith/commit/98ae292f0e53898d8c18acfff578c33165557274))

### Chores

- Update caldrith image
  ([`37ea17f`](https://github.com/MagmaMoose/caldrith/commit/37ea17f7c3475abc64bac3538bc0898ef7c6af4c))


## v1.4.1 (2026-06-21)

### Bug Fixes

- Add HEALTHCHECK to satisfy the security gate
  ([#15](https://github.com/MagmaMoose/caldrith/pull/15),
  [`1bf81c3`](https://github.com/MagmaMoose/caldrith/commit/1bf81c3e9d08a2d7fd76c79795a3f2843985db29))

- Pin runtime Python to the builder (production CrashLoopBackOff)
  ([#15](https://github.com/MagmaMoose/caldrith/pull/15),
  [`1bf81c3`](https://github.com/MagmaMoose/caldrith/commit/1bf81c3e9d08a2d7fd76c79795a3f2843985db29))

- Pin runtime Python to the builder via ARG (unbreak the container)
  ([#15](https://github.com/MagmaMoose/caldrith/pull/15),
  [`1bf81c3`](https://github.com/MagmaMoose/caldrith/commit/1bf81c3e9d08a2d7fd76c79795a3f2843985db29))

### Chores

- Update caldrith image
  ([`80b2118`](https://github.com/MagmaMoose/caldrith/commit/80b21187eb02109837a06206919daaa86c6ab2d4))


## v1.4.0 (2026-06-21)

### Chores

- Update caldrith image
  ([`52b2185`](https://github.com/MagmaMoose/caldrith/commit/52b2185d168aacb59e5339fe4120b9160df2d5dc))

### Continuous Integration

- Dogfood the Chargate security gate
  ([`ac5fa2e`](https://github.com/MagmaMoose/caldrith/commit/ac5fa2e503ef1e37d6c8cf739227fed2d8fa0de0))

- Use Diatreme for releases (evolution of calebsargeant/semantic-release)
  ([`372fd24`](https://github.com/MagmaMoose/caldrith/commit/372fd2403bd439c897d33f3558683b13aa7913ea))

### Features

- File-provisioning tier — roll required workflows out org-wide via PR
  ([`8869539`](https://github.com/MagmaMoose/caldrith/commit/8869539a7c16146565e16fdfe9d15cdcc7ededce))


## v1.3.0 (2026-06-19)

### Bug Fixes

- **reconcile**: Guard archived repos in BranchProtectionApplier; drop unreachable default-branch
  fallback
  ([`9575771`](https://github.com/MagmaMoose/caldrith/commit/9575771d3735c7ddfde9ab5bbe388ad84bde2b60))

- **reconcile**: One bad branch logs and continues, not aborts the run
  ([`3293289`](https://github.com/MagmaMoose/caldrith/commit/3293289ab056f8c2b48e4a66fe1b644cb9a4ceab))

- **schema**: Reject required_*_reviews: {} and required_status_checks: {}
  ([`531b5ab`](https://github.com/MagmaMoose/caldrith/commit/531b5abf89992b6ddb92601eeff1df6ab0c9f828))

### Build System

- **deps**: Bump python from 3.12-slim to 3.14-slim in the docker group
  ([`1afe592`](https://github.com/MagmaMoose/caldrith/commit/1afe592be0c200f09b607a6801cf770461589ed2))

### Chores

- Update caldrith image
  ([`9eb87f3`](https://github.com/MagmaMoose/caldrith/commit/9eb87f30f0a45f8ad7939afdb04f58060012bbb9))

### Documentation

- Warn that branches: wipes manual push restrictions; widen P1 scope in CLAUDE.md
  ([`9f7e9e4`](https://github.com/MagmaMoose/caldrith/commit/9f7e9e41104ed46691818fcdefe71f13c4dcc1f8))

### Features

- Branch protection tier
  ([`bd0b614`](https://github.com/MagmaMoose/caldrith/commit/bd0b614bb4a27fdfde871d564b6367222649c57b))

- Repository security tier (Dependabot + private vuln reporting)
  ([`be641bf`](https://github.com/MagmaMoose/caldrith/commit/be641bf55846ae4039892beaaf1d2edca130f7d8))


## v1.2.0 (2026-06-19)

### Chores

- Update caldrith image
  ([`bb0cf21`](https://github.com/MagmaMoose/caldrith/commit/bb0cf21bf27b232873f66e5c1120668d091251e1))

### Features

- Repo selection — restrictedRepos, exclude admin/.github, skip archived
  ([`4899d5f`](https://github.com/MagmaMoose/caldrith/commit/4899d5f2993c2eb577160b6815af1349b9ec9d55))


## v1.1.2 (2026-06-19)

### Bug Fixes

- Install cryptography via githubkit[auth-app] for RS256 app auth
  ([`ea8a527`](https://github.com/MagmaMoose/caldrith/commit/ea8a52721072ced4c0919704ee36dfd3158a3eb6))

### Chores

- Update caldrith image
  ([`e24171c`](https://github.com/MagmaMoose/caldrith/commit/e24171cc64ff33191ed7d7b73c63f896b4e449e9))


## v1.1.1 (2026-06-19)

### Bug Fixes

- Worker reads REDIS_URL (ARQ ignores the metaclass redis_settings)
  ([`5f79aee`](https://github.com/MagmaMoose/caldrith/commit/5f79aeeb81de0efa2aba479fe66fe2016363e204))

### Chores

- Update caldrith image
  ([`95dfbba`](https://github.com/MagmaMoose/caldrith/commit/95dfbbaa4a5c1cb397ca3e8b3a895fcfc721a748))


## v1.1.0 (2026-06-19)

### Chores

- Update caldrith image
  ([`cf75fec`](https://github.com/MagmaMoose/caldrith/commit/cf75feca49de8bba778bf4408a80c1a7dd076b11))

### Features

- Deploy the ARQ reconcile worker
  ([`a093d77`](https://github.com/MagmaMoose/caldrith/commit/a093d77daad98a6a06ba168a28b4dad04a9be5ca))


## v1.0.3 (2026-06-19)

### Bug Fixes

- Run as numeric uid 1000 so runAsNonRoot can verify non-root
  ([`7287e73`](https://github.com/MagmaMoose/caldrith/commit/7287e730d55d8cffdbb6809519b7dbe72805d651))

### Chores

- Update caldrith image
  ([`3c53788`](https://github.com/MagmaMoose/caldrith/commit/3c53788e4a8510e24aaee81b0bdabaef87199afe))


## v1.0.2 (2026-06-19)

### Bug Fixes

- Add docker-bake.hcl and repair the container build
  ([`0d4707d`](https://github.com/MagmaMoose/caldrith/commit/0d4707d9750ee4c706cb3cccc4620d6de51e05ad))

- Use 2nd-level webhook host caldrith.magmamoose.com
  ([`e2207c3`](https://github.com/MagmaMoose/caldrith/commit/e2207c306e7ce2aa13cae0f1bf90ffb12fa39504))


## v1.0.1 (2026-06-19)

### Bug Fixes

- Serve webhook at root path and align secrets with deployed infra
  ([`e537a99`](https://github.com/MagmaMoose/caldrith/commit/e537a9957269d2c3b2e2b92b96e4a4e705e4b153))

### Build System

- **deps**: Bump python from 3.12-slim to 3.14-slim in the docker group
  ([`49450d5`](https://github.com/MagmaMoose/caldrith/commit/49450d5a26d61f524af39bc32a4db403b145fc37))

- **deps**: Bump the github-actions group with 2 updates
  ([`9b4f3ad`](https://github.com/MagmaMoose/caldrith/commit/9b4f3ad44beb91cceccaeb0e2645eb5b74689d82))


## v1.0.0 (2026-06-19)

- Initial Release
