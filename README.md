# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality sonar preflight [PATH]
    quality sonar findings --task-file PATH

`hygiene` reads git's view of the repository at `PATH` (default `.`) and applies two rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
- `compose-port`: every published port in a tracked `compose*.y{a,}ml` or `docker-compose*.y{a,}ml` binds `127.0.0.1`.

With `--engines DIR` the `secret` rule also scans the whole history with gitleaks, always with quality's own config
(`src/quality/gitleaks.toml`): the repository's `.gitleaks.toml`, `.gitleaksignore` and `gitleaks:allow`
comments have no effect. A known false positive is allowed only by its gitleaks fingerprint
(`commit:file:rule:line`) and a reason in the repository's `quality/config.toml`:

    [secrets]
    allow = [{ fingerprint = "<commit>:src/x.py:generic-api-key:139", reason = "enum value, not a key" }]

Every allowed finding is printed (`secret: 1 allowed (src/x.py:139)`). The same key in a new commit has a new
fingerprint and is red until it is added. An entry without a reason is exit `2`.

Each finding names the rule, the file, a reason and a fix. Exit `0` is clean, `1` means findings,
`2` means the repository could not be judged (never clean).

## `quality sonar findings`

`gate.yml` runs SonarCloud's scanner in its `sonar` job and judges it through `verdict`; callers use `gate.yml`, never
an action, because quality publishes none. A caller passes the Actions secret by name
(`secrets: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}`), has automatic analysis off in SonarCloud, and commits a root
`sonar-project.properties`. A missing or empty token is red (`sonar: cannot judge - SONAR_TOKEN is empty`), never
skipped; Dependabot runs resolve the Dependabot secret of the same name.

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

`quality sonar findings --task-file report-task.txt` is the SonarCloud zero-findings rule.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey`, `organization=<owner>` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). On
`pull_request` any open finding is exit `1`; on any other event the count is reported and the exit is `0` until the
baseline exists. Anything unreadable is exit `2`.
