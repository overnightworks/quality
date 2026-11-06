# Commands

Reference for the engineer or operator who runs `quality check --fix`, `quality check secrets --staged`, `quality scoreboard`, installs the scanners,
runs `quality emergency-merge`, or reads `quality --help` or a wrong invocation's pair.

## Help and wrong invocations

`quality --help` and `quality check --help` stand in the frame of [the output picture](output.md), case 11, with what is
true today; every other `quality <command> --help` prints that command's arguments; `--version` prints the installed version. Help goes to
stdout with exit `0`, plain and whole in every stream. A wrong invocation runs nothing, prints the `error:`/`next:` pair
on stderr and exits `2`: `no command <word> · commands: …` for a first word that names no command, `no command ·
commands: …` for none, `no area <word> · areas: …` for the first word `quality check` does not know as an area, `unknown argument · <what is left over>` for what the command does not take, and otherwise the
parser's own sentence; `--dry-run needs --fix · nothing checked` for `--dry-run` without `--fix` (`next:` names `quality check --fix --dry-run`); `--staged checks only secrets · nothing checked` for `--staged` with other areas or none (`next:` names `quality check secrets --staged`); `next:` otherwise names the help of the command, of its group for a command inside one, or of the parser
that refused.

## `quality check --fix`

`quality check debt --fix` lowers the debt ceiling (`quality/baseline.toml`) where code shrank, or records it where no
ceiling lies on disk, in the index or in any commit `HEAD` reaches, and then judges the tree against the new file. It
never raises an entry and never adds one: what grew or is new stays a finding, and a raise is a hand edit with a declared
loosening. A file git renamed into the index since the ceiling's last commit takes its entries with it (`docs/rules.md`). An entry whose path the index no longer tracks, whose Python function is gone or whose file is declared generated goes; one only missing from disk stands; a held pair the tree lost goes. A ceiling or `[clones]` table git
holds and the tree lacks refuses with the restore step, and a shallow clone that cannot prove a first recording names
`git fetch --unshallow`. It takes no `--staged` and needs `debt` among the named areas, or no area named; either refusal keeps a `--dry-run` in its `next:`. `quality check --fix --dry-run` makes, judges and renders the same ceiling, refuses where the fix refuses before its write, writes nothing, and exits `1` while the fix would change the file, `0` when not; a refusal only the write meets shows at the fix alone. Exit `0` clean, `1` findings, `2` could not fix.
The rows and rules are in `docs/rules.md`, the frames in `docs/output.md`.

## `quality check secrets --staged`

`quality check secrets --staged` scans exactly what the next commit records: the effective index of the
repository it runs in, compared with `HEAD`, with no path argument. It is meant for a pre-commit hook, which runs it in the
work-tree top or below it. A relative `GIT_INDEX_FILE` is resolved against the working directory, as git does, so `git commit -a`,
`git commit <path>` and a linked worktree scan the index git is committing. `--staged` takes exactly the area `secrets`;
any other set of areas, or none, is `--staged checks only secrets · nothing checked` and exit `2`.

The index is written as a tree (`git write-tree`, only tree objects a commit writes anyway) and read into a scratch
repository inside a 0700 scratch directory. The scratch repository borrows the objects (`objects/info/alternates`),
has an empty work tree and none of the live repository's config or `info/attributes`, so a staged `.gitleaksignore`,
`.gitleaks.toml`, `.gitattributes` with `-diff`, or a `gitleaks:allow` comment changes nothing. gitleaks runs in the same
attribute-free environment as `check`, with quality's config. The allow list is the staged tree's own
`quality/config.toml` (none staged: no allowances). The staged run validates only the `[secrets]` table of that config and ignores other tables
and keys, while `check` (when a selected area needs the config, validating the whole of it) and `verdict` stay strict, so schema growth never blocks a commit.

The output is `check`'s frame: the `secrets` row with `staged change · N files` (the paths the staged diff lists, each added,
changed, deleted or renamed path one file), the finding rows and the allowed notes under it, and the verdict. It prints no unused
allowance: one change cannot tell a stale entry from one the history still needs, so `quality check secrets` reports them.
`-v` is `check`'s: it shows the raw scanner output under the row, never a secret. At a terminal stderr shows `check`'s
live line while gitleaks runs (`docs/rules.md`). A refused staged config names the fact on the row and the example under `next:`,
followed by `stage the fix, then run quality check secrets --staged again`: the refused file is the staged one, so an
edit alone changes nothing.

gitleaks' staged mode sees only added and changed lines, so a known false positive blocks only the commits that touch
its line, and then needs its allowance, as in CI. A key only in the work tree or only in `HEAD` is clean. Exit `2` (never
clean): a scanner missing or at another version, with the command that installs it, git older than 2.41, an unreadable allow list, an index with unmerged
entries, a folder that is not a repository, or a gitleaks run without a report.

## `quality scoreboard`

`quality scoreboard [PATH]` prints two markdown tables, the size of every repository `quality/fleet.toml` lists
(`repositories = [...]`, GitHub `owner/name`s, each listed once), under a heading with the date, the
quality version and that functions and CCN are measured for Python only, then the same rows as a repo × rule matrix. With `GITHUB_STEP_SUMMARY` set, the same text is
appended there. The fleet list lives once, in `quality/fleet.toml`; `.github/workflows/scoreboard.yml` runs it every
Monday at 05:17 UTC and on dispatch; the tables stand in that run's summary. The run only reports: it writes to no repository and needs no secret. A missing, malformed, duplicating
(also across case) or non-`owner/name` `quality/fleet.toml` is `cannot judge`, exit `2`, before any clone; each bad entry is named by
its place (`repositories[3]`), never by its text.

- Each repository is cloned from `https://github.com/<owner>/<name>` with `git -c credential.helper= clone --depth 1 --no-tags`, `GIT_TERMINAL_PROMPT=0` and no
  askpass, into a temporary directory that is removed on every exit, and its default branch tip is measured.
- git runs anonymously: without system, global or `GIT_CONFIG_*` config and with an empty `HOME`, so no configured
  header, helper or `~/.netrc` reaches the host.
- Lines are the physical lines of the tracked files of the `size` rule's languages, the count its 800-line limit uses
  (blank and comment lines included); `Languages` lists each with its lines, production and tests together, largest
  first, ties by name, and `—` for a row with no measured file. Production versus test is the `size` rule's file-name rule, so `scripts/test_inventory.py` counts as test.
  `test:prod` is test lines divided by production lines with two decimals, `—` without production lines.
  The debt columns count files from 800 lines, and Python's functions from 60 lines and with a CCN above 15.
- `Clone pairs prod / test` counts the `clone` rule's pairs (two near-identical functions in two files, at 40 tokens and
  90 %); a pair counts as test only when both sides are tests, so a test that copies production code counts as prod.
- A `.py` file Python cannot parse still has its lines counted; the row's note says how many such files there are, never which.
- The matrix, under a heading naming the newest release tag of `overnightworks/quality` (read by an anonymous
  `git ls-remote --tags`, never from the running code's version), has one `Gate` and one `CodeQL` cell per repository.
  They come from the job-level `uses:` of each tracked `.github/workflows/*.yml|yaml`, parsed as data and never run.
  A pin to a commit shows the release tag at that commit, with `(behind)` when it is not the newest;
  `<7 characters> (no release)` when no tag points there, `<ref> (not a commit)` for a branch or tag name, `no gate`
  or `none` without a caller, and `self` for the repository's own `./` or `$/` file in `overnightworks/quality`. Two
  callers show both, joined by ` · `. The `# v` comment is never read. The owner and repository of a `uses:` match in any
  case, the workflow path exactly.
- After `Gate` and `CodeQL` the matrix has `Mandatory jobs` (the number in `[gate] mandatory_jobs` of `quality/config.toml`,
  `no config` without the file, `—` without a gate), `Sonar legacy` (`report` or `fail` from `[gate] legacy`, `no config`
  without the file, `—` without a gate) and one column per rule whose
  enforcement depends on the release or the repository. The rules every release enforces stand once in the sentence under
  the heading. A rule cell says what the pinned gate does with the rule, never whether the repository passes it:
  `enforced`, `report-only` (`size` without `quality/baseline.toml`, `import-contract` without an import-linter config,
  `clone` on v0.4.0 and, from v0.5.0 on, without a `[clones]` table in
  `quality/baseline.toml`), `not in <tag>` when the pinned release predates the rule, `not released` when only main has
  the rule,
  `no judged project` (`test-layout`, from v0.7.0 on, when the clone has no Python project with a package under `src/` and no frontend project (a `package.json` outside every project's `tests/`)),
  and `—` without a gate. Two pins give the oldest release; a pin that is no release (a commit without a tag, a branch or
  tag name, or unreadable release tags) gives `release unknown`. The table of first releases lives in `rules.py`.
- A workflow file that cannot be read shows `cannot read <file>` in every cell that needs the pin, a malformed
  `quality/config.toml` or `quality/baseline.toml` and an import-linter config that cannot be read or that two files hold
  show `cannot read <file>` (`import-linter config`) in each cell that reads the file, while a cell that does not keeps its value
  (every cell under no gate, a rule cell under a pin before the rule or `release unknown`), unreadable release tags say so in the heading and
  show pins as commits, and each makes the exit `2`. No value, `behind`, `no gate` or `report-only` included, makes the run red.
- Rows are named by the entry as written
  and sorted by it, case-insensitively. A private repository is not measured, because the tables are published and it is not
  anonymously readable: it shows as a `cannot judge` row like any repository that cannot be cloned.
- A repository that cannot be cloned or measured shows `cannot judge: <reason>` (for example `cannot clone (git exited
  128)`), with no local path; every other row still shows, and the exit is `2`. Otherwise the exit is `0`.

## Scanners

gitleaks, actionlint and osv-scanner are not quality's to install: they come through [mise](https://mise.jdx.dev/installing-mise.html)
from the manifest and lock inside quality's installation. `quality/mise/mise.toml` holds the one version of each
scanner, `quality/mise/mise.lock` the sha256 of its release asset (linux x64 only, written by `mise lock --platform
linux-x64`, never by hand). The command that installs them from the lock is

    mise --cd <quality's installation>/quality/mise install --locked

and `check` names it once, on its own line after `next:`, however many rules lack a scanner, the path filled in. A scan finds a scanner by asking
`mise which` in that directory, for exactly the manifest's version, and runs it only when it reports that version. A
scanner on `PATH` is never used. mise reads only the manifest directory and the operator's own user and system configuration: it starts in that
directory under `MISE_CEILING_PATHS` set to the directory above it, so a `mise.toml` or `.miserc.toml` in the scanned
repository (or in the checkout the gate holds the manifest in) takes no part in which binary runs.

`check` and `check secrets --staged` exit `2` for a scanner mise cannot locate, that is at another version, or whose
answer names no executable, and for a machine without mise, which names mise's install page and the command; only the
rule that needs the scanner cannot judge. A repository with no workflow needs no actionlint and one with no lock file
no osv-scanner.

zizmor (the `workflow-security` rule) and ruff (`python-security`, `python-print`) are not mise's. They are pinned
dependencies of quality's package (`pyproject.toml`, hashed in `uv.lock`), so installing quality installs them. quality
runs the executable its own installation recorded, never one on `PATH`. A missing one, or one at another
version, makes its rules `cannot judge` (exit `2`) with the two commands that reinstall quality, one in its checkout and one for a tool install, as alternatives under `next:`; a scanner quality's own
metadata does not pin exactly makes them `cannot judge` as a defect in quality's release.

quality builds the logic and the process itself and never a specialist scanner (language linters and test runners,
gitleaks, osv-scanner, mutation tools); the scanners it uses run pinned underneath the one door (operator ruling
15.10.2026: „ja! ich agree => ein fleet quality“).

## `quality emergency-merge`

`quality emergency-merge <owner/repo> <pr>` is the operator's door past a red gate: it merges a pull request that the
gate keeps red only for legacy Sonar findings or for [declared loosenings](gate.md#declared-loosenings). The steps to
take are in [the runbook](runbook-emergency-merge.md); this is what the command does.

- **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository (checked
  first: exit `2`, nothing changed, the missing right named). Agents never run it; it refuses when stdin is not a
  terminal. This is the agent contract, not a lock: agents on the operator's machine share his `gh` login, so to an
  agent the terminal is a hurdle, and that login could switch `quality_gate` directly. A real lock (agents with a token
  without admin rights) belongs to the machine's setup, not to quality; it is a known residual risk.
- **It refuses unless the door is the only way** (exit `1`, nothing changed): the pull request is open and no draft (a draft is refused before its body or any check is read, `next:` naming `gh pr ready`); no check is still running; every check run
  and commit status other than `quality / sonar` and `quality / verdict` is green; `quality / verdict` has failed; and
  `quality / sonar` is green or has failed (it did not run, or it ended `timed_out`, `cancelled` or anything else: refused).
  The verdict is then judged again by its own decider, with the config, baseline and import-linter configs read at the pull request's
  current merge commit and at base and the Sonar job taken as green: an undeclared loosening, a declaration that matches
  nothing, or a missing or red mandatory job is refused, naming the verdict's lines. When both sides hold a baseline,
  the files git renamed between the two commits count as they do in the verdict, so the door fetches the pull request's
  base commit and merge commit (`git fetch --depth=1` from `https://github.com/<owner>/<repo>.git`) into a temporary bare
  repository that is removed afterwards, also when the command is interrupted. This needs `git` and `gh` on the path.
  git runs with an empty home and no inherited `GIT_*` variable, so `.netrc` and git's own configuration count for
  nothing; `gh` is the only credential source, told where its login lives (`GH_CONFIG_DIR`, else
  `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`), and reaches a private repository through git's credential helper. git
  never prompts and an askpass program is never run. The fetch and the rename list share one limit of 120 seconds each,
  and each of these git commands runs in its own process group, which is killed with it at that limit or when the
  command is interrupted (every other command stays in quality's own group). git's own output is never printed: a fetch or rename list
  that fails could not be checked, and its `✗ loosenings` row says so in the door's words (exit `2`), never an empty rename list.
- **The landing line.** The pull request body carries exactly one `Work-Item:` or `No-Item:` line outside a fenced code block, as the fleet's landings read it: `Work-Item: #<n>` or `Work-Item: <owner>/<repo>#<n>` of this repository, or `No-Item: docs`, `fix` or `deps`. Otherwise its `! landing` row names why and `next:` asks for the one line (exit `1`, nothing changed). The merge commit's message is the body without that line, a blank line, then the line as `Work-Item: #<n>` or `No-Item: <kind>`, the last paragraph, the trailer a landing is read from. The body is the one read with the head.
- **Sonar green: declared loosenings.** The re-derived verdict must hold at least one declared loosening (otherwise
  `re-run the verdict`). SonarCloud is not asked and `SONAR_TOKEN` is not needed.
- **Sonar failed: legacy findings,** next to any declared loosening: `[gate] legacy` is `fail` at the merge
  commit (under `report` the Sonar job cannot be red on legacy findings, and a missing config reads as `report`),
  SonarCloud's analysis of the pull request is of the head being merged and adds no finding (the findings are named
  otherwise), and the default branch has legacy findings. `SONAR_TOKEN` is read from the environment when
  the project needs one: without it the door reads a public project anonymously, with no Authorization header, and an
  anonymous read that SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN`; with a token, a 401 or 403
  says the token was rejected.
- **It shows, then asks:** on stdout a row as each step passes: the pull request (`open`, head, base), the checks, the declared loosenings, each whole with its `item` and `date` and its `reason` on the line below, the legacy count where Sonar failed, the merge method, and last the `landing` row naming the line the merge commit ends with (`work item` and `#<n>`, or `no item` and its kind); then, on stderr, `Switch quality_gate off, merge #<pr>, switch it on? Type yes: `. Only `yes` in lower case, ended with Enter, merges (whitespace around it counts for nothing; `Yes` or `y` change nothing); any other line, or the end of input (Ctrl-D) before Enter, prints `error: not confirmed · nothing changed` and `next:` naming the command again (exit `1`); Ctrl-C at the question ends the run interrupted (exit `130`); nothing has changed in either case. With stderr closed the question and the pairs print nowhere; nothing moves to stdout. A refusal is its `!` row and the `error:`/`next:` pair on stderr (exit `1`), each blocking line of the verdict whole below the `loosenings` row; a step that cannot be judged is its `✗` row and the pair (exit `2`); no terminal on stdin or a malformed name is the pair alone (exit `1`). Every line it prints is escaped as the other doors' lines are, so a `reason` cannot hide anything before `yes` or forge a line.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this takes the fleet's ruleset off it),
  runs `gh pr merge <pr> --merge --match-head-commit <sha> --body-file -`, the message on its stdin (`--squash` where the repository settings or a rule on the
  pull request's base branch exclude a merge commit but allow a squash; when neither is allowed, or the settings or rules
  cannot be read or are malformed (an answer or a page that is no array, a rule that is no object, or whose `parameters` are present but no object), its `✗ merge` row ends the run before the gate is touched (exit `2`); the shown text and the log comment name the
  method), and sets the property back to `true` in every case: merged,
  merge failed, Ctrl-C. A property that cannot be restored is exit `2` with one line naming what to set by hand and the
  merge outcome as far as it is known, printed before any further GitHub request; a re-read that settles an open
  outcome prints that line again with it. After Ctrl-C the next line starts on a line of its own. A merge whose outcome
  GitHub does not confirm (Ctrl-C, a lost answer) is re-read from the pull request; if that does not settle it, exit
  `2` with `merge state unknown`.
- **Logged:** one comment on the pull request (who, when, the legacy count and each declaration,
  `quality_gate off → <outcome> → on`, or `quality_gate not switched off → no merge → on` when switching off failed),
  also when the restore failed. A merge ends with `merged.`.
