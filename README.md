# quality

`quality` is the one door for fleet quality checks.

    quality hygiene [PATH]
    quality baseline write [PATH]
    quality scan-staged [PATH] --engines DIR
    quality engines install DIR
    quality engines verify DIR
    quality sonar preflight [PATH]
    quality sonar findings
    quality verdict [PATH]
    quality scoreboard [PATH]
    quality emergency-merge OWNER/REPO PR

Where the fleet is going and why: [docs/roadmap.md](docs/roadmap.md) (target state, waves, per repository) and [docs/rulings.md](docs/rulings.md) (the operator's rulings, verbatim and dated).

Install it with

    uv tool install "overnightworks-quality @ git+https://github.com/overnightworks/quality@<tag or commit sha>"

The gitleaks paths (`hygiene --engines`, `scan-staged`) need git 2.41 or newer; with an older git they exit `2`.

`hygiene` reads git's view of the repository `PATH` (default `.`) lies in, from its top level, so a run from `src` judges
the same files and the same `quality/baseline.toml` as one at the top, and names every file relative to the top (`size
src/big.py`). A `PATH` in a submodule or a linked worktree means that work tree's own top. It applies five rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
  The fix is `git --literal-pathspecs rm --cached -- <name>`, the name quoted for the shell, so a copy untracks
  exactly that file; a name that is not printable UTF-8 gets the hint to remove it by its bytes (`git ls-files -z`
  shows them) instead of a command.
- `compose-port`: every published port in a tracked compose file binds loopback, `127.0.0.1` or `::1` (`"[::1]:HOST:CONTAINER"`,
  `host_ip: ::1`); `[::]` and `0.0.0.0` are findings. A compose file is a `.yml` or `.yaml` file whose name holds the
  word `compose` between `.`, `-` or `_`, in any case: `docker-compose.ci.yml` and `keycloak/bootstrap.compose.yaml` are
  judged, `composer.yml`, `compose2.yaml` and `compose.yaml.example` are not. Only tracked files count: an untracked
  `compose.yaml` is never judged, one added with `git add` is. A tracked file missing from disk (deleted, or a symlink
  to nowhere or in a loop) is skipped; one the filesystem refuses to read is `hygiene: compose-port cannot judge - ...`.
  A file is read as Compose reads it: `- 22:22` is the text `22:22`, and `!override` and `!reset` are read as the
  value they wrap; any other tag (`!foo`) and any YAML error is `cannot judge`, in one line with its line and column
  (or its position for a character YAML refuses, `nested too deeply`, `a value does not fit its tag`, or `not valid
  YAML`). A port that is neither text, a number nor a mapping whose `published` (or, without one, `target`) is text or
  a number is a finding. A service with `network_mode: host` publishes whatever its process binds, and this rule does
  not check that yet (an allowlist follows in Q1b-3g).
- `size`: every tracked file of Python, TypeScript (`.ts` `.tsx` `.mts` `.cts`), JavaScript (`.js` `.jsx` `.mjs`
  `.cjs`), Svelte or Vue is measured by its lines, and every tracked `*.py` file also with
  [lizard](https://github.com/terryyin/lizard): a file from 800 lines, a Python function from 60 lines and a Python
  function with a CCN above 15 are debt, counted apart for production and tests (a path under `test/` or `tests/`;
  Python also `conftest.py`, `test_*.py`, `*_test.py`; TypeScript and JavaScript also `__tests__/`, `*.test.*` and
  `*.spec.*`). A minified or generated file is counted by its lines like any other.
  Functions of the other languages are not measured: lizard misplaces their boundaries
  ([terryyin/lizard#521](https://github.com/terryyin/lizard/issues/521)). Functions are named by their qualified
  symbol (`Class.method`); a second one of the same name, such as a property setter, is `Class.method#2`. Without
  `quality/baseline.toml` the rule only prints the counts (`size: report only (no quality/baseline.toml) - production:
  1 file(s) >= 800 lines, ...; languages: Python, TypeScript (functions in Python only)`, the languages largest
  first) and the size debt leaves the exit unchanged.
  A `.py` file is read the way Python reads source (a PEP 263 coding cookie or a UTF-8 BOM decides the encoding). One
  Python cannot decode or parse is named as not valid Python: without a baseline the count notes it (`1 file(s) not
  valid Python, functions unmeasured: bad.py`) and the exit is unchanged; with a baseline it is `hygiene: size
  unparsable - bad.py is not valid Python (...)`, and `quality baseline write` refuses with `baseline: not written -
  bad.py is not valid Python (...)`; both exit `2`. A tracked file the operating system refuses to read is `hygiene:
  size cannot judge - ...` with or without a baseline, and `quality baseline write` refuses with `baseline: not written - ...`; both exit `2`.
  The reason names the case: a file without read permission is `cannot read locked.py: [Errno 13] ...`, a file behind
  a directory without permission is `cannot reach pkg/m.py: [Errno 13] ...`. A tracked file missing from disk
  (deleted, or a dangling symlink) is skipped. A `quality/baseline.toml` that is not UTF-8 is malformed like invalid
  TOML and says to rewrite it with `quality baseline write`.
  With `quality/baseline.toml`, the tree on disk must hold the baseline: a new violation, an entry that grew and an
  entry above its measured value (stale: lower it with `quality baseline write`) are findings; an entry whose file or
  function is gone is fine.
  `quality baseline write [PATH]` writes the measured debt to `quality/baseline.toml` at the top of the repository `PATH`
  lies in (from `src`: `baseline: wrote 2 entries to ../quality/baseline.toml`; a stray `src/quality/baseline.toml` is
  neither read nor removed) as sorted TOML, one table
  per metric (`ccn`, `file_lines`, `function_lines`) keyed by path and qualified symbol. Before its `baseline: wrote N
  entries ...` line it names, in the verdict's wording, each entry it raises or adds against the previous file
  (`baseline: raises function_lines src/quality/cli.py main 82 -> 90`, `baseline: adds ccn src/a.py f 16`); a lowered
  or removed entry is not named, and without a previous file only the count prints. A previous file it cannot read is
  overwritten with `baseline: the previous quality/baseline.toml could not be read (...), so raises cannot be named`.
  The write exits `0` with raises too: the verdict is the gate.
- `clone`: a pair is two functions (methods and nested functions too) in two different tracked `*.py` files, each of at
  least 40 tokens without its docstring, whose structure is at least 90 % the same once every name and literal is
  renamed; tests count like code, and a copy inside one file is no pair. A pair is named by the path and qualified
  symbol of both sides, as `size` names a function. For now the rule only prints the counts (`clone: report only (no
  [clones] in quality/baseline.toml) - production: 1 pair(s), tests: 0 pair(s)`); a pair counts as tests only when both
  files are tests. A file Python cannot parse has no pair.
- `import-contract`: the repository's [import-linter](https://github.com/seddonym/import-linter) contracts must be
  kept. The config is the tracked `.importlinter` or `setup.cfg` (`[importlinter]`) or `pyproject.toml`
  (`[tool.importlinter]`), counted once its name exists in the tree: a tracked one deleted from disk is none, a symlink to
  nowhere cannot be read (exit `2`); without one the rule prints `import-contract: report only (no import-linter config)` and
  leaves the exit unchanged. import-linter 2.15 runs inside quality as a library, never its CLI, and writes nothing to
  the checkout (no `.import_linter_cache`). A broken contract is one finding on the config file, naming the contract
  and the chain as import-linter renders it (`shop.db -> shop.web (l.1)`), exit `1`. An `ignore_imports` entry no
  import matches follows the contract's `unmatched_ignore_imports_alerting`: `error` (the default) is a finding (`...
  ignores a -> b, which no import matches`), `warn` one `import-contract: note - ...` line, `none` nothing. Each root
  package is the one tracked `<dir>/<name>/__init__.py` outside the tests. The rule cannot judge (exit `2`, before any
  caller code runs or any file outside is read) two configs that each hold a section, a `contract_types` option
  (plugins would be imported), a type other than forbidden, layers, independence, protected and acyclic_siblings,
  invalid contract options, a config value of the wrong type (`... in pyproject.toml cannot be judged: ...`), a
  dotted root package, a root package with no or several such directories, one Python finds in another place (`root
  package yaml is src/yaml/__init__.py, but Python finds it outside the repository at site-packages/yaml/__init__.py`, a
  place outside named from the `sys.path` entry it lies under; a place inside is named repository-relative), a symlink
  under a root package that leaves the repository, directly or through a link that stays inside, and a symlink loop, and a `.py` file that "cannot be examined" (no regular file, a name or content that is not valid
  UTF-8, a coding cookie in any spelling but utf-8, utf8, unicode-1-1-utf-8, unicode11utf8, x-unicode20utf8, ascii, us-ascii or ansi_x3.4-1968 (accepted in any letter case), or one the system will not let it read). A
  module grimp cannot parse is `hygiene: import-contract unparsable - ...`; a warning grimp gives while building the
  graph is one `import-contract: note - ...` line. No message names an absolute local path.
- `claude-md`: a tracked `CLAUDE.md`, in any case (`claude.md` is named as it is), holds exactly `@AGENTS.md` with or
  without a final line feed, so Claude, Codex and Grok read the same guidance, and the repository tracks an `AGENTS.md`
  beside it. No `CLAUDE.md` is clean, with or without an `AGENTS.md`. A symlink is a finding (a dangling one too), and so
  is a tracked `.claude/CLAUDE.md`, which Claude Code reads as project memory and Codex and Grok never read. Any other
  bytes are a finding, a Windows line ending included; a nested `docs/CLAUDE.md` is not judged. The reason and the fix
  are fixed prose that names the file and never shows its content; every fix is prose with no command, and for a file's
  own text it says to move the lines into `AGENTS.md`. A tracked file missing from disk is skipped; one the filesystem refuses to read
  is `hygiene: claude-md cannot judge - ...`. No configuration switches the rule off.
- The baseline only falls: on a pull request `quality verdict` holds the head's `quality/baseline.toml` against the
  base commit's, so growing a function and rewriting the baseline stays red. Each loosening is one line, named with the
  baseline's own metric, path and symbol (a file entry has no symbol):
  - an entry raised: `loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90`;
  - an entry the base does not hold, a moved or renamed one included:
    `loosened (undeclared): size baseline adds ccn src/a.py f 16`;
  - the base's file deleted: `loosened (undeclared): quality/baseline.toml removed`.

  A lowered, equal or removed entry is green, and so is adopting a baseline (the base has none) or having none on
  either side. A malformed baseline on either side, a head config or baseline that cannot be read or links out of the
  repository, or a base commit the clone lacks is exit `2`, naming the side (`quality/baseline.toml at the base commit
  <sha>: ...`, `quality/config.toml at the head: ...`). Only the head's malformed baseline adds ``rewrite it with
  `quality baseline write` ``: a pull request cannot rewrite its base. The base must be a ref
  name or a SHA: `HEAD~1` or `-x` is exit `2` (`... is not a ref name or a SHA`) before git runs. Outside a pull
  request (push, `merge_group`, `workflow_dispatch`) `gate.yml` passes no base, so the base is never read and only
  `hygiene` holds the tree. A lizard update that measures a function higher therefore turns the pull request that
  takes it red; a ruled raise is declared (see [Declared loosenings](#declared-loosenings)) and stays red until the
  operator's door merges it.

With `--engines DIR` the `secret` rule also scans the whole history reachable from `HEAD` with gitleaks (a key removed later is still found; other branches the checkout fetched are not scanned), always with quality's own config
(`src/quality/gitleaks.toml`): the repository's `.gitleaks.toml`, `.gitleaksignore` and `gitleaks:allow`
comments have no effect. A known false positive is allowed by the value, not the place: the repository's
`quality/config.toml` names the gitleaks rule, the file and the SHA-256 of the finding's secret (UTF-8, lowercase hex)
with a reason:

    [secrets]
    allow = [{ rule = "aws-access-token", file = "tests/fixture.py", secret_sha256 = "<64 hex>", reason = "fixture value" }]

One entry allows that value in that file at any line and in every commit that holds it. Another value in the same
place is red, and the same value in another file is red. A red finding prints its hash
(`secret src/a.py:3 - gitleaks rule aws-access-token matched at 76ec522b (sha256 <64 hex>, value not shown).`), so
the entry is copied from the output. An entry without a reason, a hash that is not 64 lowercase hex, a duplicate
`(rule, file, secret_sha256)` and the old `fingerprint` form are exit `2`.

A missing `quality/config.toml` means no allowances: the scan still runs and its findings are red. Only a file that
exists and cannot be read or parsed, or links out of the repository, is exit `2`; a link that leads nowhere or loops
exists, so it is exit `2` too.

Secret handling: quality runs gitleaks without `--redact` so the report carries each `Secret` to be hashed. gitleaks
writes that report to a `tempfile` scratch directory (mode 0700); quality reads it, keeps only the hash, and deletes
the directory on every exit path (success, gitleaks failure, parse error). The value reaches no stdout,
stderr, log, exception text or retained file; gitleaks runs without `-v`. While the scan runs, the value exists in
that report file and in quality's process memory. The boundary is the deletion and the absence of any other sink,
not memory-only handling. The hash is safe to print: it appears only where the history already shows the content,
and a real secret must be rotated anyway.

The scan runs git without any attributes (not the repository's `.gitattributes`, not `info/attributes`, not the
system or global file), so a `-diff` or `binary` line cannot hide a key. That needs git 2.41 or newer; an older git
is exit `2`. The system file is excluded by `GIT_ATTR_NOSYSTEM`, git's documented switch; that is pinned as an
environment contract, not by a root-only real-file test.

Every allowed finding is printed with the entry's reason (`secret: 1 allowed (src/x.py:139)`, then
  `allowed src/x.py:139 - gitleaks rule generic-api-key allowed: enum value, not a key`). An allowance that matched
nothing is printed too, so a stale entry can be deleted: `secret: unused allowance <rule> in <file> (sha256 <hex>) - <reason>`;
`scan-staged` prints both kinds of line as well.

The `advisory` rule judges every tracked `uv.lock`, root and nested, with osv-scanner against the OSV advisory
database (dev dependencies count). It is part of `--engines`, so it is skipped without it (nothing is downloaded),
and a repository with no tracked `uv.lock` needs no osv-scanner. A lock that is tracked but missing on disk is skipped;
one that links out of the repository or is not a regular file is exit `2`. A lock listed in `.gitignore` is still judged.
A finding is one per advisory group, package and lock file, named by the tracked relative path:
`advisory uv.lock - osv-scanner GHSA-8q59-q68h-6hv4 (CVE-2020-14343, PYSEC-2021-142): PyPI pyyaml 5.3.1 is affected.`
with `Fix: upgrade pyyaml to >= 5.4 (uv lock --upgrade-package pyyaml)`. For a nested lock the command names its
directory (`in speech/: uv lock --upgrade-package ...`); several fixed versions are listed; an advisory with no fixed
version says `osv lists no fixed version: remove or replace <package>; see https://osv.dev/vulnerability/<id>`. A
repository's own `osv-scanner.toml` (ignored advisories, package overrides) and `.gitignore` change nothing: the gate
passes its own empty `src/quality/osv-scanner.toml` and each tracked lock explicitly. A per-repository allowance
does not exist yet.

quality downloads the PyPI database itself on every run (about 36 MB, `https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip`)
into the scan's own 0700 scratch directory and runs osv-scanner `--offline` on it, so osv-scanner makes no network call
and reads no cache of its own. A database that cannot be downloaded within 300 seconds in total (connect, headers and body, however the
server drags them out), cannot be placed in the scratch directory, is not a valid zip, or holds
no advisory is exit `2`, never clean; so are an osv-scanner that is missing, at another version, exits other than `0`
or `1`, runs past its time limit, or writes a report that is not the expected JSON, disagrees with its exit, or names
a lock file quality did not pass. Trust boundary: the database is an unsigned zip fetched over TLS from the same source
osv-scanner itself uses; there is deliberately no cache, because a doctored cached database would silence every pull
request. Records the pinned osv-scanner cannot read are dropped by it silently; the live-database test for
`pyyaml 5.3.1` on every quality pull request is the canary. osv-scanner judges the lock as written; the caller's own
`uv sync --locked` guards against a hand-edited lock.

Every rule is judged, even when another cannot be, and gives one verdict: clean, findings, unparsable, or cannot judge.
Each finding names the rule, the file, a reason and a fix. Repository text (a file name, a service, a port, a contract
or job name, a configuration entry) prints escaped. In the door's own fields (a finding's location, reason and fix, refusals, reports), invalid UTF-8 prints as `\udcNN` and every control character (line feed,
carriage return, ESC, tab, DEL, C1) as `\xNN`. A line taken from import-linter's rendering (a broken contract's chain and guidance) prints behind the marker `  | `; rich may drop a carriage return, expand a tab, or start a new marked line at a line feed there. Every line the door prints therefore starts with its own text, so a matcher anchored at the line start, such as setup-uv's python matcher, cannot match repository text. A refusal is one line; the lines the door means to print (an import chain, the verdict's problems, the scoreboard's table) stay lines. A rule that cannot judge prints its verdict and reason under its rule id (`hygiene: secret cannot
judge - ...`). A clean run names its rules in run order (`hygiene: clean (env-file, compose-port, size, clone, import-contract, claude-md); skipped without
--engines: ...`); any other run ends with the findings and the verdicts counted (`hygiene: 1 finding(s); verdicts: 1
clean, 1 findings, 1 unparsable`). The lines come in run order, also through a pipe that merges stdout and stderr
(`2>&1 | tee log`); each stays on its own stream. The exit is the worst verdict's: `2` if any rule is unparsable or cannot judge
(never clean), otherwise `1` if any rule has findings, otherwise `0`. A path git cannot read as a work tree (a file, a missing path, a
folder without permission, a bare repository, the inside of `.git`) is `hygiene: cannot judge - <PATH as given> is not a
readable git repository: ... (`git -C '<PATH>' status` says why; a PATH with an unprintable character gets `git status` run inside it instead)` and exit `2` before any rule runs.
In GitHub Actions (`GITHUB_ACTIONS=true`) the door first prints `::stop-commands::<token>` on stderr and on stdout, a new random token per run, so no
workflow command in repository text runs: no `::warning::` or `##[warning]` annotation, no `add-mask`, no `add-matcher`; the stop lasts for the rest of the step, and the door never prints the line that lifts it. Problem matchers registered in the job still read the log (the stop does not reach them). The gate's jobs register only setup-uv's python matcher, which is anchored at the line start and so cannot match repository text; a matcher another step registers may still match a door line that holds repository text, and the door cannot prevent that.

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
- Both tables name a row `owner/name` when it is an http, https or ssh URL with at least two path segments, else by its last path segment; a
  `|` in a name prints as `\|`.
- The matrix, under a heading naming the newest release tag of `overnightworks/quality` (read by an anonymous
  `git ls-remote --tags`, never from the running code's version), has one `Gate` and one `CodeQL` cell per repository.
  They come from the job-level `uses:` of each tracked `.github/workflows/*.yml|yaml`, parsed as data and never run.
  A pin to a commit shows the release tag at that commit, with `(behind)` when it is not the newest;
  `<7 characters> (no release)` when no tag points there, `<ref> (not a commit)` for a branch or tag name, `no gate`
  or `none` without a caller, and `self` for the repository's own `./` or `$/` file in `overnightworks/quality`. Two
  callers show both, joined by ` · `. The `# v` comment is never read.
- A workflow file that cannot be read shows `cannot read <file>` in both cells, unreadable release tags say so in the
  heading and show pins as commits, and either makes the exit `2`. No value, `behind` or `no gate` included, makes the run red.
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

## `gate.yml`

`.github/workflows/gate.yml` is the reusable gate: a `hygiene` job, a `sonar` job and a `verdict` job. A repository calls
it from one job of its own workflow:

```yaml
  quality:
    if: always()
    needs: [checks]
    permissions:
      contents: read
      actions: read
    uses: overnightworks/quality/.github/workflows/gate.yml@<commit sha> # v<tag>
    with:
      coverage-artifact: coverage
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

Each line has a reason:

- **The caller job is `quality`,** by its id or, if it sets one, by its `name:`. The gate's checks then read
  `quality / hygiene`, `quality / sonar` and `quality / verdict`. Three things look for them by those names: a ruleset
  that requires `quality / verdict` (the fleet's org ruleset does, on the default branch of every repository whose
  custom property `quality_gate` is `true`), `aco land`, which, once the default branch holds `quality/config.toml`,
  refuses without a green `quality / verdict`, and `quality emergency-merge`, which looks for `quality / sonar` and
  `quality / verdict`.
- **`needs:` lists the id of every job that a `mandatory_jobs` name belongs to.** The verdict then judges finished jobs,
  and an answer in which GitHub still lists one as running is stale: the verdict reads the run's jobs again, every 10
  seconds and at most 12 times: two minutes of waiting plus the time the reads take. Without `needs:`, a job still
  running holds the verdict for that long, then it cannot judge:
  `GitHub still lists "checks" (attempt 1) as in_progress after 13 reads; …`.
- **`if: always()` runs the gate when a needed job failed,** so the verdict names the failure. Without it, a failed needed
  job skips the gate and no verdict judges the run.
- **`permissions:` grants `contents: read` and `actions: read`.** The verdict reads the run's jobs through the API, and
  only GitHub's 403 or 404 adds `(does the caller grant actions: read?)` to the line. A job in
  a reusable workflow gets no more than its caller job grants (see
  [GitHub's reusable-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)),
  so without `actions: read` the run fails at startup and adds no `quality / verdict` check. Where a ruleset or `aco land`
  requires that check, the pull request cannot merge; where nothing requires it, it merges unjudged.
- **The door stops workflow commands for the rest of its step.** gate.yml runs `quality` alone in each of its steps; a
  workflow that runs `quality` itself writes its own workflow commands (`::notice::`, `::group::` or any other) in a separate step.
- **`secrets: SONAR_TOKEN`** and the optional **`with: coverage-artifact`** are described under
  [`quality sonar findings`](#quality-sonar-findings) and [Coverage on new code](#coverage-on-new-code).
- **`uses:` pins a commit** (`@<commit sha> # v<tag>`), and Dependabot bumps it where the repository's `.github/dependabot.yml` enables the `github-actions` ecosystem.

The verdict judges the jobs named in `quality/config.toml`:

```toml
[gate]
mandatory_jobs = ["checks"]
```

- `mandatory_jobs` is a non-empty list of job names as the run lists them: the job's `name:`, else its id, and a matrix
  leg as `check (3.12)`. A missing file or key is exit `2`, and the message shows this example.
- Each job must conclude `success` in its newest attempt up to the verdict's own. A job that is missing from the run is
  `mandatory job "x" is missing from the run`; one that failed, was skipped or cancelled is red. One GitHub still lists
  as running (`queued`, `in_progress`, `waiting`, `requested`, `pending`) is read again; after the last read the
  verdict cannot judge. Each read judges every entry any answer listed, at the furthest state an answer showed, so an
  answer that lists an entry late or drops one never hides a failure an earlier answer showed.
- On a pull request, removing a job the base config holds is `loosened (undeclared): mandatory job "x" removed`, and the
  base's job is still judged; a declared removal (below) is no longer judged.
- The gate's own jobs are judged through `needs` (`gate job "sonar" result failure`) and need no entry.
- `[gate] legacy` is described under [New and legacy findings](#new-and-legacy-findings).

The verdict prints `verdict: green` (exit `0`); each problem, then `verdict: red` (exit `1`); or
`verdict: cannot judge - ...` (exit `2`). When it read the jobs more than once, a first line says so:
`verdict: read the run's jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress`. A GitHub server error
(5xx) is read again within the same budget; if it lasts, the verdict cannot judge and names it as GitHub's fault:
`GitHub answered HTTP 502 Bad Gateway for … after 13 reads; this is GitHub's fault, not the caller's: re-run the verdict`.
A cannot-judge after a re-read still prints the last judged read's problems first. A timeout or an unreachable GitHub
is not read again: re-run the verdict. The same text goes to the run's step summary.

### Declared loosenings

A pull request that loosens the gate as the operator ruled declares each loosening in its `quality/config.toml`, one
entry per loosening:

```toml
[[gate.loosened]]
what = 'mandatory job "Acceptance evidence (integration)" removed'
item = "overnightworks/songmaker#1345"
date = 2026-10-20
reason = "the operator ruled the requirement shelf machinery out"
```

- `what` is exactly the verdict's text after `loosened (undeclared): `, unescaped: where the verdict prints a character
  escaped (`\x0a` for a line feed in a job name), the entry writes the TOML escape (`\n`). Any other text, another
  number included, declares nothing.
- `item` is `#<n>` or `<owner>/<repo>#<n>` (ASCII letters, digits, `_`, `.` and `-`), `date` a TOML date without a
  time, `reason` one non-empty line. A missing or extra key, a text date, two entries with one `what` or any other
  malformed entry is exit `2` in `verdict` and in `hygiene --engines` (the `secret` rule), naming the entry and showing
  this example; `scan-staged` reads only `[secrets]`.
- Only new entries declare: those the head holds and the base does not, compared as a whole entry. An inherited entry
  is never red and declares nothing, so a later pull request that loosens the same way again needs its own entry with
  its own `item` and `date`; deleting an inherited entry is no loosening. A new entry that matches no loosening of the
  pull request is red: `declared loosening matches nothing: <what>`. A base without `quality/config.toml` (the
  adoption) holds no entry, so every entry there is new and matches nothing. Without a base (push, `merge_group`) no
  entry is judged.
- A declared loosening stays red (exit `1`) and says so:
  `loosened (declared, overnightworks/songmaker#1345 of 2026-10-20): mandatory job "…" removed`. When nothing else is
  red, the last line is `verdict: red - only declared loosenings: only the operator's quality emergency-merge lands this
  pull request`. No entry approves itself: an agent may write one, but the ruleset and `aco land` still need a green
  `quality / verdict`, and only [the operator's emergency door](#the-operators-emergency-door) merges.

## `quality sonar findings`

`gate.yml` runs SonarCloud's scanner in its `sonar` job and judges it through `verdict`; callers use `gate.yml`, never
an action, because quality publishes none. A caller passes the Actions secret by name
(`secrets: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}`), has automatic analysis off in SonarCloud, and commits a root
`sonar-project.properties`. A missing or empty token is red (`sonar: cannot judge - SONAR_TOKEN is empty`), never
skipped; Dependabot runs resolve the Dependabot secret of the same name.

### Coverage on new code

The scan only sees a coverage report that is in the checkout when it starts. `gate.yml` never runs a caller's tests; the
caller hands over the report its checks job wrote, in four steps:

1. Upload the report from the checks job: `actions/upload-artifact` with `name: coverage` and `path: coverage.xml`.
2. Give the gate job `needs: [checks]` (the checks job's id), so the artifact exists when the gate downloads it.
3. Pass the artifact's name to the gate: `with: coverage-artifact: coverage`.
4. Name the report in `sonar-project.properties`, for example `sonar.python.coverage.reportPaths=coverage.xml`
   (or `sonar.javascript.lcov.reportPaths`). The path is relative to the upload's common directory, which is where the
   download puts it.

The sonar job downloads the artifact right after the caller checkout and before the gate's own tool checkout, so a
`.gate-tool/` path inside the artifact does not stand in for the pinned tool: the tool checkout re-creates it at the
pinned ref. This is defence in depth, not a guarantee. An artifact that plants `.gate-tool/.git/` still reaches git
through the checkout, and the caller's own checks job is arbitrary code anyway.

Preflight exits `2` naming the key and the entry when a listed report does not exist: each comma-separated entry is
judged on its own, a glob (`*`, `?`, `**`) must match at least one file and `**` must be a whole path segment, a plain
entry must be a file, and an absolute entry or one with `..` is refused. So a key whose report is missing, for instance
because no `coverage-artifact` was passed, is red, not a silent `0.0%`. A
caller that names no coverage key and passes no input scans as before.

`quality sonar preflight [PATH]` runs before the scan and refuses (exit `2`, naming the key) any property outside an
allowlist (`sonar.organization`, `sonar.projectKey`, `sonar.projectName`, `sonar.sources`, `sonar.tests`, the
inclusion/exclusion keys, `sonar.sourceEncoding`, `sonar.python.version` and the two coverage report path keys), any
value containing a `${...}` substitution after decoding (the scanner would expand `${env.SONAR_TOKEN}`), a
`sonar.organization` or `sonar.projectKey` other than `<owner>` and `<owner>_<repo>`, a tracked `.scannerwork/`, and
an empty token. The file is read with the `java.util.Properties` grammar (`=`, `:` or whitespace separators, `\`
continuation, `\uXXXX` and `\` escapes).

The scanner's transport, verified at sonar-scanner-java-library 4.1.1.1633 (the library of sonar-scanner-cli
8.1.0.6389, which `gate.yml` pins through `scannerVersion`):

- The bootstrap's authenticated requests reach only `sonarcloud.io` and `api.sonarcloud.io`. This holds only with the
  pinned `-Dsonar.host.url=https://sonarcloud.io` argument and a properties file that passed preflight.
- Residual limit: a redirect issued by SonarCloud itself carries the token to the host it names.
- The engine is downloaded from SonarCloud and is not pinned by the CLI pin. SonarSource's server-side code is an
  accepted trust boundary: the token's only purpose is to authenticate to SonarCloud.
- Re-verify all of this on any bump of the scan action or `scannerVersion`.
- Sources at 4.1.1.1633: [HttpClientFactory.java:72](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/HttpClientFactory.java#L72), [ScannerHttpClient.java:162-170](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L162-L170), [ScannerHttpClient.java:231-233](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/http/ScannerHttpClient.java#L231-L233), [ScannerEndpointResolver.java:40](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/endpoint/ScannerEndpointResolver.java#L40), [ScannerEngineLauncherFactory.java](https://github.com/SonarSource/sonar-scanner-java-library/blob/4.1.1.1633/lib/src/main/java/org/sonarsource/scanner/lib/internal/facade/forked/ScannerEngineLauncherFactory.java).

`quality sonar findings` is the SonarCloud zero-findings rule, except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside. It reads the scanner's `.scannerwork/report-task.txt` relative to the working directory (no other path can be given); a missing or unreadable file is exit 2.

The project is the caller's repository: `GITHUB_REPOSITORY` gives the key `<owner>_<repo>` and the organization
`<owner>`. The task file must name that `projectKey`, `organization=<owner>` and `serverUrl=https://sonarcloud.io`; the URLs inside it are never
requested. The command polls `api/ce/task` until its own analysis is `SUCCESS` and bound to this component, organization
and pull request (or branch), then reads the open findings with `SONAR_TOKEN` (header only, never printed). Anything
unreadable is exit `2`.

### New and legacy findings

`gate.yml` passes the repository's default branch (`github.event.repository.default_branch`) as `DEFAULT_BRANCH`; it is
never assumed to be `main`, and a missing or empty value is exit `2`. "Legacy" means the open findings of that branch,
counted by the same `issues/search` with the same open statuses, and only counted, never listed.
- **Pull request:** new findings are listed and make the run exit `1` (except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside). Both counts always print, for example
  `sonar: new 0 · legacy 17 (overnightworks_webauth, main)`.
- **Default branch:** a run on the default branch exits `1` on any open finding (except the findings the [Dependabot exception](#a-dependabot-exception-for-dockers8431) sets aside), lists them and prints
  `sonar: N open on <default>`.
- **Any other branch:** report only, exit `0`: `sonar: N open on <branch> (report only)`.

`[gate] legacy` in `quality/config.toml` decides whether legacy findings fail a pull request. `quality sonar findings`
reads that file like the verdict does: a missing file, or one without `[gate] mandatory_jobs`, is exit `2` before any request.

```toml
[gate]
mandatory_jobs = ["quality"]
legacy = "fail"
```

- `"report"` (the default when the key is absent): legacy findings are printed and never fail a pull request.
- `"fail"`: a pull request fails while the default branch has any open finding, even when it adds none.
- Any other value is a config error (exit `2`), read before the first SonarCloud request.
- A pull request whose base config says `legacy = "fail"` and whose head config says anything else is red in the verdict
  (`loosened (undeclared): legacy fail → report`; an absent key counts as `report`), like a removed mandatory job.

On pull requests and default-branch runs `quality sonar findings` reads `api/project_branches/list` once before the counts.
- The default branch has no entry, or one without `analysisDate` (a repository's first adoption pull request): the summary
  prints `legacy unknown (<default> not analysed yet)`. With `"report"` the run exits `0`; with `"fail"` it is exit `2`
  (`sonar: cannot judge - legacy unknown (<default> not analysed yet)`).
- The default branch is not SonarCloud's main branch: exit `2` in both modes, with the fix `set SonarCloud's main branch
  to <default>`. A non-main SonarCloud branch counts only changed code, so the legacy count would be too low.

### A Dependabot exception for `docker:S8431`

Sonar's rule `docker:S8431` ("Use either the version tag or the digest for the image instead of both") flags the
`image:tag@sha256:...` form that Dependabot maintains. Sonar's own sensor drops the finding when a Dependabot config
exists ([DockerSensor.java](https://github.com/SonarSource/sonar-iac/blob/2bb050011ed1a224b74d23182edcc82afca8695b/iac-extensions/docker/src/main/java/org/sonar/iac/docker/plugin/DockerSensor.java#L204-L226),
SONARIAC-3096), but only checks that the file exists, and a pull-request analysis looks at changed files only, so it
cannot see an unchanged `.github/dependabot.yml` and keeps the finding. The gate therefore judges a pull request the
way Sonar judges main:

- **The rule:** when `.github/dependabot.yml` exists in the checkout, `docker:S8431` does not count. Its content is
  never read, so an empty or broken file counts as existing; a link to a file counts and a link to nowhere does not; a directory named `.github/dependabot.yml` counts too, unlike
  Sonar, which needs a file.
  `.github/dependabot.yaml` is not looked at. The rule applies in every run kind.
- **What prints:** the finding still prints, on stdout after the counted findings and before the summary, without a
  `Fix:` line: `sonar Dockerfile:3 - docker:S8431: <message> (does not count: .github/dependabot.yml exists, Sonar's
  own exception, which its pull-request analysis misses)`. The summary line does not count it; the legacy number stays
  the raw count of the default branch.
- **An unreachable file:** a `.github` the filesystem refuses access to is exit `2` with a line naming the file, before
  any request.
- **Only this rule** has the exception; every other finding and rule counts as before.
- **Gaps:** `quality emergency-merge` does not honour it and still counts the finding as added; the legacy count is
  raw; the file's existence does not say that Dependabot maintains the Dockerfile's folder for Docker; a repository
  that has only `.github/dependabot.yaml` stays red on pull requests; Renovate is not judged.

### The operator's emergency door

Two red pull requests have no other way to land. Under `legacy = "fail"`, main can gain a finding without any pull
request introducing it (a taint finding on an unchanged line, a SonarCloud rule-profile update); then every pull request
is red, including the fixing one, and switching `legacy` back is red too. And a pull request that loosens the gate as the
operator ruled stays red for its [declared loosenings](#declared-loosenings). Both land through the operator's door,
one command (quality#37, quality#103):

```
quality emergency-merge <owner/repo> <pr>
```

- **Operator only.** It runs under the operator's own `gh` login, which needs admin permission on the repository (checked
  first: exit `2`, nothing changed, the missing right named). Agents never run it; it refuses when stdin is not a
  terminal. This is the agent contract, not a lock: agents on the operator's machine share his `gh` login, so to an
  agent the terminal is a hurdle, and that login could switch `quality_gate` directly. A real lock (agents with a token
  without admin rights) belongs to the machine's setup, not to quality; it is a known residual risk.
- **It refuses unless the door is the only way** (exit `1`, nothing changed): no check is still running; every check run
  and commit status other than `quality / sonar` and `quality / verdict` is green; `quality / verdict` has failed; and
  `quality / sonar` is green or has failed (it did not run, or it ended `timed_out`, `cancelled` or anything else: refused).
  The verdict is then judged again by its own decider, with the config and size baseline read at the pull request's
  current merge commit and at base and the Sonar job taken as green: an undeclared loosening, a declaration that matches
  nothing, or a missing or red mandatory job is refused, naming the verdict's lines.
- **Sonar green: declared loosenings.** The re-derived verdict must hold at least one declared loosening (otherwise
  `re-run the verdict`). SonarCloud is not asked and `SONAR_TOKEN` is not needed.
- **Sonar failed: legacy findings,** as before, next to any declared loosening: `[gate] legacy` is `fail` at the merge
  commit (under `report` the Sonar job cannot be red on legacy findings, and a missing config reads as `report`),
  SonarCloud's analysis of the pull request is of the head being merged and adds no finding (the findings are named
  otherwise), and the default branch has legacy findings. `SONAR_TOKEN` is read from the environment when the project
  needs one: without it the door reads a public project anonymously, with no Authorization header, and an anonymous read
  that SonarCloud refuses (a private project answers 404) is exit `2`, naming `SONAR_TOKEN`; with a token, a 401 or 403
  says the token was rejected.
- **It shows, then asks:** repository, pull request and head SHA with the legacy count (where Sonar failed) and the
  number of declared loosenings, then one line per declaration with its `item`, `date`, `what` and `reason`, then the
  typed word `ja`. Anything else changes nothing. Every line it prints is escaped as the other doors' lines are, so a
  `reason` cannot hide anything before `ja` or forge a line.
- **Off, merge, on:** it sets the repository's `quality_gate` property to `false` (this removes ruleset 25227067 for it),
  runs `gh pr merge <pr> --merge --match-head-commit <sha>`, and sets the property back to `true` in every case: merged,
  merge failed, Ctrl-C. A property that cannot be restored is exit `2` with one line naming what to set by hand and the
  merge outcome as far as it is known, printed before any further GitHub request; a re-read that settles an open
  outcome prints that line again with it. After Ctrl-C the next line starts on a line of its own. A merge whose outcome
  GitHub does not confirm (Ctrl-C, a lost answer) is re-read from the pull request; if that does not settle it, exit
  `2` with `merge state unknown`.
- **Logged:** one comment on the pull request (who, when, the legacy count and each declaration,
  `quality_gate off → <outcome> → on`, or `quality_gate not switched off → no merge → on` when switching off failed),
  also when the restore failed. The command prints `aco release <n> --abandoned "landed as PR #<pr> by emergency-merge"`
  for the head, because `aco land` refuses any non-green check and `--merged` refuses a merge commit without a
  `Work-Item:` trailer.

## `codeql.yml`

A reusable workflow that runs CodeQL for the languages a caller names, with `build-mode: none` and CodeQL's default
query suite and threat model. It uploads under the category `/language:<language>`, the Default Setup's category, so
alerts carry over when a repository switches.

A caller (`.github/workflows/code-scanning.yml`, workflow `name: CodeQL`):

```yaml
name: CodeQL

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  schedule:
    - cron: '23 4 * * 1'

permissions:
  contents: read

jobs:
  codeql:
    permissions:
      security-events: write
      contents: read
      actions: read
    uses: overnightworks/quality/.github/workflows/codeql.yml@<tag>
    with:
      languages: '["actions", "python"]'
```

- **`languages`:** the one input, required, a string holding a JSON list; `'python'` or `'[]'` fails the run. List
  every category the repository already analyses (agent-coordination also has `javascript-typescript`, which the
  template would drop).
- **Permissions:** the caller grants `security-events: write`, `contents: read` and `actions: read`, and the workflow
  asks for no more. A caller without `security-events: write` is refused at startup.
- **Updates:** pin `<tag>` to a release; Dependabot's `github-actions` ecosystem bumps it.
- **Caller path:** a reusable call records the `analysis_key` `<caller file>:analyze` (measured in quality#68), so keep
  the existing caller file's path and the key carries over.
- **Switch PR:** a caller that fails at startup adds no check to its PR (measured in quality#68: callers on
  scratch PR #70 ended `startup_failure`/`failure` with no check-run), so the switch PR must show its
  `codeql / Analyze (…)` checks.
- **Switching a repository:** the Default Setup goes off first, in the order of
  [agent-coordination#138](https://github.com/overnightworks/agent-coordination/issues/138).
