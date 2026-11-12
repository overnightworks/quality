# Commands

Reference for the engineer or operator who runs `quality check --fix`, `quality check secrets --staged`, `quality report`, installs the scanners,
runs `quality merge`, or reads `quality --help` or a wrong invocation's pair.

## Help and wrong invocations

`quality --help` shows the command tree wrapped at 80 columns (HELP-1), with `verdict` and `sonar` apart under "run by
the gate" (HELP-2), and `quality check --help` explains each area in one entry and says which rules an area holds
(HELP-3); every other command's help, `quality sonar --help` and its three commands' included, stands in a frame of its
own (HELP-4); `quality --version` prints the installed version and what this repository's gate pins: its release, read
from the tag comment of the pin (`@<commit sha> # v<tag>`), or why none is known (HELP-5); exit `0` (HELP-6). A call of
a repository's own `gate.yml` counts only in quality's checkout, told by a git remote that names
`overnightworks/quality` (HELP-7). Help goes to stdout with exit `0` (HELP-8), plain (HELP-9) and whole (HELP-10) in
every stream. `-h` prints the same help as `--help` (HELP-11).
A wrong invocation runs nothing, prints the `error:`/`next:` pair on stderr and exits `2` (HELP-12):
`no command <word> · commands: …` for a first word that names no command (HELP-13), `no command · commands: …` for
none (HELP-14), `quality sonar` refuses a word that names none of its commands, or none, the same way, with `next:`
naming `quality sonar --help` (HELP-15), `no area <word> · areas: …` for the first word `quality check` does not know
as an area (HELP-16) (an option may stand between areas) (HELP-17),
`no pull request <word> · a pull request reads OWNER/REPO#N` for a `quality merge` argument of another form (HELP-18)
(`no pull request · …` for none, `next:` naming `quality merge --help`) (HELP-19),
`unknown argument · <what is left over>` for what the command does not take (HELP-20), and otherwise the parser's own
sentence (HELP-21); `--dry-run needs --fix · nothing checked` for `--dry-run` without `--fix` (`next:` names
`quality check --fix --dry-run`) (HELP-22); `--staged checks only secrets · nothing checked` for `--staged` with other
areas or none (`next:` names `quality check secrets --staged`) (HELP-23); `next:` otherwise names the help of the
command that took the stray word, or of the parser that refused (HELP-24).

Ruled by the operator:

- Seven areas, `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies` and `code`; the `code` line of
  `quality check --help`, `code  Python source: security findings and print calls`; and the `secrets` line
  `secrets in the committed history` (HELP-3).
- The areas replace the rule names in the verdict (CLOSING-6); `quality check --help` says which rules an area holds.
  `code` holds `python-security` and `python-print`.

What a run that names areas judges and prints is in [rules](rules.md).

## Which release judges

`quality check` judges only where the running quality is the commit the repository's gate pins (PIN-1), or quality's own
checkout run from its code (PIN-2). The running commit is the one its installer recorded (`direct_url.json`) (PIN-3),
or, for an editable directory installation, that directory's HEAD while its work tree is clean and the version's release
tag names that HEAD (PIN-4). Its first row names the release (`· release  v0.10.0  pinned in .github/workflows/ci.yml`
(PIN-5), with `fetched now · ` before `pinned in` when the run fetched it (PIN-6)), or `this checkout` with the running
version in quality's checkout (PIN-7). Where no workflow calls the gate (PIN-8), the pin is unknown (PIN-9), its comment
misnames its commit (PIN-10) or it is below v0.10.0 (PIN-11), the run is exit `2` with the `error:`/`next:` pair and no
row. The one exception is the commit hook: where no workflow calls the gate, `quality check secrets --staged` judges
with the installed release and its row says `installed · no gate pins a release here` (PIN-12).

Where the gate pins another commit, `check` runs as that release (PIN-13): the same process (PIN-14) with the same
words, folder, environment and terminal (PIN-15) and its exit (PIN-16); `QUALITY_RUN_AT_PIN` marks that run (PIN-17),
which never runs another (PIN-18), and a value of it that quality never sets is refused with the pair that names the
value and asks to unset the variable and run the command again (PIN-19). It first makes sure the pinned commit is the
release the pin's comment names (PIN-20): the first time on a machine by asking quality's remote (PIN-21), which then
leaves an empty record per commit and release (PIN-22) in the user's cache directory (`$XDG_CACHE_HOME` where it is an
absolute path, else `~/.cache`, under `overnightworks-quality/releases`) (PIN-23); removing it costs one more question
per release. Then uv prepares exactly that commit (PIN-24), from its cache offline where a record stands (PIN-25), else
fetched (PIN-26). Offline, the pair
`error: cannot fetch quality v0.10.0, which the gate pins · github.com did not answer` with
`next: quality check, once online` (exit `2`) (PIN-27) stands for every release this machine cannot run without
github.com: one it has not yet checked on quality's remote, even one uv holds (PIN-28), and a checked one whose
environment uv no longer holds. Where github.com answered but uv could not prepare the release (it exited, did not
finish in its time limit or left no `quality` executable in its environment), it is exit `2`
([Exit codes](output.md#exit-codes)) with `error: cannot prepare quality v0.10.1, which the gate pins · uv exited 1` and
two steps under a bare `next:` ([Refusals](output.md#refusals)), the uv command that shows why, then the run again
(PIN-29). Where uv is not installed the pair is
`error: cannot fetch quality v0.10.1, which the gate pins · uv is not installed` with
`next: install uv, then run quality check again` (PIN-30); where it cannot start, the pair names the cause after
`cannot run uv:` and its one step is the uv command (PIN-31); where the prepared quality cannot start, the pair is
`error: cannot start quality v0.10.1 · <cause>` with `next: quality check again` (PIN-32). While quality fetches or
prepares the release, a terminal's stderr shows the live line ([output](output.md#colour-and-the-live-line)). Ctrl-C or
SIGTERM while uv prepares ends quality, uv and every process uv started (PIN-33). In quality's checkout an installation
runs the checkout's code, prepared through `uv run` (PIN-34); a preparation that fails there is
`error: cannot prepare this checkout · uv exited 2` with `next: uv run quality check` (PIN-35).

`quality merge` judges at the release its target's gate pins at the pull request's merge commit (PIN-36), read through
`gh` before its first row (PIN-37) (each listed workflow's name checked before it shapes a request (PIN-38) and sent
percent-encoded (PIN-39), every read at that commit (PIN-40)) and prepared as `check`'s is (PIN-41); its first row
names it (PIN-42). A pull request refused from its own read (no admin right, closed, a draft: see
[the door's refusals](#quality-merge)) stops at its row before the pin is read (PIN-43). No merge commit (PIN-44), no
gate (PIN-45), an unknown pin (PIN-46), a pin below v0.10.0 (PIN-47) and a preparation that fails are the pair alone,
nothing changed; so are a merge commit `gh` names that is no commit sha (PIN-48), a workflow listing or file `gh`
cannot read, a listing that is no list of files and a workflow that is a symlink (PIN-49). For quality itself, where
its gate calls its own `gate.yml`, the installed quality judges, never the pull request's own code:
`· release  installed  <version> · quality's gate pins no release` (PIN-50).

Ruled by the operator:

- The release row always (PIN-5), also in the commit hook, as a grey note: proof that locally the same release judges as
  in the gate.
- The release is missing: quality fetches exactly the pin through uv (PIN-24) and says so in the release row (PIN-6);
  offline is exit 2 (PIN-27).
- No pin, old pin, quality itself: no workflow calls `gate.yml` is exit 2 (PIN-8), except for `check secrets --staged`,
  which judges with the installed release (PIN-12); a pin older than the release with `check` is exit 2 with "raise the
  gate pin" (PIN-11); in quality itself the checkout judges (PIN-2).

## `quality check --fix`

`quality check debt --fix` lowers the debt ceiling (`quality/baseline.toml`) where code shrank, or records it where no
ceiling lies on disk, in the index or in any commit `HEAD` reaches, and then judges the tree against the new file. It
writes the file when an entry changes and when its bytes are not what the fix would render, an older header included;
one that renders byte for byte stays untouched. It never raises an entry and never adds one to a table the file holds:
what grew or is new stays a finding, and a raise is a hand edit with a declared loosening ([rules](rules.md)). A file git renamed into the index
since the ceiling's last commit takes its entries with it ([rules](rules.md)). An entry whose path the index no longer
tracks, whose Python function is gone or whose file is declared generated goes; one only missing from disk stands; a
held pair the tree lost goes. A held `[job_minutes]` entry falls to its job's rounded-up limit or goes where it is no
debt or its job is gone, and a workflow that cannot be read refuses the whole fix while such an entry is held
([rules](rules.md)). Where the file has no `[job_minutes]` table and git never held one, it records the jobs above 15 at
their rounded-up limits, none where no job is above 15 and none where a workflow cannot be read ([rules](rules.md)). A
ceiling or `[clones]` table git holds and the tree lacks, or a `[job_minutes]` table where a job above 15 would be
recorded, refuses with the restore step, and a
shallow clone that cannot prove a first recording names `git fetch --unshallow` ([rules](rules.md)). It takes no
`--staged` (HELP-25) and needs `debt` among the named areas (HELP-26), or no area named; either refusal keeps a
`--dry-run` in its `next:` (HELP-27). `quality check --fix --dry-run` makes, judges and renders the same ceiling,
refuses where the fix refuses before its write (FIXCMD-1), writes nothing, and exits `1` while the fix would change the
file, `0` when not ([rules](rules.md)); a refusal only the write meets shows at the fix alone (FIXCMD-2). Exit `0`
clean, `1` findings, `2` could not fix ([exit codes](output.md#exit-codes)). No workflow runs `--fix` or
its dry run (HELP-28).
The rows and rules are in [rules](rules.md), the rows' form in [output](output.md).

Ruled by the operator:

- A fall below a metric's threshold drops the entry (`dropped  file 805 → 790 lines · debt from 800 lines`) (FIX-4).
- A ceiling that can fall stays a finding: `!` and exit 1 (CEILING-6). The dry run `--fix --dry-run` exits 1 while
  anything can be lowered (FIX-23). Without a ceiling file the size and clone ceilings are a grey note with exit 0
  (O21); job-timeout is judged with or without a ceiling file.

## `quality check secrets --staged`

`quality check secrets --staged` scans exactly what the next commit records: the effective index of the repository it
runs in, compared with `HEAD` (STAGED-1), or taken whole before the first commit (STAGED-2), with no path argument. It
is meant for a pre-commit hook, which runs it in the work-tree top or below it (STAGED-3). A relative `GIT_INDEX_FILE`
is resolved against the working directory, as git does (STAGED-4), so `git commit -a`, `git commit <path>` and a linked
worktree scan the index git is committing (STAGED-5). `--staged` takes exactly the area `secrets`; any other set of
areas, or none, is a [wrong invocation](#help-and-wrong-invocations).

Ruled by the operator: `--staged` only with `secrets`: any other area with `--staged` is exit 2 with
`next: quality check secrets --staged` (HELP-23).

The index is written as a tree (`git write-tree`, only tree objects a commit writes anyway) and read into a scratch
repository inside a 0700 scratch directory. The scratch repository borrows the objects
(`objects/info/alternates`), has an empty work tree and none of the live repository's config or `info/attributes`, so a
staged `.gitleaksignore`, `.gitleaks.toml`, `.gitattributes` with `-diff`, or a `gitleaks:allow` comment changes
nothing (STAGED-6). gitleaks runs in the same attribute-free environment as `check`, with quality's config. The allow
list is the staged tree's own `quality/config.toml` (none staged: no allowances) (STAGED-7). The staged run validates
only the `[secrets]` table of that config and ignores other tables and keys ([gate](gate.md#declared-loosenings)),
while `check` (when a selected area needs the config, validating the whole of it) and `verdict` stay strict, so schema
growth never blocks a commit (STAGED-8).

The output is [`check`'s frame](output.md#rows): the `secrets` row with `staged change · N files` (the paths the staged
diff lists, each added, changed, deleted or renamed path one file) (STAGED-9), the finding rows and the allowed notes
under it, and the verdict. It prints no unused allowance: one change cannot tell a stale entry from one the history
still needs (STAGED-10), so `quality check secrets` reports them. [`-v`](output.md#verbose-runs) is `check`'s: it shows
the raw scanner output under the row, never a secret. At a terminal stderr shows `check`'s live line while gitleaks runs
([output](output.md#colour-and-the-live-line)). A refused staged config names the fact on the row and the example under
`next:` ([refusals](output.md#refusals)), followed by `stage the fix, then run quality check secrets --staged again`
(STAGED-11): the refused file is the staged one, so an edit alone changes nothing.

gitleaks' staged mode sees only added and changed lines, so a known false positive blocks only the commits that touch
its line, and then needs its allowance, as in CI (STAGED-12). A key only in the work tree or only in `HEAD` is clean.
Exit `2` (never clean): a scanner missing or at another version, with the command that installs it
([scanners](#scanners)), git older than 2.41 ([rules](rules.md)), an unreadable allow list (STAGED-13), an index with
unmerged entries (STAGED-14), a [folder that is not a repository](output.md#the-closing-line), or a gitleaks run without
a report (STAGED-15).

## `quality report`

`quality report` reads `quality/fleet.toml` in the folder it runs in (FLEET-28) and takes no path (FLEET-29). The file
has three optional keys, `organizations = [...]` (GitHub organization names), `repositories = [...]` (single
`owner/name`s, a fork too (FLEET-30)) and `exclude = [...]` (single `owner/name`s left out of what the organizations
list (FLEET-31)); the fleet is every public, non-archived, non-fork repository GitHub lists for each organization
(FLEET-32), less the excluded ones, plus the named ones. On stderr it prints
`· fleet  N found  <organizations> · quality/fleet.toml` (without `<organizations> · ` when the file names none)
(FLEET-33), then a row per repository as it is measured, sorted by name, case-insensitively (FLEET-34):
`✓ <owner/name>  measured  <commit> · <time>` (FLEET-35), `✗ …  not measured <reason>` (FLEET-36) or
`✗ …  partly read  cannot read <files>` (FLEET-37); a failed read of the release tags adds
`✗ release tags  not read <reason>` under the fleet line (FLEET-38). The subjects share one column (FLEET-39). On stdout
it prints the two markdown tables and nothing else, so `quality report > fleet.md` writes only them (FLEET-40): the size
of every repository under a heading with the date, the quality version and that functions and CCN are measured for
Python only (FLEET-41), then the same rows as a repo × rule matrix (FLEET-42). With `GITHUB_STEP_SUMMARY` set, the
tables are appended there (FLEET-43). Every cell puts a backslash before `` \ ` * _ ~ [ ] < > & | ``, so repository text
opens no HTML comment, link or new cell (FLEET-44); a bare URL still renders as a link to its own address. The run ends
on stderr with `✓ N of N repositories measured · <time>` (`repository` for one, exit `0`) (FLEET-45) or with `error: `
and what applies of `K of N repositories not measured`, `J partly read` and `the release tags not read`, then `the
tables say so`, and `next: quality report` (exit `2`) (FLEET-46); an unwritable summary is
`error: GITHUB_STEP_SUMMARY is not writable · <reason>` (exit `2`, with
`fix what the error names, then run quality report again` under `next:`) (FLEET-47). `-v` adds nothing: the report runs
no scanner (FLEET-48). The fleet's selection lives once, in `quality/fleet.toml` (FLEET-49);
`.github/workflows/report.yml` runs it every Monday at 05:17 UTC and on dispatch; the tables stand in that run's
summary. The run only reports: it writes to no repository and lists with its own read-only job token (`GITHUB_TOKEN`,
sent to `api.github.com` only, never to git); a local run lists anonymously (FLEET-50).
A missing (FLEET-51), unreadable (a directory, a dangling link, not UTF-8) (FLEET-52), malformed (FLEET-53), duplicating
(also across case, and across `repositories` and `exclude`: `exclude[0] repeats repositories[1]`) (FLEET-54), empty
(`selects no repository`) (FLEET-55) or badly named (FLEET-56) `quality/fleet.toml` is the pair, exit `2`, before any
request or clone (FLEET-57): `error: ` and what is wrong with `quality/fleet.toml`, named repository-relative, and
`next: write it in quality/fleet.toml like this:` with the file's shape below (FLEET-58); each bad entry is named by its
place (`repositories[3]`, `exclude[1]`, `organizations[0]`), never by its text (FLEET-59). The listing is read once per
organization, before any clone (FLEET-60), and a problem of any organization is one pair that ends `· nothing measured`
(FLEET-61): an organization GitHub does not know (`organizations[0] is no GitHub organization`) (FLEET-62), a listing
GitHub did not give (`GitHub did not list <organization> · HTTP <status>`, a timeout, an unreachable host) (FLEET-63),
more than 100 public repositories (quality reads one page) (FLEET-64), an answer that is not a list of repositories
(FLEET-65), a fleet the listing and `exclude` leave empty (`selects no repository`) (FLEET-66), or a `repositories`
entry the organization already lists (`repositories[0] is already in <organization>`) (FLEET-67), or an `exclude` entry
that matches no repository found (`exclude[0] matches no repository found`: the same for a name GitHub does not list and
for one that is private, archived or a fork (FLEET-68); an entry of an organization whose listing failed is judged on
the rerun (FLEET-69)). A failure of GitHub adds `fix what the error names, then run quality report again` under `next:`,
after the shape when the file is also at fault (FLEET-70). No listed text is printed (FLEET-71), and a private
repository a token can list is dropped without a trace (FLEET-72).

- Each repository is cloned from `https://github.com/<owner>/<name>` (FLEET-73) with `git -c credential.helper= clone
  --depth 1 --no-tags`, `GIT_TERMINAL_PROMPT=0` and no askpass, into a temporary directory that is removed on every
  exit (FLEET-74), and its default branch tip is measured (FLEET-75).
- git runs anonymously: without system, global or `GIT_CONFIG_*` config and with an empty `HOME`, so no configured
  header, helper or `~/.netrc` reaches the host (FLEET-76).
- Lines are the physical lines of the tracked files of the `size` rule's languages (FLEET-77), the count its 800-line
  limit uses (blank and comment lines included) (FLEET-78), and a tracked file whose name is not UTF-8 counts like any
  other (FLEET-79); `Languages` lists each with its lines, production and tests together (FLEET-80), largest first,
  ties by name (FLEET-81), and `—` for a row with no measured file (FLEET-82). Production versus test is the `size`
  rule's file-name rule, so `scripts/test_inventory.py` counts as test (FLEET-83). `test:prod` is test lines divided by
  production lines with two decimals (FLEET-84), `—` without production lines (FLEET-85). The debt columns count files
  from 800 lines, and Python's functions from 60 lines and with a CCN above 15, [`size`](rules.md)'s debt (FLEET-86).
- `Clone pairs prod / test` counts the `clone` rule's pairs (two near-identical functions in two files, at 40 tokens and
  90 %) (FLEET-87); a pair counts as test only when both sides are tests, so a test that copies production code counts
  as prod (FLEET-88).
- A `.py` file Python cannot parse still has its lines counted (FLEET-89); the row's note says how many such files there
  are (FLEET-90), never which (FLEET-91).
- The matrix stands under a heading naming the newest release tag of `overnightworks/quality` by version, read by an
  anonymous `git ls-remote --tags` and never from the running code's version; an annotated tag counts at the commit it
  points to, a name that is no release tag is ignored, and a listing without a release tag is unreadable (FLEET-1). It
  has one `Gate` and one `CodeQL` cell per repository (FLEET-2). They come from the job-level `uses:` of each tracked
  `.github/workflows/*.yml|yaml`, parsed as data and never run (FLEET-3). A pin to a commit (40 hexadecimal digits, in
  either case) shows the release tag at that commit, with `(behind)` when it is not the newest (FLEET-4);
  `<7 characters> (no release)` when no tag points there (FLEET-5), `<ref> (not a commit)` for a branch or tag name
  (FLEET-6), `no gate` or `none` without a caller (FLEET-7), and `self` for the repository's own `./` or `$/` file in
  `overnightworks/quality` (FLEET-8). Two callers show both, joined by ` · ` (FLEET-9). The `# v` comment is never read
  (FLEET-10). The owner and repository of a `uses:` match in any case, the workflow path exactly (FLEET-11).
- After `Gate` and `CodeQL` the matrix has `Mandatory jobs` (the number in `[gate] mandatory_jobs` of
  `quality/config.toml`, `no config` without the file, `—` without a gate), `Sonar legacy` (`report` or `fail` from
  `[gate] legacy`, `no config` without the file, `—` without a gate) (FLEET-12) and one column per rule whose
  enforcement depends on the release or the repository (FLEET-13). The rules every release enforces stand once in the
  sentence under the heading (FLEET-14). A rule cell says what the pinned gate does with the rule, never whether the
  repository passes it: `enforced`, `report-only` (`size` without `quality/baseline.toml` (FLEET-15), `import-contract`
  without an import-linter config (FLEET-16), `clone` on v0.4.0 and, from v0.5.0 on, without a `[clones]` table in
  `quality/baseline.toml` (FLEET-17)), `not in <tag>` when the pinned release predates the rule (FLEET-18),
  `not released` when only main has the rule (FLEET-19), `no judged project` (`test-layout`, from v0.7.0 on, when the
  clone has no Python project with a package under `src/` and no frontend project (a `package.json` outside every
  project's `tests/`)) (FLEET-20), and `—` without a gate (FLEET-21). Two pins give the oldest release; a pin that is
  no release (a commit without a tag, a branch or tag name, or unreadable release tags) gives `release unknown`
  (FLEET-22). The table of first releases lives in `rules.py`.
- A workflow file that cannot be read shows `cannot read <file>` in every cell that needs the pin (FLEET-23), a
  malformed `quality/config.toml` or `quality/baseline.toml` and an import-linter config that cannot be read or that two
  files hold show `cannot read <file>` (`import-linter config`) in each cell that reads the file, while a cell that does
  not keeps its value (every cell under no gate, a rule cell under a pin before the rule or `release unknown`)
  (FLEET-24), unreadable release tags say so in the heading and show pins as commits (FLEET-25), and each makes the exit
  `2` (FLEET-26). No value, `behind`, `no gate` or `report-only` included, makes the run red (FLEET-27).
- Found rows are named by GitHub's own `owner/name` (FLEET-92), named rows by the entry as written (FLEET-93); all are
  [sorted by it, case-insensitively](#quality-report). A private repository is not measured, because the tables are
  published and it is not anonymously readable: one named in `repositories` shows as a `cannot judge` row like any
  repository that cannot be cloned (FLEET-94).
- A repository that cannot be cloned or measured shows `cannot judge: <reason>` (for example `cannot clone (git exited
  128)`) (FLEET-95), with no local path (FLEET-96); every other row still shows (FLEET-97), and
  [the exit](#quality-report) is `2`.

## Scanners

gitleaks, actionlint and osv-scanner are not quality's to install: they come through
[mise](https://mise.jdx.dev/installing-mise.html) from the manifest and lock inside quality's installation.
`quality/mise/mise.toml` holds the one version of each scanner (SCANNER-1), `quality/mise/mise.lock` the sha256 of its
release asset (linux x64 only, written by `mise lock --platform linux-x64`, never by hand). The command that installs
them from the lock (SCANNER-2) is

    mise --cd <quality's installation>/quality/mise install --locked

and `check` names it once in its `next:` step, however many rules lack a scanner ([Refusals](output.md#refusals)), the
path filled in. A path with a control character is shown escaped, on one line (SCANNER-3). A scan finds a scanner by
asking `mise which` in that directory, for exactly the manifest's version (SCANNER-4), and runs it only when it reports
that version (SCANNER-5). A scanner on `PATH` is never used (SCANNER-6). mise reads only the manifest directory and the
operator's own user and system configuration: it starts in that directory under `MISE_CEILING_PATHS` set to the
directory above it (SCANNER-7), so a `mise.toml` or `.miserc.toml` in the scanned repository (or in the checkout the
gate holds the manifest in) takes no part in which binary runs (SCANNER-8).

Ruled by the operator:

- The command that installs a scanner: `mise --cd <quality's installation>/quality/mise install --locked` (SCANNER-2),
  the release's own manifest and lock; the path is quality's installation, shown as the example shows it.

`check` and `check secrets --staged` exit `2` for a scanner mise cannot locate (SCANNER-9), that is at another version
or cannot report one (SCANNER-10), or whose answer names no executable (SCANNER-11), and for a machine without mise,
which names mise's install page and the command (SCANNER-12); only the rule that needs the scanner cannot judge
([rules](rules.md)). A repository with no workflow needs no actionlint and one with no lock file no osv-scanner
(SCANNER-13).

zizmor (the `workflow-security` rule) and ruff (`python-security`, `python-print`) are not mise's. They are pinned
dependencies of quality's package (`pyproject.toml`, hashed in `uv.lock`), so installing quality installs them
(SCANNER-14). quality runs the executable its own installation recorded, never one on `PATH` (SCANNER-15). A missing one
(SCANNER-16), or one at another version or that cannot report one (SCANNER-17), makes its rules `cannot judge` (exit
`2`) with the two commands that reinstall quality, one in its checkout and one for a tool install, as alternatives under
`next:` ([Refusals](output.md#refusals)); a scanner quality's own metadata does not pin exactly makes them
`cannot judge` as a defect in quality's release (SCANNER-18).

One `check` run and one `check secrets --staged` run each have one deadline of 540 seconds, started when the run's
inputs are built, after the release row (SCANNER-19). gitleaks (in both), zizmor, actionlint, osv-scanner and ruff
have it as their only time bound (SCANNER-19), and the advisory database requests end at the earlier of their own
deadline and the run's. A rule that would start one of them, or a request, with no time left starts nothing, and a
scanner or request still running when the time ends is stopped, gitleaks with its whole process group so the `git`
it starts dies too, and its scratch directory and mirror are deleted. Both make the rule `cannot judge` (exit `2`),
worded as in [Refusals](output.md#refusals), with no step of their own and the other rules' rows standing
(SCANNER-20). The calls that locate a scanner (`mise which`) and ask it for its version run under the same deadline and
keep their own 30 seconds, taking the smaller of 30 seconds and the time left (SCANNER-19): with no time left they start
nothing, and one still running when the run ends is stopped and cuts the rule the same way, `mise` or the scanner being
its subject and no install or reinstall step following (SCANNER-20); with 30 seconds the smaller, the call ends at 30
seconds with its step (SCANNER-9, SCANNER-10, SCANNER-17). [The vet](output.md#the-vet) stays outside the clock.

Ruled by the operator: quality builds the logic and the process itself and never a specialist scanner (language
linters and test runners, gitleaks, osv-scanner, mutation tools); the scanners it uses run pinned underneath the one
door (SCANNER-5).

## `quality merge`

`quality merge <owner/repo>#<pr> [--dry-run] [-v]` (MERGE-53) is the operator's door past a red gate: it merges a pull
request that the gate keeps red only for legacy Sonar findings or for
[declared loosenings](gate.md#declared-loosenings). The steps to take are in [the runbook](runbooks/merge.md); this is
what the command does.

Ruled by the operator:

- A body the door refuses for its line (MERGE-67) stops at `! landing  refused` under the pull request's row instead,
  the same row as the last one before the question (MERGE-5).
- `merge` names no aco step; that step belongs in the head's instructions.

- **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository
  (checked first: exit `2`, nothing changed (MERGE-54), the missing right named (MERGE-55)). Agents never run it; it
  refuses when stdin is not a terminal. This is the agent contract, not a lock: agents on the operator's machine share
  his `gh` login, so to an agent the terminal is a hurdle, and that login could switch `quality_gate` directly. A real
  lock (agents with a token without admin rights) belongs to the machine's setup, not to quality; it is a known
  residual risk.
- **It refuses unless the door is the only way** (exit `1`, nothing changed): the pull request is open (MERGE-56) and
  no draft (a draft is refused before its body or any check is read (MERGE-57),
  `next:` naming `gh pr ready` (MERGE-58)); no check is still running (MERGE-59); every check run and commit status
  other than `quality / sonar` and `quality / verdict` is green (MERGE-60); `quality / verdict` has failed (MERGE-61);
  and `quality / sonar` is green or has failed (it did not run, or it ended `timed_out`, `cancelled` or anything else:
  refused) (MERGE-62).
  The verdict is then judged again by its own decider, with the config, baseline and import-linter configs read at the
  pull request's current merge commit and at base (MERGE-82) and the Sonar job taken as green (MERGE-83): an
  undeclared loosening (MERGE-84), a declaration that matches nothing (MERGE-85), or a missing (MERGE-86) or red
  mandatory job is refused, naming the verdict's lines. When both sides hold a baseline, or a contract list
  drops a module or a contract is removed, the files git renamed between the two commits count as they do in the
  verdict and the files each commit holds are listed as the verdict lists them, so the door fetches the pull request's
  base commit and merge commit (MERGE-89) (`git fetch --depth=1` from `https://github.com/<owner>/<repo>.git`) into a
  temporary bare repository that is removed afterwards (MERGE-90), also when the command is interrupted (MERGE-91). This
  needs `git` and `gh` on the path. git runs with an empty home (MERGE-92) and no inherited `GIT_*` variable (MERGE-93),
  so `.netrc` and git's own configuration count for nothing; `gh` is the only credential source (MERGE-94), told where
  its login lives (`GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`) (MERGE-95), and reaches a private
  repository through git's credential helper (MERGE-96). git never prompts and an askpass program is never run
  (MERGE-97). The fetch, the file listing and the rename list share one limit of 120 seconds each (MERGE-98), and
  each of these git commands runs in its own process group, which is killed with it at that limit (MERGE-99) or when
  the command is interrupted. git's own output is never printed (MERGE-100): a fetch, file listing or rename list
  that fails, or a rename list that came with a warning, could not be checked, and its `✗ loosenings` row says so in
  the door's words (exit `2`) (MERGE-101), never an empty file or rename list (MERGE-102).
- **The landing line.** The pull request body carries exactly one `Work-Item:` or `No-Item:` line (MERGE-63) outside a
  fenced code block (MERGE-64), as the fleet's landings read it: `Work-Item: #<n>` or `Work-Item: <owner>/<repo>#<n>` of
  this repository (MERGE-65), or `No-Item: docs`, `fix` or `deps` (MERGE-66). Otherwise its `! landing` row names why
  (MERGE-67) and `next:` asks for the one line (MERGE-68) (exit `1`, nothing changed). The merge commit's message is the
  body without that line, a blank line, then the line as `Work-Item: #<n>` or `No-Item: <kind>`, the last paragraph (a
  body that holds only the line gives the line alone) (MERGE-69), the trailer a landing is read from. The body is the
  one read with the head (MERGE-70).
- **Sonar green: declared loosenings.** The re-derived verdict must hold at least one declared loosening (otherwise
  `re-run the verdict`) (MERGE-87). SonarCloud is not asked and `SONAR_TOKEN` is not needed (MERGE-88).
- **Sonar failed: legacy findings,** with or without a declared loosening (MERGE-71): `[gate] legacy` is `fail` at the
  merge commit (MERGE-72) (under `report` the Sonar job cannot be red on legacy findings, and a missing config reads as
  `report` (MERGE-73)), SonarCloud's analysis of the pull request is of the head being merged (MERGE-74) and adds no
  finding (the findings are named otherwise) (MERGE-75), and the default branch has legacy findings (MERGE-76).
  `SONAR_TOKEN` is read from the environment and sent when set (MERGE-77), never shown in a row (MERGE-78): without it
  the door reads a public project anonymously, with no Authorization header (MERGE-79), and an anonymous read that
  SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN` (MERGE-80); with a token, a 401
  (MERGE-81) or 403 says the token was rejected.
- **It shows, then asks:** on stdout the [release](#which-release-judges) that judges first, then a row as each step
  passes (MERGE-1): the pull request (`open`, head, base), the checks, the declared loosenings, each
  [whole](output.md#length) with its `item` and `date` and its `reason` on the line below (MERGE-2), the legacy count
  where Sonar failed (MERGE-3), the merge method (`allowed on <base>`) (MERGE-4), and last the `landing` row naming the
  line the merge commit ends with (MERGE-5) (`work item` and `#<n>`, or `no item` and its kind); then, on stderr,
  `Switch quality_gate off, merge #<pr>, switch it on? Type yes: ` (MERGE-6). Only `yes` in lower case, ended with
  Enter, merges (MERGE-7) (whitespace around it counts for nothing (MERGE-8); `Yes` or `y` change nothing (MERGE-9));
  any other line, or the end of input (Ctrl-D) before Enter, prints `error: not confirmed · nothing changed` and
  `next:` naming the command again (exit `1`) (MERGE-10); Ctrl-C at the question ends the run interrupted
  (exit `130`) (MERGE-11); nothing has changed in either case (MERGE-12). With stderr closed the question and the
  pairs print nowhere; nothing moves to stdout (MERGE-13). A refusal is its `!` row and the `error:`/`next:` pair on
  stderr (exit `1`) (MERGE-14), each blocking line of the verdict [whole](output.md#length) below the `loosenings`
  row (MERGE-15); a step that cannot be judged is its `✗` row and the pair (exit `2`) (MERGE-16); a
  `quality/config.toml` it cannot read at the base or at the merge commit is the `✗ loosenings` row with the config's
  fact, and under `next:` [the example to copy](output.md#refusals) where the fact has one (MERGE-17), then the step
  for that commit: `fix it on <base branch> first` for the base (MERGE-18), which is read first (MERGE-19), or
  `push the fix to the pull request's branch, then run quality merge <owner/repo>#<pr> again` for the merge commit
  (MERGE-20); no terminal on stdin is the pair alone (exit `1`) (MERGE-21); a name not of the form
  `<owner>/<repo>#<pr>` is [a wrong invocation](#help-and-wrong-invocations) (exit `2`). Every line it prints is
  escaped as [the other doors' lines are](output.md#masking), so a `reason` cannot hide anything before `yes` or forge
  a line (MERGE-22).
- **Dry run:** `--dry-run` reads and shows every row the door shows (MERGE-23), refuses where it refuses (MERGE-24),
  and in place of the question ends with
  `~ dry run · would switch quality_gate off, merge #<pr>, switch it on · nothing changed` on stdout (exit `0`)
  (MERGE-25); it reads no answer (MERGE-26) and sends no request that changes anything (no `gh api -X`, no
  `gh pr merge`) (MERGE-27), and it needs a terminal on stdin like the door. `-v` is taken and adds nothing (MERGE-28):
  the door runs no scanner.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this takes the fleet's ruleset off
  it) (MERGE-29), runs `gh pr merge <pr> --repo <owner/repo> --merge --match-head-commit <sha> --body-file -`, the
  message on its stdin (MERGE-30) (`--squash` where the repository settings or a rule on the pull request's base branch
  exclude a merge commit but allow a squash (MERGE-31); when neither is allowed, or the settings or rules cannot be read
  or are malformed (an answer or a page that is no array, a rule that is no object, or whose `parameters` are present
  but no object), its `✗ merge` row ends the run before the gate is touched (exit `2`) (MERGE-32);
  the shown text and the log comment name the method (MERGE-33)), and sets the property back to `true`
  in every case: merged, merge failed, Ctrl-C (MERGE-34). After `yes` each step is a row on stdout as GitHub answers it
  (`quality_gate  off`, `#<pr>  merged` with the method, `quality_gate  on`, `log comment  posted`) (MERGE-35), ending
  `✓ merged #<pr> · quality_gate on again · <duration>` (counted from `yes`) (exit `0`) (MERGE-36). A step
  not done is a `✗` row (`off failed`, `not merged`, `state unknown`, `not restored`, `not posted`) and one pair on
  stderr, exit `2` (MERGE-37), the gate first (`quality_gate MAY STILL BE OFF on <owner/repo>` where it is not on)
  (MERGE-38), `next:` the commands that finish it, the gate's first, alone on its line and
  [never shortened](output.md#length) (MERGE-39). A gate not switched on is its row before any further request
  (MERGE-40). After Ctrl-C the next line starts on a line of its own (MERGE-41). A merge whose outcome GitHub does not
  confirm (Ctrl-C, a lost answer) is read back after the gate is on, its row below the gate's (MERGE-42); if that does
  not settle it, the row is `state unknown` and the pair says `#<pr> may be merged` with `gh pr view` (MERGE-43). Before
  the question a stdout that refuses a row stops the door (exit `2`, nothing changed) (MERGE-44); after `yes` such a
  stdout takes no further row and no closing line, and the run does every step and keeps its exit (MERGE-45).
- **Logged:** one comment on the pull request, opening `quality merge run by @<login>` (MERGE-46) (who, when
  (MERGE-47), the legacy count (MERGE-48) and each declaration (MERGE-49), `quality_gate off → <outcome> → on`
  (`<outcome>` is `merge`, `merge failed` or `merge state unknown`) (MERGE-50), or
  `quality_gate not switched off → no merge → on` when switching off failed (MERGE-51)), also when the restore failed,
  the comment then ending `NOT restored, it may still be off` in place of `on` (MERGE-52). A comment not posted is its
  `✗` row, its command under `next:`.
