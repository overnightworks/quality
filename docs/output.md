# Output

Reference for the engineer, operator or agent who reads or matches what a quality command prints, and for the test that
proves it. What each command does stands in [commands](commands.md), `check`'s rules in [rules](rules.md), the verdict
in [gate](gate.md), the three sonar steps in [sonar](sonar.md). Paths, repositories and ids in the examples are
invented. All terminal lines are English.

## The output owner

One output owner prints; rules return findings and never print.

- MASK-11: A line the door prints starts with its own text, so a matcher anchored at the line start, such as setup-uv's python matcher, cannot match repository text.

## Rows

Ruled by the operator:

- Every row reads `glyph · subject · event · fact` (ROW-1). A finding sits indented under its area row with `path:line`
  right after the glyph (ROW-2); one `fix:` line follows the last of consecutive findings that share the fix (ROW-3).
- Findings under their area with a `fix:` line: indented under the area row, columns aligned per area (ROW-4); `fix:`
  under the last of consecutive findings with the same fix. A finding without a line shows only the path.

- ROW-1: The columns are the glyph (state at a glance), the subject (the area, or `path:line` of the finding), the event (one
  typed word: what is the case), the fact (values, a few words) and then `fix:` (what you do).
- ROW-2: A finding is `! <path[:line]>  <event>  <fact>`, indented under its area row.
- ROW-3: One `fix:` line follows the last of consecutive findings that share the fix.
- ROW-4: Path and event align per area by display width, in every stream, and a
  row pads its subject and event the same way: an East Asian wide character takes two cells and a combining mark none;
  spacing marks, Hangul conjoining letters, skin-tone modifiers and emoji sequences are not measured.

- ROW-19: The findings of an area stand in run order.

- ROW-5: A finding's lines from another tool's rendering (import-linter's broken
  contract chain and guidance, actionlint's message) stand under its row behind the marker `| `, six spaces deep, before
  the run's `fix:`;
- ROW-6: a fix that asks for an entry ends with `:` and the entry's lines stand under it, two spaces
  deeper, whole and never shortened.

A security finding's fix names the line its `# noqa` must stand on, and a code and ruff's message are the fact
([rules](rules.md)).

- ROW-8: Areas run in the order `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies`, `code`.
- ROW-17: The first
  row of a run names the release that judges ([commands](commands.md#which-release-judges)).

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

Two other release rows ([commands](commands.md#which-release-judges)), for quality's own checkout and for the commit
hook where no gate pins a release:

```text
  · release       this checkout  0.10.1.dev3+g5d1e0b7 · quality judges itself
  · release       v0.10.0        installed · no gate pins a release here
```

One event word stands per kind of finding, shared across rules (`report.FindingEvent`).

- ROW-14: Neither a fact nor a fix ends
  in a full stop: a fix is a sentence, or the label of the entry to copy.

The events, facts and fixes of the
rows, by area:

- `files`:
  - ROW-20: a fact never restates the path its row shows;
  - ROW-21: a path allowed under two rules and no longer tracked is
    one `unused` row per rule, each fix deleting every entry of the path;
  - ROW-22: `script-name` says `misnamed` where the
    shape breaks, its fact then holding the place too, and `misplaced` where only the place does.

  A `literal-text` finding is `literal text` at the page and line, its fact the text in single quotes, its fix to read it
  through the generated module and keep it in the catalog (LITERAL-14); a covered repository's row counts the rule among
  its `N rules` ([rules](rules.md)). A `locator-text` finding is `catalog text` at the test file and line, its fact
  `'<text>' is the catalog text of <key>` (with `, and N more keys` when several keys share the text), its fix to find the
  element by its key through the generated module (LOCATOR-6).
- `debt`: a `job-timeout` finding is `missing` (`job <id> has no timeout-minutes`, at the job's line), `invalid`
  (`job <id> has a timeout-minutes that is not a number above zero`), `new debt` (`job <id> has timeout-minutes
  <value> · limit 15`), `grew` (`job <id> has timeout-minutes <value> · ceiling <entry>`) or `can fall` (the same fact
  at the line, or `job <id> is gone · ceiling <entry>` and `job <id> has no debt · ceiling <entry>` at the file with no
  line), the measured ones at the `timeout-minutes` line; the fix of the first two is `set timeout-minutes to a number
  of at most 15`, of `new debt` `lower timeout-minutes to 15 or less` (`, or run quality check --fix to record it` where
  the ceiling has no `[job_minutes]` table), of `grew` `bring it back to <entry> or below` and
  of `can fall` `quality check --fix` ([rules](rules.md)).
- `imports`: a core test that imports an adapter is an `adapter import` finding at `path:line` that names each
  forbidden module once, as in `imports shop.adapters (through tests/support/stores.py)`, a marked module that imports
  none is `unused` at its `pytestmark` line, and the held counts of marked modules are `grew`, `new debt` or `can
  fall` (ADAPTERIMPORT-25, ADAPTERIMPORT-26); a non-core test module that reaches an adapter without the `integration`
  mark is an `unmarked` finding at its first red statement, as in `imports shop.adapters · module not marked
  integration` (INTEGRATION-6);
  - ROW-23: a broken contract is a `broken` finding; an unmatched ignore is an
    `unused` finding at `error` and an `unused` note at `warn`; a grimp or import-linter warning is a `warning` note;

  a `broken` finding's detail lines are import-linter's own rendering of the contract ([Rows](#rows)).
- `secrets`:
  - ROW-24: a key is a `secret` finding whose fact names gitleaks' rule and, in history, the commit's first eight
    characters, never the value;
  - ROW-25: a staged key names no commit;
  - ROW-26: its fix is lines to copy.
- `workflows`:
  - ROW-27: actionlint's finding is `invalid` and zizmor's `insecure`, zizmor's fact naming its audit and its fix
    holding the audit's URL;

  a `pr-concurrency` finding is `missing` (`no concurrency`, `no cancel-in-progress`),
  `shared group` (`group is not a listed form`, adding `(falls back to <token>)` where the group holds the commit or a
  ref; `no group` for a `concurrency` without one) or `cancels main` (`cancel-in-progress is not a listed form`), its
  fix the block to copy ([rules](rules.md)); a `pytest-workers` finding is `serial` (`pytest runs without -n`, `pytest
  runs with -n 0`) or `invalid` (`-n count is not a number above zero or auto`, `command cannot be read to its end`,
  `command is built at run time, pytest -n cannot be read`), its fix naming the flag and the marker; a `live-layer` finding is
  `runs live` (`pytest can select tests marked live`) or `invalid` (`-m expression cannot be read`,
  `command is built at run time, pytest -m cannot be read`, `command cannot be read to its end`), its fix naming the `-m`
  expression and no marker ([rules](rules.md)); a `runtime-install`
  finding is `installs` (the manager and the install word, as `apt-get install`, or `a download runs as a shell script`)
  or `invalid` (`command cannot be read to its end`, `command is built at run time, an install cannot be read`), its fix
  naming the routes (the lockfile, the runner image, an image pinned by digest) and the marker ([rules](rules.md));
  - ROW-28: an actionlint finding names the scanner and holds its message whole as its one detail line;
  - ROW-29: a row with a finding or
    a refusal does not say `offline audits only`;

  a `catalog-check` finding is `missing`
  at `.github/workflows` with no line (`no workflow step runs design-catalogs --check on a pull request, directly or through
  make`) or `invalid` at a workflow or the `Makefile` and a line (`command cannot be read to its end`, `command is built at
  run time, design-catalogs --check cannot be read`), its fix naming the step or the make target ([rules](rules.md)); a
  `render-pass` finding is `missing` at `quality/config.toml` with no line (`N catalogs and no render pass declared`,
  `render pass "x" is no job of a tracked workflow`, `render pass "x" is not a mandatory job`), its fix naming the
  declaration, a job a workflow holds or the `[gate] mandatory_jobs` entry ([rules](rules.md)); a `pr-workflows` finding
  is `unlisted` at a workflow a pull request triggers that is neither the gate's caller nor CodeQL's (`a pull request
  triggers it, and it is neither the gate's caller nor CodeQL's`), its fix the `[[workflow.allow]]` block to copy, or
  `unused` at a declaration that names no other such workflow, its fix deleting the entry ([rules](rules.md)).
- `dependencies`:
  - ROW-30: an advisory is an `affected` row, its fact the package and version, the group's id and where it
    lists them the versions that fix it, its fix per lock kind;
  - ROW-31: the id is the group's first, never an alias;
  - ROW-32: a nested lock's fix names its folder;
  - ROW-33: the fix of a pnpm or requirements lock is the same hint for every
    package, so rows of one folder share one `fix:`;
  - ROW-34: an allowance that ran out leaves the row `affected` and says
    so;
  - ROW-35: two allowances on one group are a `conflict` finding at the config, an allowance no advisory matches an
    `unused` finding there.

  A fleet package a `uv.lock` resolves from an index is a `from index` finding at the lock, its fact the package, its
  version and the index ([rules](rules.md)).
  A `pytest-random` finding is `missing` at a `uv.lock` that locks pytest without pytest-randomly (`pytest <version> is
  locked without pytest-randomly`), `unshuffled` at the `addopts` of a `pyproject.toml`, a workflow pytest call or a
  `PYTEST_ADDOPTS` or `PYTEST_DISABLE_PLUGIN_AUTOLOAD` value that switches the plugin off or fixes its order, and `unseeded`
  where the same places hide its seed header; its fix names what to drop, and a workflow finding the
  `# pytest-random: <reason>` marker ([rules](rules.md)). A pytest line `pytest-workers` reports as `invalid` carries no
  `pytest-random` row, so it is red once. A `vitest-random` finding is `unshuffled` at the config of a vitest project that does
  not shuffle both orders under its root `test.sequence` or fixes its seed, and at its `package.json` where it has no config; its fix
  names the key to set ([rules](rules.md)). A `node-test-random` finding is `unshuffled` (`node --test runs its files and tests in a fixed order`) or `unseeded` (`no reporter of the call prints the seed to the log`) at the call's first line, or `invalid` (`command cannot be read to its end`, `command is built at run time, node --test cannot be read`); its fix names the flag or the reporter and no marker ([rules](rules.md)).
- `code`: a security finding is `insecure`, a `print` or `pprint` is a `print call`, and an owner that names no code
  found in its file is `unused` ([rules](rules.md)). A `logging` finding is no finding row but an `open` note at the
  file and line, its fact the logline rule it breaks (LOGGING-3).
- `debt`: the `test-twin` rule is no finding row but one `open` note at `.`, its fact the groups of test twins and of
  copied test helpers it counts (TWIN-4); so is `spec-citation` without a `[spec_citations]` table, its fact the spec IDs no
  test cites, the cited IDs no spec defines and the IDs defined twice (CITE-4). With the table its findings are `uncited` (at
  the definition, `spec ID ST-02 is cited by no test`, fix `cite it at the head of a test`), `undefined` and `retired` (at the
  citing test, `ST-03 is cited and defined nowhere`, `ST-03 is cited and retired`), `duplicate` (at the later definition,
  `spec ID ST-01 is defined again, first at docs/spec.md:12`) and `can fall` (at `quality/baseline.toml`, `spec ID ST-01 is
  cited`, fix `quality check --fix`) (CITE-8); a declaration of `quality/config.toml` that conflicts with the tree or matches
  nothing is `conflict` or `unused` at the config or the declared path, its fix deleting the entry (CITE-17, CITE-19). A
  `spec-length` finding is `too long` at the rule's definition, one row per rule, as in
  `! docs/spec.md:12  too long  spec rule ST-02 has 57 words · limit 40` over the fix `shorten it, or split it into rules that
  each have an ID and a citing test` (SPECLENGTH-1). Where `HEAD` has no merge base with `origin/main` the rule refuses, `spec rule
  length cannot be judged, HEAD has no merge base with origin/main`, with the next step `git fetch origin main` or, in a shallow
  clone, `git fetch --unshallow` ([refusals](#refusals), SPECLENGTH-7, SPECLENGTH-8).

The `env-file` fix's own wording stands in [rules](rules.md).

### Rule names

Ruled by the operator:

- Rule names only in an allowance fix (ROW-7): a `fix:` line that asks for an `[[….allow]]` entry names the rule
  (SUFFIX-17), because the entry needs it.
- REFUSAL-23: A config refusal's fact may name the values a config key takes, rule ids included, and the example to copy may hold
  the entry's rule line (REFUSAL-15); this is the one exception to the rule that no rule name stands outside an
  allowance's `fix:`.

- ROW-7: No rule name stands in a row (`compose-port`, `env-file`); the event says it in words. The areas replace the
  rule names in the verdict, and [`quality check --help`](commands.md#help-and-wrong-invocations) says which rules an
  area holds.

### Area rows

Ruled by the operator:

- ROW-9: Clean areas: one row per area, so you see what was checked and its extent.

- ROW-10: An area row is `✓ <area>  clean  <extent>`, also when nothing was found (ROW-9), and `! <area>  N findings` with
  findings.

The extent of a clean row is:

- ROW-36: `files`: `N rules · M files`, M the distinct paths git tracks.
- ROW-18: `debt`: `ceiling held · N entries, M clone pairs`, what the ceiling file holds; a `[job_minutes]` table holding a job
  adds `, K job limits` after the pairs (TIMEOUT-34), and a `[spec_citations]` table holding an ID adds `, K uncited spec IDs`
  after them (CITE-8).
- ROW-11: `imports`: `N contracts`.
- ROW-37: `secrets`: `N commits`, because gitleaks reads committed history only; `staged change · N files` for a staged
  run.
- ROW-38: `workflows`: `N workflows · offline audits only`, the workflow files the tree holds, said in the row and in no note
  of its own.
- ROW-39: `dependencies`: `N lockfiles · M packages`, `0 lockfiles · 0 packages` with none.
- ROW-12: `code`: `2 rules · N Python files`, the tracked regular `.py` files ruff judged, `0 Python files` with none; `3 rules`
  where the repository declares logline (LOGGING-1).

With ` · K allowed` appended where files were allowed, leaks were allowed (secrets), advisories were allowed
(dependencies) or output owners were honoured (code).

- ROW-40: Without a ceiling file the debt row is `· debt  not recorded  N
  entries over the thresholds · measured only` (`M clone pairs · partly measured only` when the ceiling file has no
  `[clones]` table, with `, K uncited spec IDs` after the pairs where a `[spec_citations]` table holds an ID, CITE-8), with `fix: quality check --fix` under it.

Under `quality check debt --fix` it is `✓
debt  lowered  N entries · quality/baseline.toml` with a `✓` row per lowered or dropped entry, `✓ debt  moved  N entries
· quality/baseline.toml` where it moves entries and lowers none, `✓ debt  recorded  N entries, M clone pairs ·
quality/baseline.toml is new` (`, K job limits` after the pairs where it records the job table, `, K uncited spec IDs` after them where it records the spec table, `, K adapter import exceptions` after those where it records the adapter table), `✓ debt  recorded  K job limits · quality/baseline.toml` with a `✓` row `recorded  K job limits` under
it (`K uncited spec IDs` for the spec table, CITE-11) (it names the clone pairs recorded beside, and stands in place of the lowered or moved row where entries fall or move in
the same fix), `✓ debt  rewritten  quality/baseline.toml` (entries equal, text not), `! debt  N findings
nothing lowered` or `✗ debt  not fixed  <reason>`; under `--dry-run` it is `~ debt  N to lower` (`N to move`, `to
record`, `to rewrite  text differs · quality/baseline.toml`; the first recording is `~ debt  to record  N entries, M clone pairs ·
quality/baseline.toml would be new`) with a `~` row per entry.

- ROW-13: Without an import-linter config the imports row is `· imports  not set up  no import-linter config`.

An area
that cannot check is `✗ <area>  not checked  <reason>` ([Refusals](#refusals)).

### Notes

- ROW-15: A note is a `·` row under its area, after the area's findings, aligned with them:
  `· <path[:line]>  <event>  <fact>`,
- ROW-16: with no `fix:` of its own.

- ROW-41: Its events are `allowed` (file rules, dependencies, secrets and python-print), `open` (logging, test-twin,
  spec-citation), `not measured` (a Python file the size rule cannot parse or a script file that is not UTF-8 or nested
  too deeply to read, without a ceiling file), `unused` and `warning` (import-contract) and `unused` (secret).
- ROW-42: An allowed advisory and an honoured output owner stand as notes, and the clean row counts them:

```text
  ✓ dependencies  clean          1 lockfile · 63 packages · 1 allowed
    · uv.lock  allowed  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6 · until 2027-01-31 (#412) · no template input
  ✓ code          clean          2 rules · 41 Python files · 1 allowed
    · src/shop/cli.py:3  allowed  output owner · the command line owns its output
```

The verdict's `time` row is a note whose events are `measured` and `not measured` ([the gate](gate.md)).

## Glyphs

Ruled by the operator:

- CLOSING-1: `✗` means "could not check or do": a finding is always `!` with exit 1; `✗` is always exit 2.

| Glyph | Meaning |
|---|---|
| `✓` | clean, or done |
| `!` | needs you: a finding, a red verdict |
| `✗` | could not check or do; always exit 2 (CLOSING-1) |
| `·` | note: release, allowed finding, not measured |
| `~` | will change: the dry run of `--fix` and `merge` |
| `⠋` | live: no glyph column of its own, terminal only, stderr, disappears |

## The closing line

Ruled by the operator:

- Every run ends with exactly one closing verdict line (CLOSING-4), or with an `error:`/`next:` pair when it could not
  answer (CLOSING-15).
- `next:` after a red verdict: a `!` verdict may carry a `next:` only when exactly one command is the answer. For
  `check` findings the `fix:` lines carry the way (CLOSING-11).
- Exit 2 beats 1 (CLOSING-27): exit 2 with the pair; the findings of the other areas stand anyway (CLOSING-13).
- Duration in the verdict (`12s`, `1m12s`, `<1s`) (CLOSING-7).

- CLOSING-2: A closing line is its glyph and its parts joined by ` · `,
- CLOSING-3: each part escaped.
- CLOSING-4: Exit `0` or `1`
  ends with one verdict line on stdout:
  - CLOSING-5: `✓ clean · 7 areas · <1s`
  - CLOSING-6: `! 5 findings in 3 areas · 4 areas clean · 14s`, where the clean count is left out if no area is clean.
- CLOSING-7: The
  duration is whole seconds rounded down, `<1s` below one; from 60 seconds on it reads minutes and two-digit seconds,
  `1m00s`.
- CLOSING-8: Before the duration stands `debt ceiling measured, not enforced` when no ceiling file holds the debt,
- CLOSING-9: `debt ceiling partly measured, not enforced` when the ceiling file has no `[clones]` table,
- CLOSING-10: then
  `imports not enforced` when the repository has no import-linter config.
- CLOSING-11: No `next:` follows a findings
  verdict.

The line of `quality check debt --fix --dry-run` when nothing is left to lower ([the fix](rules.md)):

```text
✓ clean · nothing to lower · 1 area · 4s
```

- CLOSING-12: Exit `2` ends instead with `error: K of N areas could not check` (`1 of 1 area` for one), N the areas judged,
- CLOSING-13: followed by ` · N findings in the others`
- CLOSING-14: or, when findings stand under a `✗` row,
  ` · N findings, M of them in those areas`, and `next:` [on stderr](#streams);
- CLOSING-15: it prints no verdict line,
- CLOSING-16: and the error line ends after the count of areas where no finding stands.
- CLOSING-33: A rule that cannot
  judge [refuses only itself](rules.md): the other rules' findings, those of its own area included,
  [still print](#refusals), and a note stands under a `✗` row where its rule judged.

- CLOSING-17: A run that cannot start prints only the pair and exits `2` before any rule runs, and no release row stands:
  - CLOSING-18: `error: not inside a git work tree · <the folder, ~ for the home folder>`
  - CLOSING-19: with
    `next: run quality check inside a repository`
  - CLOSING-20: when git, asked in the C locale,
  - CLOSING-21: says the folder
    lies outside any work tree (a folder outside any repository, a bare repository, the inside of `.git`),
  - CLOSING-22: and
    otherwise (a folder without permission, a repository git refuses) `error: . is not a readable git repository: ...`
  - CLOSING-23: with `next: git -C . status`,
  - CLOSING-24: and with
    `next: fix what the error names, then run quality check again` where git did not run or the folder is gone.
- CLOSING-25: A folder outside the home folder, or any folder where no home folder is known, stands as its absolute path.
- CLOSING-26: No run that cannot start ends in a traceback.

## The consent in a merge run

`quality merge` names one pull request and prints its rows in the grammar of [Rows](#rows). Its `consent` row is
`✓ consent  given  review <id> at <commit>` where an organisation owner's review carries the text, and `! consent  missing`
where none does; below the missing row the consent text stands as a block (MERGE-119, MERGE-121): each line escaped as
[Masking](#masking) says, printed whole with no row prefix and no padding, so a reader pastes the fenced text as a review.
Every line of the block starts with the door's own text (MASK-11). The refusal ends in the pair of MERGE-121, with the
state `not merged` and its reason `no consent review at <head>`:

```text
error: not merged · no consent review at <head> · nothing changed
next: the operator leaves the text above as his review at <head>, then quality merge <owner/repo>#<pr> runs again
```

## Refusals

- REFUSAL-1: A refused area is its `✗` row,
- REFUSAL-2: which carries no extent,
- REFUSAL-3: and a [pair on stderr](#streams),
  [exit 2](#glyphs); the other areas' rows stand:

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

A fact of a few words says what is missing (`mise could not locate gitleaks 8.30.1`, true for every non-zero answer of
mise). The `files` area refuses with the fact of a page the literal-text scan cannot read to its end, which names the page:
`<file> has a <script> that is opened and never closed, so the markup after it cannot be read` (the element as written in
lower case) or `<file> has a { that is never closed, so the markup after it cannot be read`, and with the shared reader's
`cannot read <file>` for a page that is not UTF-8 (LITERAL-9); it names a Python, TypeScript or JavaScript test file it cannot read, a catalog
design's strict reading refuses as `<file>:<line>: <problem>`, and a `pyproject.toml` whose `[tool.design-catalogs]` table
design refuses (LOCATOR-10).

- REFUSAL-4: A reason that several rules of one area share stands once;
- REFUSAL-5: distinct reasons make the row say
  `N reasons`
- REFUSAL-6: and stand on `✗` lines below it, and a reason counts once.
- REFUSAL-7: Findings of an area
  that also cannot check stay under its `✗` row and are counted in [the error line](#the-closing-line).

- REFUSAL-8: `next:` names each distinct step of the run's refusals once,
- REFUSAL-9: in row order:
  - REFUSAL-10: one step on the
    `next:` line,
  - REFUSAL-11: several each below a bare `next:` (a command alone on its line, one level of four spaces
    deeper per level).
- REFUSAL-12: The general instruction `fix what the ✗ rows name, then run quality check again`
  is the last line when a refusal carries no step,
- REFUSAL-13: and the only one when none does.
- REFUSAL-14: Alternative commands stand under a label ending `, one of:`, each choice's label on a line and its command one level
  deeper:

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

- REFUSAL-24: A config refusal names its fact on each `✗` row it reaches and shows the example to copy once under `next:`:

- REFUSAL-15: the
  label `write it in quality/config.toml like this:`
- REFUSAL-16: and the example's lines one level deeper,
- REFUSAL-17: each whole on its own line
- REFUSAL-18: and [never shortened](#length), so the lines copied as printed are valid TOML.
- REFUSAL-19: A config refusal that has no example shows no label.
- REFUSAL-20: The fact names an entry by its table
  and 1-based position (`[[size.generated]] entry 1 needs …`) and quotes a repeated value as a TOML string,
  never as a Python repr, so the printed value, copied into a TOML file, reads back as the value.
- REFUSAL-21: An unknown key
  stands bare when it is a non-empty name of ASCII letters, digits, `_` and `-`, else as a TOML string.

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

- REFUSAL-22: A rule that runs out of the run's time refuses in one of two fixed wordings, each carrying the run's 540 seconds and
  bringing no step: `the run's 540 seconds are spent` for a rule that would start a scanner with no time
  left, or a download that would start after the end, which names no scanner and so stands once for the rules of an
  area that share it (REFUSAL-4), and `<scanner> did not finish within the run's 540 seconds` for a scanner stopped
  while it ran, `<scanner>` being the executable's file name (`mise` for a call that locates a scanner). A download
  stopped while it ran names itself in the same wording:
  `the PyPI advisory database download did not finish within the run's 540 seconds`.

## Streams

Ruled by the operator:

- stdout carries rows and the verdict (STREAM-1). stderr carries the live line and `error:`/`next:`
  (STREAM-2). So `quality check | less` shows every finding and an error stays visible.
- `report`: rows (FLEET-33) and verdict (FLEET-45) on stderr; the tables are an artifact on stdout, so
  `quality report > fleet.md` writes only the five tables (FLEET-40).

- STREAM-1: Rows and the verdict go to stdout;
- STREAM-2: the live line and `error:`/`next:` go to stderr.

`quality report` has [its own split](commands.md#quality-report).

- STREAM-3: The lines come in run order, also through a pipe that merges stdout and stderr (`2>&1 | tee log`);
- STREAM-4: each stays
  on its own stream.
- STREAM-5: `output.set_up_streams`, which `cli.run` calls, makes stdout line-buffered.
- STREAM-6: A
  run judges the areas in [the rows' order](#rows) and prints each area's lines as its last rule ends, in a pipe as at a
  terminal, so they stand while the next area runs.
- STREAM-7: After Ctrl-C or SIGTERM during a scan the finished areas'
  lines stay, with neither verdict nor pair.
- STREAM-8: A reader that left (`| head`, a closed `less`) ends nothing: the
  run is judged to the end, that stream prints nothing more, and the exit is the run's own.
- STREAM-9: A stream closed
  before the run (`2>&-`) takes nothing; its lines never move to the other stream.

## Colour and the live line

Ruled by the operator:

- With no TTY, with `NO_COLOR` or with `TERM=dumb` the same words print without colour (COLOUR-6), without the live line
  (COLOUR-7) and never shortened (LENGTH-14).
- The CI log stays without colour: no special rule beyond "no TTY, no colour".

- COLOUR-1: On a terminal the glyphs, the verdict and the `error:`/`next:` labels are coloured
  with the 16 base colours
- COLOUR-2: and facts and notes are dim,
- COLOUR-3: each stream on its own; `~` is cyan.
- COLOUR-4: The `fix:` line and the lines
  to copy are never painted.
- COLOUR-5: `NO_COLOR` counts only when set and not empty.
- COLOUR-6: A pipe, a file (a
  runner step's log is two pipes), the CI log, `NO_COLOR` and `TERM=dumb` print the same words plain and [whole](#length).

- COLOUR-7: While a scanner runs, a stderr that is a terminal with a known window and colour on
- COLOUR-8: shows one dim line per
  scanner, the area and the scanner,
- COLOUR-9: leaving the window's last column free,
- COLOUR-10: and erases it before the
  next line prints, so the area's row takes its place.
- COLOUR-11: Ctrl-C, a refusal and an exception erase it too,
- COLOUR-12: and so does SIGTERM while gitleaks or osv-scanner runs; a SIGTERM during any other scanner ends
  quality on the spot and leaves the line standing.

A window of under three columns and a terminal that draws `…` two
cells wide are not served: there the line may wrap. A pty a step opens for itself is a terminal and shows it.

```text
  ⠋ secrets       gitleaks
```

- COLOUR-13: While quality fetches or prepares the release that judges ([commands](commands.md#which-release-judges)), the same
  line counts the seconds, `⠋ release  fetching  <tag> · <seconds>s` (for quality's own checkout, `syncing` and
  `this checkout` stand for `fetching` and `<tag>`).

## Length

Ruled by the operator:

- Length of a fact: at the terminal shortened to the window width with `…` (LENGTH-1) (LENGTH-2), never in a pipe, file
  or CI log (LENGTH-14). Commands (LENGTH-6), sha256 and entries to copy (ROW-6) stand on their own line, never
  shortened. Each kind of finding has a fixed event word and a fact of a few words.

- LENGTH-1: At a terminal the free text of a fact ends at the window with `…`: the reason on a `✗` row or line,
- LENGTH-2: and the
  fact of a finding or a note whose event is free text (a tracked file, a layout reason, a tool's message);
- LENGTH-3: `output.py` lists them, and a new event stands whole until it is listed.
- LENGTH-4: A count, a measurement or an
  outcome is never cut (`N findings`, `ceiling 64`, `none allows it`, `value not shown`),
- LENGTH-5: nor are the lines
  under a finding and `fix:`,
- LENGTH-6: lines to copy ([Rows](#rows)), the verdict, `error:` and `next:`; a
  long line wraps.
- LENGTH-7: A line under a row that the door marks whole, as it marks a declared loosening and its `reason:` line, or an
  undeclared one and its `fix:` line, stands whole at a terminal.
- LENGTH-8: The cut is at the end, never inside an escape `printable` creates;
- LENGTH-9: in
  a window at or below the text before the fact it is `…`.
- LENGTH-10: The cut counts display width, as the columns do
  ([Rows](#rows)).
- LENGTH-11: The window is what the stream's own terminal answers,
- LENGTH-12: read once at the start;
- LENGTH-13: `COLUMNS` is not read,
- LENGTH-14: and an unknown window shortens nothing.

At 60 columns:

```text
    ! .env          tracked         an env file carries con…
```

- LENGTH-15: A sha256 stands whole on its own line, because you copy it into the entry. The entry is the four lines a reader takes, whole and never shortened; the person adds `reason`. It holds no `sha256` key, a key no reader takes.

## Masking

Ruled by the operator:

- Repository text is masked: control characters as `\xNN` (MASK-6), and U+2028, U+2029 and format characters as `\uNNNN`
  (`\UNNNNNNNN` above U+FFFF) (MASK-7).

Repository text (a file name, a service, a port, a contract or job name, a configuration entry) prints escaped in the door's own fields:

- MASK-1: a row's subject and fact,
- MASK-2: a finding's location, fact, detail lines and fix,
- MASK-3: refusals
- MASK-4: and notes.
- MASK-5: Invalid UTF-8 prints as `\udcNN`;
- MASK-6: every control character (line feed, carriage return, ESC, tab, DEL, C1) as `\xNN`;
- MASK-7: U+2028, U+2029 and every format character (Unicode category Cf: bidi overrides, isolates and marks, zero-width characters, the soft hyphen, the byte order mark, tag characters) as `\uNNNN` (`\UNNNNNNNN` above U+FFFF).
- MASK-8: Every other character, a backslash included, prints as itself, so escaping text that is already escaped changes nothing.
- MASK-9: Each field is escaped before it is padded, so the columns hold whatever the text holds.

```text
    ! deploy/\u202eEDGE_SHA  tracked  a file without a suffix that is no executable
```

- MASK-10: A refusal's fact is one line;
- MASK-12: the lines the door means to print (an import chain, the verdict's problems, the
  report's tables) stay lines.
- MASK-13: A line taken from another tool's rendering prints behind `| ` ([Rows](#rows)):
  actionlint's message stays one escaped line, while rich may drop a carriage return, expand a tab, or start a new marked
  line at a line feed in import-linter's lines.

Ruled by the operator:

- In GitHub Actions the door both stops workflow commands (MASK-14) and escapes repository text.

In GitHub Actions (`GITHUB_ACTIONS=true`):

- MASK-14: the door first prints `::stop-commands::<token>` on stderr and on stdout,
- MASK-15: a new random token per run, so no workflow command in repository text runs: no `::warning::` or `##[warning]` annotation, no `add-mask`, no `add-matcher`.
- MASK-16: The stop lasts for the rest of the step, and the door never prints the line that lifts it.

Problem matchers registered in the job still read the log, because the stop does not reach them. The gate's jobs register only setup-uv's python matcher, which is anchored at the line start and so cannot match repository text ([the line start](#the-output-owner)); a matcher another step registers may still match a door line that holds repository text, and the door cannot prevent that.

## Exit codes

Ruled by the operator:

- Exit codes: 0 yes (CLOSING-29), 1 no (CLOSING-28), 2 could not answer (CLOSING-27), 130 interrupted (CLOSING-31).

The exit of `check` is the worst verdict's:

- CLOSING-27: `2` if any rule is unparsable or cannot judge (never clean),
- CLOSING-28: otherwise `1` if any rule has findings,
- CLOSING-29: otherwise `0`;
- CLOSING-30: a rule that only measured is `0`.

`✗` is always exit `2` ([Glyphs](#glyphs)).

- CLOSING-31: Ctrl-C ends a `check` run as an unhandled interrupt does:
  the process dies by SIGINT, which a shell reads as `130`,
- CLOSING-32: with no traceback.

## Verbose runs

Ruled by the operator:

- `-v` shows each scanner's exit (VERBOSE-2) and its stderr (VERBOSE-3) once gitleaks has cleared it; a text it does not
  clear is withheld and counted (VERBOSE-8); the machine reports stay out (VERBOSE-9).
  It never shows a secret (VERBOSE-14).

`quality check -v` and `quality check secrets --staged -v` add:

- VERBOSE-1: under each area that ran a scanner, after its findings and notes,
- VERBOSE-2: one `·` row per scanner run, `· <scanner>  exited  <n>`,
- VERBOSE-3: and that scanner's stderr
  line by line behind the marker `| ` ([Rows](#rows)),
- VERBOSE-4: dim,
- VERBOSE-5: [escaped](#masking) and never
  shortened,
- VERBOSE-6: split on line feeds, a carriage return counting as one, and on no other separator.
- VERBOSE-7: osv-scanner's lines name each lock by its tracked name and the checkout, the scanner's directory and the scratch
  directory as `<repository>`, `<scanner>` and `<scratch>`.
- VERBOSE-8: A text gitleaks does not clear is withheld whole
  and the row counts its lines; a failing gitleaks' lines are counted unread. A scanner that did not finish or
  printed no UTF-8 has no row.
- VERBOSE-9: A scanner's report never stands there: it is what the rows are read from, and it can quote
  repository lines.
- VERBOSE-10: `-v` changes no exit, no verdict and no scanner's arguments;
- VERBOSE-11: a run without it
  keeps no stderr
- VERBOSE-12: and adds no vet to a refusal's;
- VERBOSE-13: `check` writes no step summary.
- VERBOSE-14: A
  secret's value never appears, also not with `-v`.

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

[A secret's value never appears](#verbose-runs), and zizmor's schema refusal and osv-scanner's file error quote what
they read, so a key in a workflow or a lock file could otherwise stand in a `✗` row and a CI log, on a run without `-v`.

- VERBOSE-15: Before a fact quotes any of a scanner's stderr, the whole text goes through `gitleaks stdin` under quality's own
  `gitleaks.toml`, the definition of a secret that `quality check` judges with.
- VERBOSE-16: Only exit `0` and an empty
  report clear it.
- VERBOSE-17: A flagged text and a vet that cannot answer (gitleaks missing, a timeout, any other exit,
  a missing or unreadable report, a report that disagrees with the exit, no scratch directory) withhold it whole, never
  partly,
- VERBOSE-18: and the fact says how many lines: `· N stderr lines withheld · not cleared by gitleaks`.

[The exit and the verdict never move](#verbose-runs).

```text
  ✗ workflows     not checked    zizmor exited 1, neither 'clean' nor 'findings' · 8 stderr lines withheld · not cleared by gitleaks
    · zizmor  exited  1 · 8 stderr lines withheld · not cleared by gitleaks
```

- VERBOSE-19: Without `-v`, a run whose scanners refuse nothing starts no vet;
- VERBOSE-20: under `-v`, a finished scan that wrote
  stderr starts one,
- VERBOSE-21: as does a refusal that wrote any,
- VERBOSE-22: and a refusal with no stderr starts none,
  so a refusal costs two gitleaks starts.
- VERBOSE-23: No refusal quotes gitleaks' own stderr, because the vet cannot vet
  the tool that failed; `-v` counts a failing gitleaks (any exit but `0` and `7`) unread,
  `· N stderr lines withheld · a failing gitleaks is never quoted`, because that stream relays git's stderr line by line,
  where a value cut across two lines passes the vet. Report text in rows is not stderr and is not vetted.

- VERBOSE-24: A text holding ESC or a C1 control (U+0080 to U+009F) has no clean answer, because a terminal code can stand against a
  value without the word boundary gitleaks' rules need and the printed text escapes only the code: it is withheld whole
  and no vet starts. A scanner writes terminal codes on stderr only under forced colour (`FORCE_COLOR`,
  `CLICOLOR_FORCE`); then its refusal's reason is withheld, clean text too. Giving the scanners a plain environment
  would lift that if it hurts a dependent. A normalised text is not vetted instead: stripping a code joins two words or
  eats the first letter of a key, and quality would own a terminal parser.
- The vet judges as the secret scan judges:
  - VERBOSE-25: `--ignore-gitleaks-allow` and the scan's own config and exit code come from
    one list that feeds both calls, so a key beside `gitleaks:allow` is a finding;
  - VERBOSE-26: gitleaks runs in the vet's
    scratch directory, because with no source it reads `.gitleaksignore` from the folder it stands in and a repository
    could list the fingerprint of a stdin finding;
  - VERBOSE-27: the text opens with a fixed line, because gitleaks answers
    clean for a text whose first bytes read as a binary type;
  - VERBOSE-28: `--redact`, so the report holds no value,
  - VERBOSE-29: and it lives in a 0700 directory removed on every exit.
- What passes: a value gitleaks does not recognise, which `quality check` does not recognise either; a value gitleaks recognises only by its context (`generic-api-key` needs its `api_key = `) when a scanner quotes the bare value; a value a scanner cut or spliced across lines; a binary type's mark at a fixed offset behind the first line (`ustar` at byte 257), which needs a text built for it.
- quality does not replace every refusal's stderr with a fixed text, because every person would lose the reason; it does not show an uncleared text and name the risk, because a secret's value never appears, and it is not limited to a list of known lines per scanner (five lists that decay at each scanner bump).
