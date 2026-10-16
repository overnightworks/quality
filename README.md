# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]

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
