# Rulings

The operator's rulings and the facts and head answers they rest on, harvested from
overnightworks/marketplace#80 (the fleet quality epic) on 17.10.2026. Quotes are verbatim, in
the operator's language, with their dates. Nothing here is paraphrased into a stronger claim.
The roadmap that follows from these rulings is in [roadmap.md](roadmap.md), which also holds the
later rulings D1 to D3 and the 16.10.2026 rulings in the place where they act.

## Wish (operator, 14.10.2026, verbatim)
„zu E1 und .env => also so checks sollten alle repos haben falls die fehlen?“

## Facts (head's read-only scan of ~/git, 14.10.2026)
| Repo | `.env` in .gitignore | secret scan in CI | compose file | port lines not bound to 127.0.0.1 |
|---|---|---|---|---|
| songmaker | yes | **no** | yes | **1** (docker/docker-compose.ci.yml:60-61, owned by songmaker#1355) |
| agent-presentator | yes | yes | yes | 0 |
| agent-claim | yes | yes | no | – |
| agent-providers | **no** | **no** | no | – |
| webauth | **no** | **no** | no | – |
| claudebot | **no** | **no** | no | – |
| home-tools | **no** | **no** | no | – |
| edge | yes | **no** | yes | 0 (its CI guards ports) |
| marketplace | **no** | yes | no | – |
| hopin | yes | **no** | no | – |
| claude-revive, foreman, gmail-cleanup, ui-console | **no** | **no** | no | – |
| atelier-2 | **no** | yes | yes | 0 |

## Proposal (to refine at the pull)
- **One reusable workflow here in marketplace** (`workflow_call`), the same pattern as the fleet's SonarCloud gate. Each repo consumes it in one line. Its checks:
  1. a secret scan of the diff and history (the tool the existing scans use);
  2. `.env` (and `*.env`) ignored, failing if a tracked file matches;
  3. only where compose files exist: no published port without `127.0.0.1:`, and no `0.0.0.0`.
- **Adoption** is one small PR per repo (add the `uses:` line plus `.env` to .gitignore). Each repo gets its own item or a bundled lane, cut at dispatch.

Neighbours: overnightworks/edge#2 (its CI port and template guards are the model), songmaker#1355 (CI port binding), home-tools#10 (secret wizard).


## Wish (operator, 15.10.2026, verbatim): pulled now and broadened
„bei allen auch gucken! ob sauber gearbeitet wird, test strategy überall stimmmt etc. wir müssen und vor code explosion und test explosion schützen. am besten in ci auch einbauen und prüfen. das geänderter und enuer code auch in jedem repo immer erst guckt. gibt e schon ein owner. ist da richtige architektur konzept irgendwoe schon vorhanden und wir reihen uns eine etc... oft schreiebn agenten einfach neu ?> ds kann man aglau bich auch prüfen. und wir müssen das eindämmen, das der code wartbar und strukturiert ist. überleg dir die richtiugen konzepte => und ALLE unsere repos sollen die gleiche nsachen in ci haben. ggf am beisten ein CI repo selbst. das alle wichtigen sachen prüft?“

What this adds to the hygiene gate:
- **Size and complexity ratchets.** The marketplace code policy already rules 800 lines per file, 60 per function, CCN 15 and a duplication baseline. This item makes every repo enforce them.
- **A guard against test explosion.**
- **Owner first:** new code that duplicates or parallels an existing owner turns red.
- **Architecture conformance:** import contracts, and a decision record to line up with.
- **One shared definition for every repo.**

A read-only fleet audit with real tools (lizard, jscpd, git numstat) is running first. The concept and the cut follow from its numbers, and this body is replaced at the cut.


## Further wishes (operator, 15.10.2026, verbatim)
„nachweisbar genialen code produzieren“ · „was noch fehlt! sind specs? … jeder e2e test sollte ein spec prüfen? … weg von unit test? … gherkin nutzen? ich will ein konzept das alle repos trägt!“ · „nicht 0% unit tests => aber ein unit test muss sinn machen … alles was wir haben braucht eine anforderung … anforderungen sollten sich nicht widersprechen … überall gleich … ein tüv audit nach strengster norm bestehen … vom prinzip … was jedes repo haben sollte … was man in ci wie prüft … verbesserung: alle repos profitieren => gleiche tools … was es noch nicht gibt: eigenes konzept, die perfekte ci … per llm spec widersprüche ab und zu prüfen … konsistenz“

Head direction (to be argued in the concept, counter-checked, then ruled):
- **"Spec → proof → gate".** One shared requirements format, harvested from the three systems that already exist (songmaker `docs/requirements` + `acceptance.toml`, atelier-2 `docs/requirements`, aco `specs/*.spec.md`); no fourth one is invented.
- **Bidirectional traceability** in CI: no rule without proof, no test without a rule or a defect reference, no unknown ID.
- **Proof quality** through mutation testing on changed code.
- **Technical code** traces through the owner map and decision records ("derived requirements").
- **Gherkin as a writing style only**, with no step-glue runtime.
- **LLM consistency audits** run on a schedule as advisory distributor items, never as a hard gate.
- **Existing tools are adopted** where they exist (OpenSSF Scorecard, SLSA provenance, osv-scanner, a requirements-as-code tool if it fits). Own tools only fill the gaps.
- **One versioned reusable gate**, with updates propagated by bot PRs.
- **Maturity:** each check moves report-only → ratchet → hard gate.
- **The pilot is webauth.**

Research running: the fleet audit, norm principles and existing tools, and a comparison of the three spec systems.


## First adopter: kiln (operator, 15.10.2026: "die kiln session … könnte einer der ersten Profiteure sein … DB-Neuentwicklung")
These are provisional answers to kiln's two clashes; the concept confirms or refines them.
- **TLA+ models coexist under `specs/`**, for example `specs/kernel/*.tla`. The shared checker reads only `specs/**/*.spec.md`, with the glob configurable per repo. A **model check (TLC report) is a proof kind** next to unit, integration and browser.
- **Rust claims use a test-name convention**: the ID in snake case, then `__`, then a description (`fn kiln_ir_07__absent_key_is_branchable()` claims `KILN-IR-07`). The report is cargo-nextest JUnit. There is one ID per test.


## Operator ruling (15.10.2026, ~21:48): no copier, one door
„kein copier! … copier ist shice! und will ich wirklich 5 verschiedene tools die ich nicht durchblicke? … ggf doch selbst bauen?“

Head proposal, pending the operator's yes:
- **One front door.** Our own `fleet-quality` is the only command, with one config per repo and one plain-German report.
- **No copier.** Managed files are owned by the quality repo, generated and never hand-edited. Repo-owned files are the specs and the owner map. Sync is a plain checksum comparison with no three-way merge: a difference turns CI red.
- **Built ourselves (logic and process):**
  - managed-file sync, and propagating version bumps as PRs (instead of copier and Renovate);
  - "only new findings" (instead of reviewdog and diff-cover);
  - specs, trace, owner check and ratchets;
  - doctor, the spec audit and the quality curve.
- **Never built ourselves (specialist knowledge or world data):**
  - the language-standard linters and test runners: ruff/pyright, eslint/tsc, clippy, pytest/vitest/nextest;
  - gitleaks;
  - osv-scanner;
  - the mutation engines.

  These run invisibly under the one door.


## Operator ruling (15.10.2026, ~21:50): "ja! ich agree => ein fleet quality"
These points are agreed after the head's second round of pushback:
- **One door.** Our own `fleet-quality` is the only command, with one config per repo and one report that names the rule, the reason and the fix for every finding.
- **Build logic and process ourselves; never build specialist engines** (language linters and test runners, gitleaks, osv-scanner, mutation engines). They run underneath the door.
- **No line cap** (operator ruling 18.10.2026, replacing the hard size budget of about 3,000 lines): „wenn es mehr braucht braucht es eben mehr, aber natürlich soll der code gut sein!“ The tool still passes its own gate: its size baseline only falls, and every new function stays under the size and CCN thresholds.
- **No copier and no own write-all-repos token.** The quality repo carries plain `vX.Y.Z` tags, and Dependabot, already built into GitHub, opens the bump PRs. For that reason the head now prefers a **dedicated `overnightworks/quality` repo** over marketplace; the counter-check is to settle it.
- **Phases.** Phase 0 is enforcement plus hygiene in one day: a required check, a secret scan and `.env` everywhere. Phase 1 is the specs and trace pilot in kiln and webauth. Phase 2 is the fleet.
- **The success measure is fixed before building.** Findings and review rounds per lane, and defects found after landing, are taken from the lane journals as the baseline.
- **GitHub Team (about $4 per month)** gives enforcement on the private repos. The operator does the purchase himself; confirmation is pending.


## Operator, 15.10.2026, ~22:02 (verbatim, abridged)
„kein github team … für unsere sache will ich einen plan für alle repos … ggf fehlt noch ein knowledge graph, ein lokaler? falls es das noch nicht öffentlich gibt mit llm => dann bauen wir hier auch ein repo => ggf nutzen wir dann unsere neue db … ich bin sogar radikal … nicht refaktorieren >= sondern alte tests einfach wegschmeißen und nach neuen regeln dann schreiben? … als erstes die architektur hinterfragen … jedes repo sollte auch einen architecture checker haben … erst ein ziel und dann eine roadmap! und die muss dann befolgt werden! und das muss von einigen agenten gegengeprüft und hinterfragt werden … in der zeit sollten dann vermutlich die repos nix neues machen … erstmal den plan dafür irgendwo machen“

### Head answer (pushback given in chat)
- **No Team plan.** Enforcement goes through `aco land`, which refuses without a green `quality / verdict`. Agents land only through aco; only the operator himself can bypass it.
- **Tests: radical, but by evidence.**
  - Tests that kill no mutant are deleted outright.
  - Clones become parametrized families.
  - The rest is replaced per area, spec-first. Old tests go only when the new ones pass and catch at least as much (a mutation score at or above the old one).
- **No fleet-wide freeze.** A rolling freeze applies per repo during its migration, with security fixes exempt.
- **Knowledge graph:** the operator's own rule (marketplace AGENTS.md: a named consumer, and a measurement that beats plain search) applies. Research what exists first, then measure on real duplicate cases. kiln is not the substrate while it is experimental.
- **Architecture first, plus an architecture checker in every repo** (import-linter, dependency-cruiser, crate rules). Agreed.
- **Process:**
  1. Per-repo architecture and test assessments (running).
  2. One fleet target state plus a roadmap.
  3. A Fable, Opus and Codex counter-check.
  4. The operator's ruling.
  5. `aco next` follows the roadmap.


## Operator rulings (15.10.2026, ~22:05)
1. **Tests are rewritten, not kept.**
   - The operator: „wir haben doch den Stand in der Historie … wenn ein neues Konzept da ist, warum die alten Tests behalten? lieber weg und sauber neu … zu dem Zeitpunkt sollte sich der Code nicht ändern“.
   - Head condition, accepted: **mutation parity**. On the frozen code, the old suite (from history) and the new suite are both run under mutation. Every mutant only the old suite killed becomes a new criterion plus a test, or is consciously ticked off.
   - **Order per repo:**
     1. specs;
     2. new spec tests at the real entry points (behaviour, no internals);
     3. delete the old tests after parity;
     4. **then** the architecture changes, protected by the new behaviour tests.
2. **A rolling freeze per repo** during its migration. Agreed.
3. **A code knowledge graph as its own project.** The operator wants an LLM-based vector GraphRAG with embeddings, possibly on "our new DB" (kiln).
   - Head conditions:
     - it is its own repo and project, and **the quality gate never depends on it** (an MCP helper for agents, not a gate);
     - its storage sits behind an interface: start on a proven store (e.g. SQLite with a vector extension), and kiln becomes a second backend once ready (a real workload for kiln);
     - it is measured: recall@k of the true owner on mined real "reimplemented although an owner existed" cases must beat ripgrep plus `similar_methods`.
   - A research pass on existing tools is running.


## Operator ruling (15.10.2026, ~22:12): the migration principle
„erst müssen natürlich die Specs da sein! … danach die Tests effizient schreiben … Punkt zwei: Architektur überdenken! wo falsch und damit zusammen Architektur und Tests neu schreiben wenn möglich … nach sinnvollem Prinzip“

**Principle: pin the outside, rebuild the inside.** It runs per repo and **per area**, never a whole repo at once:
1. **Specs complete.** Every behaviour is a criterion line, rescued from rulings, issues, mockups and today's tests. The operator rules them.
2. **New tests from the specs, outside-in only.** They go through the real entry points (API, CLI, browser), with no internals, so they pass on the current code and keep passing under any internal rebuild.
3. **Architecture reviewed and the target fixed.** Where it is wrong, it is rebuilt under the protection of the new tests, and the repo's architecture checker pins the target.
4. **Old tests deleted** after mutation parity (the old suite from history vs the new one).

An area is done when its specs are ruled, its new tests are green, its architecture matches the target and its old tests are gone. Only the area in migration is frozen.

Before any area starts, every repo gets its ratchets and architecture checker at baseline (1–2 lanes), so nothing gets worse during the migration.

## Operator, 15.10.2026, ~22:20 (verbatim)
„ok und wie immer push back >= wo du verbesserunge siehst ect! => wir müssen die sachen koordienieren festhalnet und für agenten umsetzbar machen! am ende muss sichtbar sein => das der code auch besser ist und kleiner geworden ist >= die architektur besser geworden ist etc!“

Head pushback (given in chat):
- "Smaller" alone invites deleting tests or features, so the measure is lines per proven criterion, guarded by no lost proof and no falling mutation score.
- Some steps grow code on purpose (Postgres test DB, security fixes, specs).
- T0 lived only in /tmp and is now recorded here.
- No milestones (they are per repo, so one wave would need the same milestone 14 times).
- The head's own area order of 22:12 was wrong (see D8).

## Rulings recorded inside the roadmap

These rulings are quoted verbatim in [roadmap.md](roadmap.md), where they act; this list only points there.
- D1, 15.10.2026, ~22:40: „ja a“ (good suites, roadmap section B).
- D2, 15.10.2026, ~22:45: „ja a“, then „am besten Dienst löschen und Repo lokal löschen“ (atelier-2 archived, roadmap section E).
- D3, 15.10.2026, ~22:55: „die Grenzen sind Fantasiewerte von mir! nichts ist zwingend, neue Teststrategie wins“ (coverage floors, roadmap section 0, row 7).
- 16.10.2026: „0 Findings Toleranz für alle Repos“; „sollte das da nicht mit rein?“ (SonarCloud in the minimum profile, roadmap section A, point 1).
- 16.10.2026, "alles ja": the organization rulesets (roadmap section A, point 1).
- 16.10.2026: GitHub Team replacing the earlier „kein github team“ of 15.10.2026, ~22:02 (roadmap section A, point 1); #80 holds no verbatim quote for this ruling.

## Operator ruling (18.10.2026): one shared CodeQL workflow

„b macht sinn“ (one shared CodeQL workflow in quality instead of a copy per repository, #64).

## Operator ruling (19.10.2026): the env-file fix runs exactly as printed

„Immer exakt (empfohlen)“ (the env-file fix, [#86](https://github.com/overnightworks/quality/issues/86)).

It chose the fix `git --literal-pathspecs rm --cached -- <shlex-quoted name>`, a hint instead of a command for a name that is not printable UTF-8, no home-made encoder, and, for the run order of the door's lines, option (c): `cli.main` makes stdout line-buffered; the head's confirmation that this is the cleanest drew no objection.

## Operator ruling (19.10.2026): document first, then an allowlist

„Erst Doku, dann Erlaubnisliste (empfohlen)“ (`network_mode: host` in compose files, [#78](https://github.com/overnightworks/quality/issues/78)).

## Operator ruling (20.10.2026): stop the workflow commands and escape the text

Asked: „Befund 132: Dateinamen oder Compose-Texte aus einem Repo können im CI-Log GitHub-Befehle auslösen und so gefälschte Warnungen im PR erzeugen. Der Schaden ist gering, aber es ist eine Lücke im Sicherheits-Gate. Wie schließen wir sie?“ Options: „Beides (empfohlen)“, „Nur abschalten“, „Nur escapen“.

„Beides (empfohlen)“ ([#89](https://github.com/overnightworks/quality/issues/89)).

In GitHub Actions the door stops workflow commands with a per-run token, and repository text prints its control characters escaped through one boundary. The stop landed first (#89, Part A); the escaping follows as its own item.

## Operator ruling (21.10.2026): Sonar's Dependabot exception, applied on pull requests

[#98](https://github.com/overnightworks/quality/issues/98). On 20.10.2026 the operator had chosen one `quality/config.toml` entry per exact `FROM` line („Eng begrenzte Ausnahme (empfohlen)“); the plan review corrected that premise and he re-ruled in two answers.

1. The re-ruling. Asked: „Neue Fakten zu Sonar S8431: Sonar setzt seine eigene Dependabot-Ausnahme nur auf main um, bei PR-Analysen nicht … Wie soll das Gate es lösen?“ Answered: „Sonars Ausnahme anwenden (empfohlen)“. The gate applies Sonar's own exception on pull requests too, for this one rule, with no entry per line and no config table.
2. The detail. Asked: „Wann soll das Gate die Sonar-Regel S8431 nicht zählen? Sonar selbst prüft auf main nur, ob die Datei .github/dependabot.yml existiert, nicht was darin steht.“ Answered: „Wenn dependabot.yml existiert (empfohlen)“. The rule does not count when `.github/dependabot.yml` exists in the checkout; the file is never read.

## Operator ruling (20.10.2026, answered 21.10.2026): CLAUDE.md is absent or exactly `@AGENTS.md`

[#95](https://github.com/overnightworks/quality/issues/95). On 20.10.2026 the operator ruled in marketplace#195: a repository's `CLAUDE.md` is absent or exactly `@AGENTS.md`, so Claude, Codex and Grok read the same text („ich will kein auseinanderlaufen von claude.md und agents.md!“). The hygiene rule `claude-md` checks it. On 21.10.2026 he answered two lines.

1. E18. Asked: „Bei Rot nennt die Meldung nur den Dateinamen und nie den Inhalt der CLAUDE.md, weil darin ein Geheimnis stehen könnte. Einverstanden?“ Answered: „Nur Dateiname (empfohlen)“. The finding names the file and never shows its content.
2. E20. Asked: „Die Lösung, die die Meldung vorschlägt, ist ein Satz und kein Befehl: „Eigene Zeilen nach AGENTS.md verschieben, dann nur @AGENTS.md stehen lassen, oder die Datei löschen.“ Ein kopierbarer Befehl würde die Zeilen löschen und könnte über einen Symlink sogar AGENTS.md überschreiben. Einverstanden?“ Answered: „Satz, kein Befehl (empfohlen)“. The fix is prose that tells the operator to move the lines into `AGENTS.md`; it prints no command, since a copied command would lose the lines, or `AGENTS.md` itself through a link.

## Operator ruling (21.10.2026): a declared loosening merges through the operator's door

[#103](https://github.com/overnightworks/quality/issues/103), ruled as D3 on [#45](https://github.com/overnightworks/quality/issues/45).

1. D3. Asked: „D3: Ein PR lockert das Gate so, wie du es entschieden hast, etwa durch das Entfernen eines Pflicht-Jobs (songmaker #1735). Wie soll so ein PR landen?“ Answered: „Erklären + deine Tür (empfohlen)“. The pull request declares the loosening in `quality/config.toml` with its ruling (item, date, reason); the verdict stays red, but only for declared loosenings; only the operator's emergency door merges such a pull request; no agent can approve a loosening for itself.
2. E5. Asked: „Erklärte Lockerung „Pflicht-Job entfernt“ (songmaker): Soll das Gate den entfernten Job dann gar nicht mehr prüfen?“ Answered: „Ja, nicht mehr prüfen (empfohlen)“. A declared removal replaces judging that base job.
3. E8. Asked: „Darf deine Tür einen PR mergen, der Sonar-Altlasten hat (legacy = fail) und zugleich eine erklärte Lockerung?“ Answered: „Ja, beides in einem Lauf (empfohlen)“.
4. E10. Asked: „… Dass nur du lockerst, hängt damit am Vertrag, nicht an einer Sperre … Wie gehen wir damit um?“ Answered: „Bekanntes Restrisiko (empfohlen)“. Agents share the operator's `gh` login, so the door is the sanctioned path, not a lock; a real lock belongs to the machine's setup.
5. The release. Asked: „hopin und songmaker brauchen ein neues quality-Release mit #98 und Q1b-7a … Wie soll das Release heißen?“ Answered: „v0.3.0 jetzt (empfohlen)“.

## Operator ruling (21.10.2026): the verdict re-reads a stale jobs answer

[#85](https://github.com/overnightworks/quality/issues/85). GitHub's jobs listing answers late per job, so the verdict can read a mandatory job as running that the caller's `needs:` already waited for. On 21.10.2026 the operator answered three questions; E4, E5 and E6 were shown to him with their defaults the same day and keep them.

1. E1 and E2. Asked: „Das Verdict liest einen Pflicht-Job, der laut GitHub noch „läuft“, obwohl er fertig sein muss, noch einmal nach, statt sofort rot zu werden. Wie lange darf es höchstens nachlesen?“ Answered: „Bis zu 2 Minuten (empfohlen)“. The verdict reads the run's jobs again at most twelve times, 10 s apart, and judges the first answer that settles every judged job.
2. E3. Asked: „Steht der Job auch nach dem letzten Nachlesen noch auf „läuft“: Welches Ergebnis?“ Answered: „Exit 2: kann nicht urteilen (empfohlen)“. The line names GitHub's stale answer and says to re-run the verdict.
3. The release. Asked: „Das Nachlesen (#85) braucht noch einige Stunden, bis es gebaut und geprüft ist. Soll v0.3.0 darauf warten?“ Answered: „v0.3.0 jetzt, #85 als v0.3.1 (empfohlen)“.
4. The defaults. E4: a verdict that read more than once first prints how often and what GitHub said first. E5: a 5xx is read again in the same budget and, if it lasts, named as GitHub's fault without the permission hint. E6: `(does the caller grant actions: read?)` follows only a 403 or 404.

## Operator ruling (21.10.2026): an advisory a repository cannot fix is allowed with a re-check date, and only the operator lets it in

[#116](https://github.com/overnightworks/quality/issues/116), for agent-presentator#371, whose nested provider locks carry advisories upstream pins block.

1. The allowance. Asked: „Soll ein Repo eine bekannte Sicherheitslücke, für die es keinen Ausweg gibt, mit Begründung und Wiedervorlage-Datum ausnehmen dürfen? Und wer gibt das frei?“ Answered: „Ja, aber nur du gibst frei (empfohlen)“. The ruled sentences:
   - R1. Eine Ausnahme steht pro Lücke in `quality/config.toml`, mit Begründung, Item und Datum.
   - R2. Eine neue Ausnahme ist eine Lockerung: der PR bleibt rot, bis deine Tür ihn mergt.
   - R3. Ab dem Merge ist die Lücke grün bis zum Datum, danach wieder rot.
   - R4. Kein Agent kann eine Lücke allein stummschalten. (Bekanntes Restrisiko E10 von #103: Agenten teilen deinen `gh`-Login; die Tür ist der Vertrag, keine Sperre.)
2. E6. Asked: „Eine Ausnahme für eine Sicherheitslücke hat ein Wiedervorlage-Datum. Darf ein PR dieses Datum ohne dich nach hinten schieben?“ Answered: „Nein, nur über deine Tür (empfohlen)“. A later `until` for an existing allowance is a loosening like a new one; an earlier `until`, a changed reason, item or date, or a deleted entry loosens nothing.
3. E7. Asked: „Muss jede neue Ausnahme zusätzlich als eigene „erklärte Lockerung“ eingetragen werden, oder gilt die Ausnahme selbst als Erklärung? Sie trägt ohnehin Grund, Item und Datum.“ Answered: „Ausnahme erklärt sich selbst (empfohlen)“. The allowance is its own declared loosening with its own item and date; a `[[gate.loosened]]` entry for it matches nothing.
4. The release. Asked: „Mit welchem Release kommen die Ausnahmen (#116) zu den Repos? Auf main liegen inzwischen auch die Klon-Erkennung (nur Bericht) und die Zeilenzählung für TS/JS/Svelte.“ Answered: „v0.4.0 von main (empfohlen)“ (#45, row Q1b-10b).
5. The defaults. E1 to E5 and E8 to E12 were shown to the operator on 21.10.2026 and left unanswered, so each stands with its default: one `[[advisories.allow]]` entry per advisory and lock with `id`, `lock`, `until`, `date`, `item` and `reason` (E1; `date` decided by the head the same day, so the allowance fills a declaration as it is); the `id` may be any id of the group, and two entries on one group are red (E2; the red decided by the head); `until` and `date` are TOML dates, `until` inclusive in UTC (E3); after `until` the advisory is red, naming the date and item (E4); an allowance matching no advisory is red (E5); adopting quality with allowances is a loosening (E8); no latest `until` (E9); every allowed advisory is printed with its date, item and reason (E10); an advisory without a fixed version is allowed like any other (E11); an allowance holds for every package in its lock and in no other lock (E12).
