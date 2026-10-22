# Commands

Reference for the engineer who runs `quality scan-staged`, `quality scoreboard` or `quality engines`.

## `quality scan-staged`

`quality scan-staged [PATH] --engines DIR` scans exactly what the next commit records: the effective index of the
repository at `PATH` (default `.`) compared with `HEAD`. It is meant for a pre-commit hook, which runs it in the
work-tree top. A relative `GIT_INDEX_FILE` is resolved against the working directory, as git does, so `git commit -a`,
`git commit <path>` and a linked worktree scan the index git is committing.

The index is written as a tree (`git write-tree`, only tree objects a commit writes anyway) and read into a scratch
repository inside a 0700 scratch directory. The scratch repository borrows the objects (`objects/info/alternates`),
has an empty work tree and none of the live repository's config or `info/attributes`, so a staged `.gitleaksignore`,
`.gitleaks.toml`, `.gitattributes` with `-diff`, or a `gitleaks:allow` comment changes nothing. gitleaks runs in the same
attribute-free environment as `hygiene`, with quality's config. The allow list is the staged tree's own
`quality/config.toml` (none staged: no allowances). `scan-staged` validates only the `[secrets]` table of that config and ignores other tables
and keys, while `hygiene` and `verdict` stay strict, so schema growth never blocks a commit. Output and secret handling
are `hygiene`'s:
`secret src/a.py:3 - gitleaks rule aws-access-token matched in the staged change (sha256 <64 hex>, value not shown).`

gitleaks' staged mode sees only added and changed lines, so a known false positive blocks only the commits that touch
its line, and then needs its allowance, as in CI. A key only in the work tree or only in `HEAD` is clean. Exit `2` (never
clean): an engine missing or at the wrong version, git older than 2.41, an unreadable allow list, an index with unmerged
entries, a path that is not a repository, or a gitleaks run without a report.

## `quality scoreboard`

`quality scoreboard [PATH]` prints two markdown tables, the size of every repository `quality/fleet.toml` lists
(`public = [...]` and `private = [...]`, each a list of URLs, each listed once), under a heading with the date, the
quality version and that functions and CCN are measured for Python only, then the same rows as a repo × rule matrix. With `GITHUB_STEP_SUMMARY` set, the same text is
appended there. The fleet list lives once, in `quality/fleet.toml`; `.github/workflows/scoreboard.yml` runs it every
Monday at 05:17 UTC and on dispatch; the tables stand in that run's summary. The run only reports: it writes to no repository and needs no secret. A missing, malformed or
duplicating `quality/fleet.toml` is `cannot judge`, exit `2`.

- A `public` URL is cloned with `git -c credential.helper= clone --depth 1 --no-tags`, `GIT_TERMINAL_PROMPT=0` and no
  askpass, into a temporary directory that is removed on every exit, and its default branch tip is measured.
- git runs anonymously: without system, global or `GIT_CONFIG_*` config and with an empty `HOME`, so no configured
  header, helper or `~/.netrc` reaches the host. Only `http`, `https` and `file` URLs without credentials in them are
  used; any other URL is `cannot judge`.
- Lines are the physical lines of the tracked files of the `size` rule's languages, the count its 800-line limit uses
  (blank and comment lines included); `Languages` lists each with its lines, production and tests together, largest
  first, ties by name, and `—` for a row with no measured file. Production versus test is the `size` rule's file-name rule, so `scripts/test_inventory.py` counts as test.
  `test:prod` is test lines divided by production lines with two decimals, `—` without production lines.
  The debt columns count files from 800 lines, and Python's functions from 60 lines and with a CCN above 15.
- `Clone pairs prod / test` counts the `clone` rule's pairs (two near-identical functions in two files, at 40 tokens and
  90 %); a pair counts as test only when both sides are tests, so a test that copies production code counts as prod.
- A `.py` file Python cannot parse still has its lines counted; the row's note says how many such files there are, never which.
- Both tables name a row by the last two path segments (`owner/name`) when its URL is not a `file://` one and has a host and at
  least two path segments; any other URL is named by its last path segment, else its host. A `|` in a name prints as `\|`.
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
  `clone` on v0.4.0 and, where the gate knows enforcement (main today), without a `[clones]` table in
  `quality/baseline.toml`), `not in <tag>` when the pinned release predates the rule, `not released` when only main has
  the rule,
  and `—` without a gate. Two pins give the oldest release; a pin that is no release (a commit without a tag, a branch or
  tag name, or unreadable release tags) gives `release unknown`. The table of first releases lives in `scoreboard.py`.
- A workflow file that cannot be read shows `cannot read <file>` in every cell that needs the pin, a malformed
  `quality/config.toml` or `quality/baseline.toml` and an import-linter config that cannot be read or that two files hold
  show `cannot read <file>` (`import-linter config`) in each cell that reads the file, while a cell that does not keeps its value
  (every cell under no gate, a rule cell under a pin before the rule or `release unknown`), unreadable release tags say so in the heading and
  show pins as commits, and each makes the exit `2`. No value, `behind`, `no gate` or `report-only` included, makes the run red.
- A `private` repository is never measured, because the tables are published: it shows `not measured: private`. It is
  checked once with an anonymous `git ls-remote`; if anyone can read it, the row is `cannot judge: declared private
  but readable`.
- A repository that cannot be cloned or measured shows `cannot judge: <reason>` (for example `cannot clone (git exited
  128)`), with no local path; every other row still shows, and the exit is `2`. Otherwise the exit is `0`.

## `quality engines`

`quality engines install DIR` verifies each pinned engine first and downloads only one that is missing or does not report
its pinned version, so a second run downloads nothing (`gitleaks 8.30.1 already installed`; otherwise `... installed`). Every engine is a `tar.gz` release asset unless
its pin sets `format = "binary"` (osv-scanner, a bare executable), which is installed as downloaded after its sha256 check.
`quality engines verify DIR` is exit `0` when every pinned engine in `DIR` reports exactly its pinned version, otherwise
exit `2` with one line per engine.
