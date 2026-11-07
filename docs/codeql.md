# The CodeQL workflow

Reference for the engineer who calls the reusable `codeql.yml` workflow. The reasons behind its constraints stand in [ARCHITECTURE](ARCHITECTURE.md), sections 6 and 7.

## `codeql.yml`

Ruled by the operator: a reusable workflow that runs CodeQL for the languages a caller names, one for the fleet instead
of a copy per repository, with `build-mode: none` and CodeQL's default query suite and threat model. It uploads under the
category `/language:<language>`, the Default Setup's category, so alerts carry over when a repository switches.

A private repository needs GitHub Code Security for code scanning (`code-scanning/default-setup` answers 403 without it); quality's fleet does not pay for it, so private repositories run no CodeQL ([ARCHITECTURE](ARCHITECTURE.md)).

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
    uses: overnightworks/quality/.github/workflows/codeql.yml@<commit sha> # v<tag>
    with:
      languages: '["actions", "python"]'
```

- **`languages`:** the one input, required, a string holding a JSON list; `'python'` or `'[]'` fails the run. List
  every category the repository already analyses (aco also has `javascript-typescript`, which a list copied from the example above would drop).
- **Permissions:** the caller grants `security-events: write`, `contents: read` and `actions: read`, and the workflow
  asks for no more. A caller without `security-events: write` is refused at startup ([Startup failures](gate.md#startup-failures)).
- **Updates:** pin a release's commit sha with its tag in a comment; Dependabot's `github-actions` ecosystem bumps both.
- **Caller path:** a reusable call records the `analysis_key` `<caller file>:analyze`, so keep
  the existing caller file's path and the key carries over.
- **Switch PR:** a caller that fails at startup adds no check to its PR, so the switch PR must show its
  `codeql / Analyze (…)` checks.
- **Switching a repository:** the Default Setup goes off first.
