# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
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

The scan runs git without any attributes (not the repository's `.gitattributes`, not `info/attributes`, not the
system or global file), so a `-diff` or `binary` line cannot hide a key. That needs git 2.41 or newer; an older git
is exit `2`.

Every allowed finding is printed (`secret: 1 allowed (src/x.py:139)`). The same key in a new commit has a new
fingerprint and is red until it is added. An entry without a reason is exit `2`.

Each finding names the rule, the file, a reason and a fix. Exit `0` is clean, `1` means findings,
`2` means the repository could not be judged (never clean).

## `quality sonar findings`

`quality sonar findings --task-file report-task.txt` is the SonarCloud zero-findings rule. It is not wired into any
workflow yet (that is Q1a-5b); callers use `gate.yml`, never an action, because quality publishes none.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). On
`pull_request` any open finding is exit `1`; on any other event the count is reported and the exit is `0` until the
baseline exists. Anything unreadable is exit `2`.
