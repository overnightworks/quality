# Decision 3: the clone rule uses an own detector, not jscpd or lizard

Status: accepted, 21.10.2026. Reader: an engineer who proposes replacing the detector with a standard tool.

## Context

The clone ratchet needs every pair of near-identical functions to carry an identity that survives unrelated changes, so a
baseline can name it and a rename or deletion orphans it. The standing rule is to build logic and process ourselves and
never the specialist engines (see [commands.md](../commands.md#scanners)); the operator also prefers standard
tools. The choice therefore stood against a measurement.

## Decision

quality takes over the detector of the aco repository (about 150 lines then, a comparison of the functions' name-folded token streams) and judges `*.py` files
with it. A pair is two functions of at least 40 tokens whose structure is at least 90 % the same once names and literals
are renamed, named by path and qualified symbol. The behaviour stands in the `clone` rule of [rules.md](../rules.md).

## Rejected alternatives

The reasons are the record's reconstruction from #113, not the operator's words.

- **jscpd.** It finds real copies, but its identifier for a pair changes when the neighbouring function changes, so a
  baseline entry would be orphaned by an unrelated edit; its function mode leaves tests out. It stays a candidate for
  TypeScript and Svelte files, where no own detector exists.
- **lizard `-Eduplicate`.** Rejected on a measurement: 6 of 6 samples were import lists.

## Consequences

The detector is quality's to maintain, and it judges Python only. The threshold is 40 tokens, not aco's 25; aco's head is
asked when aco drops its script in favour of quality's.

## The operator's words

The ruling of 21.10.2026 stands in the `clone` entry of [rules.md](../rules.md).
