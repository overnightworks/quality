# quality vision

## What quality is

quality is the quality gate of a fleet of repositories. It judges each repository against the fleet's shared rules for
structure, size, duplication, architecture boundaries, secrets, dependency risks and automation, and it judges every
change before it lands, failing closed, so no rule loosens unseen. An exception passes only by the operator's explicit
decision. Every project releases on the same checked path: the check first, then the project's own build, then the
release. Where an established tool already does a job, quality runs it instead of rebuilding it, and it builds only what
no tool does; the scan of markup for literal text, which the fleet's languages standard assigns to it, is one quality owns because no tool the fleet runs does it. Its commands say in plain words what they do, and it asks for little upkeep.

## Who uses it today

- Engineers, who check their repository before they push.
- The operator, who decides the rare exceptions.
- Every repository of the fleet, whose changes and releases it judges.
- overnightworks/marketplace's lane relay, which matches the verdict's closing line
  `only declared loosenings · only the operator merges it` and its `next: quality merge <owner>/<repo>#<n>` line
  word for word, so a change to either is asked of marketplace before it lands.

## Deliberate non-goals

- No specialist scanner of its own, except the literal-text scan the fleet's languages standard assigns to it.
- No installer of its own.
