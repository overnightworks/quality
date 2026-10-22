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

    uv tool install "overnightworks-quality @ git+https://github.com/overnightworks/quality@<tag or commit sha>"

The gitleaks paths (`hygiene --engines`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

## Quick start

A repository adopts the gate by calling the reusable workflow from one job of its own workflow:

```yaml
  quality:
    if: always()
    needs: [checks]
    permissions:
      contents: read
      actions: read
    uses: overnightworks/quality/.github/workflows/gate.yml@<commit sha> # v<tag>
    with:
      coverage-artifact: coverage
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

and names the jobs that must pass in `quality/config.toml`:

```toml
[gate]
mandatory_jobs = ["checks"]
```

Locally, `quality hygiene .` runs the same rules on the checkout.

## Read on

- [docs/rules.md](docs/rules.md): the `hygiene` rules, the secret and advisory scans, the output contract.
- [docs/commands.md](docs/commands.md): `scan-staged`, `scoreboard` and `engines`.
- [docs/gate.md](docs/gate.md): the `gate.yml` caller contract, the verdict, declared loosenings, `codeql.yml`.
- [docs/sonar.md](docs/sonar.md): `sonar findings`, coverage on new code, new and legacy findings, the Dependabot exception.
- [docs/runbook-emergency-merge.md](docs/runbook-emergency-merge.md): the operator's door past a red gate.
- [docs/rulings.md](docs/rulings.md): the operator's rulings, verbatim and dated.
- [AGENTS.md](AGENTS.md): the contract for agents working in this repository.
