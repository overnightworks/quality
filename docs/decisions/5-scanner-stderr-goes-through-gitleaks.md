# Decision 5: A scanner's stderr goes through gitleaks before a fact quotes it

Status: accepted, 2.11.2026. Reader: an engineer who sees `· N stderr lines withheld · not cleared by gitleaks` in a
`✗` row, or adds a scanner whose stderr a refusal would quote.

## Context

Five facts quoted a scanner's stderr, the last five lines: every scanner's failed version call, actionlint's and
zizmor's refusal, osv-scanner's, ruff's, and ruff's `Failed to lint` line. zizmor's schema refusal and osv-scanner's
file error quote what they read, so a key in a workflow or a lock file could stand in a `✗` row, in a terminal and in a
CI log, on a run without `-v`. The rule is that a secret's value never appears.

## Decision

Before a fact quotes any of a scanner's stderr, the whole text goes through `gitleaks stdin` under quality's own
`gitleaks.toml`, the definition of a secret that `quality check` judges with. Only a clean answer clears the text: exit
0 and an empty report. A flagged text and a vet that cannot answer (gitleaks missing, a timeout, any other exit, a
missing or unreadable report, a report that disagrees with the exit) withhold it whole, never partly, and the fact says
how many lines: `· N stderr lines withheld · not cleared by gitleaks`. The exit and the verdict never move. A run whose
scanners refuse nothing, or write no stderr, starts no vet. No refusal quotes gitleaks' own stderr, because the vet
cannot vet the tool that failed.

How the vet calls gitleaks, so it judges as the secret scan judges:

- `--ignore-gitleaks-allow` and the scan's own config and exit code, from one list that feeds both calls: a key beside
  `gitleaks:allow` is a finding.
- gitleaks runs in the vet's scratch directory: with no source it reads `.gitleaksignore` from the folder it stands in,
  whatever `--gitleaks-ignore-path` names, and a repository could list the fingerprint of a stdin finding.
- The text opens with a fixed line: gitleaks answers clean for a text whose first bytes read as a binary type.
- The vetted text is the stderr as written, then with each terminal control code (CSI sequence) replaced by a space, then
  with each removed, in one stdin (the printed text keeps them): a code against a value takes away the word boundary
  gitleaks' rules need, and deleting it alone would join words or eat a key's first letter.
- `--redact`: the report holds no value, and it lives in a 0700 directory removed on every exit.

## What passes

Quality's definition of a secret is what its secret scan recognises in the text it is shown. So these pass the vet:

1. A value gitleaks does not recognise, which `quality check` does not recognise either.
2. A value gitleaks recognises only by its context (`generic-api-key` needs its `api_key = `), when a scanner quotes the
   bare value.
3. A value a scanner cut or spliced, such as a key across two lines of which no line is a key.
4. A binary type's mark at a fixed offset behind the first line (`ustar` at byte 257), which makes gitleaks skip the
   text. It needs a text built for it.

## Rejected alternatives

- **A closed list of known lines per scanner.** Keeps known lines and withholds exactly the text a person wants, but
  needs five lists that decay at each scanner bump.
- **Fixed refusal facts with no stderr.** Every person loses the reason, for a key no run may hold.
- **Showing the text and naming the risk.** Forbidden by the rule that a secret's value never appears.
- **A search for the values the history scan found.** Keeps values in memory across areas and misses a transformed echo.
- **A canary key on line 1 that the report must name.** It proves the scan read the text and closes every offset, but it
  inverts the clean answer, puts a key-shaped literal in production and makes every fake answer it.
- **A vet of the printed tail only.** A value above the tail, or a key across lines, passes.

## Consequences

A refusal costs two gitleaks starts, the version check and the scan of the text, when its scanner wrote stderr. Report text in rows is not stderr and
is not vetted here.
