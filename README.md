# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality scan-staged [PATH] --engines DIR
    quality engines install DIR
    quality engines verify DIR
    quality sonar findings --task-file PATH

Install it with

    uv tool install "overnightworks-quality @ git+https://github.com/overnightworks/quality@<commit sha>"

(no tag exists yet, so the line names a commit). It needs git 2.41 or newer; an older git is exit `2`.

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
  `allowed src/x.py:139 - gitleaks rule generic-api-key allowed: enum value, not a key`).

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
`quality/config.toml` (none staged: no allowances). Output and secret handling are `hygiene`'s:
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

`quality sonar findings --task-file report-task.txt` is the SonarCloud zero-findings rule. It is not wired into any
workflow yet (that is Q1a-5b); callers use `gate.yml`, never an action, because quality publishes none.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). On
`pull_request` any open finding is exit `1`; on any other event the count is reported and the exit is `0` until the
baseline exists. Anything unreadable is exit `2`.
