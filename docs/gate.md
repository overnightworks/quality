# The gate

Reference for the engineer who calls the reusable `gate.yml` workflow or reads its verdict. The reasons behind its constraints stand in [ARCHITECTURE](ARCHITECTURE.md).

## `gate.yml`

`.github/workflows/gate.yml` is the reusable gate: a `check` job, a `sonar` job and a `verdict` job. A repository calls
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
  `quality / check`, `quality / sonar` and `quality / verdict`. Three things look for them by those names: a ruleset
  that requires `quality / verdict` (the fleet's org ruleset does, on the default branch of every repository whose
  custom property `quality_gate` is `true`), `aco land`, which, once the default branch holds `quality/config.toml`,
  refuses without a green `quality / verdict`, and `quality merge`, which looks for `quality / sonar` and
  `quality / verdict`.
- **`needs:` lists the id of every job that a `mandatory_jobs` name belongs to.** The verdict then judges finished jobs,
  and a job GitHub still lists as running is unsettled and never green (READ-1), so the answer is stale: the verdict
  reads the run's jobs again, every 10 seconds (READ-2) and at most 12 times (READ-3): two minutes of waiting plus the
  time the reads take. Without `needs:`, a job still
  running holds the verdict for that long, then it cannot judge:
  `GitHub still lists "checks" (attempt 1) as in_progress after 13 reads · …`, with `next: re-run this run's failed jobs`.
- **`if: always()` runs the gate when a needed job failed,** so the verdict names the failure. Without it, a failed needed
  job skips the gate and no verdict judges the run.
- **`permissions:` grants `contents: read` and `actions: read`.** The verdict reads the run's jobs through the API, and
  only GitHub's 403 or 404 adds `grant actions: read in the caller job's permissions` under `next:`. A job in
  a reusable workflow gets no more than its caller job grants (see
  [GitHub's reusable-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)),
  so without `actions: read` the run is expected to fail at startup and add no `quality / verdict` check (see
  [Startup failures](#startup-failures)). Where a ruleset or `aco land`
  requires that check, the pull request cannot merge; where nothing requires it, it merges unjudged.
- **The door stops workflow commands for the rest of its step.** gate.yml runs `quality` alone in each of its steps; a
  workflow that runs `quality` itself writes its own workflow commands (`::notice::`, `::group::` or any other) in a separate step.
- **The `check` job installs the scanners itself.** It runs a pinned `jdx/mise-action` that installs gitleaks,
  actionlint and osv-scanner from the tool's own manifest and lock ([Scanners](commands.md#scanners)) (CALLER-1), so a
  caller adds nothing; its CI needs network access to GitHub release assets. The step pins mise's release and its
  binary's checksum, reads and writes no cache, and hands later steps neither mise's environment nor a scanner on
  `PATH` (CALLER-2). The step sets `MISE_CEILING_PATHS`, so a `mise.toml` in the caller's checkout takes no part
  (CALLER-3). quality's own `ci.yml` installs the scanners in its `tests` job through the same pinned step under the
  same ceiling and runs no `quality check` there (CALLER-4).
- **The `check` job runs the whole `quality check`.** `gate.yml` has no input for an area, and a test pins the step's words (CALLER-5). A job that names areas says so in its verdict (`✓ clean · 1 area`); a selected-area job is not evidence of a whole check.
- **`secrets: SONAR_TOKEN`** (needed while SonarCloud runs; a repository with `[gate] sonar = false` passes none) and the optional **`with: coverage-artifact`** are described under
  [`quality sonar findings`](sonar.md#quality-sonar-findings) and [Coverage on new code](sonar.md#coverage-on-new-code).
- **`uses:` pins a commit** (`@<commit sha> # v<tag>`). The comment is load-bearing: a local `quality` reads the release from it, so it names the commit's own tag. Dependabot bumps it where the repository's `.github/dependabot.yml` enables the `github-actions` ecosystem. There is no copier and no own token that writes to all repositories: quality
  carries plain version tags and Dependabot opens the bump pull requests, and quality's own action pins move the same way, so a caller receives current actions with each quality tag.
- **Each job checks out the gate's own tool at one release tag.** Every such checkout in `gate.yml` names the same tag
  (CALLER-6). In quality's own repository each job skips that checkout and runs the tool from the commit under test
  (CALLER-7). The tool is its own uv workspace root, so a caller's checkout above it can neither take it in nor
  configure it (CALLER-8).

The verdict judges the jobs named in `quality/config.toml`:

```toml
[gate]
mandatory_jobs = ["checks"]
```

- `mandatory_jobs` is a non-empty list of job names as the run lists them: the job's `name:`, else its id, and a matrix
  leg as `check (3.12)`. A missing file or key is exit `2`, and `next:` shows this example.
- Each job must conclude `success` in its newest attempt up to the verdict's own. A job that is missing from the run is
  the row `! x  missing  not in the run`; one that failed, was skipped or cancelled is red. One GitHub still lists
  as running (`queued`, `in_progress`, `waiting`, `requested`, `pending`) is read again; after the last read the
  verdict cannot judge. Each read judges every entry any answer listed, at the furthest state an answer showed, so an
  answer that lists an entry late or drops one never hides a failure an earlier answer showed.
- On a pull request, removing a job the base config holds is the loosening `mandatory job "x" removed`, and the
  base's job is still judged; a declared removal (below) is no longer judged.
- The gate's own jobs are judged through `needs` (the row `! quality / sonar  failure`, the check name GitHub shows)
  and need no entry.
- `[gate] sonar = false` turns SonarCloud off: the `sonar` job's properties step hands it `sonar=off`, prints `· sonar  off  …` and `✓ off · nothing reaches SonarCloud`, and `gate.yml` skips the scan and the findings step only on that output (CALLER-9), so the job is green and sends nothing. While off, a `sonar-project.properties` or a passed `SONAR_TOKEN` is exit `2`, so a later gate without the switch also stops before its scan. Absent or `true`, Sonar runs. Any other value, and `false` beside `legacy = "fail"`, is exit `2`. On a pull request, a head that turns Sonar off under a base that runs it is the loosening `sonar true → false`; an adoption (a base without `quality/config.toml`) may start with Sonar off.
- `[gate] legacy` is described under [New and legacy findings](sonar.md#new-and-legacy-findings).

`gate.yml`'s verdict step runs `quality verdict`, which takes no path and judges the checkout it runs in. It prints rows
on stdout, all sharing one subject column:

- the release that judges, first in every frame, a refusal's included: `· release  <tag>  this gate run`, and in quality's own run `· release  this checkout  <version> · quality judges itself`;
- the base, `· base  compared  <branch> <base sha> · head <head sha>` with the pull request's head, or
  `· base  none  a push or merge group: no loosening judged`;
- the jobs, as one `✓ jobs  success  N mandatory jobs · M gate jobs` row when all are green, else one row each, a conclusion GitHub adds later as `concluded <word>`;
- on a pull request the loosenings, `✓ loosenings  none` or `! loosenings` with each loosening below it;
- `! declarations  N unmatched` where a new entry (below) matches nothing.

One closing line follows, ending with the run's duration: `✓ green · N mandatory jobs · 4s` (exit `0`) or
`! red · <what failed> · 4s` (exit `1`). When it cannot judge (exit `2`), it prints the `error:`/`next:` pair on stderr,
with the steps apart from the fact:

- a `quality/config.toml` it cannot read shows the example to copy, then `push the fix; the gate judges the new commit`
  at the head, or `fix it on <base branch> first, then update the pull request's branch with <base branch>` at the
  base, which is read first;
- a variable the step sets that the run lacks, or holds in another form, is `<NAME> is not set` or `<NAME> is not …`,
  its value never printed; beside a base the step also sets the pull request's number and head (CALLER-10);
- a GitHub answer a re-run may clear names `re-run this run's failed jobs`.

When it read the jobs more than once, a first row says so:
`· run  2 reads  GitHub first listed "checks" (attempt 1) as in_progress`. A GitHub server error
(5xx) is read again within the same budget, and if it lasts, the fact names it as GitHub's:
`GitHub answered HTTP 502 Bad Gateway for … after 13 reads · GitHub's fault, not the caller's`. A timeout or an
unreachable GitHub is not read again. A cannot-judge after a re-read still prints the rows of the last judged read
first, on stdout. The run's step summary gets the same lines, stdout's and then the pair, in one fenced code block on a
line of its own, whose fence is longer than every backtick run in them, so no job name or `what` renders as markdown. A
summary that cannot be written adds `error: GITHUB_STEP_SUMMARY is not writable · <reason>` after them (exit `2`).
`-v` adds nothing: the verdict runs no scanner.

Ruled by the operator: the verdict reads the run's jobs again for at most two minutes, because GitHub's jobs listing
answers late per job; a job still listed as running after the last read is exit `2`.

Three defaults the operator never answered: the verdict says in a first line that it read again (READ-4), reads a 5xx
again (READ-5), and adds the permission hint only after a 403 or 404 (READ-6).

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

- `what` is exactly the text the verdict prints after `! ` for an undeclared loosening under its `loosenings` row,
  unescaped; a declared line is never copied, since its ` · <item> of <date>` is no part of the `what`. The verdict prints a line feed in a
  job name as `\x0a` and a format character as `\u202e` (`\U000e0041` above U+FFFF), and never escapes a backslash, so a
  printed backslash is either such an escape or one the name holds itself; the head's files show which. In a
  double-quoted TOML string, each escape the verdict added is written as its TOML escape (`\x0a` as `\n`; `\u202e` and
  `\U000e0041` are TOML escapes already) and each backslash the name holds itself as `\\`. A single-quoted literal
  string, as in the example, fits only a `what` the verdict printed without an escape. Any other text, another number
  included, declares nothing.
- `item` is `#<n>` or `<owner>/<repo>#<n>` (ASCII letters, digits, `_`, `.` and `-`), `date` a TOML date without a
  time, `reason` one non-empty line. A missing or extra key, a text date, two entries with one `what` or any other
  malformed entry is exit `2` in `verdict` and in `check` (the `secret` and `advisory` rules), naming the
  entry and showing this example; `check secrets --staged` reads only `[secrets]`.
- Only new entries declare: those the head holds and the base does not, compared as a whole entry. An inherited entry
  is never red and declares nothing, so a later pull request that loosens the same way again needs its own entry with
  its own `item` and `date`; deleting an inherited entry is no loosening. A new entry that matches no loosening of the
  pull request is red: it stands under `! declarations  1 unmatched` as the door shows it. A base without `quality/config.toml` (the
  adoption) holds no entry, so every entry there is new and matches nothing. Without a base (push, `merge_group`) no
  entry is judged.
- A declared loosening stays red (exit `1`) and says so: below its row it stands as
  `! mandatory job "…" removed · overnightworks/songmaker#1345 of 2026-10-20`, with `reason: …` beneath. When nothing
  else is red, the closing line is `! red · only declared loosenings · only the operator merges it · 4s`, and stderr
  names the one command: `next: quality merge <owner>/<repo>#<n>`. No entry approves itself: an agent may write one, but the ruleset and `aco land` still need a green
  `quality / verdict`, and only [the operator's door](commands.md#quality-merge) merges.
- An advisory allowance (`[[advisories.allow]]`, see the `advisory` rule under `quality check`) declares its own
  loosening with its own `item` and `date`, so it needs no `[[gate.loosened]]` entry. An allowance whose `id` and
  `lock` the base lacks prints `advisory allowance adds <id> in <lock> until <until> · <item> of <date>`; one
  whose `until` the head moves later prints `… advisory allowance extends <id> in <lock> to <until>`. An earlier
  `until`, a changed `reason`, `item` or `date`, and a deleted allowance loosen nothing; a changed `id` or `lock` is a
  new allowance. A base without `quality/config.toml` holds no allowance, so every allowance of the adoption is new. A
  `[[gate.loosened]]` entry whose `what` is such a text matches nothing and is red. The verdict and the door read the
  whole config, so a malformed `[secrets]` or `[advisories]` entry is exit `2` there too.
- A generated-file declaration (`[[size.generated]]`, see the `size` rule under `quality check`) declares its own
  loosening with its own `item` and `date`, like an allowance. A head `path` the base does not declare prints
  `size leaves out generated file <path> · <item> of <date>`, so a changed `path` and every
  declaration of an adoption (a base without `quality/config.toml`) are new. An inherited or deleted declaration and a
  changed `job`, `reason`, `item` or `date` loosen nothing. A `[[gate.loosened]]` entry whose `what` is such a text
  matches nothing and is red. A malformed `[size]` entry is exit `2` in the verdict and the door, naming the side.
- Ruled by the operator: removing, softening or bypassing an import-linter contract declares the same way, with one of these texts as `what`
  (the rule is in [rules](rules.md), "The import contracts only tighten"): `import contract "<name>" removed`,
  `import-linter config in <file> removed`, `import contract "<name>" ignores <expression>`,
  `import contract "<name>" changes <field>` and `import-linter changes <option>`.

Ruled by the operator:

- A loosening is declared and stays red, and only the operator's door merges it.
- A declared removal of a mandatory job is no longer judged.
- The door may merge legacy Sonar findings and a declared loosening in one run.
- Only the door moves an allowance's `until` later, because that date is what brings the advisory back to red.
- An allowance is its own declaration, with no second `[[gate.loosened]]` entry.

The reason and the residual risk stand in [ARCHITECTURE](ARCHITECTURE.md), sections 6 and 8.

## Startup failures

A reusable-workflow call that GitHub refuses at startup (a caller job that grants less than the called workflow asks for,
such as `security-events: write` or `actions: read`) ends its run without a check run for that job, so a ruleset or
`aco land` waiting for the check never sees it. Measured for [CodeQL callers](codeql.md): such a run ends `startup_failure` and the commit has no
`codeql / Analyze (…)` check run. It is measured for CodeQL
callers; for `gate.yml` callers the same behaviour is expected and not measured
here. To re-measure, break a caller's `permissions:` on a scratch pull request and read
`gh run list --repo <owner/repo> --commit <full sha> --json workflowName,conclusion` and
`gh api --paginate repos/<owner/repo>/commits/<full sha>/check-runs --jq '.check_runs[].name'`.
