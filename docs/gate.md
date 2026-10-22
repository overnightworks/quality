# The gate

Reference for the engineer who calls the reusable `gate.yml` or `codeql.yml` workflow, or reads its verdict.

## `gate.yml`

`.github/workflows/gate.yml` is the reusable gate: a `hygiene` job, a `sonar` job and a `verdict` job. A repository calls
it from one job of its own workflow:

```yaml
  quality:
    if: always()
    needs: [checks]
    permissions:
      contents: read
      actions: read
    uses: overnightworks/quality/.github/workflows/gate.yml@<commit sha> # v<tag>
    with:
      coverage-artifact: coverage
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

Each line has a reason:

- **The caller job is `quality`,** by its id or, if it sets one, by its `name:`. The gate's checks then read
  `quality / hygiene`, `quality / sonar` and `quality / verdict`. Three things look for them by those names: a ruleset
  that requires `quality / verdict` (the fleet's org ruleset does, on the default branch of every repository whose
  custom property `quality_gate` is `true`), `aco land`, which, once the default branch holds `quality/config.toml`,
  refuses without a green `quality / verdict`, and `quality emergency-merge`, which looks for `quality / sonar` and
  `quality / verdict`.
- **`needs:` lists the id of every job that a `mandatory_jobs` name belongs to.** The verdict then judges finished jobs,
  and an answer in which GitHub still lists one as running is stale: the verdict reads the run's jobs again, every 10
  seconds and at most 12 times: two minutes of waiting plus the time the reads take. Without `needs:`, a job still
  running holds the verdict for that long, then it cannot judge:
  `GitHub still lists "checks" (attempt 1) as in_progress after 13 reads; …`.
- **`if: always()` runs the gate when a needed job failed,** so the verdict names the failure. Without it, a failed needed
  job skips the gate and no verdict judges the run.
- **`permissions:` grants `contents: read` and `actions: read`.** The verdict reads the run's jobs through the API, and
  only GitHub's 403 or 404 adds `(does the caller grant actions: read?)` to the line. A job in
  a reusable workflow gets no more than its caller job grants (see
  [GitHub's reusable-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)),
  so without `actions: read` the run is expected to fail at startup and add no `quality / verdict` check (see
  [Startup failures](#startup-failures)). Where a ruleset or `aco land`
  requires that check, the pull request cannot merge; where nothing requires it, it merges unjudged.
- **The door stops workflow commands for the rest of its step.** gate.yml runs `quality` alone in each of its steps; a
  workflow that runs `quality` itself writes its own workflow commands (`::notice::`, `::group::` or any other) in a separate step.
- **`secrets: SONAR_TOKEN`** and the optional **`with: coverage-artifact`** are described under
  [`quality sonar findings`](sonar.md#quality-sonar-findings) and [Coverage on new code](sonar.md#coverage-on-new-code).
- **`uses:` pins a commit** (`@<commit sha> # v<tag>`), and Dependabot bumps it where the repository's `.github/dependabot.yml` enables the `github-actions` ecosystem. There is no copier and no own token that writes to all repositories: quality
  carries plain version tags and Dependabot opens the bump pull requests (operator, 15.10.2026: „kein copier! … copier
  ist shice! …“; the points agreed with the operator's 15.10.2026 answer, quoted in [commands.md](commands.md#quality-engines), add the token).

The verdict judges the jobs named in `quality/config.toml`:

```toml
[gate]
mandatory_jobs = ["checks"]
```

- `mandatory_jobs` is a non-empty list of job names as the run lists them: the job's `name:`, else its id, and a matrix
  leg as `check (3.12)`. A missing file or key is exit `2`, and the message shows this example.
- Each job must conclude `success` in its newest attempt up to the verdict's own. A job that is missing from the run is
  `mandatory job "x" is missing from the run`; one that failed, was skipped or cancelled is red. One GitHub still lists
  as running (`queued`, `in_progress`, `waiting`, `requested`, `pending`) is read again; after the last read the
  verdict cannot judge. Each read judges every entry any answer listed, at the furthest state an answer showed, so an
  answer that lists an entry late or drops one never hides a failure an earlier answer showed.
- On a pull request, removing a job the base config holds is `loosened (undeclared): mandatory job "x" removed`, and the
  base's job is still judged; a declared removal (below) is no longer judged.
- The gate's own jobs are judged through `needs` (`gate job "sonar" result failure`) and need no entry.
- `[gate] legacy` is described under [New and legacy findings](sonar.md#new-and-legacy-findings).

The verdict prints `verdict: green` (exit `0`); each problem, then `verdict: red` (exit `1`); or
`verdict: cannot judge - ...` (exit `2`). When it read the jobs more than once, a first line says so:
`verdict: read the run's jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress`. A GitHub server error
(5xx) is read again within the same budget; if it lasts, the verdict cannot judge and names it as GitHub's fault:
`GitHub answered HTTP 502 Bad Gateway for … after 13 reads; this is GitHub's fault, not the caller's: re-run the verdict`.
A cannot-judge after a re-read still prints the last judged read's problems first. A timeout or an unreachable GitHub
is not read again: re-run the verdict. The same text goes to the run's step summary.

GitHub's jobs listing answers late per job, which is why the verdict reads again instead of failing at once (operator
ruling 21.10.2026: at most two minutes, „Bis zu 2 Minuten (empfohlen)“; a job still running after the last read is exit
`2`, „Exit 2: kann nicht urteilen (empfohlen)“). The first line that says the verdict read again, the reading of a 5xx and
the permission hint only after a 403 or 404 were shown to the operator and left unanswered, so they stand at the head's
proposed default (#85, E4 to E6); they are not his words.

### Declared loosenings

A pull request that loosens the gate as the operator ruled declares each loosening in its `quality/config.toml`, one
entry per loosening:

```toml
[[gate.loosened]]
what = 'mandatory job "Acceptance evidence (integration)" removed'
item = "overnightworks/songmaker#1345"
date = 2026-10-20
reason = "the operator ruled the requirement shelf machinery out"
```

- `what` is exactly the verdict's text after `loosened (undeclared): `, unescaped: where the verdict prints a character
  escaped (`\x0a` for a line feed in a job name), the entry writes the TOML escape (`\n`). Any other text, another
  number included, declares nothing.
- `item` is `#<n>` or `<owner>/<repo>#<n>` (ASCII letters, digits, `_`, `.` and `-`), `date` a TOML date without a
  time, `reason` one non-empty line. A missing or extra key, a text date, two entries with one `what` or any other
  malformed entry is exit `2` in `verdict` and in `hygiene --engines` (the `secret` and `advisory` rules), naming the
  entry and showing this example; `scan-staged` reads only `[secrets]`.
- Only new entries declare: those the head holds and the base does not, compared as a whole entry. An inherited entry
  is never red and declares nothing, so a later pull request that loosens the same way again needs its own entry with
  its own `item` and `date`; deleting an inherited entry is no loosening. A new entry that matches no loosening of the
  pull request is red: `declared loosening matches nothing: <what>`. A base without `quality/config.toml` (the
  adoption) holds no entry, so every entry there is new and matches nothing. Without a base (push, `merge_group`) no
  entry is judged.
- A declared loosening stays red (exit `1`) and says so:
  `loosened (declared, overnightworks/songmaker#1345 of 2026-10-20): mandatory job "…" removed`. When nothing else is
  red, the last line is `verdict: red - only declared loosenings: only the operator's quality emergency-merge lands this
  pull request`. No entry approves itself: an agent may write one, but the ruleset and `aco land` still need a green
  `quality / verdict`, and only [the operator's emergency door](commands.md#quality-emergency-merge) merges.
- An advisory allowance (`[[advisories.allow]]`, see the `advisory` rule under `quality hygiene`) declares its own
  loosening with its own `item` and `date`, so it needs no `[[gate.loosened]]` entry. An allowance whose `id` and
  `lock` the base lacks prints `loosened (declared, <item> of <date>): advisory allowance adds <id> in <lock> until <until>`; one
  whose `until` the head moves later prints `… advisory allowance extends <id> in <lock> to <until>`. An earlier
  `until`, a changed `reason`, `item` or `date`, and a deleted allowance loosen nothing; a changed `id` or `lock` is a
  new allowance. A base without `quality/config.toml` holds no allowance, so every allowance of the adoption is new. A
  `[[gate.loosened]]` entry whose `what` is such a text matches nothing and is red. The verdict and the door read the
  whole config, so a malformed `[secrets]` or `[advisories]` entry is exit `2` there too.
- The ruling (operator ruling 21.10.2026: „Erklären + deine Tür (empfohlen)“) is that a loosening is declared and stays
  red, and that only the operator's door merges it; a declared removal of a mandatory job is no longer judged („Ja,
  nicht mehr prüfen (empfohlen)“), and the door may merge legacy Sonar findings and a declared loosening in one run („Ja,
  beides in einem Lauf (empfohlen)“). That agents share the operator's `gh` login is a known residual risk, „Bekanntes
  Restrisiko (empfohlen)“: the door is the agreed path, not a lock. Why the gate is not simply loosened by the pull
  request stands in [decision 1](decisions/1-loosening-door.md).


## `codeql.yml`

A reusable workflow that runs CodeQL for the languages a caller names, one for the fleet instead of a copy per repository
(operator ruling 18.10.2026: „b macht sinn“), with `build-mode: none` and CodeQL's default
query suite and threat model. It uploads under the category `/language:<language>`, the Default Setup's category, so
alerts carry over when a repository switches.

A private repository needs GitHub Code Security for code scanning (`code-scanning/default-setup` answers 403 without it); the operator declined to pay for it (operator ruling 22.10.2026), so private repositories run no CodeQL.

A caller (`.github/workflows/code-scanning.yml`, workflow `name: CodeQL`):

```yaml
name: CodeQL

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  schedule:
    - cron: '23 4 * * 1'

permissions:
  contents: read

jobs:
  codeql:
    permissions:
      security-events: write
      contents: read
      actions: read
    uses: overnightworks/quality/.github/workflows/codeql.yml@<tag>
    with:
      languages: '["actions", "python"]'
```

- **`languages`:** the one input, required, a string holding a JSON list; `'python'` or `'[]'` fails the run. List
  every category the repository already analyses (agent-coordination also has `javascript-typescript`, which the
  template would drop).
- **Permissions:** the caller grants `security-events: write`, `contents: read` and `actions: read`, and the workflow
  asks for no more. A caller without `security-events: write` is refused at startup.
- **Updates:** pin `<tag>` to a release; Dependabot's `github-actions` ecosystem bumps it.
- **Caller path:** a reusable call records the `analysis_key` `<caller file>:analyze`, so keep
  the existing caller file's path and the key carries over.
- **Switch PR:** a caller that fails at startup adds no check to its PR, so the switch PR must show its
  `codeql / Analyze (…)` checks.
- **Switching a repository:** the Default Setup goes off first.

### Startup failures

A reusable-workflow call that GitHub refuses at startup (a caller job that grants less than the called workflow asks for,
such as `security-events: write` or `actions: read`) ends its run without a check run for that job, so a ruleset or
`aco land` waiting for the check never sees it. Measured on 18.10.2026 on a scratch pull request whose CodeQL callers
were broken (commit 7a4173ae0cc52a62d5e00177fd3197a2c034102a of overnightworks/quality#70): its `CodeQL` run ended
`startup_failure` and the commit has no `codeql / Analyze (…)` check run. It is measured for CodeQL
callers; for `gate.yml` callers the same behaviour is expected and not measured
here. To re-measure, break a caller's `permissions:` on a scratch pull request and read
`gh run list --repo <owner/repo> --commit <full sha> --json workflowName,conclusion` and
`gh api --paginate repos/<owner/repo>/commits/<full sha>/check-runs --jq '.check_runs[].name'`.
