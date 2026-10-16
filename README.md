# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality sonar findings --task-file PATH

`hygiene` reads git's view of the repository at `PATH` (default `.`) and applies two rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
- `compose-port`: every published port in a tracked `compose*.y{a,}ml` or `docker-compose*.y{a,}ml` binds `127.0.0.1`.

With `--engines DIR` the `secret` rule also scans the whole history with gitleaks, always with quality's own config
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

Every allowed finding is printed (`secret: 1 allowed (src/x.py:139)`).

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
