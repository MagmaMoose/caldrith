# CHANGELOG

<!-- version list -->

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
