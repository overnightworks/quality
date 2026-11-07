# Output picture

This is the target picture of quality's command-line output; each landing that builds part of it aligns it with what was built, and until then `docs/commands.md` describes what runs today. Reader: the builder of those landings. Paths, repositories and ids in the examples are invented. All terminal lines are English.

## Principle

- One output owner prints; rules return findings and never print.
- Every row reads `glyph · subject · event · fact`. A finding sits indented under its area row with `path:line` right after the glyph; one `fix:` line follows the last of consecutive findings that share the fix.
- Every run ends with exactly one closing verdict line, or with an `error:`/`next:` pair when it could not answer.
- stdout carries rows and the verdict. stderr carries the live line, the input prompt and `error:`/`next:`. So `quality check | less` shows every finding and an error stays visible.
- Rows stand on stdout as each area finishes, in row order, in a pipe as at a terminal. An interrupt during a scan keeps the lines printed and ends without a verdict. A reader that left (`| head`, a closed `less`) ends nothing: the run is judged to the end, that stream prints nothing more, and the exit is the run's own.
- With no TTY, with `NO_COLOR` or with `TERM=dumb` the same words print without colour, without the live line and never shortened.
- On a terminal the glyphs, the verdict and the `error:`/`next:` labels are coloured with the 16 base colours and facts and notes are dim, each stream on its own, as hal does.
- Repository text is masked: control characters as `\xNN`, and U+2028, U+2029 and format characters as `\uNNNN` (`\UNNNNNNNN` above U+FFFF).
- `-v` shows the scanner's raw output indented under its row, never a secret.

Glyphs:

| Glyph | Meaning |
|---|---|
| `✓` | clean, or done |
| `!` | needs you: a finding, a red verdict |
| `✗` | could not check or do; always exit 2 |
| `·` | note: release, allowed finding, not measured |
| `~` | will change: the dry run of `--fix` and `merge` |
| `⠋` | live: no glyph column of its own, terminal only, stderr, disappears |

Anatomy of a row, and the verdict that ends the run:

```text
  ! debt          1 finding
    ! src/shop/parse.py:88  grew  function read_rows has 71 lines · ceiling 64
      fix: bring it back to 64 lines or below

! 1 finding in 1 area · 6 areas clean · 9s
```

Columns: glyph (state at a glance), subject (the area, or `path:line` of the finding), event (one typed word: what is the case), fact (values, a few words), then `fix:` (what you do).

The same row for the three readers, and foreign text:

```text
  ! secrets       1 finding      staged change · 4 files
```

(terminal: coloured per stream; pipe, file, agent, CI log: as above, uncoloured)

```text
    ! deploy/\u202eEDGE_SHA  tracked  a file without a suffix that is no executable
```

At a terminal the free text of a fact ends at the window with `…` (decision 3): the reason on a `✗` row or line, and the fact of a finding or a note whose event is free text (a tracked file, a layout reason, a tool's message; `output.py` lists them, and a new event stands whole until it is listed). A count, a measurement or an outcome is never cut (`N findings`, `ceiling 64`, `none allows it`, `value not shown`), nor are the lines under a finding, `fix:`, lines to copy, the verdict, `error:` and `next:`; a long line wraps. The cut is at the end, never inside an escape `printable` creates; in a window at or below the text before the fact it is `…`. The window is what the stream's own terminal answers, read once at the start; `COLUMNS` is not read, and an unknown window shortens nothing. At 60 columns:

```text
    ! .env          tracked         an env file carries con…
```

## Cases

Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted. Each block names its stream; an `error:`/`next:` pair and a prompt are stderr, everything else stdout, unless the block says otherwise.

### 1. `quality check`, all clean

The first row names the release the gate pins. Every area gets a row, also when clean. Exit 0. Rows and verdict: stdout.

Ruled by the operator: the workflows row counts the workflow files the tree holds and says that only offline audits ran, in the row itself and in no note of its own.

The dependencies row counts the tracked lockfiles and the packages osv-scanner lists for them, `0 lockfiles · 0 packages` with none (O48). The code row counts the area's rules and the Python files ruff judged, `0 Python files` with none. The secrets row counts the commits gitleaks reads, not the tree, because gitleaks reads committed history only (O30).

```text
$ quality check
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  ✓ debt          clean          ceiling held · 31 entries, 2 clone pairs
  ✓ imports       clean          1 contract
  ✓ secrets       clean          412 commits
  ✓ workflows     clean          5 workflows · offline audits only
  ✓ dependencies  clean          1 lockfile · 63 packages
  ✓ code          clean          2 rules · 41 Python files
✓ clean · 7 areas · 12s
```

An allowed advisory stands as a `·` row under the dependencies row, after the area's findings, and the clean row counts it (O15, O49):

```text
  ✓ dependencies  clean          1 lockfile · 63 packages · 1 allowed
    · uv.lock  allowed  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6 · until 2027-01-31 (#412) · no template input
```

An honoured output owner stands as a `·` row under the code row, after the area's findings, and the clean row counts it (O15):

```text
  ✓ code          clean          2 rules · 41 Python files · 1 allowed
    · src/shop/cli.py:3  allowed  output owner · the command line owns its output
```

Live line while the secrets scan runs (stderr, terminal only, replaced by the row below it):

```text
  ⠋ secrets       gitleaks       412 commits · 3s
```

Built: one line per scanner, this frame, the area and the scanner, dim, leaving the window's last column free (a window of one or two columns, or a terminal that draws `…` two cells wide, is not served), on a terminal whose window is known; each is erased before the next line prints, so the area's row takes its place. The moving frame and the fact (`412 commits · 3s`) stay the target.

With `-v`, under each area that ran a scanner, after its findings and notes: one `·` row per scanner run with its exit, and that scanner's stderr behind the marker, dim and never cut. A text gitleaks does not clear stands as the count of its lines; a failing gitleaks' lines are counted unread. A scanner's report never stands there.

Built:

```text
$ quality check -v
  ✓ secrets       clean          2 commits
    · gitleaks  exited  0
      | 10:40PM INF Unknown SCM platform. Use --platform to include links in findings. host=
      | 10:40PM INF 2 commits scanned.
      | 10:40PM INF scanned ~134 bytes (134 bytes) in 116ms
      | 10:40PM INF no leaks found
  ✓ workflows     clean          1 workflow · offline audits only
    · actionlint  exited  0
    · zizmor  exited  0
      |  INFO zizmor: 🌈 zizmor v1.31.1
      |  INFO audit: zizmor: 🌈 completed .github/workflows/w.yml
  ✓ dependencies  clean          1 lockfile · 2 packages
    · osv-scanner  exited  0
      | Starting filesystem walk for root: /
      | Scanned uv.lock file and found 2 packages
      | End status: 0 dirs visited, 1 inodes visited, 1 Extract calls, 121.166µs elapsed, 121.226µs wall time
      | Loaded PyPI local db from <scratch>/osv-scalibr/PyPI/all.zip
```

A scanner that refused, with a stderr gitleaks does not clear:

```text
  ✗ workflows     not checked    zizmor exited 1, neither 'clean' nor 'findings' · 8 stderr lines withheld · not cleared by gitleaks
    · zizmor  exited  1 · 8 stderr lines withheld · not cleared by gitleaks
```

### 2. `quality check`, findings in several areas

Exit 1. stdout. Two findings in a row with the same fix: the fix stands once, under the last. The findings of an area stand in run order (O14).

```text
$ quality check
  · release       v0.9.0         pinned in .github/workflows/ci.yml
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

No rule name stands in a row (`compose-port`, `env-file`); the event says it in words. The rule name appears only in a `fix:` line that asks for an `[[….allow]]` entry. No `next:` follows this verdict: the `fix:` lines carry the way. The events, facts and fixes of the rows are O12, O14, O15, O16, O18 and O19 for `files`, O21 and O22 for `debt`, O23 and O25 for `imports`, O28, O29, O32 and O33 for `secrets`, O36, O37, O38 and O39 for `workflows` and O41, O42, O43, O44, O45 and O46 for `dependencies`: one event word per kind of finding, shared across rules (`report.FindingEvent`); the env fix names the file for `.gitignore` only where it is its own shell word, else `it`; and the findings of an area in run order. Paths and events align per area by display width, in every stream: an East Asian wide character takes two cells and a combining mark none; spacing marks, Hangul conjoining letters, skin-tone modifiers and emoji sequences are not measured.

The fix of a security finding names the line its noqa must stand on, so each stands under its row, and a code and ruff's message are the fact:

```text
  ! code          2 findings
    ! src/shop/parse.py:12  insecure    S307 Use of possibly insecure function; consider using `ast.literal_eval`
      fix: remove the cause, or where it is deliberate end line 12 with `# noqa: S307 - <reason>`, a reason with a letter and no comma
    ! src/shop/parse.py:40  print call  T201 `print` found
      fix: write through the program's one output owner; in that owner's file, declare it on a line of its own with `# ruff: noqa: T201 - <reason>`, a reason with a letter and no comma
```

### 3. `quality check`, no git work tree

No release, no row: only the pair. Exit 2. stderr.

```text
~/Downloads $ quality check
error: not inside a git work tree · ~/Downloads
next: run quality check inside a repository
```

### 4. `quality check`, an area cannot check

The other areas still judge and their findings stay. Because an answer is missing, the run ends with the pair and exit 2, not with a verdict. Rows: stdout; `error:`/`next:`: stderr.

```text
$ quality check
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  ✓ debt          clean          ceiling held · 31 entries, 2 clone pairs
  ✓ imports       clean          1 contract
  ✗ secrets       not checked    mise could not locate gitleaks 8.30.1
  ✓ workflows     clean          5 workflows · offline audits only
  ! dependencies  1 finding
    ! uv.lock  affected  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6
      fix: uv lock --upgrade-package jinja2
  ✓ code          clean          2 rules · 41 Python files
error: 1 of 7 areas could not check · 1 finding in the others
next: mise --cd ~/.local/share/uv/tools/overnightworks-quality/lib/python3.14/site-packages/quality/mise install --locked
```

The `next:` form is decision 11. A scanner that reports another version than the release pins: the same row, another fact.

```text
  ✗ secrets       not checked    gitleaks 8.28.0 found, the release pins 8.30.1
```

Built: a scanner's stderr reaches a fact only once gitleaks has cleared all of it ([rules.md](rules.md)): else the fact ends in `· N stderr lines withheld · not cleared by gitleaks`, also when gitleaks cannot answer. No refusal quotes gitleaks' own stderr. The exit and the verdict do not change.

```text
  ✗ workflows     not checked    actionlint exited 3, neither 'clean' nor 'findings' · 2 stderr lines withheld · not cleared by gitleaks
```

Several areas can fail, and an area can fail for more than one reason. A reason that several rules of one area share stands once; distinct reasons make the row name their count and stand on `✗` lines below it, and findings of an area that also cannot check stay under its `✗` row and are counted in the error line. `next:` names each distinct step of the run's refusals once, in row order: one step stands on the `next:` line, several each below a bare `next:`, and the general instruction is the last line when a refusal carries no step and the only one when none does. A fact of a few words says what is missing (`mise could not locate gitleaks 8.30.1`, true for every non-zero answer of mise). Without mise the first step is `install mise: <page>` and the second the install command. Alternative commands stand under a label ending `, one of:`, each choice's label on a line and its command one level deeper.

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

A config refusal names its fact on each `✗` row it reaches and shows the example to copy once under `next:` (O10): the label `write it in quality/config.toml like this:` and the example's lines one level deeper, each whole on its own line and never shortened, so the lines copied as printed are valid TOML. The fact names an entry by its table and 1-based position, a repeated value as a TOML string, never as a Python repr (O11).

```text
  ✗ files         not checked    quality/config.toml [[size.generated]] entry 1 needs job as one of [gate] mandatory_jobs, the job whose generator check proves the file
  ✗ debt          not checked    quality/config.toml [[size.generated]] entry 1 needs job as one of [gate] mandatory_jobs, the job whose generator check proves the file
  ✗ secrets       not checked    quality/config.toml [[size.generated]] entry 1 needs job as one of [gate] mandatory_jobs, the job whose generator check proves the file
  ✗ dependencies  not checked    quality/config.toml [[size.generated]] entry 1 needs job as one of [gate] mandatory_jobs, the job whose generator check proves the file
error: 4 of 7 areas could not check
next: write it in quality/config.toml like this:
    [[size.generated]]
    path = "frontend/src/lib/api/types.ts"
    job = "Backend (Python 3.14)"
    item = "overnightworks/songmaker#1784"
    date = 2026-10-30
    reason = "written by scripts/generate_types.py, proven by its --check"
```

### 5. The debt ceiling: `check debt`, `--fix --dry-run`, `--fix`

The debt ceiling is `quality/baseline.toml`. Where code shrank it can fall; `--fix` lowers it and never raises it.

`quality check debt`: only the debt area. Exit 1. stdout.

```text
$ quality check debt
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ! debt          3 findings
    ! src/shop/cart.py:63  can fall  function total has CCN 17 · ceiling 19
    ! src/shop/orders.py   can fall  file has 812 lines · ceiling 826
    ! src/shop/legacy.py   can fall  file is gone · ceiling 802 lines
      fix: quality check --fix
! 3 findings in 1 area · 2s
```

`--fix` never raises: what grew stays a finding. Exit 1. stdout.

```text
$ quality check debt --fix
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ! debt          1 finding      nothing lowered
    ! src/shop/parse.py:88  grew  function read_rows has 71 lines · ceiling 64
      fix: bring it back to 64 lines or below; a raise is a hand edit and a declared loosening
! 1 finding in 1 area · 2s
```

Dry run over all areas: the ceiling shows what would fall, nothing is written. Exit 1 while anything can be lowered. stdout.

```text
$ quality check --fix --dry-run
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  ~ debt          3 to lower     quality/baseline.toml
    ~ src/shop/cart.py:63  lower  function total CCN 19 → 17
    ~ src/shop/orders.py   lower  file 826 → 812 lines
    ~ src/shop/legacy.py   drop   file is gone · was 802 lines
  ✓ imports       clean          1 contract
  ✓ secrets       clean          412 commits
  ✓ workflows     clean          5 workflows · offline audits only
  ✓ dependencies  clean          1 lockfile · 63 packages
  ✓ code          clean          2 rules · 41 Python files
~ dry run · 3 ceiling entries would fall · nothing written · 12s
```

Written. Exit 0. stdout.

```text
$ quality check --fix
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  ✓ debt          lowered        3 entries · quality/baseline.toml
    ✓ src/shop/cart.py:63  lowered  function total CCN 19 → 17
    ✓ src/shop/orders.py   lowered  file 826 → 812 lines
    ✓ src/shop/legacy.py   dropped  file is gone
  ✓ imports       clean          1 contract
  ✓ secrets       clean          412 commits
  ✓ workflows     clean          5 workflows · offline audits only
  ✓ dependencies  clean          1 lockfile · 63 packages
  ✓ code          clean          2 rules · 41 Python files
✓ clean · ceiling lowered by 3 entries · 7 areas · 12s
```

Ruled by the operator: a fall below a metric's threshold drops the entry (`dropped  file 805 → 790 lines · debt from 800 lines`).

That is why the examples fall to 812 lines and CCN 17, above their thresholds.

A file git renamed into the index since the ceiling's last commit takes its entries and pairs with it (`moved`); what the ceiling held under the new path before the move goes (`dropped`, `left from before the move`), and what grew stays a finding. Exit 0 when nothing else is open, stdout.

```text
$ git mv src/shop/orders.py src/shop/order_book.py
$ quality check debt --fix
  ✓ debt          moved          1 entry · quality/baseline.toml
    ✓ src/shop/order_book.py  moved  file 826 lines from src/shop/orders.py
✓ clean · ceiling moved 1 entry · 1 area · 2s
```

The stale-target case, an old entry beside a file the ceiling still holds under the new path. Exit 1.

```text
$ quality check debt --fix
  ! debt          1 finding      1 entry lowered · quality/baseline.toml
    ! src/new.py:1  grew     function f has 150 lines · ceiling 100
      fix: bring it back to 100 lines or below; a raise is a hand edit and a declared loosening
    ✓ src/new.py    moved    function f 100 lines from src/old.py
    ✓ src/new.py    dropped  function f 200 lines · left from before the move
! 1 finding in 1 area · ceiling lowered by 1 entry · 2s
```

Built: every frame, without the release row (C10). `--fix` takes no `--staged` and needs `debt` among the named areas, or none named; `--dry-run` needs `--fix`. Beside the frames: a gone or generated drop names the value it held; the debt row reads `N to lower`, `N to move` or `to record`; the dry run exits 1 while the fix would change the file, 0 when not; `~` is cyan at a terminal.

No ceiling yet: first a note, then the first recording. Both exit 0. stdout.

```text
$ quality check debt
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  · debt          not recorded   31 entries over the thresholds · measured only
      fix: quality check --fix
✓ clean · 1 area · debt measured, not enforced · 2s
$ quality check debt --fix
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ debt          recorded       31 entries, 2 clone pairs · quality/baseline.toml is new
✓ clean · ceiling recorded · 1 area · 2s
```

A ceiling file without a `[clones]` table is the partly recorded form. The row names the pairs a recording would add (O21).

```text
  · debt          not recorded   2 clone pairs · partly measured only
      fix: quality check --fix
✓ clean · 7 areas · debt partly measured, not enforced · 2s
```

Ruled by the operator: without an import-linter config the imports row is a grey note, not counted among the clean areas, and the verdict names it.

The verdict names it before the duration, after debt's note when both stand.

```text
  · imports       not set up     no import-linter config
✓ clean · 7 areas · imports not enforced · 2s
```

### 6. `quality check secrets --staged`: the commit hook

Checks exactly what the next commit records. A secret's value never appears, also not with `-v`. The sha256 stands whole on its own line, because you copy it into the entry. An unused allowance is not reported as a finding by the hook.

Clean, one allowed finding as a note. Exit 0. stdout.

```text
$ quality check secrets --staged
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ secrets       clean          staged change · 4 files · 1 allowed
    · tests/fixtures/token.txt:1  allowed  generic-api-key · a test fixture, not a real key
✓ clean · 1 area · <1s
```

A finding: the commit stays out. Exit 1. stdout.

```text
$ quality check secrets --staged
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ! secrets       1 finding      staged change · 4 files
    ! src/shop/settings.py:12  secret  gitleaks rule generic-api-key · value not shown
      fix: rotate it; if it is no secret, allow it in quality/config.toml with a reason:
        [[secrets.allow]]
        rule = "generic-api-key"
        file = "src/shop/settings.py"
        secret_sha256 = "3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f3b1f"
! 1 finding in 1 area · <1s
```

Built: both frames without the release row (C10); `N files` counts the paths the staged diff lists; a refused run is the `✗` row and the pair, its rerun `next:` names this command; no unused row, the history scan keeps it; `-v` stands under the row.

The entry is the four lines a reader takes, whole and never shortened; the person adds `reason` (O31). It holds no `sha256`, a key no reader takes.

`--staged` with another area. Exit 2. stderr.

```text
$ quality check debt --staged
error: --staged checks only secrets · nothing checked
next: quality check secrets --staged
```

Built: `--staged` needs exactly `secrets`; no area counts as all.

### 7. `quality verdict` in CI

The Actions log is no terminal: lines come without colour, as in a pipe, and the run summary gets the same lines. Rows and verdict: stdout; `next:`: stderr.

Jobs stand one by one while one fails. Exit 1.

```text
Run quality verdict
  · release       v0.9.0         this gate run
  · base          main 4e1a9c2   head 5d1e0b7
  ✓ lint          success
  ! test          failure        attempt 2
  ✓ static        success
  ✓ sonar         success
  ✓ loosenings    none
! red · 1 of 4 mandatory jobs failed
```

Red only because of a declared loosening: one line sums up the green jobs so the loosening stands on top, and the one next command is named. Exit 1.

```text
Run quality verdict
  · release       v0.9.0         this gate run
  · base          main 4e1a9c2   head 5d1e0b7
  ✓ jobs          success        4 mandatory jobs
  ! loosenings    1 declared
    ! src/shop/orders.py  ceiling raised  766 → 790 lines · #231 of 2026-10-28
! red · only declared loosenings · only the operator merges it
next: quality merge overnightworks/shop#232
```

An undeclared loosening looks the same, with `undeclared` as the fact and without `next:`: the door is closed then.

### 8. `quality merge owner/repo#n`: the operator's door

First everything you approve, then the prompt (stderr), then the three steps as rows. At the end the gate is on again, or the run says loudly that it is not. The prompt names the number; the word is `yes`, in lower case. The last row before the prompt is the line the merge commit ends with, `work item` and its number or `no item` and its kind.

Ruled by the operator: a body the door refuses for its line stops at `! landing  refused` under the pull request's row instead, the same row as the last one before the question.

Approved and merged. Exit 0. Rows: stdout; the prompt line: stderr.

```text
$ quality merge overnightworks/shop#232
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  · #232          open           head 5d1e0b7 · into main
  ✓ checks        ok             only quality / verdict is red
  ! loosenings    1 declared
    ! src/shop/orders.py  ceiling raised  766 → 790 lines · #231 of 2026-10-28
        reason: the order split lands with #233
  · merge         merge commit   the only method main allows
  · landing       work item      #231
Switch quality_gate off, merge #232, switch it on? Type yes: yes
  ✓ quality_gate  off            overnightworks/shop
  ✓ #232          merged         9a1b2c3 · merge commit
  ✓ quality_gate  on
  ✓ log comment   posted         on #232
✓ merged #232 · quality_gate on again · 14s
```

Refused, another check is red: nothing changed. Exit 1. Rows: stdout; pair: stderr.

```text
$ quality merge overnightworks/shop#232
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  · #232          open           head 5d1e0b7 · into main
  ! checks        red            test (failure) beside quality / verdict
error: not merged · the operator's merge lands only declared loosenings · nothing changed
next: gh pr checks 232 --repo overnightworks/shop
```

Dry run. Exit 0. stdout.

```text
$ quality merge overnightworks/shop#232 --dry-run
  …
~ dry run · would switch quality_gate off, merge #232, switch it on · nothing changed
```

(`…` stands for the same rows as above, up to "landing".)

Broken: the gate could not be switched on again. Exit 2. Rows: stdout; pair: stderr. The restore command stands on its own line and is never shortened.

```text
$ quality merge overnightworks/shop#232
  …
  ✓ quality_gate  off            overnightworks/shop
  ✓ #232          merged         9a1b2c3 · merge commit
  ✗ quality_gate  not restored   GitHub answered HTTP 502
error: quality_gate MAY STILL BE OFF on overnightworks/shop · #232 is merged
next:
    echo '{"properties":[{"property_name":"quality_gate","value":"true"}]}' | gh api -X PATCH repos/overnightworks/shop/properties/values --input -
```

Built (C8a1): the rows before the prompt and the refused frame, without the release row (C10). A declaration reads `what · item of date` with `reason:` below until C9's typed loosenings; a red Sonar adds `! sonar  N legacy findings  on <branch>` and the error names declared loosenings and legacy findings (decision 4); the method row reads `allowed on <base>`; a draft is `! #N  draft` with `next: gh pr ready N --repo R`. Built (C8a2): the landing row last and the prompt on stderr with the picture's words. Anything but `yes` ended with Enter, the end of input included, is `error: not confirmed · nothing changed` with `next: quality merge <owner/repo>#<n>`, exit 1; Ctrl-C at the prompt writes nothing, the shell ends the line, exit 130; a closed stderr takes the prompt and the pairs, and nothing moves to stdout.

Built (C8b): after `yes` the rows, the closing line and the broken pair are drawn as above, the restore command read from the request the door sent. The merged row names the method, never a sha; the broken case also shows the `log comment` row the door posts; a merge whose answer was lost stands after `quality_gate  on`, once read back; after Ctrl-C the next row starts on a line of its own; the duration runs from the answer. Before the question a stdout that refuses a row stops the door: `error: stdout refused a row · nothing changed`, exit 2.

### 9. `quality report`

The two markdown tables are an artifact and go alone to stdout. The row per repository and the verdict go to stderr, so `quality report > fleet.md` writes a clean file.

All measured. Exit 0. Tables: stdout; every other line: stderr.

```text
$ quality report
  · fleet         14 listed      quality/fleet.toml
  ✓ quality       measured       8c8121b · 3s
  ✓ aco           measured       2f4e9a0 · 5s
  ✓ songmaker     measured       71c3d8e · 11s
  …
**Fleet report, 2026-10-28, quality v0.9.0 — functions and CCN: Python only**

| Repo | Commit | Languages | Lines prod / test | … |
|---|---|---|---|---|
| …

**Repo × rule, newest release v0.9.0**
| …
✓ 14 of 14 repositories measured · 1m12s
```

(`…` after the third repository stands for eleven more rows.)

Into a file, a repository not measurable. Exit 2. All lines shown: stderr.

```text
$ quality report > fleet.md
  · fleet         14 listed      quality/fleet.toml
  ✓ quality       measured       8c8121b · 3s
  ✗ hopin         not measured   clone timed out after 300s
  …
error: 1 of 14 repositories not measured · its row says so in fleet.md
next: quality report
```

### 10. The pinned release is not there yet

`check` and `merge` run with the release the gate pins.

Fetched now (decision 8 A): quality fetches the release through uv and says so in the release row; exit as usual. The first line is live only (stderr, terminal), the rest is stdout.

```text
$ quality check
  ⠋ release       fetching       v0.9.0 · 4s
  · release       v0.9.0         fetched now · pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  …
```

Offline. Exit 2. stderr.

```text
$ quality check
error: cannot fetch quality v0.9.0, which the gate pins · github.com did not answer
next: quality check, once online
```

No workflow pins the gate. Exit 2. stderr.

```text
$ quality check
error: no workflow here calls quality's gate.yml, so no release is pinned
next: add the gate as quality's docs/gate.md describes
```

The pin is older than `check`. Exit 2. stderr.

```text
$ quality check
error: the gate pins quality v0.8.0, which has no quality check
next: raise the gate pin to v0.9.0 or later
```

In quality itself the checkout judges itself. stdout.

```text
~/git/quality $ quality check
  · release       this checkout  0.9.1.dev4+g8c8121b · quality judges itself
  …
```

`quality --version`: one line, stdout.

```text
$ quality --version
quality 0.9.0 · this repository's gate pins v0.9.0
~/Downloads $ quality --version
quality 0.9.0 · not inside a repository
```

### 11. `quality --help` and `quality check --help`

The command tree, wrapped at 80 columns; `verdict` stands apart under "run by the gate". The line per area in `check --help` is part of the picture. Both: stdout, exit 0.

```text
$ quality --help
usage: quality COMMAND [-v]

Commands:
  check [AREA ...] [--staged] [--fix] [--dry-run]
      Check this repository against the fleet's rules, with the quality
      release its gate pins.
  merge OWNER/REPO#N [--dry-run]
      Operator only: merge one pull request whose gate is red only for
      loosenings he ruled, after he types yes.
  report
      Measure every fleet repository: size, debt, the gate release it pins.

Run by the gate, not by people:
  verdict
      Decide whether this pull request may merge.

Options:
  --version   the installed version and the release this repository's gate pins
  -h, --help  show this help

Every command takes -v: the raw scanner output, never a secret.
Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.
```

```text
$ quality check --help
usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]

Check this repository against the fleet's rules, with the quality release
its gate pins.

Areas (default: all):
  files         what the repository tracks: layout, tests, env files,
                compose ports, the Python floor
  debt          size and near copies against the debt ceiling
                (quality/baseline.toml)
  imports       the import contracts
  secrets       secrets in the committed history
  workflows     GitHub workflows: lint and security
  dependencies  known advisories in the lockfiles
  code          Python source: security findings and print calls

Options:
  --staged      secrets in exactly what the next commit records; the commit
                hook runs: quality check secrets --staged
  --fix         lower the debt ceiling (quality/baseline.toml) where code
                shrank, or record it the first time; never raises it
  --dry-run     with --fix: show what would change, write nothing
  -v            show the raw scanner output, never a secret
  -h, --help    show this help

Exit codes: 0 clean, 1 findings, 2 could not check, 130 interrupted.
```

Built: `usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]`, the `Areas` block from `Area` (`rules.py`) and the options that exist; `--staged`, `--fix` and `--dry-run` are built.

Wrong invocation: the pair instead of argparse text. Exit 2. stderr.

```text
$ quality check docs
error: no area docs · areas: files, debt, imports, secrets, workflows, dependencies, code
next: quality check --help
$ quality check --dry-run
error: --dry-run needs --fix · nothing checked
next: quality check --fix --dry-run
```

```text
$ quality hgyiene
error: no command hgyiene · commands: check, merge, report, verdict, sonar
next: quality --help
$ quality
error: no command · commands: check, merge, report, verdict, sonar
next: quality --help
$ quality check --nonsense
error: unknown argument · --nonsense
next: quality check --help
```

The pair stands for a first word that names no command, for none at all and for what a command does not take; what only the parser can word stands in the pair as its own sentence.

Built: the first word that names no area stands in the pair; an option may stand between areas; `--dry-run` without `--fix` is the pair drawn above, and a refused `--fix` beside `--dry-run` keeps it in `next:`.

## Areas

Ruled by the operator: seven areas, `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies` and `code`; the `code` line of `quality check --help` as case 11 draws it; and the `secrets` line `secrets in the committed history`.

The areas replace the rule names in the verdict; `quality check --help` says which rules an area holds. `code` holds `python-security` and `python-print`. A run that names areas judges and prints only those, in this order, each once.

## Decisions

Ruled by the operator:

1. Clean areas: one row per area, six fixed rows (now seven), so you see what was checked and its extent.
2. The release row always, also in the commit hook, as a grey note: proof that locally the same release judges as in the gate.
3. Length of a fact: at the terminal shortened to the window width with `…`, never in a pipe, file or CI log. Commands, sha256 and entries to copy stand on their own line, never shortened. Each kind of finding has a fixed event word and a fact of a few words.
4. Findings under their area with a `fix:` line: indented under the area row, columns aligned per area; `fix:` under the last of consecutive findings with the same fix. A finding without a line shows only the path.
5. `next:` after a red verdict: a `!` verdict may carry a `next:` only when exactly one command is the answer. For `check` findings the `fix:` lines carry the way.
6. Exit 2 beats 1: exit 2 with the pair; the findings of the other areas stand anyway (case 4).
7. A ceiling that can fall stays a finding: `!` and exit 1, like today's "stale". The dry run `--fix --dry-run` exits 1 while anything can be lowered. Without a ceiling file debt is a grey note with exit 0, like today's "report only".
8. The release is missing: A, quality fetches exactly the pin through uv and says so in the release row; offline is exit 2. Not B (exit 2 with a `uvx --from git+…` command).
9. No pin, old pin, quality itself: no workflow calls `gate.yml` is exit 2; a pin older than the release with `check` is exit 2 with "raise the gate pin"; in quality itself the checkout judges.
10. `--staged` only with `secrets`: any other area with `--staged` is exit 2 with `next: quality check secrets --staged`.
11. The command that installs a scanner: `mise --cd <quality's installation>/quality/mise install --locked`, the release's own manifest and lock; the path is quality's installation, shown as the example shows it.
12. Rule names only in an allowance fix: a `fix:` line that asks for an `[[….allow]]` entry names the rule, because the entry needs it. A config refusal's fact may name the values a config key takes, rule ids included, and the example under `next:` may hold the entry's rule line, because the entry needs it.
13. The CI log stays without colour: no special rule beyond "no TTY, no colour".
14. `merge` no longer names aco: the "For the head: aco release …" ending goes; that step belongs in the head's instructions.
15. `report`: rows and verdict on stderr; the tables are an artifact on stdout, so `quality report > fleet.md` writes only the tables.
16. `✗` means "could not check or do": a finding is always `!` with exit 1; `✗` is always exit 2.
17. Duration in the verdict (`12s`, `1m12s`, `<1s`); `check --help` explains each area in one line.
