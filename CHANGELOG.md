# Changelog

All notable changes to this project are documented here.
This file is generated from [Conventional Commits](https://www.conventionalcommits.org)
by [git-cliff](https://git-cliff.org) — do not edit by hand.


## [0.3.1] - 2026-07-11

### Documentation

- **access:** Roles need AGENTS.USE + MCP_SERVERS.USE to use agent tools ([`3afc86d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3afc86da40c3dc1698210b60481bef9bb1a8c3b8))

## [0.3.0] - 2026-07-11

### Features

- **chat:** AC-2 per-role agents + ACLs (L1/Engineer) ([`8961786`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/896178639a905d8908ee758f2661a58a4c8d3fd7))

### Documentation

- Add Admin Panel & access reference (accounts, roles, testing) ([`56fd6a1`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/56fd6a1038de03454da7afa61396c70e36cc8554))
- **spec+plan:** AC-2 curated agents + ACLs ([`ad3c578`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ad3c578e782b6a281f08b573ee1ec20cb862f8da))

## [0.2.0] - 2026-07-11

### Features

- **mcp-server:** Add _blocked_method read-only proxy decision helper ([`615974a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/615974a32d7a13c2c9b208b93c812dc438da4ae6))
- **mcp-server:** Reject non-GET proxy calls via ReadOnlyProxyMiddleware ([`3080d67`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3080d67dd0352084fd8093400298331e5a03b83d))
- **mcp-server:** Register ReadOnlyProxyMiddleware in build_server ([`8f420c3`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8f420c357eb4935fb4b10a6a09122a216de51409))
- **chat:** Add LibreChat admin-panel service (AC-1) ([`50f56e6`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/50f56e6df8177f88d7f6e31a71414ccc2718ed8f))

### Refactoring

- **mcp-server:** Tidy P0-4 test imports + drop plan ref per review ([`6dfa923`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6dfa923f3a37eb4de7b7368b27a9e993519b32c2))

### Documentation

- **spec:** P0-4 read-only charger-proxy enforcement design ([`27001c5`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/27001c521ae5fb23e6659110833cf78de2c3171d))
- **plan:** P0-4 read-only charger-proxy implementation plan ([`6be181d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6be181d6ea9315332eeadb4862d5c3e1c3d4425a))
- **backlog:** Reframe P1-2 — govern share-links, don't disable ([`65fa512`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/65fa512fb31eaca467abeedb87a5d1b09d064dc8))
- **spec+plan:** P3-1 CI test gate ([`693bcdc`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/693bcdc57fcd4a820b4823772b85e3a36bea7516))
- **spec+plan:** AC-1 admin panel + roles/groups ([`d69dc83`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d69dc83b933f0d94f5246fdeb7d1a77268484a14))

### CI/CD

- Run both MCP servers' pytest suites on PR and push ([`ac45de3`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ac45de331b1bc78edda897c92af1df32d99071e6))
- Run tests via 'python -m pytest' so the package resolves ([`34fe4d0`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/34fe4d03bd0ab8c87d70ad93bf6058790294d526))

### Chores

- Mark P0-4 (read-only charger proxy) done ([`3946902`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3946902591587247308a668f44eb310fb819ff43))
- P3-1 CI test gate implemented (pending push + branch protection) ([`98dda93`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/98dda937350eae279502d3a638ad00f9f27baa69))
- P3-1 CI green on GitHub; only branch protection remains ([`9456b70`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9456b70844d286f0ef23acae0a133d6c91119d51))
- Mark AC-1 done (admin panel up; noc-* roles created + assigned) ([`326dc14`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/326dc148e563ac39ad05575317053e47182da62a))

## [0.1.1] - 2026-07-10

### Features

- **chat:** Rename app to Adore Manthan.AI ([`560c7f9`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/560c7f9f90d954ce5973c70be621e636fbfc5c2b))

### Documentation

- Add production-readiness & access-control backlog ([`090cd82`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/090cd82919a6b353fdbddd0a697baed790abb721))

## [0.1.0] - 2026-07-10

### Features

- **mcp-server:** Read-only FastMCP server over the NOC backend ([`5ebcfdb`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5ebcfdb2b65b16d4cc3b1a0ca0210ccda90c68ff))
- **chat:** Phase 2 config — LibreChat + GLM 5.2 + NOC MCP (turnkey) ([`743b7a2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/743b7a22805bcf28f4a879ae9b2a84f7f5523d58))
- **mcp-server:** Add on-charger control-center API catalog tool ([`d380545`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d38054578e8851411a5439e4844d544dd58f51e7))
- **chat:** Add GLM vision models (glm-4.6v/glm-4.5v) for image input ([`7a99f4d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7a99f4d222a35b40d0fd0f70968aa39c0c5940ba))
- **chat:** Add glm-4.7 to the model selector for A/B comparison ([`8688b98`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8688b9816038523f2f47feab7b1c671119ac1149))
- **chat:** Expose all available GLM models in the selector ([`e51a431`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e51a431984c1959325617a0aad6f703738741dbc))
- **cms-mcp-server:** Read-only MCP for the legacy CMS (old NOC) ([`a163484`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a1634849bd2864c21dca51cf466b01bcca7f304c))
- **chat:** Show running version + commit id in UI, rename app to Manthan AI ([`5ece4d6`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5ece4d694204e5d9ecc7c2ddc3195568cb1f6f71))

### Bug Fixes

- **chat:** Allowlist internal MCP address + add .env init script ([`cdd0531`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/cdd053127155690a50f73020ea8c3fef3b95fdf8))
- **chat:** Point GLM endpoint at the Coding Plan base URL ([`388f87e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/388f87e9112602be3978fba2c4541efce5599fd3))

### Documentation

- **chat:** Add "respond in English" + agent tool-wiring notes to setup ([`6bbd993`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6bbd993dc0b4872729dfef5e15e6f69c61853c71))
- **chat:** Add local-run RUNBOOK for the LibreChat + MCP stack ([`8e2ddaf`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8e2ddaf77a3c2ef7e37e0ba8f5ff6e04b4147bf2))

### Build System

- **changelog:** Link commit ids + add bump-version release script ([`29d2351`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/29d2351295b4071cb54bf2922ee8a5967a1302b5))

### CI/CD

- **changelog:** Commit-generated CHANGELOG via git-cliff + CI ([`377a150`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/377a150b9aa39b67a6e5de9010edbaadf2173573))


