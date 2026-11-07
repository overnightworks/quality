# Output

Reference for the engineer, operator or agent who reads or matches what a quality command prints, and for the test that proves it. What each command does stands in [commands](commands.md), `check`'s rules in [rules](rules.md), the verdict in [gate](gate.md), the two sonar steps in [sonar](sonar.md). Paths, repositories and ids in the examples are invented. All terminal lines are English.

One output owner prints; rules return findings and never print. A line the door prints starts with its own text, so a matcher anchored at the line start, such as setup-uv's python matcher, cannot match repository text.

## Rows

Ruled by the operator:

- Every row reads `glyph · subject · event · fact`. A finding sits indented under its area row with `path:line` right after the glyph; one `fix:` line follows the last of consecutive findings that share the fix.
- Findings under their area with a `fix:` line: indented under the area row, columns aligned per area; `fix:` under the last of consecutive findings with the same fix. A finding without a line shows only the path.

The columns are the glyph (state at a glance), the subject (the area, or `path:line` of the finding), the event (one typed word: what is the case), the fact (values, a few words) and then `fix:` (what you do). A finding is `! <path[:line]>  <event>  <fact>`. Path and event align per area by display width, in every stream: an East Asian wide character takes two cells and a combining mark none; spacing marks, Hangul conjoining letters, skin-tone modifiers and emoji sequences are not measured. The findings of an area stand in run order (O14). A finding's lines from another tool's rendering (import-linter's broken contract chain and guidance, actionlint's message) stand under its row behind the marker `| `, six spaces deep, before the run's `fix:`; a fix that asks for an entry ends with `:` and the entry's lines stand under it, two spaces deeper, whole and never shortened. A security finding's fix names the line its `# noqa` must stand on, and a code and ruff's message are the fact.

Areas run in the order `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies`, `code`. The first row of a run names the release that judges ([commands](commands.md#which-release-judges)).

```text
  · release       v0.10.0        pinned in .github/workflows/ci.yml
  ! files         3 findings
    ! .env          tracked         an env file carries configuration, often secrets
      fix: git --literal-pathspecs rm --cached -- .env, then add .env to .gitignore
    ! compose.yaml  published port  service web: 8080:80 beyond loopback
    ! compose.yaml  published port  service db: 5432 beyond loopback
      fix: bind it to loopback, for example "127.0.0.1:HOST:CONTAINER"
  ! debt          1 finding
    ! src/shop/parse.py:88  grew  function read_rows has 71 lines · ceiling 64
      fix: bring it back to 64 lines or below
  ✓ imports       clean          1 contract
  ✓ secrets       clean          412 commits
  ✓ workflows     clean          5 workflows · offline audits only
  ! dependencies  1 finding
    ! uv.lock  affected  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6
      fix: uv lock --upgrade-package jinja2
  ✓ code          clean          2 rules · 41 Python files
! 5 findings in 3 areas · 4 areas clean · 14s
```

One event word stands per kind of finding, shared across rules (`report.FindingEvent`). The events, facts and fixes of the rows are O12, O14, O15, O16, O18 and O19 for `files`, O21 and O22 for `debt`, O23 and O25 for `imports`, O28, O29, O32 and O33 for `secrets`, O36, O37, O38 and O39 for `workflows` and O41, O42, O43, O44, O45 and O46 for `dependencies`. The env fix names the file for `.gitignore` only where it is its own shell word, else `it`.

### Rule names

Ruled by the operator:

- Rule names only in an allowance fix: a `fix:` line that asks for an `[[….allow]]` entry names the rule, because the entry needs it.
- A config refusal's fact may name the values a config key takes, rule ids included, and the example to copy may hold the entry's rule line; this is the one exception to the rule that no rule name stands outside an allowance's `fix:`.

No rule name stands in a row (`compose-port`, `env-file`); the event says it in words. The areas replace the rule names in the verdict, and `quality check --help` says which rules an area holds.

### Area rows

Ruled by the operator:

- Clean areas: one row per area, so you see what was checked and its extent.

An area row is `✓ <area>  clean  <extent>`, also when nothing was found, and `! <area>  N findings` with findings:

| Area | Extent |
|---|---|
| `files` | `N rules · M files`, M the distinct paths git tracks |
| `debt` | `ceiling held · N entries, M clone pairs`, what the ceiling file holds |
| `imports` | `N contracts` |
| `secrets` | `N commits` (O30), because gitleaks reads committed history only; `staged change · N files` for a staged run |
| `workflows` | `N workflows · offline audits only`, the workflow files the tree holds, said in the row and in no note of its own |
| `dependencies` | `N lockfiles · M packages`, `0 lockfiles · 0 packages` with none (O48) |
| `code` | `2 rules · N Python files`, the tracked regular `.py` files ruff judged, `0 Python files` with none |

With ` · K allowed` appended where files were allowed, leaks were allowed (secrets), advisories were allowed (dependencies) or output owners were honoured (code). Without a ceiling file the debt row is `· debt  not recorded  N entries over the thresholds · measured only` (`M clone pairs · partly measured only` when the ceiling file has no `[clones]` table, O21), with `fix: quality check --fix` under it. Under `quality check debt --fix` it is `✓ debt  lowered  N entries · quality/baseline.toml` with a `✓` row per lowered or dropped entry, `✓ debt  recorded  N entries, M clone pairs · quality/baseline.toml is new`, `! debt  N findings  nothing lowered` or `✗ debt  not fixed  <reason>`; under `--dry-run` it is `~ debt  N to lower` (`N to move`, `to record`) with a `~` row per entry. Without an import-linter config the imports row is `· imports  not set up  no import-linter config`. An area that cannot check is `✗ <area>  not checked  <reason>` ([Refusals](#refusals)).

### Notes

A note is a `·` row under its area, after the area's findings, aligned with them: `· <path[:line]>  <event>  <fact>`. Its events are `allowed` (file rules, dependencies, secrets and python-print), `not measured` (a Python file the size rule cannot parse, without a ceiling file), `unused` and `warning` (import-contract) and `unused` (secret). An allowed advisory and an honoured output owner stand as notes, and the clean row counts them (O15, O49):

```text
  ✓ dependencies  clean          1 lockfile · 63 packages · 1 allowed
    · uv.lock  allowed  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6 · until 2027-01-31 (#412) · no template input
  ✓ code          clean          2 rules · 41 Python files · 1 allowed
    · src/shop/cli.py:3  allowed  output owner · the command line owns its output
```

## Glyphs

Ruled by the operator:

- `✗` means "could not check or do": a finding is always `!` with exit 1; `✗` is always exit 2.

| Glyph | Meaning |
|---|---|
| `✓` | clean, or done |
| `!` | needs you: a finding, a red verdict |
| `✗` | could not check or do; always exit 2 |
| `·` | note: release, allowed finding, not measured |
| `~` | will change: the dry run of `--fix` and `merge` |
| `⠋` | live: no glyph column of its own, terminal only, stderr, disappears |

## The closing line

Ruled by the operator:

- Every run ends with exactly one closing verdict line, or with an `error:`/`next:` pair when it could not answer.
- `next:` after a red verdict: a `!` verdict may carry a `next:` only when exactly one command is the answer. For `check` findings the `fix:` lines carry the way.
- Exit 2 beats 1: exit 2 with the pair; the findings of the other areas stand anyway.
- Duration in the verdict (`12s`, `1m12s`, `<1s`).

Exit `0` or `1` ends with one verdict line on stdout: `✓ clean · 7 areas · <1s` or `! 5 findings in 3 areas · 4 areas clean · 14s`. Before the duration stands `debt measured, not enforced` (`partly measured, not enforced`) when no ceiling file holds the debt, then `imports not enforced` when the repository has no import-linter config. No `next:` follows a findings verdict.

Exit `2` ends instead with `error: K of N areas could not check` (`1 of 1 area` for one), N the areas judged, followed by ` · N findings in the others` or, when findings stand under a `✗` row, ` · N findings, M of them in those areas`, and `next:` on stderr; it prints no verdict line. A rule that cannot judge refuses only itself: the other rules' findings, those of its own area included, still print, and a note stands under a `✗` row where its rule judged.

A run that cannot start prints only the pair and exits `2` before any rule runs, and no release row stands: `error: not inside a git work tree · <the folder, ~ for the home folder>` with `next: run quality check inside a repository` when git, asked in the C locale, says the folder lies outside any work tree (a folder outside any repository, a bare repository, the inside of `.git`), and otherwise (a folder without permission, a repository git refuses) `error: . is not a readable git repository: ...` with `next: git -C . status`, and with `next: fix what the error names, then run quality check again` where git did not run or the folder is gone.

## Refusals

A refused area is its `✗` row and a pair on stderr, exit 2; the other areas' rows stand:

```text
  ✗ secrets       not checked    mise is not installed
  ! code          1 finding
    ! src/shop/a.py:1  print call  T201 `print` found
      fix: write through the program's one output owner; in that owner's file, declare it on a line of its own with `# ruff: noqa: T201 - <reason>`, a reason with a letter and no comma
error: 1 of 2 areas could not check · 1 finding in the others
next:
    install mise: https://mise.jdx.dev/installing-mise.html
    mise --cd ~/.local/share/uv/tools/overnightworks-quality/lib/python3.14/site-packages/quality/mise install --locked
```

A fact of a few words says what is missing (`mise could not locate gitleaks 8.30.1`, true for every non-zero answer of mise). A reason that several rules of one area share stands once; distinct reasons make the row say `N reasons` and stand on `✗` lines below it, and a reason counts once. Findings of an area that also cannot check stay under its `✗` row and are counted in the error line.

`next:` names each distinct step of the run's refusals once, in row order: one step on the `next:` line, several each below a bare `next:` (a command alone on its line, one level of four spaces deeper per level). The general instruction `fix what the ✗ rows name, then run quality check again` is the last line when a refusal carries no step, and the only one when none does. Alternative commands stand under a label ending `, one of:`, each choice's label on a line and its command one level deeper:

```text
  ✗ workflows     not checked    quality's installation holds no zizmor
error: 1 of 7 areas could not check
next: reinstall quality with its dependencies, one of:
    in quality's checkout:
        uv sync --locked --reinstall
    for a tool install:
        uv tool upgrade --reinstall overnightworks-quality
```

```text
  ✗ files         not checked    2 reasons
    ✗ cannot read compose.yaml: line 1, column 11 ...
    ✗ pyproject.toml has a requires-python quality cannot read
    ! .env  tracked  an env file carries configuration, often secrets
      fix: git --literal-pathspecs rm --cached -- .env, then add .env to .gitignore
error: 1 of 7 areas could not check · 1 finding, 1 of them in those areas
next: fix what the ✗ rows name, then run quality check again
```

A config refusal names its fact on each `✗` row it reaches and shows the example to copy once under `next:` (O10): the label `write it in quality/config.toml like this:` and the example's lines one level deeper, each whole on its own line and never shortened, so the lines copied as printed are valid TOML. The fact names an entry by its table and 1-based position (`[[size.generated]] entry 1 needs …`) and quotes a repeated value as a TOML string, never as a Python repr (O11), so the printed value, copied into a TOML file, reads back as the value.

```text
  ✗ files         not checked    quality/config.toml [[size.generated]] entry 1 needs exactly a path, a job, an item, a date and a reason
  ✗ debt          not checked    quality/config.toml [[size.generated]] entry 1 needs exactly a path, a job, an item, a date and a reason
error: 2 of 2 areas could not check
next: write it in quality/config.toml like this:
    [[size.generated]]
    path = "frontend/src/lib/api/types.ts"
    job = "Backend (Python 3.14)"
    item = "overnightworks/songmaker#1784"
    date = 2026-10-30
    reason = "written by scripts/generate_types.py, proven by its --check"
```

## Streams

Ruled by the operator:

- stdout carries rows and the verdict. stderr carries the live line, the input prompt and `error:`/`next:`. So `quality check | less` shows every finding and an error stays visible.
- `report`: rows and verdict on stderr; the tables are an artifact on stdout, so `quality report > fleet.md` writes only the tables.

The lines come in run order, also through a pipe that merges stdout and stderr (`2>&1 | tee log`); each stays on its own stream. `output.set_up_streams`, which `cli.run` calls, makes stdout line-buffered. A run judges the areas in the rows' order and prints each area's lines as its last rule ends, in a pipe as at a terminal, so they stand while the next area runs. After Ctrl-C or SIGTERM during a scan the finished areas' lines stay, with neither verdict nor pair. A reader that left (`| head`, a closed `less`) ends nothing: the run is judged to the end, that stream prints nothing more, and the exit is the run's own. A stream closed before the run (`2>&-`) takes nothing; its lines never move to the other stream.

## Colour and the live line

Ruled by the operator:

- With no TTY, with `NO_COLOR` or with `TERM=dumb` the same words print without colour, without the live line and never shortened.
- The CI log stays without colour: no special rule beyond "no TTY, no colour".

On a terminal the glyphs, the verdict and the `error:`/`next:` labels are coloured with the 16 base colours and facts and notes are dim, each stream on its own; `~` is cyan. `NO_COLOR` counts only when set and not empty. A pipe, a file (a runner step's log is two pipes), the CI log, `NO_COLOR` and `TERM=dumb` print the same words plain and whole.

While a scanner runs, a stderr that is a terminal with a known window shows one dim line per scanner, the area and the scanner, leaving the window's last column free, and erases it before the next line prints, so the area's row takes its place. Ctrl-C, a refusal and an exception erase it too, and so does SIGTERM while gitleaks or osv-scanner runs; a SIGTERM during any other scanner ends quality on the spot and leaves the line standing. A window of under three columns and a terminal that draws `…` two cells wide are not served: there the line may wrap. A pty a step opens for itself is a terminal and shows it.

```text
  ⠋ secrets       gitleaks
```

While quality fetches or prepares the release that judges, the same line counts the seconds, `⠋ release  fetching  <tag> · <seconds>s` (`syncing` for quality's own checkout).

## Length

Ruled by the operator:

- Length of a fact: at the terminal shortened to the window width with `…`, never in a pipe, file or CI log. Commands, sha256 and entries to copy stand on their own line, never shortened. Each kind of finding has a fixed event word and a fact of a few words.

At a terminal the free text of a fact ends at the window with `…`: the reason on a `✗` row or line, and the fact of a finding or a note whose event is free text (a tracked file, a layout reason, a tool's message; `output.py` lists them, and a new event stands whole until it is listed). A count, a measurement or an outcome is never cut (`N findings`, `ceiling 64`, `none allows it`, `value not shown`), nor are the lines under a finding, `fix:`, lines to copy, the verdict, `error:` and `next:`; a long line wraps. The cut is at the end, never inside an escape `printable` creates; in a window at or below the text before the fact it is `…`. The window is what the stream's own terminal answers, read once at the start; `COLUMNS` is not read, and an unknown window shortens nothing. At 60 columns:

```text
    ! .env          tracked         an env file carries con…
```

A sha256 stands whole on its own line, because you copy it into the entry. The entry is the four lines a reader takes, whole and never shortened; the person adds `reason` (O31). It holds no `sha256` key, a key no reader takes.

## Masking

Ruled by the operator:

- Repository text is masked: control characters as `\xNN`, and U+2028, U+2029 and format characters as `\uNNNN` (`\UNNNNNNNN` above U+FFFF).

Repository text (a file name, a service, a port, a contract or job name, a configuration entry) prints escaped in the door's own fields: a finding's location, fact and fix, refusals and notes. Invalid UTF-8 prints as `\udcNN`; every control character (line feed, carriage return, ESC, tab, DEL, C1) as `\xNN`; U+2028, U+2029 and every format character (Unicode category Cf: bidi overrides, isolates and marks, zero-width characters, the soft hyphen, the byte order mark, tag characters) as `\uNNNN` (`\UNNNNNNNN` above U+FFFF).

```text
    ! deploy/‮EDGE_SHA  tracked  a file without a suffix that is no executable
```

A refusal's fact is one line; the lines the door means to print (an import chain, the verdict's problems, the report's tables) stay lines. A line taken from another tool's rendering prints behind `| `: actionlint's message stays one escaped line, while rich may drop a carriage return, expand a tab, or start a new marked line at a line feed in import-linter's lines.

Ruled by the operator: in GitHub Actions the door both stops workflow commands and escapes repository text.

In GitHub Actions (`GITHUB_ACTIONS=true`) the door first prints `::stop-commands::<token>` on stderr and on stdout, a new random token per run, so no workflow command in repository text runs: no `::warning::` or `##[warning]` annotation, no `add-mask`, no `add-matcher`. The stop lasts for the rest of the step, and the door never prints the line that lifts it. Problem matchers registered in the job still read the log, because the stop does not reach them. The gate's jobs register only setup-uv's python matcher, which is anchored at the line start and so cannot match repository text; a matcher another step registers may still match a door line that holds repository text, and the door cannot prevent that.

## Exit codes

Ruled by the operator:

- Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.

The exit of `check` is the worst verdict's: `2` if any rule is unparsable or cannot judge (never clean), otherwise `1` if any rule has findings, otherwise `0`; a rule that only measured is `0`. `✗` is always exit `2` ([Glyphs](#glyphs)).

## Verbose runs

Ruled by the operator:

- `-v` shows each scanner's exit and its stderr once gitleaks has cleared it; a text it does not clear is withheld and counted; the machine reports stay out. It never shows a secret.

`quality check -v` and `quality check secrets --staged -v` add, under each area that ran a scanner, after its findings and notes, one `·` row per scanner run, `· <scanner>  exited  <n>`, and that scanner's stderr line by line behind the marker `| `, dim, escaped and never shortened, split on line feeds alone. A text gitleaks does not clear is withheld whole and the row counts its lines; a failing gitleaks' lines are counted unread. A scanner that did not finish or printed no UTF-8 has no row. A scanner's report never stands there: it is what the rows are read from, and it can quote repository lines. `-v` changes no exit, no verdict and no scanner's arguments; a run without it keeps no stderr and adds no vet to a refusal's; `check` writes no step summary. A secret's value never appears, also not with `-v`.

```text
  ✓ secrets       clean          2 commits
    · gitleaks  exited  0
      | 10:40PM INF 2 commits scanned.
      | 10:40PM INF no leaks found
  ✓ workflows     clean          1 workflow · offline audits only
    · actionlint  exited  0
    · zizmor  exited  0
      |  INFO zizmor: 🌈 zizmor v1.31.1
```

## The vet

A secret's value never appears, and zizmor's schema refusal and osv-scanner's file error quote what they read, so a key in a workflow or a lock file could otherwise stand in a `✗` row and a CI log, on a run without `-v`. Before a fact quotes any of a scanner's stderr, the whole text goes through `gitleaks stdin` under quality's own `gitleaks.toml`, the definition of a secret that `quality check` judges with. Only exit `0` and an empty report clear it. A flagged text and a vet that cannot answer (gitleaks missing, a timeout, any other exit, a missing or unreadable report, a report that disagrees with the exit) withhold it whole, never partly, and the fact says how many lines: `· N stderr lines withheld · not cleared by gitleaks`. The exit and the verdict never move.

```text
  ✗ workflows     not checked    zizmor exited 1, neither 'clean' nor 'findings' · 8 stderr lines withheld · not cleared by gitleaks
    · zizmor  exited  1 · 8 stderr lines withheld · not cleared by gitleaks
```

Without `-v`, a run whose scanners refuse nothing starts no vet; under `-v`, a finished scan that wrote stderr starts one, as does a refusal that wrote any, and a refusal with no stderr starts none, so a refusal costs two gitleaks starts. No refusal quotes gitleaks' own stderr, because the vet cannot vet the tool that failed; `-v` counts a failing gitleaks (any exit but `0` and `7`) unread, `· N stderr lines withheld · a failing gitleaks is never quoted`, because that stream relays git's stderr line by line, where a value cut across two lines passes the vet. Report text in rows is not stderr and is not vetted.

- A text holding ESC or a C1 control (U+0080 to U+009F) has no clean answer, because a terminal code can stand against a value without the word boundary gitleaks' rules need and the printed text escapes only the code: it is withheld whole and no vet starts. A scanner writes terminal codes on stderr only under forced colour (`FORCE_COLOR`, `CLICOLOR_FORCE`); then its refusal's reason is withheld, clean text too. Giving the scanners a plain environment would lift that if it hurts a dependent. A normalised text is not vetted instead: stripping a code joins two words or eats the first letter of a key, and quality would own a terminal parser.
- The vet judges as the secret scan judges: `--ignore-gitleaks-allow` and the scan's own config and exit code come from one list that feeds both calls, so a key beside `gitleaks:allow` is a finding; gitleaks runs in the vet's scratch directory, because with no source it reads `.gitleaksignore` from the folder it stands in and a repository could list the fingerprint of a stdin finding; the text opens with a fixed line, because gitleaks answers clean for a text whose first bytes read as a binary type; `--redact`, so the report holds no value, and it lives in a 0700 directory removed on every exit.
- What passes: a value gitleaks does not recognise, which `quality check` does not recognise either; a value gitleaks recognises only by its context (`generic-api-key` needs its `api_key = `) when a scanner quotes the bare value; a value a scanner cut or spliced across lines; a binary type's mark at a fixed offset behind the first line (`ustar` at byte 257), which needs a text built for it.
- quality does not replace every refusal's stderr with a fixed text, because every person would lose the reason; it does not show an uncleared text and name the risk, because a secret's value never appears, and it is not limited to a list of known lines per scanner (five lists that decay at each scanner bump).
