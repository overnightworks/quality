# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality baseline write [PATH]
    quality scan-staged [PATH] --engines DIR
    quality engines install DIR
    quality engines verify DIR
    quality sonar preflight [PATH]
    quality sonar findings
    quality verdict [PATH]
    quality scoreboard [PATH]
    quality emergency-merge OWNER/REPO PR

Where the fleet is going and why: [docs/roadmap.md](docs/roadmap.md) (target state, waves, per repository) and [docs/rulings.md](docs/rulings.md) (the operator's rulings, verbatim and dated).

Install it with

    uv tool install "overnightworks-quality @ git+https://github.com/overnightworks/quality@<commit sha>"

(no tag exists yet, so the line names a commit). The gitleaks paths (`hygiene --engines`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

`hygiene` reads git's view of the repository at `PATH` (default `.`) and applies four rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
- `compose-port`: every published port in a tracked `compose*.y{a,}ml` or `docker-compose*.y{a,}ml` binds `127.0.0.1`.
- `size`: every tracked `*.py` file is measured with [lizard](https://github.com/terryyin/lizard): a file from 800
  lines, a function from 60 lines and a function with a CCN above 15 are debt, counted apart for production and tests
  (a path under `test/` or `tests/`, `conftest.py`, `test_*.py`, `*_test.py`). Functions are named by their qualified
  symbol (`Class.method`); a second one of the same name, such as a property setter, is `Class.method#2`. Without
  `quality/baseline.toml` the rule only prints the counts (`size: report only (no quality/baseline.toml) - production:
  1 file(s) >= 800 lines, ...`) and the size debt leaves the exit unchanged.
  Files are read the way Python reads source (a PEP 263 coding cookie or a UTF-8 BOM decides the encoding). A file
  Python cannot decode or parse is named as not valid Python: without a baseline the count notes it (`1 file(s) not
  valid Python, functions unmeasured: bad.py`) and the exit is unchanged; with a baseline it is `hygiene: size
  unparsable - bad.py is not valid Python (...)`, and `quality baseline write` refuses with `baseline: not written -
  bad.py is not valid Python (...)`; both exit `2`. A tracked file the operating system refuses to read is `hygiene:
  size cannot judge - ...` with or without a baseline, and `quality baseline write` refuses with `baseline: not written - ...`; both exit `2`.
  The reason names the case: a file without read permission is `cannot read locked.py: [Errno 13] ...`, a file behind
  a directory without permission is `cannot reach pkg/m.py: [Errno 13] ...`. A tracked file missing from disk
  (deleted, or a dangling symlink) is skipped.
  With `quality/baseline.toml`, the tree on disk must hold the baseline: a new violation, an entry that grew and an
  entry above its measured value (stale: lower it with `quality baseline write`) are findings; an entry whose file or
  function is gone is fine.
  `quality baseline write [PATH]` writes the measured debt to `PATH/quality/baseline.toml` as sorted TOML, one table
  per metric (`ccn`, `file_lines`, `function_lines`) keyed by path and qualified symbol. Before its `baseline: wrote N
  entries ...` line it names, in the verdict's wording, each entry it raises or adds against the previous file
  (`baseline: raises function_lines src/quality/cli.py main 82 -> 90`, `baseline: adds ccn src/a.py f 16`); a lowered
  or removed entry is not named, and without a previous file only the count prints. A previous file it cannot read is
  overwritten with `baseline: the previous quality/baseline.toml could not be read (...), so raises cannot be named`.
  The write exits `0` with raises too: the verdict is the gate.
- `import-contract`: the repository's [import-linter](https://github.com/seddonym/import-linter) contracts must be
  kept. The config is the tracked `.importlinter` or `setup.cfg` (`[importlinter]`) or `pyproject.toml`
  (`[tool.importlinter]`); without one the rule prints `import-contract: report only (no import-linter config)` and
  leaves the exit unchanged. import-linter 2.15 runs inside quality as a library, never its CLI, and writes nothing to
  the checkout (no `.import_linter_cache`). A broken contract is one finding on the config file, naming the contract
  and the chain as import-linter renders it (`shop.db -> shop.web (l.1)`), exit `1`. An `ignore_imports` entry no
  import matches follows the contract's `unmatched_ignore_imports_alerting`: `error` (the default) is a finding (`...
  ignores a -> b, which no import matches`), `warn` one `import-contract: note - ...` line, `none` nothing. Each root
  package is the one tracked `<dir>/<name>/__init__.py` outside the tests. The rule cannot judge (exit `2`, before any
  caller code runs or any file outside is read) two configs that each hold a section, a `contract_types` option
  (plugins would be imported), a type other than forbidden, layers, independence, protected and acyclic_siblings,
  invalid contract options, a config value of the wrong type (`... in pyproject.toml cannot be judged: ...`), a
  dotted root package, a root package with no or several such directories, one Python finds in another place (`root
  package yaml is src/yaml/__init__.py, but Python finds it outside the repository at site-packages/yaml/__init__.py`, a
  place outside named from the `sys.path` entry it lies under; a place inside is named repository-relative), a symlink
  under a root package that leaves the repository, directly or through a link that stays inside, and a symlink loop. A
  module grimp cannot parse is `hygiene: import-contract unparsable - ...`; a warning grimp gives while building the
  graph is one `import-contract: note - ...` line. No message names an absolute local path.
- The baseline only falls: on a pull request `quality verdict` holds the head's `quality/baseline.toml` against the
  base commit's, so growing a function and rewriting the baseline stays red. Each loosening is one line, named with the
  baseline's own metric, path and symbol (a file entry has no symbol):
  - an entry raised: `loosened: size baseline raises function_lines src/quality/cli.py main 82 -> 90`;
  - an entry the base does not hold, a moved or renamed one included: `loosened: size baseline adds ccn src/a.py f 16`;
  - the base's file deleted: `loosened: quality/baseline.toml removed`.

  A lowered, equal or removed entry is green, and so is adopting a baseline (the base has none) or having none on
  either side. A malformed baseline on either side, a head config or baseline that cannot be read or links out of the
  repository, or a base commit the clone lacks is exit `2`, naming the side (`quality/baseline.toml at the base commit
  <sha>: ...`, `quality/config.toml at the head: ...`). Only the head's malformed baseline adds ``rewrite it with
  `quality baseline write` ``: a pull request cannot rewrite its base. The base must be a ref
  name or a SHA: `HEAD~1` or `-x` is exit `2` (`... is not a ref name or a SHA`) before git runs. Outside a pull
  request (push, `merge_group`, `workflow_dispatch`) `gate.yml` passes no base, so the base is never read and only
  `hygiene` holds the tree. A lizard update that measures a function higher therefore turns the pull request that
  takes it red until the baseline may declare that loosening.

With `--engines DIR` the `secret` rule also scans the whole history reachable from `HEAD` with gitleaks (a key removed later is still found; other branches the checkout fetched are not scanned), always with quality's own config
(`src/quality/gitleaks.toml`): the repository's `.gitleaks.toml`, `.gitleaksignore` and `gitleaks:allow`
comments have no effect. A known false positive is allowed by the value, not the place: the repository's
`quality/config.toml` names the gitleaks rule, the file and the SHA-256 of the finding's secret (UTF-8, lowercase hex)
with a reason:

    [secrets]
    allow = [{ rule = "aws-access-token", file = "tests/fixture.py", secret_sha256 = "<64 hex>", reason = "fixture value" }]

One entry allows that value in that file at any line and in every commit that holds it. Another value in the same
place is red, and the same value in another file is red. A red finding prints its hash
(`secret src/a.py:3 - gitleaks rule aws-access-token matched at 76ec522b (sha256 <64 hex>, value not shown).`), so
the entry is copied from the output. An entry without a reason, a hash that is not 64 lowercase hex, a duplicate
`(rule, file, secret_sha256)` and the old `fingerprint` form are exit `2`.

A missing `quality/config.toml` means no allowances: the scan still runs and its findings are red. Only a file that
exists and cannot be read or parsed, or links out of the repository, is exit `2`; a link that leads nowhere or loops
exists, so it is exit `2` too.

Secret handling: quality runs gitleaks without `--redact` so the report carries each `Secret` to be hashed. gitleaks
writes that report to a `tempfile` scratch directory (mode 0700); quality reads it, keeps only the hash, and deletes
the directory on every exit path (success, gitleaks failure, parse error). The value reaches no stdout,
stderr, log, exception text or retained file; gitleaks runs without `-v`. While the scan runs, the value exists in
that report file and in quality's process memory. The boundary is the deletion and the absence of any other sink,
not memory-only handling. The hash is safe to print: it appears only where the history already shows the content,
and a real secret must be rotated anyway.

The scan runs git without any attributes (not the repository's `.gitattributes`, not `info/attributes`, not the
system or global file), so a `-diff` or `binary` line cannot hide a key. That needs git 2.41 or newer; an older git
is exit `2`. The system file is excluded by `GIT_ATTR_NOSYSTEM`, git's documented switch; that is pinned as an
environment contract, not by a root-only real-file test.

Every allowed finding is printed with the entry's reason (`secret: 1 allowed (src/x.py:139)`, then
  `allowed src/x.py:139 - gitleaks rule generic-api-key allowed: enum value, not a key`). An allowance that matched
nothing is printed too, so a stale entry can be deleted: `secret: unused allowance <rule> in <file> (sha256 <hex>) - <reason>`;
`scan-staged` prints both kinds of line as well.

Every rule is judged, even when another cannot be, and gives one verdict: clean, findings, unparsable, or cannot judge.
Each finding names the rule, the file, a reason and a fix; a file name that is not valid UTF-8 prints
backslash-escaped. A rule that cannot judge prints its verdict and reason under its rule id (`hygiene: secret cannot
judge - ...`). A clean run names its rules in run order (`hygiene: clean (env-file, compose-port, size, import-contract); skipped without
--engines: ...`); any other run ends with the findings and the verdicts counted (`hygiene: 1 finding(s); verdicts: 1
clean, 1 findings, 1 unparsable`). The exit is the worst verdict's: `2` if any rule is unparsable or cannot judge
(never clean), otherwise `1` if any rule has findings, otherwise `0`. A path that is not a readable git repository is
`hygiene: cannot judge - ...` and exit `2` before any rule runs.

## `quality scan-staged`

`quality scan-staged [PATH] --engines DIR` scans exactly what the next commit records: the effective index of the
repository at `PATH` (default `.`) compared with `HEAD`. It is meant for a pre-commit hook, which runs it in the
work-tree top. A relative `GIT_INDEX_FILE` is resolved against the working directory, as git does, so `git commit -a`,
`git commit <path>` and a linked worktree scan the index git is committing.

The index is written as a tree (`git write-tree`, only tree objects a commit writes anyway) and read into a scratch
repository inside a 0700 scratch directory. The scratch repository borrows the objects (`objects/info/alternates`),
has an empty work tree and none of the live repository's config or `info/attributes`, so a staged `.gitleaksignore`,
`.gitleaks.toml`, `.gitattributes` with `-diff`, or a `gitleaks:allow` comment changes nothing. gitleaks runs in the same
attribute-free environment as `hygiene`, with quality's config. The allow list is the staged tree's own
`quality/config.toml` (none staged: no allowances). `scan-staged` validates only the `[secrets]` table of that config and ignores other tables
and keys, while `hygiene` and `verdict` stay strict, so schema growth never blocks a commit. Output and secret handling
are `hygiene`'s:
`secret src/a.py:3 - gitleaks rule aws-access-token matched in the staged change (sha256 <64 hex>, value not shown).`

gitleaks' staged mode sees only added and changed lines, so a known false positive blocks only the commits that touch
its line, and then needs its allowance, as in CI. A key only in the work tree or only in `HEAD` is clean. Exit `2` (never
clean): an engine missing or at the wrong version, git older than 2.41, an unreadable allow list, an index with unmerged
entries, a path that is not a repository, or a gitleaks run without a report.

## `quality scoreboard`

`quality scoreboard [PATH]` prints one markdown table of the Python size of every repository `quality/fleet.toml` lists
(`public = [...]` and `private = [...]`, each a list of URLs, each listed once), under a heading with the date, the
quality version and the languages measured (`Python only` for now). With `GITHUB_STEP_SUMMARY` set, the same text is
appended there. The fleet list lives once, in `quality/fleet.toml`; `.github/workflows/scoreboard.yml` runs it every
Monday at 05:17 UTC and on dispatch; the table stands in that run's summary. The run only reports: it writes to no repository and needs no secret. A missing, malformed or
duplicating `quality/fleet.toml` is `cannot judge`, exit `2`.

- A `public` URL is cloned with `git -c credential.helper= clone --depth 1 --no-tags`, `GIT_TERMINAL_PROMPT=0` and no
  askpass, into a temporary directory that is removed on every exit, and its default branch tip is measured.
- git runs anonymously: without system, global or `GIT_CONFIG_*` config and with an empty `HOME`, so no configured
  header, helper or `~/.netrc` reaches the host. Only `http`, `https` and `file` URLs without credentials in them are
  used; any other URL is `cannot judge`.
- Lines are the physical lines of the tracked `*.py` files, the count the `size` rule's 800-line limit uses (blank and
  comment lines included). Production versus test is the `size` rule's file-name rule, so `scripts/test_inventory.py`
  counts as test. `test:prod` is test lines divided by production lines with two decimals, `—` without production
  lines. The debt columns count files from 800 lines, functions from 60 lines and functions with a CCN above 15.
- A file Python cannot parse still has its lines counted; the row's note says how many such files there are, never which.
- A `private` repository is never measured, because the table is published: it shows `not measured: private`. It is
  checked once with an anonymous `git ls-remote`; if anyone can read it, the row is `cannot judge: declared private
  but readable`.
- A repository that cannot be cloned or measured shows `cannot judge: <reason>` (for example `cannot clone (git exited
  128)`), with no local path; every other row still shows, and the exit is `2`. Otherwise the exit is `0`.

## `quality engines`

`quality engines install DIR` verifies each pinned engine first and downloads only one that is missing or does not report
its pinned version, so a second run downloads nothing (`gitleaks 8.30.1 already installed`; otherwise `... installed`).
`quality engines verify DIR` is exit `0` when every pinned engine in `DIR` reports exactly its pinned version, otherwise
exit `2` with one line per engine.

## `quality sonar findings`

`gate.yml` runs SonarCloud's scanner in its `sonar` job and judges it through `verdict`; callers use `gate.yml`, never
an action, because quality publishes none. A caller passes the Actions secret by name
(`secrets: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}`), has automatic analysis off in SonarCloud, and commits a root
`sonar-project.properties`. A missing or empty token is red (`sonar: cannot judge - SONAR_TOKEN is empty`), never
skipped; Dependabot runs resolve the Dependabot secret of the same name.

### Coverage on new code

The scan only sees a coverage report that is in the checkout when it starts. `gate.yml` never runs a caller's tests; the
caller hands over the report its tests job wrote, in four steps:

1. Upload the report from the tests job: `actions/upload-artifact` with `name: coverage` and `path: coverage.xml`.
2. Give the gate job `needs: [tests]` (the tests job's id), so the artifact exists when the gate downloads it.
3. Pass the artifact's name to the gate: `with: coverage-artifact: coverage`.
4. Name the report in `sonar-project.properties`, for example `sonar.python.coverage.reportPaths=coverage.xml`
   (or `sonar.javascript.lcov.reportPaths`). The path is relative to the upload's common directory, which is where the
   download puts it.

The sonar job downloads the artifact right after the caller checkout and before the gate's own tool checkout, so a
`.gate-tool/` path inside the artifact does not stand in for the pinned tool: the tool checkout re-creates it at the
pinned ref. This is defence in depth, not a guarantee. An artifact that plants `.gate-tool/.git/` still reaches git
through the checkout, and the caller's own tests job is arbitrary code anyway.

Preflight exits `2` naming the key and the entry when a listed report does not exist: each comma-separated entry is
judged on its own, a glob (`*`, `?`, `**`) must match at least one file and `**` must be a whole path segment, a plain
entry must be a file, and an absolute entry or one with `..` is refused. So a key whose report is missing, for instance
because no `coverage-artifact` was passed, is red, not a silent `0.0%`. A
caller that names no coverage key and passes no input scans as before.

`quality sonar preflight [PATH]` runs before the scan and refuses (exit `2`, naming the key) any property outside an
allowlist (`sonar.organization`, `sonar.projectKey`, `sonar.projectName`, `sonar.sources`, `sonar.tests`, the
inclusion/exclusion keys, `sonar.sourceEncoding`, `sonar.python.version` and the two coverage report path keys), any
value containing a `${...}` substitution after decoding (the scanner would expand `${env.SONAR_TOKEN}`), a
`sonar.organization` or `sonar.projectKey` other than `<owner>` and `<owner>_<repo>`, a tracked `.scannerwork/`, and
an empty token. The file is read with the `java.util.Properties` grammar (`=`, `:` or whitespace separators, `\`
continuation, `\uXXXX` and `\` escapes).

The scanner's transport, verified at sonar-scanner-java-library 4.1.1.1633 (the library of sonar-scanner-cli
8.1.0.6389, which `gate.yml` pins through `scannerVersion`):

- The bootstrap's authenticated requests reach only `sonarcloud.io` and `api.sonarcloud.io`. This holds only with the
  pinned `-Dsonar.host.url=https://sonarcloud.io` argument and a properties file that passed preflight.
- Residual limit: a redirect issued by SonarCloud itself carries the token to the host it names.
- The engine is downloaded from SonarCloud and is not pinned by the CLI pin. SonarSource's server-side code is an
  accepted trust boundary: the token's only purpose is to authenticate to SonarCloud.
- Re-verify all of this on any bump of the scan action or `scannerVersion`.
- Sources at 4.1.1.1633: [HttpClientFactory.java:72](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/HttpClientFactory.java#L72), [ScannerHttpClient.java:162-170](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L162-L170), [ScannerHttpClient.java:231-233](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L231-L233), [ScannerEndpointResolver.java:40](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/endpoint/ScannerEndpointResolver.java#L40), [ScannerEngineLauncherFactory.java](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/facade/forked/ScannerEngineLauncherFactory.java).

`quality sonar findings` is the SonarCloud zero-findings rule. It reads the scanner's `.scannerwork/report-task.txt` relative to the working directory (no other path can be given); a missing or unreadable file is exit 2.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey`, `organization=<owner>` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). Anything
unreadable is exit `2`.

### New and legacy findings

`gate.yml` passes the repository's default branch (`github.event.repository.default_branch`) as `DEFAULT_BRANCH`; it is
never assumed to be `main`, and a missing or empty value is exit `2`. "Legacy" means the open findings of that branch,
counted by the same `issues/search` with the same open statuses, and only counted, never listed.
- **Pull request:** new findings are listed and make the run exit `1`. Both counts always print, for example
  `sonar: new 0 · legacy 17 (overnightworks_webauth, main)`.
- **Default branch:** a run on the default branch exits `1` on any open finding, lists them and prints
  `sonar: N open on <default>`.
- **Any other branch:** report only, exit `0`: `sonar: N open on <branch> (report only)`.

`[gate] legacy` in `quality/config.toml` decides whether legacy findings fail a pull request. `quality sonar findings`
reads that file like the verdict does: a missing file, or one without `[gate] mandatory_jobs`, is exit `2` before any request.

```toml
[gate]
mandatory_jobs = ["quality"]
legacy = "fail"
```

- `"report"` (the default when the key is absent): legacy findings are printed and never fail a pull request.
- `"fail"`: a pull request fails while the default branch has any open finding, even when it adds none.
- Any other value is a config error (exit `2`), read before the first SonarCloud request.
- A pull request whose base config says `legacy = "fail"` and whose head config says anything else is red in the verdict
  (`loosened: legacy fail → report`; an absent key counts as `report`), like a removed mandatory job.

On pull requests and default-branch runs `quality sonar findings` reads `api/project_branches/list` once before the counts.
- The default branch has no entry, or one without `analysisDate` (a repository's first adoption pull request): the summary
  prints `legacy unknown (<default> not analysed yet)`. With `"report"` the run exits `0`; with `"fail"` it is exit `2`
  (`sonar: cannot judge - legacy unknown (<default> not analysed yet)`).
- The default branch is not SonarCloud's main branch: exit `2` in both modes, with the fix `set SonarCloud's main branch
  to <default>`. A non-main SonarCloud branch counts only changed code, so the legacy count would be too low.

### The way back to green under `legacy = "fail"`

Main can gain a finding without any pull request introducing it (a taint finding on an unchanged line, a SonarCloud
rule-profile update). Then every pull request is red, including the fixing one, and switching `legacy` back is red too.
The way out is the operator's emergency door, one command (quality#37):

```
quality emergency-merge <owner/repo> <pr>
```

- **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository (checked
  first: exit `2`, nothing changed, the missing right named). Agents never run it; it refuses when stdin is not a terminal.
- **It refuses unless the door is the only way** (exit `1`): `quality / sonar` and `quality / verdict` have failed, every
  other check run and commit status is green, the verdict is red for the Sonar gate alone (the verdict's own judgment,
  re-derived with the Sonar job assumed green, finds no other problem, so a loosened gate, a raised or removed size
  baseline (read at the merge commit and at base) or a missing mandatory job is refused), `[gate] legacy` is `fail` at the pull request's current merge commit (under `report` the Sonar job cannot be red on legacy findings, and a
  missing config reads as `report`), SonarCloud's analysis of the pull request is of the head being merged and adds no finding (the findings are
  named otherwise), and the default branch has legacy findings. `SONAR_TOKEN` is read from the environment when the project
  needs one: without it the door reads a public project anonymously, with no Authorization header, and an anonymous read
  that SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN`; with a token, a 401 or 403 says the token was rejected.
- **It shows, then asks:** repository, pull request, head SHA and legacy count, then the typed word `ja`. Anything else
  changes nothing.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this removes ruleset 25227067 for it),
  runs `gh pr merge <pr> --merge --match-head-commit <sha>`, and sets the property back to `true` in every case: merged,
  merge failed, Ctrl-C. A property that cannot be restored is exit `2` with one line naming what to set by hand and the merge outcome. A merge whose
  outcome GitHub does not confirm (Ctrl-C, a lost answer) is re-read from the pull request; if that does not settle it, exit `2`
  with `merge state unknown`.
- **Logged:** one comment on the pull request (who, when, legacy count, `quality_gate off → <outcome> → on`), also when the
  restore failed. The command
  prints `aco release <n> --abandoned "landed as PR #<pr> by emergency-merge"` for the head, because `aco land` refuses any non-green check and `--merged` refuses a merge commit without a `Work-Item:` trailer.

## `codeql.yml`

A reusable workflow that runs CodeQL for the languages a caller names, with `build-mode: none` and CodeQL's default
query suite and threat model. It uploads under the category `/language:<language>`, the Default Setup's category, so
alerts carry over when a repository switches.

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
  every category the repository already analyses: the template's `["actions", "python"]` would drop
  `/language:javascript-typescript` and its alerts in agent-coordination, which analyses `actions`,
  `javascript-typescript` and `python`.
- **Permissions:** the caller grants `security-events: write`, `contents: read` and `actions: read`, and the workflow
  asks for no more. A caller without `security-events: write` is refused at startup.
- **Updates:** pin `<tag>` to a release; Dependabot's `github-actions` ecosystem bumps it.
- **Caller path:** a reusable call records the `analysis_key` `<caller file>:analyze` (measured in quality#68), so keep
  the existing caller file's path and the key carries over.
- **Switch PR:** a caller that fails at startup adds no check to its PR (#5 finding 117), so the switch PR must show its
  `codeql / Analyze (…)` checks.
- **Switching a repository:** the Default Setup goes off first, in the order of
  [agent-coordination#138](https://github.com/overnightworks/agent-coordination/issues/138).
