# Runbook: a pull request the gate keeps red and only the operator's consent merges

Runbook for the operator and for the agent that runs the door. What the command does and refuses is in [commands.md](../commands.md#quality-merge).

Symptom: `quality / verdict` is red on a pull request and no change to the pull request can turn it green. Two cases:

- every pull request is red, the fixing one included, because the default branch gained a legacy Sonar finding without a
  pull request introducing it (`[gate] legacy = "fail"`; a taint finding on an unchanged line, a SonarCloud rule-profile
  update);
- the pull request loosens the gate as you ruled, and the verdict's only red row is `! loosenings  N declared`, its
  closing line `! red · only declared loosenings · only the operator merges it` and `next:` names this command.

Impact: the pull request cannot merge through `aco land` or the ruleset.

## Check

1. `gh pr checks <pr> --repo <owner/repo>`: every check other than `quality / sonar` and `quality / verdict` is green and
   none is running.
2. Read the verdict's rows in the failed `quality / verdict` job. In the second case each declared loosening is the one
   you ruled; if it is not, send the pull request back instead.
3. The pull request is no draft; the command refuses a draft.
4. The pull request body carries its one `Work-Item:` or `No-Item:` line outside a code block; the command refuses otherwise.
5. The branch holds `<base>`'s tip (`gh pr update-branch <pr> --repo <owner/repo>`, then wait for the checks); the command refuses a head behind it.

## Fix

1. Run `quality merge <owner/repo>#<pr> --dry-run`: it reads every row, changes nothing and, where no consent stands yet,
   prints the consent text below `! consent  missing`.
2. Read the rows (first the release that judges, the one the target's gate pins at the merge commit; the first run of a
   release prepares it before the rows, online; then the pull request and its head, whether the head holds the base's tip,
   the checks, each declared loosening with its item, date and reason, the legacy count, the method, and last the
   `landing` row: the work item or the issue-less kind the merge commit ends with).
3. To consent, submit the fenced text as your own review on the pull request, a comment or an approval, under your login
   as an organisation owner: the text exactly as printed, nothing before or after it, at the head the door names. A push
   changes the head and the consent is void; the door prints the new text.
4. Run `quality merge <owner/repo>#<pr>` again. It merges only where your latest review at the head carries the text.
   Another reviewer's review, an earlier head's review and an edited text are no consent. One pull request per run; name
   the next after it.

Rollback: the command switches the repository's `quality_gate` property off for the merge and back on in every case. To
undo the merge, revert it with a new pull request. If the property could not be restored the command exits `2` with
`error: quality_gate MAY STILL BE OFF on <owner/repo>` and the command that switches it on alone below `next:`; run it at
once, since the repository is ungated until then.

## Escalate

- The command refuses (exit `1`, nothing changed) and names a reason that is not yours to remove, for example a verdict
  that is red for an undeclared loosening: the pull request is not a case for this door; fix the pull request.
- The command exits `2`: do what `next:` names; where it says `may be merged`, read the pull request on GitHub before
  running the door again.
- A refusal you think is wrong: open an idea in overnightworks/quality with the command's output as evidence; quality's
  head answers it.
