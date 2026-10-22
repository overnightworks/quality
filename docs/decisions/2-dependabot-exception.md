# Decision 2: Sonar's Dependabot exception applies on pull requests, once per rule

Status: accepted, 21.10.2026. Reader: an engineer who sees `docker:S8431` red on a pull request, or wants a per-image
exception.

## Context

Sonar's rule `docker:S8431` flags the `image:tag@sha256:...` form that Dependabot maintains. Sonar's own sensor drops the
finding when `.github/dependabot.yml` exists, but a pull-request analysis looks at changed files only, so it cannot see an
unchanged file and keeps the finding. On the default branch the finding is gone, on every pull request it is red.

## Decision

The gate applies Sonar's own exception on pull requests too, for this one rule: when `.github/dependabot.yml` exists in
the checkout, `docker:S8431` does not count. The file's content is never read. The behaviour and its gaps stand in
[sonar.md](../sonar.md#a-dependabot-exception-for-dockers8431).

## Rejected alternatives

The reasons are the record's reconstruction from #98 and its plan review, not the operator's words.

- **One `quality/config.toml` entry per exact `FROM` line.** The operator chose this on 20.10.2026 („Eng begrenzte
  Ausnahme (empfohlen)“) on a premise the plan review corrected (Sonar applies its exception on the default branch
  only); he re-ruled. #98 records the re-ruled answer as: "Dependabot updates stay green, and a pull request is judged
  the way main is" (the item's text, not a quote of his).
- **Reading `dependabot.yml` to see that it covers the Dockerfile's folder.** Sonar itself checks existence only; the
  gate aligns a pull request with how Sonar judges the default branch; the differences stand in
  [sonar.md](../sonar.md#a-dependabot-exception-for-dockers8431) (an empty file counts, a directory counts).
- **An exception table or a general mechanism for other rules.** Only this rule has the premise (Sonar's own exception
  that its pull-request analysis misses); every other finding counts as before.

## Consequences

An empty or broken `dependabot.yml` counts as existing, a repository with only `.github/dependabot.yaml` stays red, and
`quality emergency-merge` still counts the finding.

## The operator's words

The two rulings of 21.10.2026 stand in [sonar.md](../sonar.md#a-dependabot-exception-for-dockers8431).
