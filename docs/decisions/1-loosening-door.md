# Decision 1: a loosening of the gate merges only through the operator's door

Status: accepted, 21.10.2026. Reader: an engineer or agent who wants the gate to let a loosening through by itself.

## Context

The verdict holds a pull request's `quality/config.toml` and `quality/baseline.toml` against the base commit's, so a
pull request cannot loosen the gate unnoticed. Two cases still need a way to land. A pull request may loosen the gate as
the operator decided (removing a mandatory job, for example). And a repository may hold an advisory it cannot fix (nested
provider locks whose upstream pins block the fix). In both the verdict is red and no change to the pull request turns it
green.

## Decision

A pull request declares each loosening in `quality/config.toml` with its item, date and reason; an advisory allowance is
its own declaration. The verdict stays red for it, labelled declared, and only the operator's
[`quality emergency-merge`](../commands.md#quality-emergency-merge) merges such a pull request. A later `until` on an
existing allowance is a loosening like a new one. The behaviour stands in [gate.md](../gate.md#declared-loosenings) and
the `advisory` rule of [rules.md](../rules.md).

## Rejected alternatives

- **The pull request approves its own loosening** (a declaration that turns the verdict green): an agent could write the
  declaration and the loosening in one pull request, so the gate would hold only against cooperative agents. The
  declaration is read by the verdict, but the red and the door are the gate.
- **Moving an allowance's re-check date without the operator**: the date is what brings the advisory back to red, so
  letting a pull request push it back removes the re-check. Only the door may.
- **A separate `[[gate.loosened]]` entry for each allowance**: the allowance already carries reason, item and date, so a
  second entry would be a second copy of one declaration.
- **A real lock** (agents holding a token without admin rights): it belongs to the machine's setup, not to quality. Agents
  on the operator's machine share his `gh` login, so the door is the agreed path and not a lock; the operator accepted
  that as a known residual risk.

The other options offered to the operator with these questions are not recorded.

## Consequences

A loosening costs the operator one terminal command and a typed `ja`, and no agent lands one alone. The residual risk
above stays until the machine's setup removes the shared login.

## The operator's words

Questions and answers of 21.10.2026: „Erklären + deine Tür (empfohlen)“; „Ja, nicht mehr prüfen (empfohlen)“ (a declared
removal replaces judging that base job); „Ja, beides in einem Lauf (empfohlen)“ (legacy findings and a declared loosening
in one run); „Bekanntes Restrisiko (empfohlen)“; for advisories „Ja, aber nur du gibst frei (empfohlen)“, „Nein, nur über
deine Tür (empfohlen)“ and „Ausnahme erklärt sich selbst (empfohlen)“. The ruled sentences for advisories: a new
allowance is a loosening, the pull request stays red until the door merges it; from the merge the advisory is green until
its date and red after; no agent can silence an advisory alone.
