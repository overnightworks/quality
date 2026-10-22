# Runbook: the emergency merge

Runbook for the operator who merges a pull request past the gate with `quality emergency-merge`.

## The operator's emergency door

Two red pull requests have no other way to land. Under `legacy = "fail"`, main can gain a finding without any pull
request introducing it (a taint finding on an unchanged line, a SonarCloud rule-profile update); then every pull request
is red, including the fixing one, and switching `legacy` back is red too. And a pull request that loosens the gate as the
operator ruled stays red for its [declared loosenings](gate.md#declared-loosenings). Both land through the operator's door,
one command (quality#37, quality#103):

```
quality emergency-merge <owner/repo> <pr>
```

- **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository (checked
  first: exit `2`, nothing changed, the missing right named). Agents never run it; it refuses when stdin is not a
  terminal. This is the agent contract, not a lock: agents on the operator's machine share his `gh` login, so to an
  agent the terminal is a hurdle, and that login could switch `quality_gate` directly. A real lock (agents with a token
  without admin rights) belongs to the machine's setup, not to quality; it is a known residual risk.
- **It refuses unless the door is the only way** (exit `1`, nothing changed): no check is still running; every check run
  and commit status other than `quality / sonar` and `quality / verdict` is green; `quality / verdict` has failed; and
  `quality / sonar` is green or has failed (it did not run, or it ended `timed_out`, `cancelled` or anything else: refused).
  The verdict is then judged again by its own decider, with the config and baseline read at the pull request's
  current merge commit and at base and the Sonar job taken as green: an undeclared loosening, a declaration that matches
  nothing, or a missing or red mandatory job is refused, naming the verdict's lines.
- **Sonar green: declared loosenings.** The re-derived verdict must hold at least one declared loosening (otherwise
  `re-run the verdict`). SonarCloud is not asked and `SONAR_TOKEN` is not needed.
- **Sonar failed: legacy findings,** as before, next to any declared loosening: `[gate] legacy` is `fail` at the merge
  commit (under `report` the Sonar job cannot be red on legacy findings, and a missing config reads as `report`),
  SonarCloud's analysis of the pull request is of the head being merged and adds no finding (the findings are named
  otherwise), and the default branch has legacy findings. `SONAR_TOKEN` is read from the environment when the project
  needs one: without it the door reads a public project anonymously, with no Authorization header, and an anonymous read
  that SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN`; with a token, a 401 or 403
  says the token was rejected.
- **It shows, then asks:** repository, pull request and head SHA with the legacy count (where Sonar failed) and the
  number of declared loosenings, then one line per declaration with its `item`, `date`, `what` and `reason`, then the
  typed word `ja`. Anything else changes nothing. Every line it prints is escaped as the other doors' lines are, so a
  `reason` cannot hide anything before `ja` or forge a line.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this removes ruleset 25227067 for it),
  runs `gh pr merge <pr> --merge --match-head-commit <sha>` (`--squash` where the repository settings or a rule on the
  pull request's base branch exclude a merge commit but allow a squash; when neither is allowed, or the settings or rules
  cannot be read or are malformed (an answer or a page that is no array, a rule that is no object, or whose `parameters` are present but no object), it is refused before the gate is touched; the shown text and the log comment name the
  method), and sets the property back to `true` in every case: merged,
  merge failed, Ctrl-C. A property that cannot be restored is exit `2` with one line naming what to set by hand and the
  merge outcome as far as it is known, printed before any further GitHub request; a re-read that settles an open
  outcome prints that line again with it. After Ctrl-C the next line starts on a line of its own. A merge whose outcome
  GitHub does not confirm (Ctrl-C, a lost answer) is re-read from the pull request; if that does not settle it, exit
  `2` with `merge state unknown`.
- **Logged:** one comment on the pull request (who, when, the legacy count and each declaration,
  `quality_gate off → <outcome> → on`, or `quality_gate not switched off → no merge → on` when switching off failed),
  also when the restore failed. The command prints `aco release <n> --abandoned "landed as PR #<pr> by emergency-merge"`
  for the head, because `aco land` refuses any non-green check and `--merged` refuses a merge commit without a
  `Work-Item:` trailer.
