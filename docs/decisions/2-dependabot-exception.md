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

- **One `quality/config.toml` entry per exact `FROM` line.** The operator chose this on 20.10.2026 („Eng begrenzte
  Ausnahme (empfohlen)“) while believing the exception was Sonar's to apply on pull requests. The plan review found that
  Sonar applies it on the default branch only, so a per-line entry would copy by hand what Sonar already does there; the
  operator re-ruled.
- **Reading `dependabot.yml` to see that it covers the Dockerfile's folder.** Sonar itself checks existence only; the
  gate judges a pull request the way Sonar judges the default branch, so the two cannot disagree.
- **An exception table or a general mechanism for other rules.** Only this rule has the premise (Sonar's own exception
  that its pull-request analysis misses); every other finding counts as before.

## Consequences

An empty or broken `dependabot.yml` counts as existing, a repository with only `.github/dependabot.yaml` stays red, and
`quality emergency-merge` still counts the finding.

## The operator's words

21.10.2026, asked how the gate should treat Sonar's exception: „Sonars Ausnahme anwenden (empfohlen)“. Asked when the gate
should not count S8431: „Wenn dependabot.yml existiert (empfohlen)“.
