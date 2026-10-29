# Output picture

This is the target picture of quality's command-line output that the slices of #228 build; each landing aligns it with what was built, and until then `docs/commands.md` describes what runs today. Reader: the builder of those slices. Paths, repositories and ids in the examples are invented. All terminal lines are English.

## Principle

- One output owner prints; rules return findings and never print.
- Every row reads `glyph · subject · event · fact`. A finding sits indented under its area row with `path:line` right after the glyph; one `fix:` line follows the last of consecutive findings that share the fix.
- Every run ends with exactly one closing verdict line, or with an `error:`/`next:` pair when it could not answer.
- stdout carries rows and the verdict. stderr carries the live line, the input prompt and `error:`/`next:`. So `quality check | less` shows every finding and an error stays visible.
- With no TTY, with `NO_COLOR` or with `TERM=dumb` the same words print without colour, without the live line and never shortened.
- Repository text is masked: control characters as `\xNN`, and U+2028, U+2029 and format characters as `\uNNNN`.
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

! 1 finding in 1 area · 5 areas clean · 9s
```

Columns: glyph (state at a glance), subject (the area, or `path:line` of the finding), event (one typed word: what is the case), fact (values, a few words), then `fix:` (what you do).

The same row for the three readers, and foreign text:

```text
  ! secrets       1 finding      staged change · 4 files
```

(terminal: coloured per stream; pipe, file, agent, CI log: as above, uncoloured)

```text
    ! src/\u202egnp.py  tracked  a file without a suffix that is no executable
```

## Cases

Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted. Each block names its stream; an `error:`/`next:` pair and a prompt are stderr, everything else stdout, unless the block says otherwise.

### 1. `quality check`, all clean

The first row names the release the gate pins. Every area gets a row, also when clean. Exit 0. Rows and verdict: stdout.

```text
$ quality check
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ✓ files         clean          10 rules · 214 files
  ✓ debt          clean          ceiling held · 31 entries, 2 clone pairs
  ✓ imports       clean          1 contract
  ✓ secrets       clean          tree and 412 commits
  ✓ workflows     clean          5 workflows
  ✓ dependencies  clean          1 lockfile · 63 packages
  ✓ code          clean          2 rules · 41 Python files
✓ clean · 7 areas · 12s
```

Live line while the secrets scan runs (stderr, terminal only, replaced by the row below it):

```text
  ⠋ secrets       gitleaks       412 commits · 3s
```

### 2. `quality check`, findings in several areas

Exit 1. stdout. Two findings in a row with the same fix: the fix stands once, under the last.

```text
$ quality check
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ! files         3 findings
    ! compose.yaml  published port  service web: 8080:80 beyond loopback
    ! compose.yaml  published port  service db: 5432 beyond loopback
      fix: bind it to loopback, for example "127.0.0.1:HOST:CONTAINER"
    ! .env          tracked         an env file carries configuration, often secrets
      fix: git --literal-pathspecs rm --cached -- .env, then add .env to .gitignore
  ! debt          1 finding
    ! src/shop/parse.py:88  grew  function read_rows has 71 lines · ceiling 64
      fix: bring it back to 64 lines or below
  ✓ imports       clean          1 contract
  ✓ secrets       clean          tree and 412 commits
  ✓ workflows     clean          5 workflows
  ! dependencies  1 finding
    ! uv.lock  affected  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6
      fix: uv lock --upgrade-package jinja2
  ✓ code          clean          2 rules · 41 Python files
! 5 findings in 3 areas · 4 areas clean · 14s
```

No rule name stands in a row (`compose-port`, `env-file`); the event says it in words. The rule name appears only in a `fix:` line that asks for an `[[….allow]]` entry. No `next:` follows this verdict: the `fix:` lines carry the way.

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
  ✗ secrets       not checked    gitleaks is not installed
  ✓ workflows     clean          5 workflows
  ! dependencies  1 finding
    ! uv.lock  affected  jinja2 3.1.4 · GHSA-q8x2-4m7c-9v3p · fixed in 3.1.6
      fix: uv lock --upgrade-package jinja2
  ✓ code          clean          2 rules · 41 Python files
error: 1 of 7 areas could not check · 1 finding in the others
next: mise install gitleaks@8.30.1
```

The `next:` form is open (decision 11). Wrong version on the PATH: the same row, another fact.

```text
  ✗ secrets       not checked    gitleaks 8.28.0 found, the release pins 8.30.1
```

### 5. The debt ceiling: `check debt`, `--fix --dry-run`, `--fix`

The debt ceiling is `quality/baseline.toml`. Where code shrank it can fall; `--fix` lowers it and never raises it.

`quality check debt`: only the debt area. Exit 1. stdout.

```text
$ quality check debt
  · release       v0.9.0         pinned in .github/workflows/ci.yml
  ! debt          3 findings
    ! src/shop/orders.py   can fall  file has 742 lines · ceiling 766
    ! src/shop/cart.py:63  can fall  function total has CCN 9 · ceiling 12
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
    ~ src/shop/orders.py   lower  file 766 → 742 lines
    ~ src/shop/cart.py:63  lower  function total CCN 12 → 9
    ~ src/shop/legacy.py   drop   file is gone · was 802 lines
  ✓ imports       clean          1 contract
  ✓ secrets       clean          tree and 412 commits
  ✓ workflows     clean          5 workflows
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
    ✓ src/shop/orders.py   lowered  file 766 → 742 lines
    ✓ src/shop/cart.py:63  lowered  function total CCN 12 → 9
    ✓ src/shop/legacy.py   dropped  file is gone
  ✓ imports       clean          1 contract
  ✓ secrets       clean          tree and 412 commits
  ✓ workflows     clean          5 workflows
  ✓ dependencies  clean          1 lockfile · 63 packages
  ✓ code          clean          2 rules · 41 Python files
✓ clean · ceiling lowered by 3 entries · 7 areas · 12s
```

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
        [[secrets.allow]] rule = "generic-api-key", file = "src/shop/settings.py",
        sha256 = "3b1f0c9a6e2d47b8a5c91f03d6e8b2a47c5d9e1f0a3b6c8d2e4f7a9b1c3d5e7f"
! 1 finding in 1 area · <1s
```

`--staged` with another area. Exit 2. stderr.

```text
$ quality check debt --staged
error: --staged checks only secrets · nothing checked
next: quality check secrets --staged
```

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

First everything you approve, then the prompt (stderr), then the three steps as rows. At the end the gate is on again, or the run says loudly that it is not. The prompt names the number; `ja` stays the word.

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
Switch quality_gate off, merge #232, switch it on? Type ja: ja
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

(`…` stands for the same rows as above, up to "merge".)

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

### 9. `quality report`

The two markdown tables are an artifact and go alone to stdout. The row per repository and the verdict go to stderr, so `quality report > fleet.md` writes a clean file.

All measured. Exit 0. Tables: stdout; every other line: stderr.

```text
$ quality report
  · fleet         13 listed      quality/fleet.toml · 4 private, never measured
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
✓ 9 of 9 public repositories measured · 4 private not measured · 1m12s
```

(`…` after the third repository stands for six more rows.)

Into a file, a repository not measurable. Exit 2. All lines shown: stderr.

```text
$ quality report > fleet.md
  · fleet         13 listed      quality/fleet.toml · 4 private, never measured
  ✓ quality       measured       8c8121b · 3s
  ✗ hopin         not measured   clone timed out after 300s
  …
error: 1 of 9 public repositories not measured · its row says so in fleet.md
next: quality report
```

### 10. The pinned release is not there yet

`check` and `merge` run with the release the gate pins (#172).

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

The tree from #228, wrapped at 80 columns; `verdict` stands apart under "run by the gate". The line per area in `check --help` is part of the picture. Both: stdout, exit 0.

```text
$ quality --help
usage: quality COMMAND [-v]

Commands:
  check [AREA ...] [--staged] [--fix] [--dry-run]
      Check this repository against the fleet's rules, with the quality
      release its gate pins.
  merge OWNER/REPO#N [--dry-run]
      Operator only: merge one pull request whose gate is red only for
      loosenings he ruled, after he types ja.
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
  secrets       secrets in the tree and its history
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

Wrong invocation: the pair instead of argparse text. Exit 2. stderr.

```text
$ quality check docs
error: no area docs · areas: files, debt, imports, secrets, workflows, dependencies, code
next: quality check --help
$ quality check --dry-run
error: --dry-run needs --fix · nothing checked
next: quality check --fix --dry-run
```

## Areas

Seven, as ruled: `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies`, `code`. The areas replace the rule names in the verdict; `quality check --help` says which rules an area holds. `code` holds `python-security` and `python-print`.

## Decisions

Ruled by the operator on 28.10.2026 („ok“). Those marked * add to or change a rule of #228.

1. Clean areas: one row per area, six fixed rows (now seven), so you see what was checked and its extent.
2. The release row always, also in the commit hook, as a grey note: proof that locally the same release judges as in the gate.
3. * Length of a fact (adds to rule 7): at the terminal shortened to the window width with `…`, never in a pipe, file or CI log. Commands, sha256 and entries to copy stand on their own line, never shortened. Each kind of finding has a fixed event word and a fact of a few words.
4. * Findings under their area with a `fix:` line (adds to rule 7): indented under the area row, columns aligned per area; `fix:` under the last of consecutive findings with the same fix. A finding without a line shows only the path.
5. * `next:` after a red verdict (changes rule 7): a `!` verdict may carry a `next:` only when exactly one command is the answer. For `check` findings the `fix:` lines carry the way.
6. * Exit 2 beats 1 (adds to rule 2): exit 2 with the pair; the findings of the other areas stand anyway (case 4).
7. A ceiling that can fall stays a finding: `!` and exit 1, like today's "stale". The dry run `--fix --dry-run` exits 1 while anything can be lowered. Without a ceiling file debt is a grey note with exit 0, like today's "report only".
8. * The release is missing (changes rule 6): A, quality fetches exactly the pin through uv and says so in the release row; offline is exit 2. Not B (exit 2 with a `uvx --from git+…` command).
9. No pin, old pin, quality itself: no workflow calls `gate.yml` is exit 2; a pin older than the release with `check` is exit 2 with "raise the gate pin"; in quality itself the checkout judges.
10. `--staged` only with `secrets`: any other area with `--staged` is exit 2 with `next: quality check secrets --staged`.
11. * The command that installs a scanner (open in rule 4): the picture shows the placeholder with version; the exact command is fixed in the mise slice and must use the release's manifest.
12. Rule names only in an allowance fix: a `fix:` line that asks for an `[[….allow]]` entry names the rule, because the entry needs it.
13. The CI log stays without colour: no special rule beyond "no TTY, no colour".
14. `merge` no longer names aco: the "For the head: aco release …" ending goes; that step belongs in the head's instructions.
15. `report`: rows and verdict on stderr; the tables are an artifact on stdout, so `quality report > fleet.md` writes only the tables.
16. * `✗` means "could not check or do" (adds to rule 7): a finding is always `!` with exit 1; `✗` is always exit 2.
17. Duration in the verdict (`12s`, `1m12s`, `<1s`); `check --help` explains each area in one line.
