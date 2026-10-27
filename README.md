# quality

`quality` is the one door for the fleet's quality checks: repository hygiene rules, secret and advisory scans, the
SonarCloud findings, and the fail-closed pull request verdict. It is for the engineer who adds the gate to a
repository and for the operator who merges past it.

    quality hygiene [PATH]
    quality baseline write [PATH]
    quality scan-staged [PATH] --engines DIR
    quality engines install DIR
    quality engines verify DIR
    quality sonar preflight [PATH]
    quality sonar findings
    quality verdict [PATH]
    quality scoreboard [PATH]
    quality emergency-merge OWNER/REPO PR

Exit codes: `0` clean, `1` findings, `2` cannot judge (never clean).

## Install

    uv tool install --python 3.14 "overnightworks-quality @ git+https://github.com/overnightworks/quality@<tag or commit sha>"

`--python 3.14` matters because `uv tool install --force` keeps an existing tool environment's older interpreter, which then refuses v0.3.0 or later.

The gitleaks paths (`hygiene --engines`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

## Quick start

A repository adopts the gate with one caller job of `gate.yml` and a `quality/config.toml` that names the mandatory
jobs; [docs/gate.md](docs/gate.md#gateyml) has the snippet and the reason for each line. The gate's `sonar` job also
needs a SonarCloud project with automatic analysis off, a root `sonar-project.properties` and the `SONAR_TOKEN` secret
(a missing token is red); see [docs/sonar.md](docs/sonar.md#quality-sonar-findings).

Locally, `quality hygiene .` runs the hygiene rules; `--engines DIR` (after `quality engines install DIR`) adds the
secret, workflow and advisory scans the gate runs.

## Read on

- [docs/rules.md](docs/rules.md): the `hygiene` rules, the secret and advisory scans, the output contract.
- [docs/commands.md](docs/commands.md): `scan-staged`, `scoreboard`, `engines` and `emergency-merge`.
- [docs/gate.md](docs/gate.md): the `gate.yml` caller contract, the verdict, declared loosenings, `codeql.yml`.
- [docs/sonar.md](docs/sonar.md): `sonar findings`, coverage on new code, new and legacy findings, the quality gate, the Dependabot exception.
- [docs/runbook-emergency-merge.md](docs/runbook-emergency-merge.md): the operator's door past a red gate.
- [docs/decisions/](docs/decisions/): the decisions whose rejected alternatives keep coming back.
- [AGENTS.md](AGENTS.md): the contract for agents working in this repository.
