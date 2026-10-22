# Runbook: a pull request the gate keeps red and only the operator may merge

Runbook for the operator. What the command does and refuses is in [commands.md](commands.md#quality-emergency-merge).

Symptom: `quality / verdict` is red on a pull request and no change to the pull request can turn it green. Two cases:

- every pull request is red, the fixing one included, because the default branch gained a legacy Sonar finding without a
  pull request introducing it (`[gate] legacy = "fail"`; a taint finding on an unchanged line, a SonarCloud rule-profile
  update);
- the pull request loosens the gate as you ruled, and the verdict's only red lines are `loosened (declared, …)` ending in
  `verdict: red - only declared loosenings`.

Impact: the pull request cannot merge through `aco land` or the ruleset.

## Check

1. `gh pr checks <pr> --repo <owner/repo>`: every check other than `quality / sonar` and `quality / verdict` is green and
   none is running.
2. Read the verdict's lines in the failed `quality / verdict` job. In the second case each declared loosening is the one
   you ruled; if it is not, send the pull request back instead.

## Fix

1. In a terminal, under your own `gh` login: `quality emergency-merge <owner/repo> <pr>`.
2. Read what it shows (head SHA, legacy count, each declared loosening with its item, date and reason) and type `ja`.
   Anything else changes nothing.
3. Release the claim as the command prints it (`aco release <n> --abandoned "landed as PR #<pr> by emergency-merge"`).

Rollback: the command switches the repository's `quality_gate` property off for the merge and back on in every case. To
undo the merge, revert it with a new pull request. If the property could not be restored the command exits `2` and
names what to set by hand; set it back to `true` at once, since the repository is ungated until then.

## Escalate

- The command refuses (exit `1`, nothing changed) and names a reason that is not yours to remove, for example a verdict
  that is red for an undeclared loosening: the pull request is not a case for this door; fix the pull request.
- The command exits `2`, or `merge state unknown`: read the pull request on GitHub before running it again.
- A refusal you think is wrong: open an item in overnightworks/quality with the command's output. Who answers it beyond
  that is not recorded.
