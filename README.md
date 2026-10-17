# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality scan-staged [PATH] --engines DIR
    quality engines install DIR
    quality engines verify DIR
    quality sonar preflight [PATH]
    quality sonar findings

Install it with

    uv tool install "overnightworks-quality @ git+https://github.com/overnightworks/quality@<commit sha>"

(no tag exists yet, so the line names a commit). The gitleaks paths (`hygiene --engines`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

`hygiene` reads git's view of the repository at `PATH` (default `.`) and applies two rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
- `compose-port`: every published port in a tracked `compose*.y{a,}ml` or `docker-compose*.y{a,}ml` binds `127.0.0.1`.

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
exists and cannot be read or parsed is exit `2`.

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

Each finding names the rule, the file, a reason and a fix. Exit `0` is clean, `1` means findings,
`2` means the repository could not be judged (never clean).

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

- **Operator only.** It runs under the operator's own `gh` login, which needs the `admin:org` scope (checked first: exit `2`,
  nothing changed, the missing right named). Agents never run it; it refuses when stdin is not a terminal.
- **It refuses unless the door is the only way** (exit `1`): the pull request adds no finding of its own (the findings are
  named otherwise), the default branch has legacy findings, the only red checks are `quality / sonar` and
  `quality / verdict`, and every other check is green. `SONAR_TOKEN` is read from the environment when the project needs one.
- **It shows, then asks:** repository, pull request, head SHA and legacy count, then the typed word `ja`. Anything else
  changes nothing.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this removes ruleset 25227067 for it),
  runs `gh pr merge <pr> --merge --match-head-commit <sha>`, and sets the property back to `true` in every case: merged,
  merge failed, Ctrl-C. A property that cannot be restored is exit `2` with one line naming what to set by hand.
- **Logged:** one comment on the pull request (who, when, legacy count, `quality_gate off → merge → on`). The command
  prints `aco release <n> --merged <pr>` for the head, because `aco land` refuses any non-green check.
