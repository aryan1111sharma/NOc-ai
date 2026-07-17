# Changelog

All notable changes to this project are documented here.
This file is generated from [Conventional Commits](https://www.conventionalcommits.org)
by [git-cliff](https://git-cliff.org) — do not edit by hand.


## [0.7.0] - 2026-07-17

### Features

- **cz-jira:** Cz_jira_export (QCH CSV export -> structured rows) ([`0c36d72`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/0c36d725c849a39b94171503c864760ed070a4b5))
- **cz-jira:** Cz_jira_stats (GET-only paginated QCH analytics, cap 2000) ([`a7e84e9`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a7e84e9b10b8b4e2f553e8c5149bcca00cd6b621))
- **cz-jira:** Cz_jira_project_meta (QCH statuses/types/priorities) ([`b2a18b3`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/b2a18b3ab29a015885e19c5338d82cdb7ea5de63))
- **cz-jira:** Cz_jira_list_dashboards (dashboards + QCH filters) ([`930effa`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/930effad3773c02c5a6b20a2082bcea763aa11bb))
- **agents:** Add clarify-before-invoke (slot-filling) policy to all 4 NOC agents ([`77ebdc6`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/77ebdc62c0ea252a1aca2a4b2ba5e33efe702514))
- **cz-jira:** Attach 4 analytics/export tools to NOC Engineer agent ([`a4fe425`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a4fe42583212d5fdccf9a91363020902c7b39424))

### Bug Fixes

- **cz-jira:** Strip CSV BOM, tolerate bad created dates, clamp export limit ([`3a4838e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3a4838e5ab48dbedf1ca3844d269727c77b30ee8))

### Documentation

- **spec:** Add daily email report + tool-health to Manthan loop (D10/D11/§13) ([`d1ad53c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d1ad53ccb84c9c69a5fe885f3bf2e872da2085f7))
- **spec:** Add GitHub-PR approval workflow for escalations (D12/§14) ([`9ecbe8d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9ecbe8dd30366149b460c6245389927531d891d2))
- **spec:** Add human steering channels for Hermes (D13/§15) ([`a21d168`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a21d1680dacf30baebb877651afc77c2ed05511a))
- **spec:** Hybrid engines — GLM 5.2 reviews, local Claude Code develops (D14/§16) ([`d9d6333`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d9d633309d67a98d8833e570b784206b3642affb))
- **spec:** Add program index — 3-feature Manthan improvement roadmap ([`c10598d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/c10598d6e364ebcebe2f8ee67dde3d4338b46ef2))
- **spec:** ChargeZone Jira analytics + export tools (QCH-scoped) ([`ea78c97`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ea78c973cfa1002f3fceedcc70e24f920bfc888e))
- **plan:** Implementation plan for clarify-before-invoke slot-filling ([`dfeae30`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/dfeae30d193ca035d3e2b90f921484524c67d8b5))
- **plan:** ChargeZone Jira analytics + export tools implementation plan ([`e87a8d6`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e87a8d6ba5b1a8c510e9631b6af8a12183fabc1f))

### Testing

- **cz-jira:** Register 8 tools; document analytics/export in README ([`e7b2f2e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e7b2f2e97b1f7389c6af94bf3d7b0fd548530a0a))

## [0.6.0] - 2026-07-17

### Features

- **cz-jira:** Scaffold package + QCH-scoped config ([`78dcfb4`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/78dcfb4d40635d13a20ac87ad0550c452c28c2bc))
- **cz-jira:** Basic-auth Jira client ([`61e60b2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/61e60b221ebcb990766bf093fced94dbda900952))
- **cz-jira:** ADF-flatten + issue/comment shaping helpers ([`070af85`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/070af85115312fb4a585ee7b377f692cfe2eee20))
- **cz-jira:** QCH key guard + project-scoped JQL builder ([`c4add1a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/c4add1a11b29245204240040d365b700809cb748))
- **cz-jira:** 4 read-only QCH-locked tools + server factory ([`15d5f06`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/15d5f063c2f9eed3c8ddc996026ffb87d84f9c5c))
- **cz-jira:** Entrypoint, Dockerfile, README ([`1d2807d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/1d2807da02ecc78c8a26a997262308dfe8c67b29))
- **cz-jira:** Wire chargezone-jira into compose + librechat (:8105) ([`483a600`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/483a60001b08e5ad61dae3f0b0d5b674b5a15d82))
- **cz-jira:** Attach cz_jira tools to NOC Engineer agent + allow :8105 ([`9830f8d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9830f8d279889dc33f5b90d0904350351ac8df64))

### Bug Fixes

- **cz-jira:** Block JQL scope-escape + read-only enforce + robustness (review fixes) ([`ac58f76`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ac58f761484528d10cd782b214c260b6209d0d8e))
- **cz-jira:** Eliminate ORDER-BY split scope-escape; conditions are WHERE-only ([`7c771b2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7c771b2bdf0e84a3937349a16cbfe95bbeebc330))

### Documentation

- **spec:** Manthan nightly self-improvement loop design ([`0d0b123`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/0d0b123781ee66d99f863748e7864be1ebc45a99))
- **spec:** Add propose-only feature-discovery to Manthan loop (D9/§12) ([`a7e6926`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a7e69263370c36e5037938da97cd46ff1be44384))
- **spec:** ChargeZone (Quench-scoped) Jira MCP server design ([`dbf0412`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/dbf0412f90def5832d2f88a5ed61375bd968a265))
- **plan:** ChargeZone (Quench) Jira MCP server implementation plan ([`51c29dc`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/51c29dc2c6f9ceaa1b9dfcd9b5b589e8ed6a0d1f))
- **spec:** Slot-filling feature + switch Manthan loop reasoning to GLM 5.2 ([`5e80d83`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5e80d83172de6c424be94a900d73f34ca1f43469))
- **cz-jira:** Add CZ_JIRA_* keys to .env.example ([`284fadb`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/284fadb9c99825ca8e728614758db64993b18ff5))

## [0.5.0] - 2026-07-16

### Features

- **cms:** Add device-command settings ([`e700b1e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e700b1e0945ddbd0d6857df8255a00eebd0eef95))
- **cms:** Capture CMS UserID on login for ExecuteApi audit ([`01e5ec1`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/01e5ec1803bbca3dcef18075fd916d2eae69623d))
- **cms:** Device-commands module — allow-list, path guard, token store ([`648bf09`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/648bf09b873d543a4487827140a7994fe64beefc))
- **cms:** Device-command network helpers (wrapped POST, catalog, correlation) ([`625a65a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/625a65a2145a66de498d813d45549cb1df997186))
- **cms:** Register device-query, gated-action, and read-log tools ([`8994e96`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8994e96d07b8ea2f6fddbc768ac07c54c902b9aa))
- **cms:** Add fleet fan-out settings ([`8cc933a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8cc933a46021b54202aa72cf9f72dea0cb770653))
- **cms:** Fleet status (ping-recency online) and output unwrap helpers ([`91a9ea5`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/91a9ea5a0c778b1dad3e3d81d464527505876cd2))
- **cms:** Bounded fleet fan-out engine with per-charger status classification ([`f6a39ac`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/f6a39ac0c66482b458a5a76c43d0f880eaafa637))
- **cms:** Cms_get_charger_firmware and cms_get_fleet_firmware ([`3c59c16`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3c59c16ff94df07cd9384e4dd469dd29dad47941))
- **scripts:** Both-NOC firmware inventory sweep + Excel export ([`87faf46`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/87faf462688447e7ef566852c85795cfb83237d3))
- **scripts:** List offline chargers in the firmware inventory ([`b91a81e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/b91a81eeeec77911feef2efb4446d868f5c36a64))

### Bug Fixes

- **cms:** Reject path-separator injection in device-command args ([`01cd38a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/01cd38ae9a2cd26d0f2eb55ea3ba1a86f0b9552c))
- **cms:** Never retry the ExecuteApi write (double-execute on flap); harden args ([`7b11cdd`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7b11cdd6259faf8567416d0186a82be7624f2d07))
- **cms:** Post DataTables actions form-urlencoded (silent empty-body bug) ([`302eaaa`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/302eaaa18afa15d4095466b07f279eef2ce9dca1))
- **chat:** Expose the 9 missing CMS tools to the NOC agents ([`1b89274`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/1b89274b9446c990d4f4a1beadec0a3723da6542))
- **cms:** Report a flapping CMS as a CMS error, not "charger may be offline" ([`0f87109`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/0f8710960427f618ebb9db2f23d35b377b5f9d3d))
- **cms:** Resolve ChargerID from the id map, not the fleet-map feed ([`4ecabaa`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/4ecabaae20309b6a6560be08d1f2f93aa9d27c4d))
- **cms:** Raise device poll budget 20s -> 90s (live-measured latency) ([`47e5190`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/47e5190d8d6886ef8fbfbb073c8e02a3fa57c558))
- **cms:** Raise on degraded DataTables reads; dedupe/case-fix fleet firmware ([`10cf722`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/10cf7225bd9b3828ab019d3da8f01f1a31b4e409))
- **chat:** Expose the firmware tools to the NOC agents ([`d8fe54e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d8fe54ef44c6538d3ed85a2fff46141c1b4c9ae7))
- **chat:** Guardrail against wrong-charger confabulation + stale NOC count ([`19f13a7`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/19f13a7b1df53248104ca95942b1fc968bf943c6))
- **chat:** Correct "Adore" -> "Ador" branding; show real version in footer ([`5cefb9f`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5cefb9f5e4ad053ecc10b1f64d48f5134e26b878))
- **scripts:** Make sweep_cms importable when run as a file (sys.path) ([`1a1a46d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/1a1a46df5e092c2efd0e24251787699396b177bd))

### Refactoring

- **cms:** Extract reusable query_device core from cms_run_device_query ([`d78e0d3`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d78e0d35e725db814655484a5a8f145cc18ec515))

### Documentation

- **cms:** Spec + device-API catalog for gated device-command tools ([`f1c55dc`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/f1c55dc37338f495927e7c9d92b9c15ebe8f8a01))
- **cms:** Implementation plan for gated device-command tools ([`6d848a0`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6d848a07266ab23233a822762648c6ccc6892f6c))
- **cms:** Document device-command tools + read/write boundary ([`3e2a5a9`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3e2a5a95f4b8f7ed443881488b2767d4f4c857b8))
- **cms:** Design for dedicated firmware/release tools ([`7e3c55c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7e3c55ca9efe20706cec5785ac588aed7340ff14))
- **cms:** Implementation plan for the firmware/release tools ([`346224a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/346224a6e68ecd9bca99bbdbb5f66ac90a97250d))
- **cms:** Document the firmware/release tools ([`4b361a6`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/4b361a6fb0127da49853fd335c7c7a10b29d955b))

### Testing

- **cms:** Device-command gate, path-guard, and end-to-end round-trip ([`37c8d13`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/37c8d13ebd611107bceafa0768298221edf4cba7))
- **cms:** Pin the _history_rows flap-swallow regression ([`af15a54`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/af15a544268a55a31bead58fb233dec1e1f212e4))
- **cms:** Fake the fleet-map feed with controllable ping stamps ([`d303daf`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d303daf1f71bdccb90424ff5123faa97b4e88962))
- **cms:** Set CMS creds in conftest so test files run standalone ([`f889cf7`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/f889cf7960196f2bfdccd90bd323ab766b28f205))
- **cms:** Make the dedupe test actually reproduce the bug ([`7d14461`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7d14461a35a92dace6ecdbbac3f262d37f320920))

## [0.4.3] - 2026-07-14

### Bug Fixes

- **noc:** Strip PTY login banner/prompts/echoed-command from SSH output ([`2424245`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/2424245b66cac7f96c5872d8da135107e863ce55))
- **cms:** Ride through transient backend flaps with backoff retry ([`8d38efc`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8d38efcfab4fae43e2a5c6f343691bfbcbbfdeb5))

### Documentation

- **backlog:** JR-6 — Ecog Service Desk ticket creation (future, draft-first) ([`db6e94a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/db6e94a27bcf776f7792d3b41580efd27f9d20e1))
- **cms:** Spec for transient-flap resilience in CmsAuthClient ([`03f0d68`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/03f0d68e959c7969d21d7a29ff8e168643ccd8a4))

## [0.4.2] - 2026-07-13

### Features

- **noc:** Add charger_ssh_prepare + charger_ssh_execute (two-step, audited) ([`7b4195a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7b4195a7bfcd6a1d05645706ef052d1d21747932))
- **agents:** Capture agents as config-as-code with export/restore (P3-4) ([`8909d6f`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8909d6f259b84453480c53c6c4a3e92c811f2a4a))

### Bug Fixes

- **noc:** SSH exit-code survives truncation; wire up audit logging ([`ca7f08d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ca7f08d69d60c194a65cd274579f08055502485e))

### Documentation

- **noc:** Document the two-step SSH command-execution tools ([`281fa88`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/281fa880765985429e4ebd508eaafe55cb4e5e69))

### Testing

- **noc:** Bound fake SSH SLOW sleep (30s -> 2s) so suite teardown is fast ([`06d8c97`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/06d8c9712a1323ca6a7001701015330631bf949e))

## [0.4.1] - 2026-07-12

### Features

- **ecog-sd:** Sd_set_session_cookie tool (validated in-chat cookie refresh) ([`b0a6d61`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/b0a6d617fbb556e64f930d22c9496c8e6d75a224))
- **noc:** Add SSH-exec config settings + websockets dependency ([`3ff747b`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3ff747b93494509399f61f3484d262a1eafce962))
- **noc:** Add single-use SSH confirmation-token store ([`491eb67`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/491eb6763500c3016806008eb2c07dced9d75c26))
- **noc:** Add ANSI stripping + sentinel exit-code parsing for SSH output ([`b031ba7`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/b031ba7be1e8cb6c0d888d366a3cf2d8d7f2cc41))
- **noc:** Add _run_ssh_command WebSocket round-trip + fake SSH server ([`e0e0ec8`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e0e0ec807844c6ae29c8d0bb075bd95250569b01))

### Bug Fixes

- **docgen:** Persist files to a volume + serve PDFs inline ([`eb41f39`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/eb41f394bb0f640be189804e17d182a72041076a))

### Documentation

- **spec:** NOC SSH command-execution tools (two-step, audited) ([`dcbc291`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/dcbc2918bc302ff5b9c85898fce4c9127683a348))
- **plan:** Implementation plan for NOC SSH command-execution tools ([`22e17a2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/22e17a29384782e4b2d633a16039c8d82a4d5940))

## [0.4.0] - 2026-07-12

### Features

- **jira-mcp:** Scaffold package + config ([`dfde143`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/dfde1431b3d450bc67167616a389129c1ffa0c17))
- **jira-mcp:** Basic-auth httpx client ([`fe8bdab`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/fe8bdaba7e621ac3891f955c81d4c25daf44fcf3))
- **jira-mcp:** ADF flattener, trimmers, and _get_json ([`bbe5156`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/bbe5156cd23ca3dc12fefc847d930267f1b15f49))
- **jira-mcp:** Jira_search (JQL) tool ([`fff9f09`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/fff9f096a2186265db7e1ab68521d8a71556b3c8))
- **jira-mcp:** Jira_get_issue tool ([`7265af8`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7265af80c07589bbe6ed70261867c15256f96442))
- **jira-mcp:** Jira_get_comments tool ([`90dbbe0`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/90dbbe0c9a9090defdeba47d6d72a70382381826))
- **jira-mcp:** Jira_get_issue_transitions (read-only status view) ([`558906c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/558906ca42b056b5b01784db559b2ed2e7c0e18c))
- **cms:** Add _post_json helper for legacy AJAX POST actions ([`2495178`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/2495178590ef1234fee1098135e1c511254009c0))
- **cms:** Add _datatables_body helper for per-charger log actions ([`94b1e3e`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/94b1e3e9c5851815ce249927e1bce547aaf4b91e))
- **cms:** Add cached QChargerID -> numeric ChargerID resolver ([`e25cbab`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e25cbab24e63f1da5c2cd5528e3e57c96c1f53f7))
- **jira-mcp:** Entrypoint, Dockerfile, README ([`33d262c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/33d262cd058efd9d53f11215b07277e392267a28))
- **jira-mcp:** Register Ecog Jira MCP in compose + librechat (port 8102) ([`d601da1`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d601da166bd1154c16a3d0cbdea39ddd25cd649d))
- **cms:** Add cms_get_charger_details tool (metadata + live status) ([`812bfa0`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/812bfa0a9e934f2ec34678955b1eb242cea0f013))
- **cms:** Add cms_get_charger_sessions tool ([`4e96973`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/4e96973e754ebac1348b69c02f6cb1ac55942c81))
- **cms:** Add cms_get_charger_alerts tool ([`9f4462b`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9f4462bee0063cae395733ed7efe29e81a202339))
- **cms:** Add cms_get_charger_action_history tool ([`a5725b5`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a5725b555ba61d3914b6a25c4116501db4fc087b))
- **cms:** Add cms_list_charger_actions catalog tool ([`d992b62`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d992b622e93085e6da2516fecfc1616fa5a46c1e))
- **ecog-sd:** Scaffold package + config ([`d20bb0c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d20bb0cc826d6b1c819b6bd6c1bb0ca6152ef57a))
- **ecog-sd:** Cookie serialization + cache helpers ([`5ffb224`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5ffb22446d63c72eebcedfc1b716e895315675b7))
- **ecog-sd:** CookieAuthClient with refresh-on-logout + single-login concurrency ([`536a99a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/536a99a62fce4322b2d082449455a36d7f35b8d8))
- **ecog-sd:** Playwright password login + CAPTCHA guard ([`6913c7a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6913c7ae1f4d4f77e202764aad4b3f2a74e7249f))
- **ecog-sd:** Service-desk trim/HTML helpers + client/server factories ([`bc32aef`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/bc32aefa5bc06f5ff56f374fb699fa792c9c34dd))
- **ecog-sd:** Sd_list_requests tool ([`8b5a22c`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/8b5a22cea778569e3fa52e3aec43210d978f8cfe))
- **ecog-sd:** Sd_get_request tool ([`4841129`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/484112945420052f4c9533b042984aa844fd621e))
- **ecog-sd:** Sd_get_request_comments tool ([`3b74334`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3b74334e82f6d2a826c6603b4439f9d0bb52117f))
- **ecog-sd:** Sd_list_servicedesks tool ([`619ed18`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/619ed180769102f9c07e512a9c3db1ea98243f44))
- **ecog-sd:** Entrypoint, Playwright Dockerfile, README ([`2376141`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/2376141edd40d39ee06af8e73e7a741b066f59bd))
- **ecog-sd:** Register Ecog Service Desk MCP in compose + librechat (port 8103) ([`5d327c9`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5d327c961c65769eee48ce6e7b522fe01e975c83))
- **noc:** Add control_center module with proxy-GET helper, wired into build_server ([`142754a`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/142754a1d9987b9fe97bcd3e109ec27f091ce8fd))
- **noc:** Add charger_get_charging_status tool + shared fake NOC harness ([`b3a1c05`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/b3a1c05ead2aa7e602dd573b87fa4f497538cac9))
- **noc:** Add the remaining 12 Tier-1 control-center tools ([`0b6ea17`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/0b6ea1700c9d4d9a979c2729aec3bbba28cf7123))
- **docgen:** Add document-generation MCP server (Excel/Word/PDF downloads) ([`1fa6e7b`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/1fa6e7becfae76a605c1726f57d9f66dd0686811))

### Bug Fixes

- **jira-mcp:** Friendly 404, ADF mention/emoji text, doc fixes ([`d2c0916`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/d2c091670a2ac9b9ede47cd84356cc9234c122ef))
- **cms:** _trim preserves boolean False (inactive charger, input-required) ([`e8afaef`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e8afaefc09f81bfe69babe78ae0e9a13b865450d))
- **cms:** Surface last event/ping timestamp; drop stale deferred note ([`e3a1c0b`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e3a1c0bf1f0ee3bcb518865ce1ba352207ca016c))
- **ecog-sd:** Use port 8104 (8103 already taken by docgen-mcp-server) ([`1b2ea77`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/1b2ea7716266fa34ccd7afe9a98b32aa910ba5ae))
- **ecog-sd:** Expand serviceDesk/requestType, cookie-send robustness, captcha+html-strip hardening ([`45707bf`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/45707bf506fb366ba03bba1d860a1082637f554a))
- **ecog-sd:** Pin playwright==1.48.0 to match Docker base image browsers ([`bb641f0`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/bb641f0fedf5c36652f583868da88474a914b34a))
- **ecog-sd:** Correct Atlassian login selectors + clear verification-challenge error ([`9c6cfd2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9c6cfd2f76ea84364ae88e1c2b13dce5dca981c6))

### Refactoring

- **cms:** Allow injecting an httpx client into build_server for tests ([`35ea6fc`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/35ea6fc914ad1b871be35cd73dbff03314f4d3f9))
- **noc:** Allow injecting a client into build_server for tests ([`345fbb2`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/345fbb2b2c345f36e19a3b65256013aa16ef625d))

### Documentation

- **backlog:** Service-manager-only access model; defer per-role enforcement (AC-3..AC-6) ([`c688774`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/c688774016f4852c91c7e1878ac115f43b01949e))
- **spec:** Ecog Jira read-only MCP pilot design ([`131f3b5`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/131f3b5bc6939fd2659ef51174db6ed45e315600))
- **plan:** Ecog Jira read-only MCP implementation plan ([`fd7c893`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/fd7c893567cce5cd2fb2b5163376ea89c60c9c97))
- **spec:** CMS ChargerSession per-charger tools (sessions/alerts/actions) ([`7ba3739`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/7ba3739454fd0e037ef2cb7e5c86746aa831e777))
- **plan:** Implementation plan for CMS ChargerSession per-charger tools ([`a658eab`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a658eab640587162e7a44d00aa66e602f26f8408))
- **cms:** Document the 5 new per-charger tools ([`6554275`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/6554275a17d1f8ff0095785d17e2ab02c4002a00))
- **spec:** Ecog Service Desk MCP (session-cookie + Playwright auto-reauth) ([`f6b79d4`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/f6b79d45932e3a60cfc52098df336c61b71dfe02))
- **plan:** Ecog Service Desk MCP implementation plan ([`ef5eb84`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ef5eb84253ceed084522e3f9e87af5f8e68d6b49))
- **spec:** NOC on-charger control-center Tier-1 tools ([`75b3909`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/75b3909d39aed9c58fdcc42b05a07a9d27aaf404))
- **plan:** Implementation plan for NOC control-center Tier-1 tools ([`922068b`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/922068bd05064fbf63f4a02b0df7a0e166be4b1d))
- **noc:** Document the 13 Tier-1 on-charger tools ([`a2ab404`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/a2ab404861f3ccf73e9f160f4d7676f6a6b03cb7))

### Testing

- **jira-mcp:** Assert the server is read-only (no write tools) ([`3fa66b4`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/3fa66b429a135530ea7a669227ac67f21379156d))
- **ecog-sd:** Assert server is read-only (no write tools) ([`5f4ca5d`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/5f4ca5d38f64ae1001fb2ce19ef47113c66a13d3))
- **ecog-sd:** Update port assertion to 8104 ([`9795fb8`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/9795fb89aeb1653487ed7d460ccb6645c348494f))
- **noc:** Assert composite partial-failure; add 'online' to tool docstrings ([`92a0fbd`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/92a0fbd92fee6400eab3621111878194e7a6177e))

### CI/CD

- **jira-mcp:** Add jira-mcp-server to the pytest matrix ([`ee7ba23`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/ee7ba23bf73a12087b751dc2dcfe3104b196e297))
- **ecog-sd:** Add ecog-sd-mcp-server to the pytest matrix ([`e8adb9f`](https://github.com/Quench-EV-Charger/noc-mcp-agent/commit/e8adb9f06847b148a981e3a6b7d8a07d2618ab43))

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


