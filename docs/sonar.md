# SonarCloud

Reference for the engineer who reads SonarCloud findings through `quality sonar findings` or the gate. Why quality
judges the findings itself and keeps the properties check stands in [ARCHITECTURE](ARCHITECTURE.md).

## `quality sonar findings`

`gate.yml` runs SonarCloud's scanner in its `sonar` job and judges it through `verdict`; callers use `gate.yml`, never
an action, because quality publishes none. A caller passes the Actions secret by name
(`secrets: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}`), has automatic analysis off in SonarCloud, and commits a root
`sonar-project.properties` (SONAR-47). A missing or empty token is red (`error: SONAR_TOKEN is empty: …`, with the step
to set and pass the secret), never skipped while SonarCloud runs; `[gate] sonar = false` turns it off
([the gate](gate.md#gateyml)). Dependabot runs resolve the Dependabot secret of the same name.

### What the two steps print

Both commands print rows on stdout and end with one closing line: `✓ clean · <time>`, `✓ off · nothing reaches
SonarCloud · <time>`, `✓ clean · new 0 · legacy N · <time>`, `! red · …` or `✓ report only · …`. When they cannot judge
(exit `2`) they print the `error:`/`next:` pair on stderr instead. A refusal that knows its own step names it: a
`quality/config.toml` it cannot read shows its example to copy and `push the fix; the gate judges the new commit`; an
empty `SONAR_TOKEN` the step to set the secret and pass it to `gate.yml`; an unwritable `GITHUB_OUTPUT` `fix what the
error names, then re-run this run's failed jobs`; a changed tracked file the step to let the artifact add reports only. Every other refusal falls back to its step's general step:
`properties` the push step, `findings` the re-run step. `-v` adds nothing: neither step runs a scanner.

```text
$ quality sonar properties
  ✓ properties    clean          sonar-project.properties
  · scan          excludes       docker:S8431 · .github/dependabot.yml exists
✓ clean · <1s
```

```text
$ quality sonar findings        # a pull request
  · analysis      pull request   #7 · overnightworks_quality
  ! new           2 findings
    ! src/a1.py:1  open  python:S1: m
    ! src/a2.py:2  open  python:S1: m
      fix: fix it in the code; an accepted or false-positive mark in SonarCloud still counts
  · legacy        17 open        on main
  ! quality gate  failed         1 condition
    ! new_coverage is 70.0, the gate needs at least 80
! red · new 2 · legacy 17 · quality gate failed · 4s
```

### Coverage on new code

The scan only sees a coverage report that is in the checkout when it starts. `gate.yml` never runs a caller's tests; the
caller hands over the report its checks job wrote, in four steps:

1. Upload the report from the checks job: `actions/upload-artifact` with `name: coverage` and `path: coverage.xml`.
2. Give the gate job `needs: [checks]` (the checks job's id), so the artifact exists when the gate downloads it.
3. Pass the artifact's name to the gate: `with: coverage-artifact: coverage`.
4. Name the report in `sonar-project.properties`, for example `sonar.python.coverage.reportPaths=coverage.xml` (or
   `sonar.javascript.lcov.reportPaths` for JavaScript and TypeScript). The path is relative to the upload's common
   directory, which is where the download puts it.

Before anything reads the checkout, the properties check refuses (exit `2`) a checkout whose tracked files differ from
the checked-out commit, naming the first (SONAR-48). The artifact may add a report but never change a file the commit
tracks: not `quality/config.toml`, whose `[gate] legacy` the findings step reads and the verdict holds at the commit,
not `sonar-project.properties`, not a source the scan reads (SONAR-49). The download comes before the tool checkout, so
a `.gate-tool/` path in the artifact does not stand in for the pinned tool. A planted `.git/` still reaches git;
upload-artifact leaves hidden paths out unless the caller's own workflow sets `include-hidden-files`, which the caller's
workflow could do anyway.

The properties check exits `2` naming the key and the entry when a listed report does not exist (SONAR-50): each
comma-separated entry is judged on its own (SONAR-51), a glob (`*`, `?`, `**`) must match at least one file (SONAR-52)
and `**` must be a whole path segment (SONAR-53), a plain entry must be a file (SONAR-54), and an absolute entry or one
with `..` is refused (SONAR-55). So a key whose report is missing, for instance because no `coverage-artifact` was
passed, is red, not a silent `0.0%`. A caller that names no coverage key and passes no input scans as before (SONAR-56).

`quality sonar properties` runs before the scan and refuses (exit `2`, naming the key) any property outside an allowlist
(`sonar.organization`, `sonar.projectKey`, `sonar.projectName`, `sonar.sources`, `sonar.tests`, the inclusion/exclusion
keys, `sonar.sourceEncoding`, `sonar.python.version` and the two coverage report path keys) (SONAR-57), any value
containing a `${...}` substitution after decoding (the scanner would expand `${env.SONAR_TOKEN}`) (SONAR-58), a
`sonar.organization` or `sonar.projectKey` other than `<owner>` and `<owner>_<repo>` (SONAR-59), a tracked
`.scannerwork/` (SONAR-60), a tracked file that differs from the checked-out commit, and an empty token (SONAR-61). The
file is read with the `java.util.Properties` grammar (`=`, `:` or whitespace separators, `\` continuation, `\uXXXX` and
`\` escapes) (SONAR-62). The file is read inside the checkout; a link leading out of it is refused (SONAR-63), and every
refusal names the file repository-relative (SONAR-64). The check does not stop a pull request that edits the caller's
workflow, which holds the token itself.

After its checks pass (SONAR-13), the properties check also hands the scan its issue exclusion: it appends
`scanner-arguments=<value>` to the file named by `$GITHUB_OUTPUT`, keeping the lines the file holds (SONAR-14); outside
a workflow step it appends nothing, prints the `· scan  excludes  docker:S8431 · .github/dependabot.yml exists` row and
exits `0` (SONAR-15). `gate.yml` appends that step output to the scan's `args`. The value is empty unless
`.github/dependabot.yml` exists, and the keys it sets are all `sonar.issue.ignore.multicriteria` keys (SONAR-16; see the
Dependabot exception below). With `[gate] sonar = false` the properties check refuses a properties file and a passed
token (SONAR-65), prints `· sonar  off  quality/config.toml sets [gate] sonar = false` instead and appends `sonar=off`
(SONAR-66).

The scanner's transport, verified at sonar-scanner-java-library 4.1.1.1633 (the library of sonar-scanner-cli
8.1.0.6389, which `gate.yml` pins through `scannerVersion`):

- The bootstrap's authenticated requests reach only `sonarcloud.io` and `api.sonarcloud.io`. This holds only with the
  pinned `-Dsonar.host.url=https://sonarcloud.io` argument and a properties file that passed the properties check.
- Residual limit: a redirect issued by SonarCloud itself carries the token to the host it names.
- The engine is downloaded from SonarCloud and is not pinned by the CLI pin. SonarSource's server-side code is an
  accepted trust boundary: the token's only purpose is to authenticate to SonarCloud.
- Re-verify all of this on any bump of the scan action or `scannerVersion`.
- Sources at 4.1.1.1633: [HttpClientFactory.java:72](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/HttpClientFactory.java#L72), [ScannerHttpClient.java:162-170](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L162-L170), [ScannerHttpClient.java:231-233](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L231-L233), [ScannerEndpointResolver.java:40](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/endpoint/ScannerEndpointResolver.java#L40), [ScannerEngineLauncherFactory.java](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/facade/forked/ScannerEngineLauncherFactory.java).

`quality sonar findings` is the SonarCloud zero-findings rule: every open finding counts. Where `.github/dependabot.yml` exists the scan itself excludes one rule, see the [Dependabot exception](#a-dependabot-exception-for-dockers8431). It reads the scanner's `.scannerwork/report-task.txt` relative to the working directory (no other path can be given); a missing or unreadable file is exit 2.

Ruled by the operator: every open finding counts, in every repository that runs SonarCloud; SonarCloud belongs to every repository's minimum gate, except a repository whose files the operator rules must not reach it, which sets `[gate] sonar = false`.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey`, `organization=<owner>` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). Anything
unreadable is exit `2` (SONAR-29).

### New and legacy findings

`gate.yml` passes the repository's default branch (`github.event.repository.default_branch`) as `DEFAULT_BRANCH` to
the findings step alone (SONAR-30); it is never assumed to be `main` (SONAR-31), and a missing or empty value is exit
`2` before any request (SONAR-32). "Legacy" means the open findings of that branch, counted by the same
`issues/search` with the same open statuses, and only counted, never listed (SONAR-33). `quality sonar findings`
counts them on a pull request only (SONAR-34).
- **Pull request:** the rows `· analysis  pull request  #<n> · <key>`, `new`, `legacy` and `quality gate`; new
  findings are listed below `! new  N findings` and make the run exit `1` (SONAR-35). Both counts always stand in the
  closing line, for example `✓ clean · new 0 · legacy 17 · 3s` (SONAR-36), the legacy count as `legacy unknown` while
  the default branch is not analysed yet (SONAR-67).
- **Default branch:** a run on the default branch exits `1` on any open finding and lists them below
  `! open  N findings` (SONAR-37).
- **Any other branch:** report only, exit `0`: `· open  N findings  report only`, each finding a `·` note below it
  (SONAR-38).

`[gate] legacy` in `quality/config.toml` decides whether legacy findings fail a pull request. `quality sonar findings`
reads that file like the verdict does: a missing file, or one without `[gate] mandatory_jobs`, is exit `2` before any
request (SONAR-39).

```toml
[gate]
mandatory_jobs = ["quality"]
legacy = "fail"
```

- `"report"` (the default when the key is absent): legacy findings are printed and never fail a pull request
  (SONAR-40).
- `"fail"`: a pull request fails while the default branch has any open finding, even when it adds none (SONAR-41).
- Any other value is a config error (exit `2`), read before the first SonarCloud request (SONAR-42).
- A pull request whose base config says `legacy = "fail"` and whose head config says anything else is red in the verdict
  (the loosening `legacy fail → report`; an absent key counts as `report`), like a removed mandatory job.
- `legacy = "fail"` beside `[gate] sonar = false` is a config error (exit `2`).

On pull requests and default-branch runs `quality sonar findings` reads `api/project_branches/list` once before the
counts (SONAR-43). A run on any other branch does not read it (SONAR-44).
- The default branch has no entry, or one without `analysisDate` (a repository's first adoption pull request): the run
  prints `· legacy  unknown  <default> not analysed yet`. With `"report"` the run exits `0`; with `"fail"` it is exit
  `2` (`error: legacy unknown (<default> not analysed yet)`) (SONAR-45).
- The default branch is not SonarCloud's main branch, analysed or not: exit `2` in both modes, with the fix
  `set SonarCloud's main branch to <default>` (SONAR-46). A non-main SonarCloud branch counts only changed code, so the
  legacy count would be too low.

### The quality gate

On a pull request `quality sonar findings` also reads SonarCloud's quality gate for this run's own analysis (SONAR-1),
by `api/qualitygates/project_status?analysisId=…` with the id of the finished task it waited for (SONAR-2); the token
travels in the header only (SONAR-3). `quality` names no gate and holds no threshold: the repository's gate in
SonarCloud decides, and a move to another gate changes the verdict with no change here. Default-branch and other-branch
runs make no gate request (SONAR-4).

| SonarCloud answers | Exit | Output |
| --- | --- | --- |
| `OK` (SONAR-5) | by the findings, as before | `✓ quality gate  passed` |
| `ERROR` (SONAR-6) | `1` | `! quality gate  failed  N conditions`, each failed condition below it: `! new_coverage is 70.0, the gate needs at least 80` (`LT` reads "at least", `GT` "at most") |
| `NONE`, `WARN`, any other word, no `projectStatus`, an unreadable body, an `ERROR` that names no failed condition or a comparator other than `LT` and `GT` (SONAR-7) | `2` | `error:` and `next:` |
| the task has no `analysisId` (SONAR-8), HTTP 401, 403, 404 or 5xx, a timeout (SONAR-9) | `2` | `error:` and `next:`, before the findings are read |

A failed gate fails the `sonar` job whatever the findings count says (SONAR-10), and `legacy = "fail"` still fails it on
its own (SONAR-11); the verdict then prints the row `! quality / sonar  failure`. `-Dsonar.qualitygate.wait=false` stays
in `gate.yml`: the scanner does not wait for the gate, this command reads it.

- **What the gates check.** The gate set in SonarCloud for the repository; the organization's default is the built-in
  "Sonar way". It judges new code (coverage, duplication, ratings, reviewed security hotspots).
- **A new security hotspot** needs its review mark in the SonarCloud UI and a re-run before the gate is `OK`; unlike a
  finding, it is not fixed in code.
- **A small change.** SonarCloud skips the coverage and duplication conditions for a change of fewer than 20 new lines.
  When its answer says so (`ignoredConditions`), the quality gate row's fact reads
  `coverage and duplication not judged: fewer than 20 new lines`, with `passed` and with `failed`; the exit is unchanged
  (SONAR-12). Branch coverage of a small change is therefore not judged.
- **Joined coverage.** The gate judges the coverage SonarCloud holds, so it measures the 100 % branch rule only for a
  repository whose report joins every test layer and carries branches. For a repository that has not joined its layers
  yet, the coverage gap is SonarCloud's figure.
  SonarCloud indexes no `.svelte` file, so a component's lines and branches stay outside the judge: a component keeps
  its logic in a module SonarCloud measures, and the end-to-end suites prove its markup.

Ruled by the operator: the verdict takes over the gate status, and the gate's result fails the job.

### A Dependabot exception for `docker:S8431`

Sonar's rule `docker:S8431` ("Use either the version tag or the digest for the image instead of both") flags the
`image:tag@sha256:...` form that Dependabot maintains. Sonar's own sensor drops the finding when its scanner has
indexed a Dependabot config
([DockerSensor.java](https://github.com/SonarSource/sonar-iac/blob/2bb050011ed1a224b74d23182edcc82afca8695b/iac-extensions/docker/src/main/java/org/sonar/iac/docker/plugin/DockerSensor.java#L204-L226),
SONARIAC-3096). A pull-request analysis does not see an unchanged `.github/dependabot.yml`, and a repository whose
`sonar.sources` leaves `.github` out keeps the finding on its default branch too. The gate therefore keeps the rule out
of the analysis:

- **In the analysis:** where `.github/dependabot.yml` exists, the properties check hands the scan a multi-criteria
  issue exclusion for `docker:S8431` over every file, so no analysis, pull request or branch, raises the finding; where
  it does not exist it hands nothing (SONAR-17). A finding an earlier analysis raised is closed by the next analysis of
  its branch. The exclusion replaces a multi-criteria exclusion set in SonarCloud's interface on such a repository,
  because a value given to the scanner takes precedence over the interface's.
- **The rule:** `quality sonar properties` tests whether `.github/dependabot.yml` exists. Its content is never read, so
  an empty or broken file counts as existing (SONAR-18); a link to a file counts and a link to nowhere does not
  (SONAR-19); a directory named `.github/dependabot.yml` counts too, unlike Sonar, which needs a file (SONAR-20).
  `.github/dependabot.yaml` is not looked at (SONAR-21).
- **What counts:** `quality sonar findings` and `quality merge` count every open finding (SONAR-25 at merge) and read no
  `dependabot.yml` (SONAR-26). A `docker:S8431` finding that an analysis raises anyway (no `.github/dependabot.yml`,
  only `.github/dependabot.yaml`, or an analysis that predates the exclusion) is an ordinary finding: it prints as its
  row with its `fix:` line, counts in `new` and in the legacy number (SONAR-24), and fails the run (SONAR-23). On the
  default branch and on another branch it is as ordinary as on a pull request: listed, counted, and judged by the
  branch's own rule (SONAR-27).
- **An unreachable file:** a `.github` the filesystem refuses access to is exit `2` from `quality sonar properties`
  with a line naming the file, before it writes the step output (SONAR-22).
- **Only this rule** has the exception (SONAR-28), because only it has the premise (Sonar's own exception that its
  pull-request analysis misses); every other finding and rule counts as before. There is no per-image entry in
  `quality/config.toml` and no reading of `dependabot.yml`: Sonar itself checks existence only, and a pull request is
  judged the way the default branch is. The Docker rule is no reason to move the zero rule to the hosted gate, because
  the scan's own exclusion keeps `docker:S8431` out where `dependabot.yml` exists.
- **Gaps:** the file's existence does not say that Dependabot maintains the Dockerfile's folder for Docker; a repository
  that has only `.github/dependabot.yaml` stays red on pull requests; Renovate is not judged.

Ruled by the operator: Sonar's exception applies on pull requests too, and it holds when `.github/dependabot.yml` exists.
