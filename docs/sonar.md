# SonarCloud

Reference for the engineer who reads SonarCloud findings through `quality sonar findings` or the gate.

## `quality sonar findings`

`gate.yml` runs SonarCloud's scanner in its `sonar` job and judges it through `verdict`; callers use `gate.yml`, never
an action, because quality publishes none. A caller passes the Actions secret by name
(`secrets: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}`), has automatic analysis off in SonarCloud, and commits a root
`sonar-project.properties`. A missing or empty token is red (`sonar: cannot judge - SONAR_TOKEN is empty`), never
skipped; Dependabot runs resolve the Dependabot secret of the same name.

### Coverage on new code

The scan only sees a coverage report that is in the checkout when it starts. `gate.yml` never runs a caller's tests; the
caller hands over the report its checks job wrote, in four steps:

1. Upload the report from the checks job: `actions/upload-artifact` with `name: coverage` and `path: coverage.xml`.
2. Give the gate job `needs: [checks]` (the checks job's id), so the artifact exists when the gate downloads it.
3. Pass the artifact's name to the gate: `with: coverage-artifact: coverage`.
4. Name the report in `sonar-project.properties`, for example `sonar.python.coverage.reportPaths=coverage.xml`
   (or `sonar.javascript.lcov.reportPaths`). The path is relative to the upload's common directory, which is where the
   download puts it.

The sonar job downloads the artifact right after the caller checkout and before the gate's own tool checkout, so a
`.gate-tool/` path inside the artifact does not stand in for the pinned tool: the tool checkout re-creates it at the
pinned ref. This is defence in depth, not a guarantee. An artifact that plants `.gate-tool/.git/` still reaches git
through the checkout, and the caller's own checks job is arbitrary code anyway.

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

`quality sonar findings` is the SonarCloud zero-findings rule, except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside. It reads the scanner's `.scannerwork/report-task.txt` relative to the working directory (no other path can be given); a missing or unreadable file is exit 2.
The zero is the operator's (operator ruling 16.10.2026: „0 Findings Toleranz für alle Repos“; SonarCloud belongs in
the minimum profile: „sollte das da nicht mit rein?“).

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey`, `organization=<owner>` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). Anything
unreadable is exit `2`.

### New and legacy findings

`gate.yml` passes the repository's default branch (`github.event.repository.default_branch`) as `DEFAULT_BRANCH`; it is
never assumed to be `main`, and a missing or empty value is exit `2`. "Legacy" means the open findings of that branch,
counted by the same `issues/search` with the same open statuses, and only counted, never listed; the findings the
[Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside are subtracted, one more count per such rule.
- **Pull request:** new findings are listed and make the run exit `1` (except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside). Both counts always print, for example
  `sonar: new 0 · legacy 17 (overnightworks_webauth, main)`.
- **Default branch:** a run on the default branch exits `1` on any open finding (except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside), lists them and prints
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
  (`loosened (undeclared): legacy fail → report`; an absent key counts as `report`), like a removed mandatory job.

On pull requests and default-branch runs `quality sonar findings` reads `api/project_branches/list` once before the counts.
- The default branch has no entry, or one without `analysisDate` (a repository's first adoption pull request): the summary
  prints `legacy unknown (<default> not analysed yet)`. With `"report"` the run exits `0`; with `"fail"` it is exit `2`
  (`sonar: cannot judge - legacy unknown (<default> not analysed yet)`).
- The default branch is not SonarCloud's main branch: exit `2` in both modes, with the fix `set SonarCloud's main branch
  to <default>`. A non-main SonarCloud branch counts only changed code, so the legacy count would be too low.

### A Dependabot exception for `docker:S8431`

Sonar's rule `docker:S8431` ("Use either the version tag or the digest for the image instead of both") flags the
`image:tag@sha256:...` form that Dependabot maintains. Sonar's own sensor drops the finding when a Dependabot config
exists ([DockerSensor.java](https://github.com/SonarSource/sonar-iac/blob/2bb050011ed1a224b74d23182edcc82afca8695b/iac-extensions/docker/src/main/java/org/sonar/iac/docker/plugin/DockerSensor.java#L204-L226),
SONARIAC-3096), but only checks that the file exists, and a pull-request analysis looks at changed files only, so it
cannot see an unchanged `.github/dependabot.yml` and keeps the finding. The gate therefore judges a pull request the
way Sonar judges main:

- **The rule:** when `.github/dependabot.yml` exists in the checkout, `docker:S8431` does not count. Its content is
  never read, so an empty or broken file counts as existing; a link to a file counts and a link to nowhere does not; a directory named `.github/dependabot.yml` counts too, unlike
  Sonar, which needs a file.
  `.github/dependabot.yaml` is not looked at. The rule applies in every run kind.
- **What prints:** the finding still prints, on stdout after the counted findings and before the summary, without a
  `Fix:` line: `sonar Dockerfile:3 - docker:S8431: <message> (does not count: .github/dependabot.yml exists, Sonar's
  own exception, which its pull-request analysis misses)`. The summary line does not count it, and the legacy number
  leaves it out too: the default branch's open total less that rule's open count on the default branch. A rule count
  above the total is exit `2`.
- **An unreachable file:** a `.github` the filesystem refuses access to is exit `2` with a line naming the file, before
  any request.
- **Only this rule** has the exception; every other finding and rule counts as before.
- **Gaps:** `quality emergency-merge` does not honour it and still counts the finding as added; the file's existence does not say that Dependabot maintains the Dockerfile's folder for Docker; a repository
  that has only `.github/dependabot.yaml` stays red on pull requests; Renovate is not judged.

(operator rulings 21.10.2026 and 23.10.2026: Sonar's exception applies on pull requests too, „Sonars Ausnahme anwenden (empfohlen)“, and
holds when the file exists, „Wenn dependabot.yml existiert (empfohlen)“; the rejected per-line entries stand in
[decision 2](decisions/2-dependabot-exception.md); 23.10.2026, „ja A“, relayed by edge's head: the legacy count sets
aside the same findings, re-ruling E3 of #98.)
