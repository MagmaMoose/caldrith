# CHANGELOG

<!-- version list -->

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
