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
  that requires `quality / verdict` (`src/opentofu/github/organization/main.tf` in the overnightworks/platform
  repository defines the fleet's organisation ruleset), `aco land`, which, once the default branch holds
  `quality/config.toml`, refuses without a green `quality / verdict`, and `quality merge`, which looks for
  `quality / sonar` and `quality / verdict`. That definition pins the name `quality / verdict` and GitHub Actions as
  its producer, so renaming the check or moving its producer to another App is asked of platform's head first.
- **`needs:` lists the id of every job that a `mandatory_jobs` name belongs to.**
  - READ-1: The verdict then judges finished jobs, and a job GitHub still lists as running is unsettled and never green,
  - READ-2: so the answer is stale: the verdict reads the run's jobs again, every 10 seconds
  - READ-3: and at most 12 times: two minutes of waiting plus the time the reads take.
  - VERDICT-1: Without `needs:`, a job still running holds the verdict for that long, then it cannot judge:
    `GitHub still lists "checks" (attempt 1) as in_progress after 13 reads · …`, with two steps under `next:`,
    `re-run this run's failed jobs` and `a mandatory job missing from the caller job's needs: add it there`.
- **`if: always()` runs the gate when a needed job failed,** so the verdict names the failure. Without it, a failed needed
  job skips the gate and no verdict judges the run.
- **`permissions:` grants `contents: read` and `actions: read`.**
  - VERDICT-36: The verdict reads the run's jobs through the API with the step's `GITHUB_TOKEN`
  - VERDICT-37: and never follows a redirect, so the token reaches no other host. Only GitHub's 403 or 404 adds
    `grant actions: read in the caller job's permissions` under `next:`. A job in
    a reusable workflow gets no more than its caller job grants (see
    [GitHub's reusable-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)),
    so without `actions: read` the run is expected to fail at startup and add no `quality / verdict` check (see
    [Startup failures](#startup-failures)). Where a ruleset or `aco land`
    requires that check, the pull request cannot merge; where nothing requires it, it merges unjudged.
- **The door stops workflow commands for the rest of its step.** gate.yml runs `quality` alone in each of its steps; a
  workflow that runs `quality` itself writes its own workflow commands (`::notice::`, `::group::` or any other) in a separate step.
- **The `check` job installs the scanners itself.**
  - CALLER-1: It runs a pinned `jdx/mise-action` that installs gitleaks,
    actionlint and osv-scanner from the tool's own manifest and lock ([Scanners](commands.md#scanners)),
  - CALLER-2: so a caller adds nothing; its CI needs network access to GitHub release assets. The step pins mise's release and its
    binary's checksum, reads and writes no cache, and hands later steps neither mise's environment nor a scanner on
    `PATH`.
  - CALLER-3: The step sets `MISE_CEILING_PATHS`, so a `mise.toml` in the caller's checkout takes no part.
  - CALLER-4: quality's own `ci.yml` installs the scanners in its `tests` job through the same pinned step under the
    same ceiling and runs no `quality check` there.
  - CALLER-11: Both steps set `MISE_HTTP_RETRIES` to the same count, so
    mise retries a failed scanner download for about 20 seconds instead of about one.
- **The install step and the scan step are timed.**
  - CALLER-12: Each declares a `timeout-minutes` of its own, so a slow download or scan ends before the job does.
  - CALLER-13: The `verdict` job's limit covers its git fetches, its re-reads of the jobs and one request.
- CALLER-5: **The `check` job runs the whole `quality check`.** `gate.yml` has no input for an area, and a test pins the step's words. A job that names areas says so in its verdict (`✓ clean · 1 area`); a selected-area job is not evidence of a whole check.
- **`secrets: SONAR_TOKEN`** (needed while SonarCloud runs; a repository with `[gate] sonar = false` passes none) and the optional **`with: coverage-artifact`** are described under
  [`quality sonar findings`](sonar.md#quality-sonar-findings) and [Coverage on new code](sonar.md#coverage-on-new-code).
- **`uses:` pins a commit** (`@<commit sha> # v<tag>`). The comment is load-bearing: a local `quality` reads the release from it, so it names the commit's own tag. Dependabot bumps it where the repository's `.github/dependabot.yml` enables the `github-actions` ecosystem. There is no copier and no own token that writes to all repositories: quality
  carries plain version tags and Dependabot opens the bump pull requests, and quality's own action pins move the same way, so a caller receives current actions with each quality tag.
- **Each job checks out the gate's own tool at one release tag.**
  - CALLER-6: Every such checkout in `gate.yml` names the same tag.
  - CALLER-7: In quality's own repository each job skips that checkout and runs the tool from the commit under test.
  - CALLER-8: The tool is its own uv workspace root, so a caller's checkout above it can neither take it in nor
    configure it.

- VERDICT-2: The verdict judges the jobs named in `quality/config.toml`:

```toml
[gate]
mandatory_jobs = ["checks"]
```

- VERDICT-3: `mandatory_jobs` is a non-empty list of job names as the run lists them: the job's `name:`, else its id, and a matrix
  leg as `check (3.12)`.
- VERDICT-4: A missing file or key is exit `2`, and `next:` shows this example.
  A job a workflow splits into shards keeps its name as an aggregator job that needs every shard: a name the run holds
  only as legs (`checks (3.12)` for `checks`) is `missing  not in the run` (VERDICT-7), and dropping the name from the
  config is a loosening (VERDICT-13). Whether the aggregator fails on a failed or skipped shard is not machine-read.
- VERDICT-5: Each job must conclude `success` in its newest attempt up to the verdict's own.
- VERDICT-6: A job the run lists
  several times in that attempt is green only when every entry succeeded.
- VERDICT-7: A job that is missing from the run
  is the row `! x  missing  not in the run`;
- VERDICT-8: one that failed, was skipped or cancelled is red.
- VERDICT-9: One GitHub still lists as running (`queued`, `in_progress`, `waiting`, `requested`, `pending`) is read again,
  unless it carries a `conclusion` and a `completed_at`, which read as finished with that conclusion.
- VERDICT-10: Each read judges every entry any answer listed, at the furthest state an answer showed, so an answer
  that lists an entry late or drops one never hides a failure an earlier answer showed.
- VERDICT-11: The verdict reads every page of the run's jobs.
- VERDICT-12: A read whose jobs differ in number from the count the
  listing gives, or that runs past 10 pages, cannot be judged.
- VERDICT-13: On a pull request, removing a job the base config holds is the loosening `mandatory job "x" removed`, and the base's
  job is still judged; a declared removal (below) is no longer judged.
- VERDICT-14: The gate's own jobs are judged through `needs` (the row `! quality / sonar  failure`, the check name GitHub shows)
  and need no entry.
- VERDICT-15: `[gate] sonar = false` turns SonarCloud off.
- CALLER-9: The `sonar` job's properties step then hands it `sonar=off`,
  which [Coverage on new code](sonar.md#coverage-on-new-code) describes, with the refusals that make a later gate
  without the switch stop before its scan, and `gate.yml` skips the scanner install, the scan and the findings step only
  on that output, so the job is green and sends nothing. The same output ends a run that is neither a pull request
  nor on the default branch, which SonarCloud serves nothing of ([Runs SonarCloud serves](sonar.md#runs-sonarcloud-serves)).
- VERDICT-16: Absent or `true`, Sonar runs.
- VERDICT-17: Any other
  value is exit `2`.
- VERDICT-18: On a pull request, a head that turns Sonar off under a base that runs it is the
  loosening `sonar true → false`;
- VERDICT-19: an adoption (a base without `quality/config.toml`) may start with Sonar
  off.
- VERDICT-51: `[gate] legacy` is described under [New and legacy findings](sonar.md#new-and-legacy-findings). On a pull request, a
  head whose `legacy` is not `fail` under a base whose `legacy` is `fail` is the loosening `legacy fail → report`; an
  absent key counts as `report`.

  Ruled by the operator: switching back needs the operator. A pull request whose base config says `legacy = "fail"` and
  whose head config says anything else is red in the verdict. An absent key counts as `report`. This is the same
  mechanism as a removed mandatory job.

- VERDICT-22: `gate.yml`'s verdict step runs `quality verdict`, which takes no path and judges the checkout it runs in. It prints rows
  on stdout, all sharing one subject column:
  - VERDICT-23: the release that judges, first in every frame, a refusal's included: `· release  <tag>  this gate run`,
  - VERDICT-24: and in quality's own run `· release  this checkout  <version> · quality judges itself`;
  - VERDICT-25: the base, `· base  compared  <branch> <base sha> · head <head sha>` with the pull request's head,
  - VERDICT-26: or `· base  none  a push or merge group: no loosening judged`;
  - VERDICT-27: the jobs, as one `✓ jobs  success  N mandatory jobs · M gate jobs` row when all are green,
  - VERDICT-28: else one row
    each, its event the job's conclusion in words,
  - VERDICT-29: a conclusion GitHub adds later as `concluded <word>`;
  - VERDICT-74: the run's CI time, a note after the jobs, `· time  measured  6m12s wall · slowest quality / sonar 55s · longest wait 2s · longest chain 3 jobs 5m35s`:
    the wall clock from the first attempt's first job to the last finished job (a re-run counts from its
    first attempt), the slowest finished job of the newest attempt, and the longest a job waited between its creation and
    its start (runner wait only, since a job behind `needs` is created when its needs finish), and the longest chain of
    jobs waiting on each other: a job created within two seconds of another's completion waits on it (a skipped job ran
    nothing and waits on none), and the chain runs in the newest attempt back from its last finished job, printed as its
    job count and its time from the first job's creation to the last job's completion; where a job was created within two
    seconds of several jobs' completions that lead to chains of different counts or starts, the timestamps do not tell
    which it waited on and the chain reads `longest chain not measured`; a re-run's dependents are created at the re-run,
    so it shows a shorter chain.
  - VERDICT-75: A job lacking a timestamp
    GitHub gives makes it `· time  not measured  GitHub gave no timestamps`; the exit is the jobs' either way;
  - VERDICT-30: on a pull request the loosenings, `✓ loosenings  none` or `! loosenings  N undeclared  M declared` (a count of none
    left out) with each loosening below it;
  - VERDICT-31: `! declarations  N unmatched` where a new entry (below) matches nothing.

- VERDICT-32: One closing line follows, ending with the run's duration (its form: [the closing line](output.md#the-closing-line)):
  `✓ green · N mandatory jobs · 4s` (exit `0`)
- VERDICT-33: or `! red · <what failed> · 4s` (exit `1`), where
  `<what failed>` counts what applies, in this order: `N of M mandatory jobs failed`, `N gate jobs failed`,
  `N undeclared loosenings`, `N declarations unmatched` and `N declared loosenings`. When only declared
  loosenings are red, the line says so ([Declared loosenings](#declared-loosenings)).
- VERDICT-34: The duration counts from the door's
  start.
- VERDICT-38: When it cannot judge (exit `2`), it prints the `error:`/`next:` pair on stderr, with the steps apart
  from the fact, or `fix what the error names, then re-run this run's failed jobs` where it has none:
  - VERDICT-39: a `quality/config.toml` it cannot read (a key this quality does not know included) shows its fact and, where its rule
    has one, [the example to copy](output.md#refusals),
  - VERDICT-40: then `push the fix; the gate judges the new commit`
    at the head,
  - VERDICT-41: or `fix it on <base branch> first, then update the pull request's branch with <base branch>`
    at the base,
  - VERDICT-42: which is read first;
  - VERDICT-43: a variable the step sets that the run lacks (unset or empty) is `<NAME> is not set`,
  - VERDICT-44: or holds in another
    form `<NAME> is not …`,
  - VERDICT-45: each with the step
    `quality verdict runs as gate.yml's verdict step, which sets it` and its value never printed;
  - CALLER-10: beside a
    base the step also sets the pull request's number and head;
  - VERDICT-46: a jobs page that is not GitHub's jobs answer (not JSON, no `total_count`, a job whose `id`, `name` or `run_attempt`
    has the wrong type) cannot be judged;
  - VERDICT-47: a GitHub answer a re-run may clear names `re-run this run's failed jobs`.

- VERDICT-20: When it read the jobs more than once, a first row says so:
  `· run  2 reads  GitHub first listed "checks" (attempt 1) as in_progress`. A GitHub server error
  (5xx) is read again within the same budget, and if it lasts, the fact names it as GitHub's:
  `GitHub answered HTTP 502 Bad Gateway for … after 13 reads · GitHub's fault, not the caller's`. Any other failure, such
  as a 403, a timeout, a body cut short or an unreachable GitHub, is not read again.
- VERDICT-21: A cannot-judge after a
  re-read still prints the rows of the last judged read first, on stdout.
- VERDICT-35: The run's step summary gets the
  same lines, stdout's and then the pair, in one fenced code block on a line of its own,
- VERDICT-48: whose fence is
  longer than every backtick run in them, so no job name or `what` renders as markdown.
- VERDICT-49: A summary that
  cannot be written adds `error: GITHUB_STEP_SUMMARY is not writable · <reason>` after them (exit `2`).
- VERDICT-50: `-v` adds nothing: the verdict runs no scanner.

Ruled by the operator: the verdict reads the run's jobs again for at most two minutes, because GitHub's jobs listing
answers late per job; a job still listed as running after the last read is exit `2`.

- READ-4: The verdict says in a first line that it read again,
- READ-5: reads a 5xx again,
- READ-6: and adds the permission hint
  only after a 403 or 404.

The optional `[languages]` table beside `[gate]` holds one key, `render_pass`, a non-empty job name as `mandatory_jobs` names
it. It is read by the `render-pass` rule under `quality check` ([rules](rules.md)), not by the verdict: the verdict does not
load it, so removing the declaration or pointing it at a job that is not mandatory turns the head red through that rule, and
no loosening event exists for it. Any other key, or a value that is not a non-empty string, is exit `2` in the door
(RENDER-3).

### Declared loosenings

- VERDICT-52: A pull request that loosens the gate as the operator ruled declares each loosening in its `quality/config.toml`, one
  entry per loosening:

```toml
[[gate.loosened]]
what = 'mandatory job "Acceptance evidence (integration)" removed'
item = "overnightworks/songmaker#1345"
date = 2026-10-20
reason = "the operator ruled the requirement shelf machinery out"
```

- VERDICT-53: `what` is exactly the text the verdict prints after `! ` for an undeclared loosening under its `loosenings` row,
  unescaped; a declared line is never copied, since its ` · <item> of <date>` is no part of the `what`, and neither is the
  line of an added root or layout entry, which a ruling written on the entry declares (VERDICT-76).
- VERDICT-54: The
  verdict prints a job name escaped as [Masking](output.md#masking) says, so a printed backslash is either such an
  escape or one the name holds itself; the head's files show which.
- VERDICT-55: In a double-quoted TOML string, each
  escape the verdict added is written as its TOML escape (`\x0a` as `\n`; `\u202e` and `\U000e0041` are TOML escapes
  already) and each backslash the name holds itself as `\\`. A single-quoted literal string, as in the
  example, fits only a `what` the verdict printed without an escape.
- VERDICT-56: Any other text, another number included, declares
  nothing.
- VERDICT-57: `item` is `#<n>` or `<owner>/<repo>#<n>` (ASCII letters, digits, `_`, `.` and `-`), `date` a TOML date without a
  time, `reason` one non-empty line. A missing or extra key, a text date, two entries with one `what` or any other
  malformed entry is exit `2` in `verdict` and in `check` (the `secret` and `advisory` rules), naming the entry and
  showing this example;
- VERDICT-58: `check secrets --staged` reads only `[secrets]`.
- VERDICT-59: Only new entries declare: those the head holds and the base does not, compared as a whole entry. An inherited entry
  is never red and declares nothing, so a later pull request that loosens the same way again needs its own entry with
  its own `item` and `date`; deleting an inherited entry is no loosening.
- VERDICT-60: A new entry that matches no
  loosening of the pull request is red: it stands under `! declarations  1 unmatched` as the door shows it. A base
  without `quality/config.toml` (the adoption) holds no entry, so every entry there is new and matches nothing.
- VERDICT-61: Without a base (push, `merge_group`) no entry, allowance, root or layout entry, generated-file or
  implemented-document declaration is judged.
- VERDICT-62: A declared loosening stays red (exit `1`) and says so: below its row it stands as
  `! mandatory job "…" removed · overnightworks/songmaker#1345 of 2026-10-20`, with `reason: …` beneath.
- VERDICT-63: When nothing else is red, the closing line is `! red · only declared loosenings · only the operator merges it · 4s`,
  and stderr names the one command: `next: quality merge <owner>/<repo>#<n>`. No entry approves itself:
  an agent may write one, but the ruleset and `aco land` still need a green `quality / verdict`, and only
  [the operator's door](commands.md#quality-merge) merges.
- VERDICT-64: An advisory allowance (`[[advisories.allow]]`, see the `advisory` rule under `quality check`) declares its own
  loosening with its own `item` and `date`, so it needs no `[[gate.loosened]]` entry. An allowance whose `id` and
  `lock` the base lacks prints `advisory allowance adds <id> in <lock> until <until> · <item> of <date>`; one whose
  `until` the head moves later prints `… advisory allowance extends <id> in <lock> to <until>`.
- VERDICT-65: An earlier
  `until`, a changed `reason`, `item` or `date`, and a deleted allowance loosen nothing; a changed `id` or `lock` is a
  new allowance.
- VERDICT-66: A base without `quality/config.toml` holds no allowance, so every allowance of the
  adoption is new.
- VERDICT-67: A `[[gate.loosened]]` entry whose `what` is such a text matches nothing and is red.
  The verdict and the door read the whole config, so
  [a malformed `[secrets]` or `[advisories]` entry](rules.md) is exit `2` there too.
- VERDICT-71: A secret allowance (`[[secrets.allow]]` or an inline `allow = [...]` list, see the `secret` rule under `quality check`)
  is a loosening with no `item` and `date` of its own, since its entry carries none and no `until` brings it back to red.
  A head `(rule, file, secret_sha256)` the base lacks prints
  `secret allowance adds <rule> in <file> · sha256 <first 12 hex of secret_sha256>`; it is declared by a
  `[[gate.loosened]]` entry with that text as `what`, like a removed mandatory job, and stays red for the operator's door.
- VERDICT-72: A deleted entry and a changed `reason` loosen nothing; a changed `rule`, `file` or `secret_sha256` is a
  new entry.
- VERDICT-73: A base without `quality/config.toml` holds no allowance, so every secret allowance of the
  adoption is new.
- VERDICT-68: A generated-file declaration (`[[size.generated]]`, see the `size` rule under `quality check`) declares its own
  loosening with its own `item` and `date`, like an allowance. A head `path` the base does not declare prints
  `size leaves out generated file <path> · <item> of <date>`, so a changed `path` and every declaration of an adoption
  (a base without `quality/config.toml`) are new.
- VERDICT-69: An inherited or deleted declaration and a changed `job`,
  `reason`, `item` or `date` loosen nothing.
- VERDICT-70: A `[[gate.loosened]]` entry whose `what` is such a text
  matches nothing and is red. A [malformed `[size]` entry](rules.md) is exit `2` in the verdict and the
  door, naming the side.
- An implemented-document declaration (`[[spec_citations.implemented]]`, see the `spec-citation` rule under `quality check`)
  declares its own loosening the same way: a head `path` the base does not declare prints
  `spec citation leaves <path> to its implementers · <item> of <date>`, after the generated-file lines, and every other case
  of the bullet above holds for it (CITE-18). A [malformed `[spec_citations]` entry](rules.md) is exit `2` in the verdict and
  the door, naming the side. An implementer's `[[spec_citations.foreign]]` entry is no loosening.
- VERDICT-76: A root or layout entry (`[[root.allow]]` and `[[layout.allow]]`, see the `root-layout` rule and the layout rules
  under `quality check`) is a loosening of its own when it is added: a head `entry` the base lacks prints
  `root allowance adds <entry> until <ends>`, a head `rule` and `path` the base lacks prints
  `layout allowance adds <path> for <rule> until <ends>`, both after the implemented-document lines. `until` names the
  open item whose landing removes the entry and is never compared. Every entry of an adoption (a base without
  `quality/config.toml`) is added.
- VERDICT-77: With `item` and `date` written on the entry, the operator's ruling that lets it in, the line is declared by
  them, as `· <item> of <date>` with `reason: …` beneath. Without them the line is undeclared and has
  `fix: write the operator's ruling that lets it in on the entry in quality/config.toml, its item and its date` beneath,
  where `reason:` stands for a declared line. The verdict is red either way, and only [the operator's
  door](commands.md#quality-merge) lands a declared one: an undeclared one stops the door, whose blocking row shows the
  same line with the same `fix:`.
- VERDICT-78: An inherited entry, a changed `reason`, `ends` or ruling and a deleted entry loosen nothing; a layout entry
  whose `rule` changes is a new entry.
- VERDICT-79: The base side of the two tables is read by identity only, `entry` for root and `rule` and `path` for
  layout, and every other key and value is ignored, a repeated identity included, so a base written before `ends`
  existed, or with a reason or a key this release refuses, is read, and the pull request that writes `ends` is no
  loosening. A base entry without its identity keys, or with one that is not text, is exit `2` naming the base, and its
  example shows the identity keys alone. The head side is read strictly: an entry without `ends`, with an `ends` that is
  no item reference, or with only one of `item` and `date` is a config it cannot read (VERDICT-39 to VERDICT-41), and
  `next:` shows the table's example.
- VERDICT-80: A `[[gate.loosened]]` entry whose `what` is the text of such a line matches nothing and is red.
- VERDICT-81: A layout `path`, and a root entry that names a file, moves when git's rename listing (BASELINE-11) maps the
  base path to the head path: the head entry at the new path is inherited, not added. While the head still declares the
  old name, the new one is added. A root directory has no rename git reports, so a directory declared under a new name
  is added. The listing is read only when the head holds an entry whose identity the base lacks, so a run with no added
  entry reads and fetches nothing more than before.
- VERDICT-82: A base whose `[root]` or `[layout]` table cannot be read, an `allow` that is no list or a key the table does
  not have, is refused as well, exit `2` naming the base, with the identity-only example.
- A baseline loosening (`size baseline raises …`, `clone baseline adds …`, `job limit baseline raises …`, `spec citation
  baseline adds …`, and `adapter import baseline raises <directory> <from> -> <to>`, `adapter import baseline adds
  <directory> <count>` or `adapter import baseline removed`, see "The baseline only falls" in [rules](rules.md)) is declared
  by a `[[gate.loosened]]` entry with the printed text as `what`, like a removed mandatory job.
- Ruled by the operator: removing, softening or bypassing an import-linter contract declares the same way, with one of these texts as `what`
  (the rule is in [rules](rules.md), "The import contracts only tighten"): `import contract "<name>" removed`,
  `import-linter config in <file> removed`, `import contract "<name>" ignores <expression>`,
  `import contract "<name>" changes <field>`, `import-linter changes <option>` and
  `import contract id core-imports-no-adapter removed` or `moves "<name>" -> "<name>"`.

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
