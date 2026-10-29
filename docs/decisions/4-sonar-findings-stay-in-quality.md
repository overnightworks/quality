# Decision 4: SonarCloud findings stay quality's judgement

Status: accepted, 29.10.2026 (operator, #228: option 2 applies when option 1's preconditions fail). Reader: an engineer who
proposes moving the zero-findings rule to SonarCloud's hosted gate.

## Context

[`quality sonar findings`](../sonar.md) counts open findings on a pull request and, with `legacy = "fail"`, on the default
branch. The pre-scan properties check refuses what could steer the token. Moving the zero rule to the hosted gate would
have removed the count, `legacy = "fail"`, the failing default-branch runs, the Dependabot exception, the properties
check, the door's legacy path with the `[gate] legacy` key, and about 670 lines of `sonar.py`. It was ruled on three
preconditions, measured anonymously on 29.10.2026.

## Decision

quality keeps judging the findings; only the word `preflight` leaves, for `quality sonar properties`. The preconditions:

- **The hosted gate fails on new issues.** False: the built-in "Sonar way" and the custom "presentator" gate have no
  condition on `new_violations`.
- **`docker:S8431` is excluded on SonarCloud's side.** False: the rule is active in the built-in Docker profile, and no
  project sets `sonar.issue.ignore.multicriteria`.
- **The token boundary holds at the pinned scanner without the properties check.** False: at sonar-scanner-java-library
  4.1.1.1633 a properties file can still set proxy, truststore, Java executable, engine jar and Java option keys, and
  `${env.SONAR_TOKEN}` substitutions, none of which `-Dsonar.host.url` pins.

## Rejected alternatives

- **Option 1, the hosted gate alone.** It would keep the properties check anyway, which removes most of its gain, and
  SonarCloud backdates an issue raised on an unchanged line, so a `new_violations` condition would not catch it either:
  the finding would stay open with no red check.

## Consequences

The count, the legacy path and the door stay. Reopen when the hosted gate can fail on a finding on an unchanged line, the
Docker rule is excluded in SonarCloud, and the scanner no longer lets a properties file reach the token.
