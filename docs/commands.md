# Commands

Reference for the engineer or operator who runs `quality check --fix`, `quality check secrets --staged`, `quality report`, installs the scanners,
runs `quality merge`, or reads `quality --help` or a wrong invocation's pair.

## Help and wrong invocations

- HELP-1: `quality --help` shows the command tree wrapped at 80 columns,
- HELP-2: with `verdict` and `sonar` apart under "run by
  the gate",
- HELP-3: and `quality check --help` explains each area in one entry and says which rules an area holds;
- HELP-4: every other command's help, `quality sonar --help` and its three commands' included, stands in a frame of its
  own;
- HELP-5: `quality --version` prints the installed version and what this repository's gate pins: its release, read
  from the tag comment of the pin (`@<commit sha> # v<tag>`), or why none is known;
- HELP-6: exit `0`.
- HELP-7: A call of
  a repository's own `gate.yml` counts only in quality's checkout, told by a git remote that names
  `overnightworks/quality`.
- HELP-8: Help goes to stdout with exit `0`,
- HELP-9: plain
- HELP-10: and whole in
  every stream.
- HELP-11: `-h` prints the same help as `--help`.
- HELP-12: A wrong invocation runs nothing, prints the `error:`/`next:` pair on stderr and exits `2`:
- HELP-13: `no command <word> · commands: …` for a first word that names no command,
- HELP-14: `no command · commands: …` for
  none,
- HELP-15: `quality sonar` refuses a word that names none of its commands, or none, the same way, with `next:`
  naming `quality sonar --help`,
- HELP-16: `no area <word> · areas: …` for the first word `quality check` does not know
  as an area
- HELP-17: (an option may stand between areas),
- HELP-18: `no pull request <word> · a pull request reads OWNER/REPO#N` for a `quality merge` argument of another form
- HELP-19: (`no pull request · …` for none, `next:` naming `quality merge --help`),
- HELP-20: `unknown argument · <what is left over>` for what the command does not take,
- HELP-21: and otherwise the parser's own
  sentence;
- HELP-22: `--dry-run needs --fix · nothing checked` for `--dry-run` without `--fix` (`next:` names
  `quality check --fix --dry-run`);
- HELP-23: `--staged checks only secrets · nothing checked` for `--staged` with other
  areas or none (`next:` names `quality check secrets --staged`);
- HELP-24: `next:` otherwise names the help of the
  command that took the stray word, or of the parser that refused.

Ruled by the operator:

- Seven areas, `files`, `debt`, `imports`, `secrets`, `workflows`, `dependencies` and `code`; the `code` line of
  `quality check --help`, `code  Python source: security findings and print calls`; and the `secrets` line
  `secrets in the committed history` (HELP-3).
- The areas replace the rule names in the verdict (CLOSING-6); `quality check --help` says which rules an area holds.
  `code` holds `python-security` and `python-print`.

What a run that names areas judges and prints is in [rules](rules.md).

## Which release judges
- PIN-1: `quality check` judges only where the running quality is the commit the repository's gate pins,
- PIN-2: or quality's own
  checkout run from its code.
- PIN-3: The running commit is the one its installer recorded (`direct_url.json`),
- PIN-4: or,
  for an editable directory installation, that directory's HEAD while its work tree is clean and the version's release
  tag names that HEAD.
- PIN-5: Its first row names the release (`· release  v0.10.0  pinned in .github/workflows/ci.yml`,
- PIN-6: with `fetched now · ` before `pinned in` when the run fetched it),
- PIN-7: or `this checkout` with the running
  version in quality's checkout.
- PIN-8: Where no workflow calls the gate,
- PIN-9: the pin is unknown,
- PIN-10: its comment
  misnames its commit
- PIN-11: or it is below v0.10.0, the run is exit `2` with the `error:`/`next:` pair and no
  row.
- PIN-12: The one exception is the commit hook: where no workflow calls the gate, `quality check secrets --staged` judges
  with the installed release and its row says `installed · no gate pins a release here`.

- PIN-13: Where the gate pins another commit, `check` runs as that release:
- PIN-14: the same process
- PIN-15: with the
  same words, folder, environment and terminal
- PIN-16: and its exit;
- PIN-17: `QUALITY_RUN_AT_PIN` marks that run,
- PIN-18: which never runs another,
- PIN-19: and a value of it that quality never sets is refused with the pair that names the
  value and asks to unset the variable and run the command again.
- PIN-20: It first makes sure the pinned commit is the
  release the pin's comment names:
- PIN-21: the first time on a machine by asking quality's remote,
- PIN-22: which then
  leaves an empty record per commit and release
- PIN-23: in the user's cache directory (`$XDG_CACHE_HOME` where it is an
  absolute path, else `~/.cache`, under `overnightworks-quality/releases`); removing it costs one more question
  per release.
- PIN-24: Then uv prepares exactly that commit,
- PIN-25: from its cache offline where a record stands,
- PIN-26: else
  fetched.
- PIN-27: Offline, the pair
  `error: cannot fetch quality v0.10.0, which the gate pins · github.com did not answer` with
  `next: quality check, once online` (exit `2`)
- PIN-28: stands for every release this machine cannot run without
  github.com: one it has not yet checked on quality's remote, even one uv holds, and a checked one whose
  environment uv no longer holds.
- PIN-29: Where github.com answered but uv could not prepare the release (it exited, did not
  finish in its time limit or left no `quality` executable in its environment), it is exit `2`
  ([Exit codes](output.md#exit-codes)) with `error: cannot prepare quality v0.10.1, which the gate pins · uv exited 1` and
  two steps under a bare `next:` ([Refusals](output.md#refusals)), the uv command that shows why, then the run again.
- PIN-30: Where uv is not installed the pair is
  `error: cannot fetch quality v0.10.1, which the gate pins · uv is not installed` with
  `next: install uv, then run quality check again`;
- PIN-31: where it cannot start, the pair names the cause after
  `cannot run uv:` and its one step is the uv command;
- PIN-32: where the prepared quality cannot start, the pair is
  `error: cannot start quality v0.10.1 · <cause>` with `next: quality check again`. While quality fetches or
  prepares the release, a terminal's stderr shows the live line ([output](output.md#colour-and-the-live-line)).
- PIN-33: Ctrl-C or
  SIGTERM while uv prepares ends quality, uv and every process uv started.
- PIN-34: In quality's checkout an installation
  runs the checkout's code, prepared through `uv run`; a run that is not the checkout's code (a marked child, which never
  runs another, PIN-18) is `error: this is quality <version>, not this checkout's code` with `next: uv run quality check`;
- PIN-35: a preparation that fails there is
  `error: cannot prepare this checkout · uv exited 2` with `next: uv run quality check`.

- PIN-36: `quality merge` judges at the release its target's gate pins at the pull request's merge commit,
- PIN-37: read through
  `gh` before its first row
- PIN-38: (each listed workflow's name checked before it shapes a request
- PIN-39: and sent
  percent-encoded,
- PIN-40: every read at that commit)
- PIN-41: and prepared as `check`'s is;
- PIN-42: its first row
  names it.
- PIN-43: A pull request refused from its own read (no admin right, closed, a draft: see
  [the door's refusals](#quality-merge)) stops at its row before the pin is read.
- PIN-44: No merge commit,
- PIN-45: no
  gate,
- PIN-46: an unknown pin,
- PIN-47: a pin below v0.10.0
- PIN-48: and a preparation that fails are the pair alone,
  nothing changed; so are a merge commit `gh` names that is no commit sha,
- PIN-49: a workflow listing or file `gh`
  cannot read, a listing that is no list of files and a workflow that is a symlink.
- PIN-50: For quality itself, where
  its gate calls its own `gate.yml`, the installed quality judges, never the pull request's own code:
  `· release  installed  <version> · quality's gate pins no release`.

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
tracks, whose Python or script function is gone or whose file is declared generated goes; one only missing from disk stands; a
held pair the tree lost goes, and so does a held spec ID that is now cited, retired or undefined, which `--fix` never adds
([rules](rules.md)). A held `[job_minutes]` entry falls to its job's rounded-up limit or goes where it is no
debt or its job is gone, and a workflow that cannot be read refuses the whole fix while such an entry is held
([rules](rules.md)). Where the file has no `[job_minutes]` table and git never held one, it records the jobs above 15 at
their rounded-up limits, none where no job is above 15 and none where a workflow cannot be read ([rules](rules.md)), and
without a `[spec_citations]` table, where git never held one and the tree defines a live ID with no undefined or
retired citation, no duplicate and no unread file, it records the uncited IDs ([rules](rules.md)), and without an
`[adapter_imports]` table, where git never held one and a registered `imports_adapter` mark exempts an import, it
records the marked modules per test directory. A held `[adapter_imports]` count falls to the measured one and goes at
0, never rises, and a count follows a renamed marked module to its new directory; without the contract that carries the
id the table is left as it is. For the counts `quality check debt --fix` reads the import contracts and builds the
import graph, and a read that cannot be judged refuses the fix while the table is held ([rules](rules.md)). A
`[literal_text]` table is read and ignored, and the rewrite leaves it out ([rules](rules.md)). A
ceiling or `[clones]` table git holds and the tree lacks, or a `[job_minutes]` table where a job above 15 would be
recorded or a `[spec_citations]` table where it would be recorded, refuses with the restore step, and a
shallow clone that cannot prove a first recording names `git fetch --unshallow` ([rules](rules.md)).

- HELP-25: It takes no
  `--staged`
- HELP-26: and needs `debt` among the named areas,
- HELP-27: or no area named; either refusal keeps a
  `--dry-run` in its `next:`.
- FIXCMD-1: `quality check --fix --dry-run` makes, judges and renders the same ceiling,
  refuses where the fix refuses before its write,
- FIXCMD-2: writes nothing, and exits `1` while the fix would change the
  file, `0` when not ([rules](rules.md)); a refusal only the write meets shows at the fix alone. Exit `0`
  clean, `1` findings, `2` could not fix ([exit codes](output.md#exit-codes)).
- HELP-28: No workflow runs `--fix` or
  its dry run.

The rows and rules are in [rules](rules.md), the rows' form in [output](output.md).

Ruled by the operator:

- A fall below a metric's threshold drops the entry (`dropped  file 805 → 790 lines · debt from 800 lines`) (FIX-4).
- A ceiling that can fall stays a finding: `!` and exit 1 (CEILING-6). The dry run `--fix --dry-run` exits 1 while
  anything can be lowered (FIX-23). Without a ceiling file the size and clone ceilings are a grey note with exit 0
  (O21); job-timeout is judged with or without a ceiling file.

## `quality check secrets --staged`

- STAGED-1: `quality check secrets --staged` scans exactly what the next commit records: the effective index of the repository it
  runs in, compared with `HEAD`,
- STAGED-2: or taken whole before the first commit, with no path argument.
- STAGED-3: It
  is meant for a pre-commit hook, which runs it in the work-tree top or below it.
- STAGED-4: A relative `GIT_INDEX_FILE`
  is resolved against the working directory, as git does,
- STAGED-5: so `git commit -a`, `git commit <path>` and a linked
  worktree scan the index git is committing.

`--staged` takes exactly the area `secrets`; any other set of
areas, or none, is a [wrong invocation](#help-and-wrong-invocations).

Ruled by the operator: `--staged` only with `secrets`: any other area with `--staged` is exit 2 with
`next: quality check secrets --staged` (HELP-23).

The index is written as a tree (`git write-tree`, only tree objects a commit writes anyway) and read into a scratch
repository inside a 0700 scratch directory.

- STAGED-6: The scratch repository borrows the objects
  (`objects/info/alternates`), has an empty work tree and none of the live repository's config or `info/attributes`, so a
  staged `.gitleaksignore`, `.gitleaks.toml`, `.gitattributes` with `-diff`, or a `gitleaks:allow` comment changes
  nothing. gitleaks runs in the same attribute-free environment as `check`, with quality's config.
- STAGED-7: The allow
  list is the staged tree's own `quality/config.toml` (none staged: no allowances).
- STAGED-8: The staged run validates
  only the `[secrets]` table of that config and ignores other tables and keys ([gate](gate.md#declared-loosenings)),
  while `check` (when a selected area needs the config, validating the whole of it) and `verdict` stay strict, so schema
  growth never blocks a commit.

- STAGED-9: The output is [`check`'s frame](output.md#rows): the `secrets` row with `staged change · N files` (the paths the staged
  diff lists, each added, changed, deleted or renamed path one file), the finding rows and the allowed notes
  under it, and the verdict.
- STAGED-10: It prints no unused allowance: one change cannot tell a stale entry from one the history
  still needs, so `quality check secrets` reports them. [`-v`](output.md#verbose-runs) is `check`'s: it shows
  the raw scanner output under the row, never a secret. At a terminal stderr shows `check`'s live line while gitleaks runs
  ([output](output.md#colour-and-the-live-line)).
- STAGED-11: A refused staged config names the fact on the row and the example under
  `next:` ([refusals](output.md#refusals)), followed by `stage the fix, then run quality check secrets --staged again`: the refused file is the staged one, so an edit alone changes nothing.

gitleaks' staged mode sees only added and changed lines, so a known false positive blocks only the commits that touch
its line, and then needs its allowance, as in CI.

- STAGED-12: A key only in the work tree or only in `HEAD` is clean.
- STAGED-13: Exit `2` (never clean): a scanner missing or at another version, with the command that installs it
  ([scanners](#scanners)), git older than 2.41 ([rules](rules.md)), an unreadable allow list,
- STAGED-14: an index with
  unmerged entries,
- STAGED-15: a [folder that is not a repository](output.md#the-closing-line), or a gitleaks run without
  a report.

## `quality report`

- FLEET-28: `quality report` reads `quality/fleet.toml` in the folder it runs in
- FLEET-29: and takes no path.
- FLEET-30: The file
  has three optional keys, `organizations = [...]` (GitHub organization names), `repositories = [...]` (single
  `owner/name`s, a fork too)
- FLEET-31: and `exclude = [...]` (single `owner/name`s left out of what the organizations
  list);
- FLEET-32: the fleet is every public, non-archived, non-fork repository GitHub lists for each organization, less the excluded ones, plus the named ones.
- FLEET-33: On stderr it prints
  `· fleet  N found  <organizations> · quality/fleet.toml` (without `<organizations> · ` when the file names none),
- FLEET-34: then a row per repository as it is measured, sorted by name, case-insensitively:
- FLEET-35: `✓ <owner/name>  measured  <commit> · <time>`,
- FLEET-36: `✗ …  not measured <reason>`,
- FLEET-103: whose reason for a repository without a commit is `holds no commit`,
- FLEET-37: or
  `✗ …  partly read  cannot read <files>`,
- FLEET-105: or, for a repository whose runs could not be read, `✗ …  partly read  cannot read CI time`;
- FLEET-38: a failed read of the release tags adds
  `✗ release tags  not read <reason>` under the fleet line.
- FLEET-39: The subjects share one column.
- FLEET-40: On stdout
  it prints the five markdown tables and nothing else, so `quality report > fleet.md` writes only them:
- FLEET-41: the size
  of every repository under a heading with the date and the quality version,
- FLEET-42: then the same rows as a repo × rule matrix,
- FLEET-104: then under the heading
  `CI time, last 7 days · N API calls` one row per repository: `Repo`, `Commits`, `Median`, `p90`, `Red`, `Red on main`,
  `Re-runs`, `Green on re-run`, `Jobs sampled`, `Queue p90` and `Over 12 min`, then the pairs between repositories (FLEET-113), then the tests per layer (FLEET-119).

A commit is the unit, except in `Re-runs` and `Green on re-run`, which count runs. One commit fans out into several
runs: the runs created in the 7 days before the report's day over the events `pull_request` and `push`, grouped by head
commit; its wall is its latest `updated_at` minus its earliest `created_at`, so a re-run counts from its first attempt. A
commit with a run still in progress and a commit whose runs are all `cancelled` are left out. `Commits`, `Median` and `p90`
are over every counted commit, median and p90 nearest-rank over the walls. `Red` is the share of the counted commits
with a `pull_request` run that have one concluded `failure` or `timed_out`, `Red on main` the same over the commits with a
`push` run on the default branch; each cell reads `N % (K of M)` over its own population, a push run on another branch, or on none (a null `head_branch`), is in
neither, and a commit red only through a verdict of declared loosenings counts as red. After every repository's runs are
read, the jobs listing reads a sample of its runs spread evenly over the window (at most 10, an even share of the calls the
report's cap of 600 leaves, and only while the report's deadline allows (FLEET-126)): `Jobs sampled` is the sample's size, `Queue p90` the nearest-rank 90th percentile of the time
the sampled jobs waited between creation and start (a skipped or cancelled job is left out), counted apart from their
running time, and `Over 12 min` each job name with at least 5 samples whose running time is above 12 minutes at its 90th
percentile, the slowest first, or `none`; a repository whose jobs cannot be read shows `cannot read jobs`, one whose runs were read but whose jobs the cap left no call for shows `not sampled · call cap` in all three cells, and one whose jobs the deadline stopped before the first call shows `not sampled · time limit`. The report reads
runs without a page cap per repository, within its 600 calls; a repository the cap stops shows `partly read` and the pages
read, or `cannot read CI time` where its runs cannot be read, in place of its numbers, and the table raises no finding and
changes no exit (FLEET-105, FLEET-130). One large repository can use up the cap and leave those read after it `partly read`, in the
listing's order. Private repositories are not read, and a commit whose runs begin before the window counts by its runs inside it.

`Re-runs` and `Green on re-run` count runs, not commits, per repository over the same window and events, from the
`run_attempt` and `conclusion` of each finished run's latest attempt: `Re-runs` the runs with a `run_attempt` above 1,
`Green on re-run` those among them concluded `success`. A `run_attempt` that is not an integer makes the repository
`cannot read CI time`. A `partly read` row, or one with no counted commit, shows dashes.

- FLEET-112: They add no API call, no finding
  and no exit, and the JSON document (FLEET-106) does not carry them.

Named gaps: G1, a run re-run after a green
first attempt counts as green on re-run; G2, no job names or failing steps are read; G3, a run created before the window
but re-run inside it is not counted.

The CI-time read starts before the first clone and runs beside the clones, the size measurement and the pairs between
repositories, one request at a time; its rows print after the last repository's row, and the tables and the document are
those a read after the clones gives. The report has one deadline of 720 seconds from its start: no request
and no clone starts after it, and a clone in flight ends at it.

- FLEET-125: A repository whose runs it stops is `partly read · N pages
  read` and makes `complete` false; one whose jobs it stops before the first call shows `not sampled · time limit` in its
  three cells, and one it stops mid-sample shows the sample it read; one whose clone it does not start is `not measured ·
  the report's 720 seconds are spent` (exit `2`, FLEET-46).
- FLEET-126: Like the call cap, it falls on the repositories read last, in
  the listing's order.
- FLEET-127: The `fleet report` job's limit covers its setup, the deadline, one request in flight
  and the tail (tables, summary, document, upload): 60 + 720 + 30 + 90 = 900 seconds.

Named gaps: the pairs
between repositories are not bounded (24 s at 18 repositories, inside the tail); a size measurement in flight finishes
after the deadline; a cut is not spread evenly.

- FLEET-128: The runs are read from the repository's runs listing without a date filter, newest first, 100 to a page, because a
  filtered listing returns at most 1,000 runs; the read stops after the first page whose oldest run, of any event, was
  created before the window, or at the listing's end, and a run created before the window is not counted, so a window of
  more than 1,000 runs is counted whole.
- FLEET-129: A run listed on two pages, because runs created during the read shift
  the pages, counts once by its id, as its later sighting shows it.
- FLEET-130: A listing that ends before the run total it
  reports is `partly read · N pages read` and makes `complete` false.

Named gaps: a run deleted during the read
can let one run slip past a page boundary unread; the stop assumes the listing's id order agrees with `created_at` at the
window's edge.

- FLEET-43: With `GITHUB_STEP_SUMMARY` set, the
  tables are appended there.
- FLEET-44: Every cell puts a backslash before `` \ ` * _ ~ [ ] < > & | ``, so repository text
  opens no HTML comment, link or new cell; a bare URL still renders as a link to its own address.
- FLEET-45: The run ends
  on stderr with `✓ N of N repositories measured · <time>` (`repository` for one, exit `0`)
- FLEET-46: or with `error: `
  and what applies of `K of N repositories not measured`, `J partly read` and `the release tags not read`, then `the
  tables say so`, and `next: quality report` (exit `2`);
- FLEET-47: an unwritable summary is
  `error: GITHUB_STEP_SUMMARY is not writable · <reason>` (exit `2`, with
  `fix what the error names, then run quality report again` under `next:`).
- FLEET-48: `-v` adds nothing: the report runs
  no scanner.
- FLEET-49: The fleet's selection lives once, in `quality/fleet.toml`; `.github/workflows/report.yml` runs it every Monday at 05:17 UTC and on dispatch; the tables stand in that run's
  summary.
- FLEET-50: The report writes to no repository and lists with its own read-only job token (`GITHUB_TOKEN`,
  sent to `api.github.com` only, never to git); a local run lists anonymously.

`quality report --ci-time-json` also writes the CI time as one JSON document to the fixed relative path
`site/fleet-ci-time.json`, creating `site/` when it is missing, after the step summary, in UTF-8 with a trailing newline,
and prints nothing of its own; without the switch no file is written. The switch takes no path, so no caller chooses
where the file lands.

- FLEET-106: The top level holds, in this
  order, `schema` (`1`; a change of any key goes through it), `generated_at` and `window_start` and `window_end` (RFC 3339 UTC
  instants: `window_start` is midnight UTC seven days before the report's day, the bound of the runs read, and `window_end`
  equals `generated_at`), `limit_seconds` (`600`, the one limit the CI-time module owns), `complete` and `repositories`, a flat
  array with one row per repository of the report's selection in the table's order, each with `name`, `median_seconds`,
  `commits_sampled`, `measured` and `breach`: only the name, whole numbers and booleans, no commit author and no reason
  text.
- FLEET-107: A repository with a median over at least one counted commit is `measured` with that median in whole
  seconds and its count of commits, and `breach` is true when the median is strictly above `limit_seconds`; a repository whose
  runs the cap left `partly read`, whose runs `cannot read CI time`, or with no counted commit is not measured and carries
  `0`, `0` and `breach` false, so a thin week is judged by `commits_sampled` and never by a minimum the file applies.
- FLEET-108: `complete` is false when any repository's CI time is `partly read` or `cannot read CI time`, and true
  otherwise, zero counted commits included; the size rows do not touch it.
- FLEET-109: An unwritable path (`site` a file, no
  write permission) is `error: site/fleet-ci-time.json is not writable · <reason>` with `fix what the error names, then run quality report
  again` under `next:`, exit `2`, after the tables. An unwritable step summary ends the report before the
  document is written, and a report that ends with a repository not measured still writes it.
- FLEET-110: Private repositories are not
  read, so the public document names none; that is a named gap, not a list.
- FLEET-111: `report.yml` passes the bare switch, uploads `site/` as the Pages artifact whenever the file
  exists, and its `publish` job deploys it to the repository's GitHub Pages at `https://overnightworks.github.io/quality/fleet-ci-time.json`
  (`application/json`, no token): the workflow keeps `contents: read`, and the `publish` job alone adds `pages: write` and
  `id-token: write` and runs only on the repository's default branch, so a dispatch from another branch uploads the
  artifact and never deploys. A run that wrote no file deploys nothing and leaves the last file in place.

The
Pages source (GitHub Actions) is a repository setting that no code owns.

- FLEET-51: A missing,
- FLEET-52: unreadable (a directory, a dangling link, not UTF-8),
- FLEET-53: malformed,
- FLEET-54: duplicating
  (also across case, and across `repositories` and `exclude`: `exclude[0] repeats repositories[1]`),
- FLEET-55: empty
  (`selects no repository`)
- FLEET-56: or badly named
- FLEET-57: `quality/fleet.toml` is the pair, exit `2`, before any
  request or clone:
- FLEET-58: `error: ` and what is wrong with `quality/fleet.toml`, named repository-relative, and
  `next: write it in quality/fleet.toml like this:` with the file's shape below;
- FLEET-59: each bad entry is named by its
  place (`repositories[3]`, `exclude[1]`, `organizations[0]`), never by its text.
- FLEET-60: The listing is read once per
  organization, before any clone,
- FLEET-61: and a problem of any organization is one pair that ends `· nothing measured`:
- FLEET-62: an organization GitHub does not know (`organizations[0] is no GitHub organization`),
- FLEET-63: a listing
  GitHub did not give (`GitHub did not list <organization> · HTTP <status>`, a timeout, an unreachable host),
- FLEET-64: more than 100 public repositories (quality reads one page),
- FLEET-65: an answer that is not a list of repositories,
- FLEET-66: a fleet the listing and `exclude` leave empty (`selects no repository`),
- FLEET-67: or a `repositories`
  entry the organization already lists (`repositories[0] is already in <organization>`),
- FLEET-68: or an `exclude` entry
  that matches no repository found (`exclude[0] matches no repository found`: the same for a name GitHub does not list and
  for one that is private, archived or a fork;
- FLEET-69: an entry of an organization whose listing failed is judged on
  the rerun).
- FLEET-70: A failure of GitHub adds `fix what the error names, then run quality report again` under `next:`,
  after the shape when the file is also at fault.
- FLEET-71: No listed text is printed,
- FLEET-72: and a private
  repository a token can list is dropped without a trace.

- FLEET-73: Each repository is cloned from `https://github.com/<owner>/<name>`
- FLEET-74: with `git -c credential.helper= clone
  --depth 1 --no-tags`, `GIT_TERMINAL_PROMPT=0` and no askpass, into a temporary directory that is removed on every
  exit,
- FLEET-75: and its default branch tip is measured.
- FLEET-76: git runs anonymously: without system, global or `GIT_CONFIG_*` config and with an empty `HOME`, so no configured
  header, helper or `~/.netrc` reaches the host.
- FLEET-77: Lines are the physical lines of the tracked files of the `size` rule's languages,
- FLEET-78: the count its 800-line
  limit uses (blank and comment lines included),
- FLEET-79: and a tracked file whose name is not UTF-8 counts like any
  other;
- FLEET-80: `Languages` lists each with its lines, production and tests together,
- FLEET-81: largest first,
  ties by name,
- FLEET-82: and `—` for a row with no measured file.
- FLEET-83: Production versus test is the `size`
  rule's file-name rule, so `scripts/test_inventory.py` counts as test.
- FLEET-84: `test:prod` is test lines divided by
  production lines with two decimals,
- FLEET-85: `—` without production lines.
- FLEET-86: The debt columns count files
  from 800 lines, and the functions from 60 lines and with a CCN above 15 of Python, TypeScript, JavaScript, Svelte and Vue, [`size`](rules.md)'s debt.
- FLEET-87: `Clone pairs prod / test` counts the `clone` rule's pairs (two near-identical functions in two files, at 40 tokens and
  90 %);
- FLEET-88: a pair counts as test only when both sides are tests, so a test that copies production code counts
  as prod.
- FLEET-113: Under `Clone pairs between repositories · N pairs`, after the CI-time table, stands one row per pair of near-identical
  functions in two different measured repositories, found by the `clone` rule's detector and thresholds: `Repo`,
  `Function`, `Repo`, `Function` and `Kind`, where `Function` reads `<path> <symbol>` as `clone` names a function; each
  pair stands once, its sides in sorted order.
- FLEET-114: A pair inside one repository stands only in that
  repository's `Clone pairs prod / test` cell, never in this table.
- FLEET-115: `Kind` is `test` when both sides are
  test files under the `size` rule's file-name rule, else `prod`; `prod` rows come first, then rows by their sides.
- FLEET-116: The table is computed each run from the measured default-branch tips, so a pair whose copy is gone or
  drifted below the threshold leaves it, and a repository not measured adds no pair.
- FLEET-117: It raises no finding
  and changes no exit. Named limit: the detector reads Python, not workflow files, so the copied jobs of
  `ci.yml` are outside it; the CI standard's shared building blocks own them.
- FLEET-119: Under `Tests per layer`, last, stands one row per repository: `Repo`, `Unit`, `Integration`, `Flow`, `Live`,
  `Skipped`, `Fixme` and `Note`, numbers with thousands separators as the size table. It is counted in the clone the
  size table measures, with no further GitHub call or clone, runs no repository code, raises no finding, changes no exit
  and is not in `site/fleet-ci-time.json`.
- FLEET-120: A Python test is a function named `test*` in a Python test file,
  a parametrized one once; a script test is a call of `it` or `test` in a TypeScript or JavaScript test file, bare or
  with a modifier (`skip`, `skipIf(…)`, `runIf(…)`, `only`, `fixme`, `concurrent`, `each(…)`), whose first argument
  is a string or a template; `test.step(`, `test.describe(`, `test.use(`, hooks, a member `x.test(` and a runtime
  `test.skip(<condition>, …)` are no test. The layer comes from the path first: `Live` is a test under
  `tests/e2e/live/` of the root or of a judged project, or under a frontend project's `e2e/live/`; `Flow` is one under
  `tests/e2e/` or a frontend project's `e2e/` otherwise.
- FLEET-121: Every other Python test is `Integration` when an `integration`
  mark reaches it, in the forms `Skipped` reads (on the test, on an enclosing class, in a class's or the module's
  `pytestmark`, or through a module-level name bound to the mark), and `Unit` otherwise; every other script test is
  `Unit`.
- FLEET-122: `Skipped` counts each test a skip mark reaches: Python `pytest.mark.skip` or `skipif`, bare or
  called, on the test, an enclosing class, a module's `pytestmark` (one mark, a list or a tuple) or through a
  module-level name bound to such a mark; script `skip` or `skipIf(…)` on the call, every test inside `describe.skip(`,
  `describe.skipIf(…)(` or `test.describe.skip(`, and a runtime `test.skip(<condition>, …)`, which marks the innermost
  enclosing test or, in a describe outside any test, every test of that describe, and outside any test or describe every
  test of the file; a conditional mark counts even where CI runs the test. The forms of a Python test's
  marks are read by `pytest_marks`, the one owner `adapter-import` and `integration-marker` share, and `suites` maps
  its mark names to these columns. A module's or class's `pytestmark` is read only where the name is bound once in that
  body, by a plain or annotated assignment standing directly in it, to a mark or a list or tuple of marks, and a
  module-level alias only where it is bound once directly in the module body; every other shape (a second binding in
  any form, a binding inside an `if`, `try`, `with`, `for`, `while` or `match`, a value that is no mark) gives no marks,
  so the test counts as unmarked, `adapter-import` exempts nothing and `integration-marker` leaves the module red. A write no
  statement shows (a star import, `globals()`, a conftest hook) is not seen.
- FLEET-123: `Fixme` counts the same way for Playwright `test.fixme`, `describe.fixme` and pytest `pytest.mark.xfail`; a
  test with both marks counts in both columns.
- FLEET-124: A Python test file that does not parse and a script test file
  that is not UTF-8 or that the lexer refuses count no test; `Note` reads `N files not read`, and an unmeasured
  repository reads `cannot judge: <reason>` there with `—` in every number. Named limits: probe and
  `browser`-marked pytest tests outside `tests/e2e/` count by their `integration` mark; an `integration` mark that only
  a conftest hook adds counts as `Unit`, as does an integration layer by folder (`tests/integration/`), and a script
  test has no integration mark; not read are imperative `pytest.skip()` and `pytest.xfail()`, `pytest.param(marks=…)`,
  `from pytest import mark`, marks added by conftest hooks, `-m` or `--deselect` in workflows, `unittest.skip`,
  Playwright `test.fail`, vitest `todo` and `fails` and `.only`; a test written in a loop counts once; the counts are
  static, not what a run skipped.
- FLEET-89: A `.py` file Python cannot parse, or a TypeScript, JavaScript, Svelte or Vue file that is not UTF-8 or
  nested too deeply to read, still has its lines counted;
- FLEET-90: the row's note says how many such files there are, as `N files not valid Python`, `N files not UTF-8`
  and `N files nested too deeply to read`,
- FLEET-91: never which.
- FLEET-1: The matrix stands under a heading naming the newest release tag of `overnightworks/quality` by version, read by an
  anonymous `git ls-remote --tags` and never from the running code's version; an annotated tag counts at the commit it
  points to, a name that is no release tag is ignored, and a listing without a release tag is unreadable.
- FLEET-2: It
  has one `Gate` and one `CodeQL` cell per repository.
- FLEET-3: They come from the job-level `uses:` of each tracked
  `.github/workflows/*.yml|yaml`, parsed as data and never run.
- FLEET-4: A pin to a commit (40 hexadecimal digits, in
  either case) shows the release tag at that commit, with `(behind)` when it is not the newest;
- FLEET-5: `<7 characters> (no release)` when no tag points there,
- FLEET-6: `<ref> (not a commit)` for a branch or tag name,
- FLEET-7: `no gate` or `none` without a caller,
- FLEET-8: and `self` for the repository's own `./` or `$/` file in
  `overnightworks/quality`.
- FLEET-9: Two callers show both, joined by ` · `.
- FLEET-10: The `# v` comment is never read.
- FLEET-11: The owner and repository of a `uses:` match in any case, the workflow path exactly.
- FLEET-12: After `Gate` and `CodeQL` the matrix has `Mandatory jobs` (the number in `[gate] mandatory_jobs` of
  `quality/config.toml`, `no config` without the file, `—` without a gate), `Sonar legacy` (`report` or `fail` from
  `[gate] legacy`, `no config` without the file, `—` without a gate)
- FLEET-13: and one column per rule whose
  enforcement depends on the release or the repository.
- FLEET-14: The rules every release enforces stand once in the
  sentence under the heading.
- FLEET-15: A rule cell says what the pinned gate does with the rule, never whether the
  repository passes it: `enforced`, `report-only` (`size` without `quality/baseline.toml`,
- FLEET-16: `import-contract`
  without an import-linter config,
- FLEET-17: `clone` on v0.4.0 and, from v0.5.0 on, without a `[clones]` table in
  `quality/baseline.toml`,
- FLEET-18: `catalog-check` under a tag, which only reports it (FLEET-98), `logging` and `test-twin` on every release that has them, which report notes only (LOGGING-6, TWIN-6), `spec-citation` without a `[spec_citations]` table in `quality/baseline.toml` (CITE-6, FLEET-118), `literal-text` under a tag below v0.16.0, which only measures, and from v0.16.0 until the release that names the catalogs declaration without a `[literal_text]` table (FLEET-101, FLEET-102; `SwitchChange` in `src/quality/rules.py`)),
  `no adapter contract` (`adapter-import` and `integration-marker`, when the clone's import-linter config holds no `forbidden` contract with the id `core-imports-no-adapter`) (ADAPTERIMPORT-21, INTEGRATION-11),
  `not in <tag>` when the pinned release predates the rule,
- FLEET-19: `not released` when only main has the rule,
- FLEET-20: `no catalogs declaration` (`catalog-check` on main, `literal-text` on main and from the release that names the catalogs declaration as its switch, `locator-text` on main and from v0.21.0 on, `render-pass` on main and from v0.17.0 on, in the gate's own
  repository, when the clone's `pyproject.toml` does not declare `overnightworks-design[catalogs]`; `enforced` when it does)
  (FLEET-99), `no judged project` (`test-layout`, from v0.7.0 on, when the
  clone has no Python project with a package under `src/` and no frontend project (a `package.json` outside every
  project's `tests/`)),
- FLEET-21: and `—` without a gate.
- FLEET-22: Two pins give the oldest release; a pin that is
  no release (a commit without a tag, a branch or tag name, or unreadable release tags) gives `release unknown`. The table of first releases lives in `rules.py`.
- FLEET-23: A workflow file that cannot be read shows `cannot read <file>` in every cell that needs the pin,
- FLEET-100: a
  malformed `quality/config.toml`, `quality/baseline.toml` or `pyproject.toml` and an import-linter config that cannot be read
  or that two files hold show `cannot read <file>` (`import-linter config`) in each cell that reads the file,
- FLEET-24: while a cell that does
  not keeps its value (every cell under no gate, a rule cell under a pin before the rule or `release unknown`),
- FLEET-25: unreadable release tags say so in the heading and show pins as commits,
- FLEET-26: and each makes the exit
  `2`.
- FLEET-27: No value, `behind`, `no gate` or `report-only` included, makes the run red.
- FLEET-92: Found rows are named by GitHub's own `owner/name`,
- FLEET-93: named rows by the entry as written; all are
  [sorted by it, case-insensitively](#quality-report).
- FLEET-94: A private repository is not measured, because the tables are
  published and it is not anonymously readable: one named in `repositories` shows as a `cannot judge` row like any
  repository that cannot be cloned.
- FLEET-95: A repository that cannot be cloned or measured shows `cannot judge: <reason>` (for example `cannot clone (git exited
  128)`),
- FLEET-96: with no local path;
- FLEET-97: every other row still shows, and
  [the exit](#quality-report) is `2`.

## Scanners

gitleaks, actionlint and osv-scanner are not quality's to install: they come through
[mise](https://mise.jdx.dev/installing-mise.html) from the manifest and lock inside quality's installation.

- SCANNER-1: `quality/mise/mise.toml` holds the one version of each scanner, `quality/mise/mise.lock` the sha256 of its
  release asset (linux x64 only, written by `mise lock --platform linux-x64`, never by hand).

The command that installs
them from the lock (SCANNER-2) is

    mise --cd <quality's installation>/quality/mise install --locked

and `check` names it once in its `next:` step, however many rules lack a scanner ([Refusals](output.md#refusals)), the
path filled in.

- SCANNER-3: A path with a control character is shown escaped, on one line.
- SCANNER-4: A scan finds a scanner by
  asking `mise which` in that directory, for exactly the manifest's version,
- SCANNER-5: and runs it only when it reports
  that version.
- SCANNER-6: A scanner on `PATH` is never used.
- SCANNER-7: mise reads only the manifest directory and the
  operator's own user and system configuration: it starts in that directory under `MISE_CEILING_PATHS` set to the
  directory above it,
- SCANNER-8: so a `mise.toml` or `.miserc.toml` in the scanned repository (or in the checkout the
  gate holds the manifest in) takes no part in which binary runs.

Ruled by the operator:

- SCANNER-2: The command that installs a scanner: `mise --cd <quality's installation>/quality/mise install --locked`,
  the release's own manifest and lock; the path is quality's installation, shown as the example shows it.

- SCANNER-9: `check` and `check secrets --staged` exit `2` for a scanner mise cannot locate,
- SCANNER-10: that is at another version
  or cannot report one,
- SCANNER-11: or whose answer names no executable,
- SCANNER-12: and for a machine without mise,
  which names mise's install page and the command; only the rule that needs the scanner cannot judge
  ([rules](rules.md)).
- SCANNER-13: A repository with no workflow needs no actionlint and one with no lock file no osv-scanner.

zizmor (the `workflow-security` rule) and ruff (`python-security`, `python-print`) are not mise's.

- SCANNER-14: They are pinned
  dependencies of quality's package (`pyproject.toml`, hashed in `uv.lock`), so installing quality installs them.
- SCANNER-15: quality runs the executable its own installation recorded, never one on `PATH`.
- SCANNER-16: A missing one,
- SCANNER-17: or one at another version or that cannot report one,
- SCANNER-18: makes its rules `cannot judge` (exit
  `2`) with the two commands that reinstall quality, one in its checkout and one for a tool install, as alternatives under
  `next:` ([Refusals](output.md#refusals)); a scanner quality's own metadata does not pin exactly makes them
  `cannot judge` as a defect in quality's release.

- SCANNER-19: One `check` run and one `check secrets --staged` run each have one deadline of 540 seconds, started when the run's
  inputs are built, after the release row. gitleaks (in both), zizmor, actionlint, osv-scanner and ruff
  have it as their only time bound, and the advisory database requests end at the earlier of their own
  deadline and the run's. A rule that would start one of them, or a request, with no time left starts nothing, and a
  scanner or request still running when the time ends is stopped, gitleaks with its whole process group so the `git`
  it starts dies too, and its scratch directory and mirror are deleted.
- SCANNER-20: Both make the rule `cannot judge` (exit `2`),
  worded as in [Refusals](output.md#refusals), with no step of their own and the other rules' rows standing. The calls
  that locate a scanner (`mise which`) and ask it for its version run under the same deadline and
  keep their own 30 seconds, taking the smaller of 30 seconds and the time left (SCANNER-19): with no time left they start
  nothing, and one still running when the run ends is stopped and cuts the rule the same way, `mise` or the scanner being
  its subject and no install or reinstall step following; with 30 seconds the smaller, the call ends at 30
  seconds with its step (SCANNER-9, SCANNER-10, SCANNER-17). [The vet](output.md#the-vet) stays outside the clock.

Ruled by the operator: quality builds the logic and the process itself and never a specialist scanner (language
linters and test runners, gitleaks, osv-scanner, mutation tools); the scanners it uses run pinned underneath the one
door (SCANNER-5).

## `quality merge`

- MERGE-53: `quality merge <owner/repo>#<pr> [<owner/repo>#<pr> ...] [--dry-run] [-v]` is the operator's door past a red
  gate: it merges the pull requests that the gate keeps red only for legacy Sonar findings or for
  [declared loosenings](gate.md#declared-loosenings), each on the operator's own `yes`. The steps to take are in
  [the runbook](runbooks/merge.md); this is what the command does, and [the lines on several names](#several-names)
  say what a run over more than one pull request adds.

Ruled by the operator:

- A body the door refuses for its line (MERGE-67) stops at `! landing  refused` under the pull request's row instead,
  the same row as the last one before the question (MERGE-5).
- `merge` names no aco step; that step belongs in the head's instructions.

- MERGE-54: **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository
  (checked first: exit `2`, nothing changed,
- MERGE-55: the missing right named). Agents never run it; it
  refuses when stdin is not a terminal. This is the agent contract, not a lock: agents on the operator's machine share
  his `gh` login, so to an agent the terminal is a hurdle, and that login could switch `quality_gate` directly. A real
  lock (agents with a token without admin rights) belongs to the machine's setup, owned by
  [overnightworks/platform](https://github.com/overnightworks/platform), not to quality; it is a known residual risk.
- MERGE-56: **It refuses unless the door is the only way** (exit `1`, nothing changed): the pull request is open
- MERGE-57: and
  no draft (a draft is refused before its body or any check is read,
- MERGE-58: `next:` naming `gh pr ready`);
- MERGE-59: no check is still running;
- MERGE-60: every check run and commit status
  other than `quality / sonar` and `quality / verdict` is green;
- MERGE-61: `quality / verdict` has failed;
- MERGE-62: and `quality / sonar` is green or has failed (it did not run, or it ended `timed_out`, `cancelled` or anything else:
  refused).
- MERGE-82: The verdict is then judged again by its own decider, with the config, baseline and import-linter configs read at the
  pull request's current merge commit and at the tip the base comparison named (MERGE-106)
- MERGE-83: and the Sonar job
  taken as green:
- MERGE-84: an
  undeclared loosening,
- MERGE-85: a declaration that matches nothing,
- MERGE-86: or a missing or red
  mandatory job is refused, naming the verdict's lines.
- MERGE-89: When both sides hold a baseline, or a contract list
  drops a module or a contract is removed, the files git renamed between the two commits count as they do in the
  verdict and the files each commit holds are listed as the verdict lists them, so the door fetches the pull request's
  compared tip and merge commit
- MERGE-90: (`git fetch --depth=1` from `https://github.com/<owner>/<repo>.git`) into a
  temporary bare repository that is removed afterwards,
- MERGE-91: also when the command is interrupted.
- MERGE-92: This
  needs `git` and `gh` on the path. git runs with an empty home
- MERGE-93: and no inherited `GIT_*` variable,
- MERGE-94: so `.netrc` and git's own configuration count for nothing; `gh` is the only credential source,
- MERGE-95: told where
  its login lives (`GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`),
- MERGE-96: and reaches a private
  repository through git's credential helper.
- MERGE-97: git never prompts and an askpass program is never run.
- MERGE-98: The fetch, the file listing and the rename list share one limit of 120 seconds each,
- MERGE-99: and
  each of these git commands runs in its own process group, which is killed with it at that limit
- MERGE-100: or when
  the command is interrupted. git's own output is never printed:
- MERGE-101: a fetch, file listing or rename list
  that fails, or a rename list that came with a warning, could not be checked, and its `✗ loosenings` row says so in
  the door's words (exit `2`),
- MERGE-102: never an empty file or rename list.
- MERGE-104: **The head holds the base's tip** (exit `1`, nothing changed, no log comment): after the landing line and before the
  checks
- MERGE-103: the door reads GitHub's comparison of `refs/heads/<base>`, the live tip, with the pull request's
  head sha, one commit per page. A head that lacks commits of the tip is `! base  behind  <n> commit(s)
  behind <base>` and `error: not merged · the head lacks <n> commit(s) of <base>, so its checks did not run on what
  would merge · nothing changed`, `next:` being `gh pr update-branch <pr> --repo <owner/repo>` (on a fork the author may
  have to allow maintainer edits or update the branch himself), then waiting for the checks, then the door again. A head
  that contains the tip is `✓ base  up to date  <base> at <tip, 7 characters>`, the row after the
  pull request's (MERGE-104).
- MERGE-105: A comparison that cannot be read, or whose `behind_by` is no whole number of zero or
  more or whose `base_commit.sha` is no commit sha, is the `✗ base` row (exit `2`), never up to date.
- MERGE-106: The
  verdict's base side and the fetch of the base commit use the compared tip, never the `base.sha` the pull request
  names.
- MERGE-107: After `yes` and before the gate is touched the comparison is read again, outside the gate's
  restore: another tip than the one shown is `! base  moved` with `error: not merged · <base> moved since the rows
  were shown · nothing changed` (exit `1`, no log comment), `next:` being the update step where the head is now behind,
  else the door again; Ctrl-C during that read ends the run interrupted (exit `130`) with nothing changed.
  Because the head contains the tip and the default branch only moves forward (`non_fast_forward`), every check run on
  the head tested the tree that merges. A non-default base that is rewound can leave a head tested against commits
  since removed, and the few seconds between the gate request and the merge request stay open, since GitHub pins only
  the head.
- MERGE-63: **The landing line.** The pull request body carries exactly one `Work-Item:` or `No-Item:` line
- MERGE-64: outside a
  fenced code block,
- MERGE-65: as the fleet's landings read it: `Work-Item: #<n>` or `Work-Item: <owner>/<repo>#<n>` of
  this repository,
- MERGE-66: or `No-Item: docs`, `fix` or `deps`.
- MERGE-67: Otherwise its `! landing` row names why
- MERGE-68: and `next:` asks for the one line (exit `1`, nothing changed).
- MERGE-69: The merge commit's message is the
  body without that line, a blank line, then the line as `Work-Item: #<n>` or `No-Item: <kind>`, the last paragraph (a
  body that holds only the line gives the line alone), the trailer a landing is read from.
- MERGE-70: The body is the
  one read with the head.
- MERGE-87: **Sonar green: declared loosenings.** The re-derived verdict must hold at least one declared loosening (otherwise
  `re-run the verdict`).
- MERGE-88: SonarCloud is not asked and `SONAR_TOKEN` is not needed.
- MERGE-71: **Sonar failed: legacy findings,** with or without a declared loosening:
- MERGE-72: `[gate] legacy` is `fail` at the
  merge commit
- MERGE-73: (under `report` the Sonar job cannot be red on legacy findings, and a missing config reads as
  `report`),
- MERGE-74: SonarCloud's analysis of the pull request is of the head being merged
- MERGE-75: and adds no
  finding (the findings are named otherwise),
- MERGE-76: and the default branch has legacy findings.
- MERGE-77: `SONAR_TOKEN` is read from the environment and sent when set,
- MERGE-78: never shown in a row:
- MERGE-79: without it
  the door reads a public project anonymously, with no Authorization header,
- MERGE-80: and an anonymous read that
  SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN`;
- MERGE-81: with a token, a 401 or 403 says the token was rejected.
- MERGE-1: **It shows, then asks:** on stdout the [release](#which-release-judges) that judges first, then a row as each step
  passes:
- MERGE-2: the pull request (`open`, head, base), the `base` row, the checks, the declared loosenings, each
  [whole](output.md#length) with its `item` and `date` and its `reason` on the line below,
- MERGE-3: the legacy count
  where Sonar failed,
- MERGE-4: the merge method (`allowed on <base>`),
- MERGE-5: and last the `landing` row naming the
  line the merge commit ends with
- MERGE-6: (`work item` and `#<n>`, or `no item` and its kind); then, on stderr,
  `Switch quality_gate off, merge #<pr>, switch it on? Type yes: `.
- MERGE-7: Only `yes` in lower case, ended with
  Enter, merges
- MERGE-8: (whitespace around it counts for nothing;
- MERGE-9: `Yes` or `y` change nothing);
- MERGE-10: any other line, or the end of input (Ctrl-D) before Enter, prints `error: not confirmed · nothing changed` and
  `next:` naming the command again (exit `1`);
- MERGE-11: Ctrl-C at the question ends the run interrupted
  (exit `130`);
- MERGE-12: nothing has changed in either case.
- MERGE-13: With stderr closed the question and the
  pairs print nowhere; nothing moves to stdout.
- MERGE-14: A refusal is its `!` row and the `error:`/`next:` pair on
  stderr (exit `1`),
- MERGE-15: each blocking line of the verdict [whole](output.md#length) below the `loosenings`
  row;
- MERGE-16: a step that cannot be judged is its `✗` row and the pair (exit `2`);
- MERGE-17: a
  `quality/config.toml` it cannot read at the base or at the merge commit is the `✗ loosenings` row with the config's
  fact, and under `next:` [the example to copy](output.md#refusals) where the fact has one,
- MERGE-18: then the step
  for that commit: `fix it on <base branch> first` for the base,
- MERGE-19: which is read first,
- MERGE-20: or
  `push the fix to the pull request's branch, then run quality merge <owner/repo>#<pr> again` for the merge commit;
- MERGE-21: no terminal on stdin is the pair alone (exit `1`); a name not of the form
  `<owner>/<repo>#<pr>` is [a wrong invocation](#help-and-wrong-invocations) (exit `2`).
- MERGE-22: Every line it prints is
  escaped as [the other doors' lines are](output.md#masking), so a `reason` cannot hide anything before `yes` or forge
  a line.
- MERGE-23: **Dry run:** `--dry-run` reads and shows every row the door shows,
- MERGE-24: refuses where it refuses before the question (the read after `yes` it cannot make),
- MERGE-25: and in place of the question ends with
  `~ dry run · would switch quality_gate off, merge #<pr>, switch it on · nothing changed` on stdout (exit `0`);
- MERGE-26: it reads no answer
- MERGE-27: and sends no request that changes anything (no `gh api -X`), and it needs a terminal on stdin like the door.
- MERGE-28: `-v` is taken and adds nothing:
  the door runs no scanner.
- MERGE-29: **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this takes the fleet's ruleset off
  it), runs `gh api -X PUT repos/<owner/repo>/pulls/<pr>/merge --input -`, the JSON on its stdin
  holding `sha` (the head sha), `merge_method` and `commit_message` (the message of MERGE-69, sent as it is; no
  `commit_title`, so a squash takes its headline from the repository's squash title setting).
- MERGE-30: The request merges or
  fails and has no merge queue or auto-merge path; `#<pr> merged` is shown only on an answer with `merged: true`, and
  anything else (a refused or failed request, no answer, Ctrl-C, an answer without `merged: true`) is read back as
  below
- MERGE-31: (`merge_method` is `squash` where the repository settings or a rule on the pull request's base
  branch exclude a merge commit but allow a squash, else `merge`;
- MERGE-32: when neither is allowed, or the settings or rules cannot be read
  or are malformed (an answer or a page that is no array, a rule that is no object, or whose `parameters` are present
  but no object), its `✗ merge` row ends the run before the gate is touched (exit `2`);
- MERGE-33: the shown text and the log comment name the method),
- MERGE-34: and sets the property back to `true`
  in every case: merged, merge failed, Ctrl-C.
- MERGE-35: After `yes` each step is a row on stdout as GitHub answers it
  (`quality_gate  off`, `#<pr>  merged` with the method, `quality_gate  on`, `log comment  posted`),
- MERGE-36: ending
  `✓ merged #<pr> · quality_gate on again · <duration>` (counted from `yes`) (exit `0`).
- MERGE-37: A step
  not done is a `✗` row (`off failed`, `not merged`, `state unknown`, `not restored`, `not posted`) and one pair on
  stderr, exit `2`,
- MERGE-38: the gate first (`quality_gate MAY STILL BE OFF on <owner/repo>` where it is not on),
- MERGE-39: `next:` the commands that finish it, the gate's first, alone on its line and
  [never shortened](output.md#length).
- MERGE-40: A gate not switched on is its row before any further request.
- MERGE-41: After Ctrl-C the next line starts on a line of its own.
- MERGE-42: A merge whose outcome GitHub does not
  confirm (Ctrl-C, a lost answer) is read back after the gate is on, its row below the gate's;
- MERGE-43: if that does
  not settle it, the row is `state unknown` and the pair says `#<pr> may be merged` with `gh pr view`.
- MERGE-44: Before
  the question a stdout that refuses a row stops the door (exit `2`, nothing changed);
- MERGE-45: after `yes` such a
  stdout takes no further row and no closing line, and the run does every step and keeps its exit.
- MERGE-46: **Logged:** one comment on the pull request, opening `quality merge run by @<login>`
- MERGE-47: (who, when,
- MERGE-48: the legacy count
- MERGE-49: and each declaration,
- MERGE-50: `quality_gate off → <outcome> → on`
  (`<outcome>` is `merge`, `merge failed` or `merge state unknown`),
- MERGE-51: or
  `quality_gate not switched off → no merge → on` when switching off failed),
- MERGE-52: also when the restore failed,
  the comment then ending `NOT restored, it may still be off` in place of `on`. A comment not posted is its
  `✗` row, its command under `next:`.

### Several names

Ruled by the operator:

- MERGE-108: `quality merge` takes several pull requests in one run; each is judged, shown with its rows and refused exactly as a
  run for it alone is, every MERGE line holds per pull request and the `quality_gate` property is switched off and
  restored per repository, and each merges only on the operator's own `yes` to it.

`quality merge A#1 B#2 ...` judges and shows each named pull request one after the other and merges each on the
operator's own `yes` to it (MERGE-108). It takes the names given and finds none: the door reads only the repositories
it is named. Every line of this section above holds per pull request, because each pull request is a run for it alone.

- MERGE-109: **A process per pull request.** For each name the run starts `<python> -P -m quality.cli merge <name>` with
  `--dry-run` and `-v` as given, the interpreter being the one that runs the door, and waits until it has
  exited before it starts the next, in the order named, two of one repository included (MERGE-108). The child judges
  at its own pinned release, switches the repository's `quality_gate` off and on again itself and shares nothing with
  the next. `-P` keeps the working directory off the child's module path, so a `quality` package in a pull
  request's checkout never loads under the operator's login. A second pull request of a repository is
  judged from scratch, and after a merge moved its base it is refused as behind (MERGE-108).
- MERGE-110: **Rows.** Before each child one `· <n> of <total>  judging  <name>` row names the pull request and its
  place. The run flushes its output and discards what the terminal holds typed ahead before each child, so a
  line typed during one pull request never answers the next one's question; the run itself reads no line.
- MERGE-111: Each child's own rows and closing line or pair stand as a run for it alone prints them. At the end one
  row per name says what that child came to, from its exit alone: `✓ merged` (exit `0`), `~ would merge` (exit `0` of
  a dry run), `! not merged` (exit `1`), `! interrupted` (it ended by the interrupt), `✗ stopped` (exit `2`),
  `✗ killed` with `signal <n>` (any other signal) and `· not run`.
- **Refused: the run goes on.** A child that exits `1` (a refusal, a `no`, a moved base) leaves nothing changed, and
  the next name runs (MERGE-111).
- MERGE-112: **Stopped: the run ends.** A child that exits `2` ends the run, because the run cannot tell an unjudged read from a
  gate that may still be off; the later names show as not run. The pair says `stopped at <name>` and the
  count, and under `next:` names first the step that child's own pair printed above, to be done at once, then
  `quality merge` with the names not run.
- MERGE-114: **Killed by a signal.** A child killed by a signal other than SIGINT printed no pair: the run says
  `quality_gate MAY STILL BE OFF on <owner/repo>`, names the restore command first and then `quality merge` with the
  names not run, ends `2` and starts no child.
- MERGE-113: **Interrupted.** Ctrl-C reaches the child and the run together. The child ends as a run for it alone does; the run
  waits for it, never kills it, starts no further child and ends interrupted. A Ctrl-C that arrives
  outside the wait (as a child starts, or while the run prints its rows, summary or pair) is noted, not raised: the run
  still waits for a started child and prints the summary and the pair, a restore step included. SIGTERM to the run alone
  ends the run and leaves the child to finish and restore the property by itself.
- MERGE-118: **The exit.** The run exits `2` if a child exited `2` or was killed, else `130` if it was interrupted, else `1` if
  a child exited `1`, else `0`: an interrupted run never exits `0` or `1`, whatever the interrupted
  child's own exit was, so a gate that may be off is never hidden behind an interruption.
- MERGE-117: **The last line.** All merged ends `✓ merged <n> of <n> · <duration>` on stdout (MERGE-111); a dry run ends
  `~ dry run · would merge <n> of <n> · nothing changed`. Exit `1` ends with the pair
  `merged <m> of <n> · <k> not merged · nothing changed` and `next:` the command for the names not merged (MERGE-111);
  exit `2` with the pair of the stopped child and the names not run (MERGE-112), `--dry-run` carried in the command
  where the run had it. An interrupted run ends with its rows and no pair (MERGE-113).
- MERGE-115: **No terminal.** Without a terminal on stdin the run is the pair of MERGE-21, exit `1`, and no child starts.
- MERGE-116: **A word that names no pull request,** among several, is refused naming that word, before any child starts (exit
  `2`). With one name the door runs in the same process and prints as it does without this section
  (MERGE-117).

A terminal that hangs up between the switch-off and the restore leaves the property off without a word, as a run for
one pull request does; a name given twice is judged twice.
