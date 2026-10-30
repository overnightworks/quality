# quality

`quality` is the one door for the fleet's quality checks: repository hygiene rules, secret and advisory scans, the
SonarCloud findings, and the fail-closed pull request verdict. It is for the engineer who adds the gate to a
repository and for the operator who merges past it.

    quality hygiene [PATH]
    quality baseline write [PATH]
    quality scan-staged [PATH]
    quality sonar properties
    quality sonar findings
    quality verdict [PATH]
    quality scoreboard [PATH]
    quality emergency-merge OWNER/REPO PR

Exit codes: `0` clean, `1` findings, `2` cannot judge (never clean), `130` interrupted (Ctrl-C).

## Install

    uv tool install --python 3.14 "overnightworks-quality @ git+https://github.com/overnightworks/quality@<tag or commit sha>"

`--python 3.14` matters because `uv tool install --force` keeps an existing tool environment's older interpreter, which then refuses v0.3.0 or later.

The gitleaks paths (`hygiene`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

gitleaks, actionlint and osv-scanner come through [mise](https://mise.jdx.dev/installing-mise.html), from the manifest
and lock inside quality's installation; a scanner that is missing exits `2` and names the command that installs it:

    mise --cd <quality's installation>/quality/mise install --locked

## Quick start

A repository adopts the gate with one caller job of `gate.yml` and a `quality/config.toml` that names the mandatory
jobs; [docs/gate.md](docs/gate.md#gateyml) has the snippet and the reason for each line. The gate's `sonar` job also
needs a SonarCloud project with automatic analysis off, a root `sonar-project.properties` and the `SONAR_TOKEN` secret
(a missing token is red); see [docs/sonar.md](docs/sonar.md#quality-sonar-findings).

Locally, `quality hygiene .` runs every rule the gate runs, the secret, workflow and advisory scans included.

## Read on

- [docs/rules.md](docs/rules.md): the `hygiene` rules, the secret and advisory scans, the output contract.
- [docs/output.md](docs/output.md): the picture of what the commands print; `hygiene` prints its frame today.
- [docs/commands.md](docs/commands.md): `scan-staged`, the scanners, `scoreboard` and `emergency-merge`.
- [docs/gate.md](docs/gate.md): the `gate.yml` caller contract, the verdict, declared loosenings, `codeql.yml`.
- [docs/sonar.md](docs/sonar.md): `sonar findings`, coverage on new code, new and legacy findings, the quality gate, the Dependabot exception.
- [docs/runbook-emergency-merge.md](docs/runbook-emergency-merge.md): the operator's door past a red gate.
- [docs/decisions/](docs/decisions/): the decisions whose rejected alternatives keep coming back.
- [AGENTS.md](AGENTS.md): the contract for agents working in this repository.
