# Fleet target state and roadmap, v3 (15.10.2026; D1–D3 ruled, D7/D8 at the head default)

**Basis:**
- v1;
- the counter-checks: Fable ANCHOR-WITH-CHANGES, Opus ANCHOR-WITH-CHANGES (8 blocking), Codex REVISE (7 blocking);
- concept v2;
- the fleet audit of 15.10.2026;
- 14 per-repo assessments;
- the operator rulings in overnightworks/marketplace#80, including 15.10's "sichtbar besser und kleiner".

**Status:** draft for the operator's ruling; D1 to D3 ruled, D7 and D8 at the head default, D4 to D6 open. It moved here from the body of overnightworks/marketplace#80 on 17.10.2026 (quality#43), which was the fleet epic until it was closed into quality#2 on 18.10.2026. The operator's rulings it rests on are in [rulings.md](rulings.md).

**Landed since the v3 draft** (the sentences below are copied from the draft and brought current only where a landing made them false):
- quality v0.1.0 (bab8770) and v0.1.1 (75467b7) are tagged.
- quality v0.2.0 (b16b525, 17.10.2026) is quality#33, the legacy switch, not the Q1b engine. The Q1b engine therefore takes the next minor version after v0.2.0.
- The emergency-merge door (quality#37) landed (b38045d).

## 0. What changed against v1, and why
| # | Change | Raised by |
|---|---|---|
| 1 | **The area order is corrected.** Old tests are deleted **before** the architecture moves, after parity on the frozen code. v1 (and the head's text in #80) moved the architecture first, which means repairing tests that are about to be deleted (aco has 530 `monkeypatch.setattr` calls, songmaker 744 `patch(` calls at the import location), and parity can then no longer be measured. | Fable B1, Opus B1, Codex 1 |
| 2 | **Security comes now (Q0)**, not in wave 2 behind tooling. | all three |
| 3 | **Building the quality tool is counted** (Q1a/Q1b/Q3, about 12 lanes). | Fable B3, Opus B2 |
| 4 | **`aco land` requires the verdict only where the gate is adopted**; otherwise every repo stalls on day 1. | Fable B4, Opus B5, Codex 5 |
| 5 | **Every baseline lives in `quality/baseline.toml`**, which `loosened[]` watches (no `ignore_imports` side door). | Opus B3 |
| 6 | **The parity proof is named per area**: mutation only where affordable. | Fable B6, Codex 3 |
| 7 | **Coverage floors** (songmaker 93 %, vitest 93/90 %, presentator and home-tools 100 %) are dropped for a renewed area at its parity lane. Its mutation score (not below the old suite's) and changed-line coverage at report level replace them. Ruled D3, operator 15.10.2026, ~22:55: „die Grenzen sind Fantasiewerte von mir! nichts ist zwingend, neue Teststrategie wins“. | Fable B5, Opus |
| 8 | **The scoreboard makes "better and smaller" visible** (section C). | operator, 15.10.2026 |
| 9 | **Lifecycle classes**: dormant repos get the minimum plus an activation gate, not migration lanes. | Codex 7, Opus |
| 10 | **Converted specs keep their approval**; only new or changed lines need a spoken ruling (about 1,180 lines would otherwise swamp the operator). | Fable, Codex, Opus |
| 11 | **A reviewer stopgap applies from the ruling day** until each repo's ratchet lands. | Fable |
| 12 | **The waves are renamed Q0–Q5** (v1's W2c/W1b collided with webauth's slice names). | Opus |
| 13 | (v3) **Delta check:** the verdict names its mandatory jobs; admission control; an owner and an objective exit per wave; the dormant repos are routed through Q2; the work omitted from v2 (aco, agent-providers, marketplace) is restored. | Codex delta |

## A. Target state per active repo (unchanged from v1 except for 5)
1. The fleet minimum profile is enforced by a fail-closed `quality / verdict`. A repo may tighten it, never loosen it.
   - **SonarCloud is part of the minimum profile** (operator 16.10.2026: „0 Findings Toleranz für alle Repos“; „sollte das da nicht mit rein?“). Every repo has a SonarCloud project, and the gate fails on **any open finding on the PR** at every severity, except the one rule the README's [Dependabot exception](../README.md#a-dependabot-exception-for-dockers8431) sets aside. It reads the findings through the CI, so agents see them in the log and fix them without a browser.
   - The gate's Sonar check is quality's own `quality sonar findings`.
   - It replaced marketplace's `sonar-findings-gate`, which marketplace removed on 19.10.2026 (overnightworks/marketplace#140).
   - Existing debt is counted per repo and may only fall: webauth and agent-providers each hold 8 `githubactions:S8541/S8544` findings on their CI's `uv` lines. That is the fleet's "Security C".
   - Sonar complements the ratchets and does not replace them: it excludes tests, so it cannot see test duplication.
   - Operator step: the org secret `SONAR_TOKEN` must reach every repo, private ones included. Without it, the gate fails closed by design.
   - **GitHub Team, operator 16.10.2026.** This replaces the earlier "kein GitHub Team" ruling. `SONAR_TOKEN` is one org secret for all repositories, and rulesets requiring `quality / verdict` apply to private repositories too, so enforcement no longer rests on `aco land` alone. Concept v2 phase 3's "private repos after the Team plan" is unblocked.
   - **Organization rulesets, active since 16.10.2026, operator "alles ja":**
     - `main: PR only (all repositories)` (id 25224011): no deletion, no force push, PR required.
     - `release tags are immutable (all repositories)` (id 25224012): `v*` tags cannot be updated or deleted.

     The per-repository copies on webauth and quality were removed (one owner). Before the switch, no repository pushed to main without a PR in 14 days, apart from one-time aco adoption commits. `quality / verdict` becomes a required check per repository when it adopts the gate (Q1a-6, Q2), and org-wide once all have. The ruleset definitions later move into quality as code, with a drift check.
2. An architecture checker holds the repo's target contract, written as a picture plus rules in `docs/architecture.md`.
3. Size/CCN and clone ratchets apply to code and tests.
4. Specs are in the one criterion format, with approval, trace and verification kept separate.
5. **Tests prove behaviour through real entry points** and claim a criterion. Unit tests stay only where they pin a pure rule cheaper than an entry point can.
6. `aco land` enforces the gate where it is adopted. A daily check reports any merge that bypassed aco.

## B. The area cycle (per repo, per area; never a whole repo at once)
1. **Specs.** Every behaviour of the area becomes a criterion line, rescued from rulings, issues, mockups and today's tests. Converted lines keep their source as `Quelle:` and count as approved. New or changed lines wait for the operator's ruling.
2. **New tests from the specs, outside-in**, at the real entry points (API, CLI, browser). They are green on today's unchanged code.
3. **Parity on the frozen code**, with the proof the repo table names:

   | Proof | When |
   |---|---|
   | **M — mutation** | Pure modules, one module at a time. Mutation of `process` code only in CI, never locally: it spawns real shells (the 02.09 incident). |
   | **I — assertion inventory** | Every assertion of the old suite maps to a criterion or is ticked off. This is for aco's CLI families, where mutating the 9,297-line `cli.py` is impractical before the cut. It is an exception to the accepted mutation-parity condition, so it needs the operator's ruling (D7). |
   | **D — deletion-green** | Migration and fixture tests whose behaviour no longer exists. |

   **The protocol is pinned per area in the parity item:** mutator and version, the frozen production SHA, the mutant set or assertion list, both suites run against identical code, and the result attached by link. Every mutant or assertion only the old suite caught becomes a criterion plus a test, or is ticked off by the head with one sentence.
4. **Delete the old tests of the area.**
5. **Rebuild the architecture of the area under the new tests.** (The head's own text in #80, 22:12, had deletion after the rebuild. The operator's words fix only specs, then tests, then rethinking the architecture "nach sinnvollem Prinzip". Deletion before the rebuild is the sensible principle, because otherwise tests are repaired just before they are deleted. Raised to the operator on 15.10.2026.) The checker's baseline for the area drops to zero, and the contract pins it.

**Done when** the area's specs are ruled, its new tests are green, its old tests are gone and its checker violations are 0. Only the area in its cycle is frozen; security fixes are exempt.

**Good suites (D1, ruled by the operator 15.10.2026, ~22:40: „ja a“):** kiln, webauth, edge, gmail-cleanup and the core of presentator already test through entry points. Their tests are tagged with criterion IDs, the ones that kill no mutant are deleted, and steps 2–4 are skipped. This amends the 22:05 ruling ("tests are rewritten, not kept") for these suites.

## C. Scoreboard: how "better and smaller" becomes visible
**T0 is the fleet audit of 15.10.2026.** It is provisional; the exact T0 per repo is captured by that repo's Q2 adoption lane at its base SHA, and is the ratchet baseline itself. It is re-measured at every wave exit and weekly by `quality scoreboard`, a Q1b deliverable harvested from the audit scripts; it runs in the `scoreboard` workflow of overnightworks/quality (Monday 05:17 UTC and on dispatch), whose run summary holds the table. The table stands in this file and is re-measured by the scoreboard: **T0 → now → target**.

| What | Metric (per repo) | Direction |
|---|---|---|
| Smaller | production lines, test lines, test:prod | ↓ |
| Smaller where it hurts | files ≥ 800 lines, functions ≥ 60 lines, CCN > 15 (prod and test) | ↓ to 0 |
| Less duplicated | clone pairs (prod and test) | ↓ |
| Architecture | checker violations (by stable identity), import cycles | ↓ to 0 |
| Tests better | share of tests claiming a criterion; `patch`/`monkeypatch`/private accesses in tests; mutation score of renewed modules | ↑, ↓, ↑ |
| Specs | criteria, and the share with a passing proof | ↑ |
| Process | blocking findings per lane, review rounds per lane, defects escaped within 30 days (concept §8) | ↓ |

**Guard against cheating "smaller":** lines shrink only with the same or more proven behaviour. No criterion may lose its passing proof, and a renewed module's mutation score must not fall. The honest headline is therefore **lines per proven criterion**.

Some steps grow code on purpose (the Postgres test DB, security fixes, specs); the curve is judged per wave, not per lane.

**T0 now** (production / test lines; test:prod; files ≥ 800 prod/test):

| Repo | Lines (prod / test) | test:prod | Files ≥ 800 (prod / test) |
|---|---|---|---|
| songmaker | 107.6k / 154.8k | 1.44 | 22 / 49 |
| aco | 33.5k / 65.4k | 1.95 | 12 / 13 |
| presentator | 20.4k / 25.4k | 1.25 | 5 / 9 |
| agent-providers | 8.8k / 9.0k | 1.03 | 2 / 2 |
| webauth | 4.5k / 8.3k | 1.86 | 0 / 2 |
| atelier-2 | 134.3k / 205.2k | 1.53 | 38 / 68 |

**Visible architecture:** each repo's `docs/architecture.md` shows its target layers as a Mermaid picture. The scoreboard shows the before/after import graph (cycles in red) at T0 and at each area exit.

## D. Waves (entry → content → exit evidence → owner → rollback)
| Wave | Entry | Content | Exit, by link | Lanes |
|---|---|---|---|---|
| **Q0, now** (no gate dependency, parallel) | the ruling | • presentator #169 (HIGH, open since 03.10)<br>• edge alarm `subprocess` timeout (`src/edge_check/host.py:24`)<br>• the **reviewer stopgap** in lane.js: no new file ≥ 800 lines, no new test without a criterion or `defect:#n`, no new `patch(` at an import location | each fix with its regression proof; lane.js on marketplace main | 3 |
| **Q1a, quality v0.1** | Q0 dispatched | `overnightworks/quality` (public, the operator creates it or says yes) with gate.yml, hygiene, the fail-closed verdict and known-pass/fail fixtures. webauth adopts it **between OIDC slices**. `aco land` requires the verdict **where `quality/config.toml` exists**. **`quality/config.toml` names the repo's mandatory jobs; the verdict reads their results from the run itself, is red when a named job is missing or not successful, and removing a named job is a `loosened[]` entry.** Minutes are measured. | the three concept-v2 phase-0 proofs, plus `aco land` refusing a red verdict in webauth and passing an unadopted repo, plus a PR that drops a mandatory test job turning red | 4–5 |
| **Q1b, quality engine (the next minor after v0.2.0)** (serial, quality only) | Q1a | • size/CCN ratchet (harvested Python; lizard for TS/Rust; Svelte by lines)<br>• clone ratchet<br>• checker adapters (import-linter, dependency-cruiser, cargo-deny) with every baseline in `baseline.toml`<br>• `loosened[]`<br>• `fleet-quality scoreboard`<br>• a **repo-by-rule matrix** (enforced, report-only, ruled exemption or activation-gated) in quality's `docs/` | proven on aco and songmaker at report level, old scripts deleted (net lines negative) | 6–8 |
| **Q2, adoption** (parallel, one lane per repo) | Q1b | • every active repo: gate, checker contract at baseline, ratchets, T0<br>• every dormant repo: the minimum profile only<br>• **Admission control:** while a repo's adoption lane is open, the head dispatches no feature item in that repo (security exempt), and the lane regenerates its baseline against trunk right before landing | every active repo is at its baseline and can grow no violation; every dormant repo is green on the minimum profile; scoreboard T0 complete | 9 + 4 |
| **Q3, prerequisites** (parallel) | Q2 in that repo | • songmaker Postgres test DB per worker (material)<br>• agent-providers takes over its tests that songmaker holds (one owner)<br>• quality v0.3: spec lint L1/L12/L13 plus JUnit trace, proven on webauth<br>• the parity protocol proven on one area | each by link | 8–10 |
| **Q4, area cycles** | Q3 in that repo | section B per repo, in the order of section E | each area's done-when; the scoreboard after each area | ~85 |
| **Q5, dormant repos** | the operator pulls the repo | its area cycles, as in Q4 | as Q4, per area | 0 now |
| **Alongside** | after Q2, idle capacity | code-graph benchmark (mined real cases; adoption only at ≥ 10 pp recall@5); the operator's GraphRAG on kiln as a later arm | benchmark frozen, first comparison reported | 2–3 |

**Owners:**
- **Q0:** each fix's own item.
- **Q1a, Q1b and Q3's v0.3:** the quality epic in `overnightworks/quality`.
- **Q2 and Q4:** each repo's quality epic.
- **The wave as a whole:** quality#2 (Done-when 8). marketplace#80 was closed into it on 18.10.2026.

The head dispatches and reports, and each wave's exit proof is linked from quality#2 and from this roadmap.

**Rollback:**
- A wave or area that runs past 2× its lane estimate stops. Its freeze lifts, its open slices return to the epic body, and its baseline stays as the floor.
- Kill switch: the `aco land` requirement flag, plus pinning back to the last good quality tag.
- Old tests are deleted only in the parity lane, so before that lane lands nothing is lost.

## E. Per repo (lifecycle, area order, parity proof, lanes)
| Repo | Class | Area order after Q2/Q3 | Parity | Lanes |
|---|---|---|---|---|
| **songmaker** | active, **critical path** | 1. Postgres test DB<br>2. player/navigation (spec 0006, 41 rulings) with #1354<br>3. remaining frontend store areas<br>4. backend cycles `api_models↔db↔scoring`, then `jobs↔lifecycle↔cover_runner`, then workers off the HTTP router, then `api_helpers`<br>5. REQ conversion (106 REQs) per area | M per module (Stryker per store, mutmut per Python module, public CI) | ~29 |
| **aco** | active | 1. size ratchet<br>2. delete `access_jwt.py` (dead code, already settled by AGENTS.md)<br>3. shared rules (landing, freed, closable) out of `cli.py` into one board/report owner<br>4. per command family: criteria (953 converted), CLI tests with `proves` markers (201 IDs cited, 0 marked), parity, cut out a `cmd_*` module (~13 families)<br>5. one board server (#5)<br>6. git port<br>7. clone families in tests | I per family, plus M on each extracted `cmd_*` module | ~23–28 |
| **agent-providers** | active | 1. the ported tests (Q3)<br>2. the `process` owner<br>3. `claude/provider.py` split (renews `test_claude_provider.py`)<br>4. login probes out of `process`; prompting separated from the provider layer<br>5. `ProviderName` plus config #12: a breaking 0.3.0 plus consumer bumps (songmaker, atelier-2 if kept)<br><br>The AI-service slices (A1a2 onward, epic #18) continue, but not in the area that is cycling. | M (CI only for `process`) | ~14 |
| **presentator** | active | 1. #169 (Q0)<br>2. exhaustive contracts plus speech/copresenter **in its Q2 lane**, before #185 is built<br>3. lobby renewed with #185<br>4. product code out of `examples/`<br>5. one `deck.toml` owner<br>6. one speech start-up | D1 default (good core), M on the lobby | ~8 + #185 |
| **webauth** | active | 1. OIDC slices first (W2c, W1b, R2, D1, D2)<br>2. spec file (REQ-ADMIN taken over from songmaker, OIDC rulings)<br>3. framework-free core; one strict-JSON parser; `SessionCache` at its next natural release | D1 default | ~5 |
| **kiln** | active, new | gate with cargo-deny and `unsafe` forbidden; §2.2 converted in place. `validate`/`Frame` belong to its first lowering item, not to quality lanes. | D1 default | ~2 |
| **edge** | active | timeout fix (Q0); split the 1,137-line backup test | D1 default | ~1 |
| **home-tools** | active | layering cli > check > … ; finder tests into families | M | ~3 |
| **marketplace** | active | ruff; `scripts/*.py` independent of each other (its assessed import rule); a prompt-drift check; its root-layout and sonar gates move to quality | — | ~1 |
| **claudebot** | dormant (travel fallback) | minimum profile plus the auth table ("every handler refuses a stranger") in its Q2 lane | — | 1 |
| **hopin** | dormant, offline, public since 15.10 | minimum profile in Q2; identity and room scoping are bound to going online (#25) | — | 0 now |
| **gmail-cleanup** | dormant | minimum profile in Q2 | — | 0 |
| **ui-console** | decision D4 | fold into presentator (its only consumer), then archive | — | 0–1 |
| **atelier-2** | archived (D2, operator 15.10.2026, ~22:45: „ja a“, then „am besten Dienst löschen und Repo lokal löschen“): live units stopped and deleted, GitHub repo archived, local clone deleted | nothing | — | 0 |
| **foreman, claude-revive** | archived | nothing | — | 0 |

**Total:** about 115–125 lanes. At 4–6 disjoint lanes and about 1.5 h each, that is **4–6 weeks of wall clock**.
- **Critical path: songmaker**, about 29 near-serial lanes (3–4 weeks), with its freeze windows inside #1354.
- aco (~23 serial lanes in `cli.py`) runs in parallel to it.
- Real bottlenecks: `/tmp/probe-stack.lock`, one heavy mutation job at a time, and the operator's ruling queue.

## F. Coordination: recorded once, executable by agents
- **Fleet epic: quality#2** (marketplace#80 was closed into it on 18.10.2026). Its body links here instead of repeating. This roadmap is `docs/roadmap.md` in `overnightworks/quality`; the parity protocol is planned as `docs/parity.md` and the area cycle as `docs/area-cycle.md` (until they exist, section B above holds the cycle).
- **One epic per active repo** ("Quality: <repo>"), opened only when that repo enters Q2 (no item without a caller).
  - Its body is the per-repo roadmap: area order with paths, spec file, parity proof, target contract, freeze window and done-when.
  - Each area becomes its own item only at dispatch, and is cut by the breakdown workflow first.
- **Order across repos:** a `quality-q0`…`q5` label plus `Blocked by` with real item numbers; `aco next` already computes from them. **No milestones:** GitHub milestones are per repo, so one wave would need the same milestone in 14 repos, which is double bookkeeping next to label and epic. Milestones would be added only if the operator wants them as a view.
- **Agent contract:** every lane brief points at the area item's body. The area cycle, parity protocol and spec format live in quality's `docs/` so a fresh agent needs nothing else. The reviewer stopgap (Q0) and later the gate refuse what the documents forbid.
- **Rust trace convention** (kiln feedback, 15.10.2026): `fn kiln_ir_07__name()` trips `non_snake_case` under `clippy -D warnings`. The head's recommendation is the ID as a module (`mod kiln_ir_07 { #[test] fn name() }`), read from the test path in nextest's JUnit, which needs no lint exception. kiln has 36 criteria in `specs/kiln-ir.spec.md` (lane kiln#26). quality v0.3's trace reader accepts that form.
- **Re-baselining:** the scoreboard is re-measured weekly and at each wave exit, and the roadmap is re-checked at each wave exit. A baseline only falls automatically; it rises only by a cited ruling.

## G. Decisions for the operator
| # | Decision | Head's recommendation | Default if unanswered |
|---|---|---|---|
| D4 | ui-console: fold into presentator and archive | yes | fold |
| D5 | Converted spec lines keep their approval through `Quelle:`; only new or changed lines are ruled | yes | yes |
| D6 | No milestones; wave label plus `Blocked by` instead | yes | yes |

**D7 and D8 (15.10.2026, ~23:10):** the operator went to sleep without answering and ordered autonomous work "in our best sense, no questions possible". Both therefore stand at the head's recommended default until he overrules them:
- **D7:** aco's CLI families use the assertion inventory before the cut, and mutation on each extracted `cmd_*` module.
- **D8:** per area, the old tests are deleted before the architecture rebuild.

