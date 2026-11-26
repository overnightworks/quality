# Rules

Reference for the engineer or operator who needs the exact behaviour of `quality check`.

- CHECK-1: `quality check` reads git's view of the repository it is started in, from its top level, so a run from `src` judges
  the same files and the same `quality/baseline.toml` as one at the top, and names every file relative to the top (`size
  src/big.py`).
- CHECK-2: There is no path argument; in a submodule or a linked worktree a run judges that work tree's own
  top.

- CHECK-3: `quality check [AREA ...]` judges only the areas it names, each whole, in the order of `quality check --help` and each
  once, however they are named; with none it judges all seven.
- CHECK-6: An area not named starts nothing and has no
  row;
- CHECK-7: the verdict counts only the named areas (`✓ clean · 1 area`)
- CHECK-8: and the exit is the worst of
  theirs, so a named run says nothing of the rest.

The gate runs all seven ([gate](gate.md#gateyml)).

Three statements hold for each of six declaration tables of `quality/config.toml` and the rule that reads it:
`[[compose.allow]]`, `[[root.allow]]`, `[[layout.allow]]`, `[[python.allow]]`, `[[workflow.allow]]` and
`[[spec_citations.foreign]]`. A malformed
table other than the rule's own is forwarded with its validation error; the values of `[gate]` stay the verdict's to
judge. A declaration is no loosening, so `quality verdict` asks for no `[[gate.loosened]]` entry. A config reader older
than the rule refuses its table as an unknown key.

It applies these rules:

- `env-file`:
  - ENV-1: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
  - ENV-2: The fix is `git --literal-pathspecs rm --cached -- <name>, then add <name> to .gitignore`, the first name quoted for
    the shell, so a copy untracks exactly that file;
  - ENV-3: the second stands only where the name needs no quoting, else
    it reads `it`.

  A name without a slash, such as `.env`, also matches that name in every folder
  (`gitignore(5)`).

  - ENV-4: A name that is not printable UTF-8 gets the hint to remove it by its bytes (`git ls-files -z` shows
    them) instead of a command.

  For an app repository it checks the rule of
  [platform's app standard](https://github.com/overnightworks/platform/blob/main/docs/app.md) that env files pass
  quality's rules.

  Ruled by the operator: the command runs exactly as printed, with no home-made encoder for the name (ENV-2).
- `compose-port`:
  - COMPOSE-1: every published port in a tracked compose file binds loopback, `127.0.0.1` or `::1` (`"[::1]:HOST:CONTAINER"`,
    `host_ip: ::1`); `[::]` and `0.0.0.0` are findings.

  For an app repository it checks the rule of
  [platform's app standard](https://github.com/overnightworks/platform/blob/main/docs/app.md) that published compose
  ports pass quality's rules.

  - COMPOSE-2: A compose file is a `.yml` or `.yaml` file whose name holds the word `compose` between
    `.`, `-` or `_`, in any case: `docker-compose.ci.yml` and `keycloak/bootstrap.compose.yaml` are judged,
    `composer.yml`, `compose2.yaml` and `compose.yaml.example` are not.
  - COMPOSE-3: Only tracked files count: an untracked
    `compose.yaml` is never judged, one added with `git add` is.
  - COMPOSE-4: Its rows say `published port`,
    `unreadable port`, `host network` or `unused` (a stale `[[compose.allow]]`).
  - COMPOSE-5: A tracked file missing from
    disk (deleted, or a symlink to nowhere or in a loop) is skipped;
  - COMPOSE-6: one the filesystem refuses to read makes
    the `files` row `✗ not checked` with the reason (exit `2`).
  - COMPOSE-7: A file is read as Compose reads it: `- 22:22` is the text `22:22`,
  - COMPOSE-8: and `!override` and `!reset` are read
    as the value they wrap;
  - COMPOSE-9: any other tag (`!foo`) and any YAML error is `cannot judge`,
  - COMPOSE-10: in one
    line with its line and column (or its position for a character YAML refuses, `nested too deeply`, or `a value does not
    fit its tag`).
  - COMPOSE-11: A port that is neither text, a number nor a mapping whose `published` (or, without one, `target`) is text or a number
    is a finding.
  - COMPOSE-12: A `ports` that is neither a list nor empty, `0` or `false` is a finding too.
  - COMPOSE-13: A
    service with `network_mode: host` publishes whatever its process binds, so it is a finding too,
  - COMPOSE-14: and so is
    a `network_mode` that holds a Compose interpolation (`${...}`), which may resolve to `host`;
  - COMPOSE-15: `none`,
    `service:x` and any other literal are clean.
  - COMPOSE-16: A service is declared in `quality/config.toml`, a table of
    its own beside `[[layout.allow]]`, with the exact tracked path of its compose file, the service name and a one-line
    reason:

  ```toml
  [[compose.allow]]
  file = "compose.yaml"
  service = "traefik"
  reason = "it terminates TLS on the host's own interfaces"
  ```

  - COMPOSE-17: A declared service passes the host check only; its ports are still judged.
  - COMPOSE-18: A declaration that matches
    nothing is a finding on its file: the file is not tracked or not a compose file, it lacks the service, or the service
    is neither host-networked nor interpolated there; the fix is to delete the entry.
  - COMPOSE-19: A duplicate
    `(file, service)`, a folder (a trailing `/`), a pattern (`*`, `?`, `[`), `..`, an absolute path, an unknown key, a
    missing key or a service or reason that is not one-line text makes the `files` row `✗ not checked` with the reason,
    and `next:` shows a `[[compose.allow]]` example.

  Ruled by the operator: a service with `network_mode: host` (COMPOSE-13), or one whose `network_mode` holds a Compose
  interpolation (COMPOSE-14), is a finding unless `quality/config.toml` declares its file and service
  (COMPOSE-16, COMPOSE-17).
- `size`:
  - SIZE-1: every tracked file of Python, TypeScript (`.ts` `.tsx` `.mts` `.cts`), JavaScript (`.js` `.jsx` `.mjs`
    `.cjs`), Svelte, Vue or CSS (`.css`) is measured by its lines,
  - SIZE-2: and no other file is,
  - SIZE-3: and every
    tracked `.py`, `.ts`, `.js`, `.mjs`, `.svelte` and `.vue` file also by its functions with
    [lizard](https://github.com/terryyin/lizard): a file from 800 lines, a named function from 60 lines and a named
    function with a CCN above 15 are debt, [counted apart](commands.md#quality-report) for production and tests
    (a path under `test/` or `tests/`; Python also `conftest.py`, `test_*.py`, `*_test.py`; TypeScript and JavaScript
    also `__tests__/`, `*.test.*`, `*.spec.*`, `*.test-d.*` and `*.spec-d.*`).

  Ruled by the operator:
  - GENERATED-1: a file that `quality/config.toml` declares generated is left out of the `size` rule; a minified or generated file the repository does not declare is counted by its lines like any other
    (SIZE-1):

  ```toml
  [[size.generated]]
  path = "frontend/src/lib/api/types.ts"
  job = "Backend (Python 3.14)"
  item = "overnightworks/songmaker#1784"
  date = 2026-10-30
  reason = "written by scripts/generate_types.py, proven by its --check"
  ```

  - GENERATED-2: A declared file's lines, functions and CCN are neither judged (GENERATED-1), counted in the not-recorded row's count
  - GENERATED-3: nor recorded by `quality check --fix`.
  - GENERATED-4: A declared Python file that Python cannot parse
    is left out likewise: the `size` rule names no note for it and is not refused by it.
  - GENERATED-5: `path` is the
    exact tracked path of one file, `job` one of `[gate] mandatory_jobs`, the job whose generator check proves the file.
  - GENERATED-6: A missing or extra key, a folder (a trailing `/`), a pattern, `..`, an absolute path, a `job` the gate
    does not list (a config without `mandatory_jobs` included), a malformed `item` or `date`, a reason that is not one
    line or two entries for one path is exit `2` naming the entry with this example:
  - GENERATED-7: in `check` as the
    `debt` row `✗ not checked` with the [example under `next:`](output.md#refusals), in `quality check --fix` as the same
    row,
  - GENERATED-8: and in the verdict and the door naming the side; [`check secrets --staged` reads only
    `[secrets]`](gate.md#declared-loosenings).
  - GENERATED-9: A declaration whose path holds no
    tracked file in a measured language is an `unused` row
    (`[[size.generated]] matches no tracked file in a measured language`) on that path, fixed by deleting the entry from
    `quality/config.toml`.

  Only `quality/config.toml` declares: `.gitattributes`, `linguist-generated`
  included, plays no part.

  - GENERATED-10: A baseline entry left under a declared file's path can fall (`file is left out as generated · ceiling 850 lines`)
    until `quality check --fix` drops it (`dropped  file is left out as generated`).

  Only the `size`
  rule leaves the file out: `clone` and the report still read it. quality cannot see what the declared job runs; the
  operator checks it once, at the door.

  - SIZE-4: Functions are measured as follows.

  lizard 1.26.1 is pinned exactly, and quality's suffix table names the
  reader, never lizard's fallback to C: `.ts` is read by lizard's TypeScript reader, `.js` and `.mjs` by its JavaScript
  reader, `.svelte` and `.vue` by its Vue reader, which reads their `<script>` blocks as TypeScript at the lines of the
  file. A function lizard gives no name (a callback passed to a call, an anonymous default export, an arrow under a
  quoted or computed key) is no entry: its lines and its branches count in the smallest named function whose lines
  hold it (of two on the same lines, the one lizard lists first), and a named function inside another adds nothing to
  the outer one. The measurement has these limits. A test pins each of the first five, so a lizard release that changes
  one fails the pull request that moves the pin; the last two are not pinned:
  - `.tsx`, `.jsx`, `.mts`, `.cts` and `.cjs` are counted by lines only.
  - A function written in Svelte or Vue markup is not measured.
  - A callback no named function holds counts nowhere, and only the 800-line file limit holds it.
  - An arrow under a quoted or computed key reads as anonymous.
  - Six misreadings of lizard stand: a statement that calls a function named `type` ends the function there; `as` with an
    object type over several lines ends an expression-bodied arrow there; an arrow whose parameters, or a function whose
    type parameters, span lines starts at their last line (all three read short); a function type as a type argument and
    each overload signature read as a function of their own, which shifts the `#n` of the same name; a call in a
    ternary branch reads as a function and takes its holder's branches (reads low); a regex literal before `!` in an
    expression body runs one statement on (reads long).
  - A byte order mark and a carriage return in a script file are unproven readings, and so are minified and built files,
    which are measured like any other file unless declared (GENERATED-1).
  - An optional chain is no decision for lizard.

  A Python function is named by its qualified symbol (`Class.method`); lizard names a function of the script languages
  without its class (`render`, an accessor `get name`, an assignment target `window.onload`).

  - SIZE-5: A second function of the
    same name, such as a property setter or a method of another class, is `Class.method#2` or `render#2`, numbered in
    source order, so a function inserted above a held one renumbers it.
  - SIZE-6: Without `quality/baseline.toml` the [debt row](output.md#area-rows) is
    `· debt  not recorded  N entries over the thresholds · measured only`, N the size entries over the thresholds that
    quality measured (a declared file left out); it equals what a first `quality check --fix` records only when every
    Python file parses and every script file is UTF-8 and not nested too deeply to read, otherwise that fix refuses.
  - SIZE-7: The size debt leaves the exit unchanged.

  The per-class and language counts leave `check`; the
  report keeps them.

  - SIZE-8: A `.py` file is read the way Python reads source (a PEP 263 coding cookie or a UTF-8 BOM decides the encoding).
  - SIZE-9: One Python 3.14 cannot decode or parse (nesting past the interpreter's recursion limit included) is
    named as not valid Python, and a script file that is not UTF-8 as `not UTF-8` or that lizard cannot read for its
    nesting as `nested too deeply to read`: without a baseline it stands as a
    [note row](output.md#notes) under the debt row (`· bad.py  not measured  not valid Python`) and the size verdict is
    unchanged,
  - SIZE-10: while `python-security` judges the file unparsable or cannot judge it (exit `2`); with a baseline
    the `debt` row is `✗ not checked` with `bad.py is not valid Python (...)`, `bad.ts is not UTF-8` or
    `bad.ts is nested too deeply to read`, and `quality check --fix`
    refuses the same way, writing nothing; both exit `2`.
  - SIZE-11: Only where no baseline is held does `--fix` refuse with
    `✗ not fixed`, since only recording the first ceiling is refused.
  - SIZE-12: A tracked file the operating system
    refuses to read makes the `debt` row `✗ not checked` with or without a baseline, and `quality check --fix` refuses
    likewise with the same `debt` row, writing nothing; both exit `2`.
  - SIZE-13: The reason names the case: a file without
    read permission is `cannot read locked.py: [Errno 13] ...`, a file behind a directory without permission is
    `cannot reach pkg/m.py: [Errno 13] ...`.
  - CEILING-1: A tracked file missing from disk
    (deleted but not staged, left out by a sparse checkout, or a dangling symlink) is skipped, and its entries stand,
    unjudged, while the index tracks it.
  - CEILING-2: A `quality/baseline.toml` that is not UTF-8 is malformed like invalid
    TOML and says to restore it from git or repair it by hand.
  - CEILING-3: A baseline with a table this quality does not
    know (`has an unknown table [future]; quality <version> knows ccn, file_lines, function_lines, clones, job_minutes, spec_citations`) is not told
    that: a newer quality may have written it, so the head is told to run the quality release its gate pins.
  - CEILING-4: With `quality/baseline.toml`, the tree on disk must hold the baseline: a new violation, an entry that grew and an
    entry above its measured value are findings, each a row under the [debt row](output.md#area-rows): `new debt`
    (`file has 800 lines · debt from 800 lines`, fix: split the file below 800 lines),
  - CEILING-5: `grew`
    (`function read_rows has 71 lines · ceiling 64`, fix: bring it back to 64 lines or below)
  - CEILING-6: and `can fall`
    (`file has 742 lines · ceiling 766`, fix: `quality check --fix`);
  - CEILING-7: an entry whose path the index no longer
    tracks, or whose Python or script file is measured without the function, can fall after the measured rows, with no line
    (`file is gone · ceiling 802 lines`, `function total is gone · ceiling CCN 19`, fix: `quality check --fix`);
  - CEILING-8: an entry with no measurement otherwise (a tracked file missing from disk, a function entry under a file
    of a suffix measured by lines only) stands and counts in the [clean row](output.md#area-rows)'s
    `ceiling held · N entries, M clone pairs`, followed by `, K job limits` when the held `[job_minutes]` table holds
    jobs (TIMEOUT-34).
  - FIX-1: [`quality check debt --fix`](commands.md#quality-check---fix) (`--fix` takes no `--staged` and needs `debt` among the
    named areas, or none named; `--dry-run` needs `--fix`; no workflow runs either) changes `quality/baseline.toml` at the
    top of the repository it is started in (a stray `src/quality/baseline.toml` is neither read nor removed)
  - FIX-2: and
    then judges the tree against the new file.
  - FIX-3: It only lowers: each held entry falls to its measured value
    (`lowered`, `function total CCN 19 → 17`)
  - FIX-4: and goes where the measured value is no longer debt (`dropped`,
    `file 805 → 790 lines · debt from 800 lines`);
  - FIX-5: an entry that grew stays a finding, its `fix:` adding
    `a raise is a hand edit and a declared loosening`,
  - FIX-6: a new one stays a finding
  - FIX-7: with its usual `fix:`,
    and the rest still falls.
  - FIX-8: An entry `check` calls gone or left out as generated goes (`dropped  file is gone`),
    counted among the lowered;
  - FIX-9: a held pair the tree lost goes (`dropped`, `pair ... is gone`),
  - FIX-10: and a new
    pair stays a finding.
  - FIX-11: It
    records a ceiling (`recorded`, `N entries, M clone pairs · quality/baseline.toml is new`, with `, K job limits` after
    the pairs where it records the job table) only where none lies on
    disk, in the index or in any commit `HEAD` reaches;
  - FIX-12: a ceiling or a `[clones]` table git holds and the tree
    lacks (a `[job_minutes]` table too, where a job above 15 would be recorded) is `✗ not fixed` with `restore quality/baseline.toml from git, or repair it by hand`
  - FIX-13: (a version no TOML
    parser reads refuses likewise where its bytes name the table, `a version of quality/baseline.toml in git cannot be read and
    names clones`),
  - FIX-46: a version no TOML parser reads whose bytes never name the table holds no table (named limit: an escaped or non-UTF-8 spelling of the name in such a version passes),
  - FIX-14: and a shallow clone that cannot prove it names
    `git fetch --unshallow`.
  - FIX-15: A file without `[clones]` that git never held it in records the pairs and keeps its
    entries; a file without `[job_minutes]` records the jobs above 15 under the same condition (TIMEOUT-27), and a file without `[adapter_imports]` records the counts of marked modules under it.
  - FIX-16: It also rewrites a file whose text is not what it would render (an older header, other blank lines
    or order, a comment), though no entry changes: `rewritten`, `quality/baseline.toml`,
  - FIX-17: or beside a finding
    `rewritten · quality/baseline.toml`.
  - FIX-18: The verdict line adds `ceiling lowered by N entries`,
    `ceiling recorded`, `ceiling rewritten` or `nothing to lower`.
  - FIX-19: Under `--dry-run` the same ceiling is made,
    judged and rendered and nothing is written:
  - FIX-20: each change is a `~` row worded as what the fix would do,
  - FIX-21: the `~` debt row (`N to lower`, `N to move`, `to record` or `to rewrite`,
    `text differs · quality/baseline.toml`)
  - FIX-22: and the verdict (`~ dry run · N ceiling entries would fall`,
    `N ceiling entries would move`, `ceiling would be recorded` or `ceiling would be rewritten`, then `· nothing written`),
  - FIX-23: the exit `1` while the fix would change the file.
  - FIX-24: The file is written whole or not at all: a hidden sibling takes the text and replaces the file in one step, so
    whatever stops the process leaves the complete old file or the complete new one,
  - FIX-25: the replacement being the
    commit point, so a Ctrl-C just after it keeps the new file,
  - FIX-26: and a killed process can leave that
    sibling.
  - FIX-27: It keeps the file's mode,
  - FIX-28: stays refused on a read-only file
  - FIX-29: and needs a writable
    folder;
  - FIX-30: a symlink on its way is never written through.
  - FIX-31: A fix writes only when an entry changes or
    the file's bytes are not its rendering; a file that renders byte for byte stays untouched.

  A `check` without
  `--fix` says nothing of the text. It promises nothing against a power loss.

  - GENERATED-11: A config or baseline the check cannot read, or a file Python cannot parse (a declared file included,
  - CEILING-9: because the clone pairs of the whole tree must be measured), refuses it: the `debt` row is
    `✗ not checked` or `✗ not fixed`, exit `2`, and nothing is written.
  - FIX-32: A refused fix leaves each rule the verdict it
    reached against the unchanged file.
  - FIX-33: Another area's refusal does not undo a finished fix: the file is written,
    the `✓` row stands, the exit is `2`.
  - FIX-34: `--fix` follows renames as the verdict carries them:
  - FIX-35: from the ceiling's last commit it reads what git
    renamed into the index (`git diff --cached -M50% -l1000`),
  - FIX-36: moves a renamed file's entries, pairs, job entries
    (TIMEOUT-19) to the new path at their values (`moved`)
  - FIX-37: and drops what the ceiling held under that path
    before the move (`dropped ... left from before the move`);
  - FIX-38: a ceiling it already moved stays,
  - FIX-39: an entry it cannot
    place refuses with the restore step,
  - FIX-40: a shallow clone whose ceiling's last commit is its edge, which is
    every depth-1 clone that holds a ceiling, refuses `--fix` with `git fetch --unshallow`,
  - FIX-41: and a move the
    index does not record is not followed.
  - FIX-42: Renames are followed before anything is judged gone, so a renamed
    file's entries move and never fall;
  - FIX-43: with no committed ceiling a moved file's entries are gone.
  - FIX-44: `check` reads no history: after a staged move it names the new path `new debt` and the old one
    `can fall … is gone`, and one `--fix` clears both.
  - FIX-45: A fix that moves entries and lowers none counts them in the [debt row](output.md#area-rows) and adds
    `ceiling moved N entries` to the verdict line.
- `clone`:
  - CLONE-1: a pair is two functions (methods and nested functions too) in two different tracked `*.py` files, each of at
    least 40 tokens without its docstring, whose structure is at least 90 % the same once every name and literal is
    renamed;
  - CLONE-4: tests count like code, and a copy inside one file is no pair.
  - CLONE-2: A pair is named by the
    path and qualified symbol of both sides, as `size` names a function;
  - CLONE-3: a pair counts as tests only when both
    files are tests.
  - CLONE-5: Without a `[clones]` table in `quality/baseline.toml` the rule only measures: where the file exists, the pairs stand in the [partly row](output.md#area-rows)
    (`· debt  not recorded  2 clone pairs · partly measured only`).
  - CLONE-6: With one, even an empty one, a new pair is a
    `near copy` row at its second side (`function check and tests/test_a.py run`, fix: give the shared body one owner and
    call it, or make the cases one parametrized test)
  - CLONE-7: and so is a held pair the tree lost, even when a side
    was deleted (`can fall` at its first side: `pair tests/test_a.py run <-> tests/test_b.py check is gone`, fix:
    `quality check --fix`).
  - CLONE-8: A Python file that cannot be parsed may hide a pair, so with a `[clones]` table the
    `debt` row is `✗ not checked` (exit `2`), as for `size`.
  - CLONE-9: The baseline is read once for both rules: one that
    cannot be read leaves both unjudged (the `debt` row [names the reason once](output.md#refusals), exit `2`), also when
    it has no `[clones]` table, since the mode is then unknown.

  Ruled by the operator: quality owns this detector instead of calling jscpd or lizard; it is the detector taken over
  from aco.

  - CLONE-10: The detector compares the functions' name-folded token streams and judges Python only.

  The threshold, what
  makes a pair, how it is named, that tests count, the report-only mode and the printed lines are defaults the operator
  never answered.

  - CLONE-11: A renamed file or function orphans the held pair and names a new one.

  Only the verdict's
  comparison with the base and `check --fix` treat a pair whose files git renamed as the same pair (see the baseline
  paragraph below).
- `import-contract`:
  - IMPORT-1: the repository's [import-linter](https://github.com/seddonym/import-linter) contracts must be
    kept.
  - IMPORT-2: The config is the tracked `.importlinter` or `setup.cfg` (`[importlinter]`) or `pyproject.toml`
    (`[tool.importlinter]`),
  - IMPORT-3: counted once its name exists in the tree: a tracked one deleted from disk is none,
  - IMPORT-4: a symlink to nowhere cannot be read (exit `2`).

  Ruled by the operator:
  - IMPORT-27: without one the rule only measures;
  - IMPORT-28: the `imports` row is a grey note (ROW-13), the
    area is not counted among the clean areas, and the verdict names it (CLOSING-10).

  The [`imports` row](output.md#area-rows) reads `· imports  not set up  no import-linter config`, dim at a terminal,
  the exit stays `0`, and the [verdict line](output.md#the-closing-line) says `imports not enforced` before the
  duration, after debt's note. A config with contracts that are kept is
  [`✓ imports  clean  N contracts`](output.md#area-rows) (a config without a contract: `0 contracts`).

  - IMPORT-5: import-linter
    runs inside quality as a library, never its CLI, and writes nothing to the checkout (no `.import_linter_cache`).

  A broken contract is one finding `! <config>  broken  contract <name>`, with the lines import-linter
  renders for it (the chain, `shop.db -> shop.web (l.1)`, and the contract's guidance) under the [row](output.md#rows)
  behind `| `, fix `change the import or the contract`, exit `1`.

  - IMPORT-6: An `ignore_imports` entry no import matches follows
    the contract's `unmatched_ignore_imports_alerting`: `error` (the default) is a finding
    `unused  contract <name> ignores a -> b`, fix `delete it from the contract's ignore_imports`, `warn` a
    [note row](output.md#notes) `· <config>  unused  contract <name> ignores a -> b`, `none` nothing.
  - IMPORT-7: Each root
    package is the one tracked `<dir>/<name>/__init__.py` outside the tests,
  - IMPORT-26: and grimp reads it from there
    even when quality itself has imported a package of that name or an untracked copy comes first on `sys.path` (IMPORT-7).
  - IMPORT-8: The rule cannot judge (exit `2`,
    before any caller code runs or any file outside is read)
  - IMPORT-9: two configs that each hold a section,
  - IMPORT-10: a `contract_types` option (plugins would be imported),
  - IMPORT-11: a type other than forbidden, layers, independence,
    protected and acyclic_siblings,
  - IMPORT-12: invalid contract options,
  - IMPORT-13: a config value of the wrong type or
    any other error of import-linter or grimp (`contract <name> in pyproject.toml cannot be judged: ...` or
    `the config pyproject.toml cannot be judged: ...`) or a panic of grimp at a source IMPORT-17 to IMPORT-23 do not refuse
    (`grimp panicked while reading the root packages; ...`),
  - IMPORT-14: a dotted root
    package, because a root package is one tracked `<name>/__init__.py` and quality judges only top-level ones,
  - IMPORT-15: a root package with no or several such directories, or whose tracked `__init__.py` the checkout does
    not hold,
  - IMPORT-17: a symlink under a root package that leaves the repository, directly or through
    a link that stays inside,
  - IMPORT-18: and a symlink loop through directories or a `.py` name that loops,
  - IMPORT-19: a dangling link named `.py`,
  - IMPORT-20: and a `.py` file the walk lists and cannot stat or read, one gone since it was listed included, that
    "cannot be examined" (no regular file,
  - IMPORT-21: a name or content that is not valid UTF-8,
  - IMPORT-22: a
    coding cookie in any spelling but utf-8, utf8, ascii, us-ascii or ansi_x3.4-1968 (accepted in any letter case),
    because quality reads every source as UTF-8 and judges only one that it and grimp read alike
    (`<path> cannot be examined: declares <spelling>; quality reads only <the accepted spellings>`),
  - IMPORT-23: or one the system will not let it read or that is gone: `<path> cannot be examined: <the system's reason>`,
    which does not say whether a rerun helps).
  - IMPORT-33: A name that does not end in `.py` is neither stat'ed nor read, so a dangling link, a link loop or a file
    under a directory without search permission that is no `.py` name is judged without refusal.
  - IMPORT-24: A module grimp cannot parse makes the `imports`
    row `✗ not checked` with the reason (exit `2`);
  - IMPORT-25: a warning grimp gives while building the graph is a
    [note row](output.md#notes) `· <config>  warning  <grimp's message>`, before the contracts' notes.

  No message quality writes names an absolute local path (IMPORT-26); grimp's own panic text is the named gap below.

  Named gaps, waiting for usage evidence: when grimp panics, its native panic text, which names an absolute path, reaches
  stderr before quality's refusal; #1066 owns it, and it ends when quality captures that descriptor during the build
  without hiding grimp's own output, or when grimp raises an error instead of panicking. Names ending in `.py` that grimp
  skips (hidden ones, and ones with more than one dot) are still examined.

  - IMPORT-29: A `forbidden` contract whose own `ignore_imports` name a plain dotted submodule of an external package (one that is
    no root package), such as `skat.table.** -> agents.calling`, is checked on its own copy of the graph in which that
    package keeps the submodules the contract names; every other contract keeps grimp's squashed package, and a wildcard
    such as `-> agents.*` names no submodule.

  Stock import-linter reports such a line as unmatched, so an app
  that uses it drops its own `lint-imports` run.

  - IMPORT-30: Every import statement at a line grimp reports for an import of that
    package is read from the importer's tracked file, an import inside a function included and one under `TYPE_CHECKING`
    left out as grimp leaves it out: `import a.b` names `a.b`, `from M import x` names `M.x`, `from M import *` names
    `M`, and each name is held at the longest prefix the contract names, otherwise at the package, so
    `from agents import calling` is `agents.calling`, `from agents.calling.sub import x` is `agents.calling`, and
    `from agents import Standing`, `from agents.turn import T` and `import agents` are `agents`.
  - IMPORT-31: On that
    copy an `ignore_imports` line naming the bare package matches only imports held at the package itself, and a line no
    import matches follows IMPORT-6.
  - IMPORT-32: A refining contract with `as_packages = false`, and a module grimp read
    that Python cannot parse, git does not track or whose tracked file the checkout does not hold, cannot be judged (exit `2`).

  On a pull request `quality verdict` and the door also hold the head's import contracts against the base's (see
  "The import contracts only tighten" below).
- `adapter-import`: a core unit test imports no adapter.
  - ADAPTERIMPORT-1: It judges a repository whose import-linter config (IMPORT-2)
    holds a `forbidden` contract with the `id` `core-imports-no-adapter`, read from the raw contract options (a TOML key,
    or an INI section suffix): the contract's `source_modules` are the core and its `forbidden_modules`
    the adapters, external packages allowed.
  - ADAPTERIMPORT-2: Without such a contract it judges nothing, and a repository without a config
    stays as IMPORT-27 says.
  - ADAPTERIMPORT-3: Two contracts carrying the id, or a wildcard in its `source_modules` or
    `forbidden_modules`, cannot be judged (exit `2`).

  Ruled by the operator: the contract's `source_modules` decide what is core, so a core-subject test that imports an
  adapter is red also when it is marked integration (#898, line 1, A).

  - ADAPTERIMPORT-4: A test module is a tracked `test_*.py` under a project's `tests/` as `test-layout` finds projects; its subject is the
    module or package its longest matching key names (LAYOUT-24), and a test named after its package's own name
    (LAYOUT-23) has that package as its subject.
  - ADAPTERIMPORT-5: It is a core test when its subject lies inside
    `source_modules` (by prefix with `as_packages`, the default, else exactly).
  - ADAPTERIMPORT-6: A test with no subject,
    because a `[[layout.allow]]` entry exempts it from `test-layout` or its project is not judged, is a core test too, so
    no declaration becomes a path allowlist.
  - ADAPTERIMPORT-7: A subject equal to or inside a forbidden module, or any
    other subject outside `source_modules`, makes no core test; these are name checks and no graph is
    asked.
  - ADAPTERIMPORT-8: Modules under `tests/scripts/`, `tests/workflows/` and `tests/e2e/`, and every `conftest.py`, are not judged.

  - ADAPTERIMPORT-9: Every `import` and `from ... import` statement of a core test is read, inside a function or under `if TYPE_CHECKING:`
    too, and so are those of every `support/` module it imports, followed through further support modules and each read
    once.
  - ADAPTERIMPORT-10: A `from M import a` judges `M` and `M.a`.
  - ADAPTERIMPORT-11: An imported name is red when it is
    a forbidden module or lies inside one.
  - ADAPTERIMPORT-12: A name that is
    an ancestor of a forbidden module (`shop` above `shop.adapters`) counts only as a namespace: its own `__init__` is not
    judged, only `M.a`.
  - ADAPTERIMPORT-13: Any other name the import graph holds is red when it reaches a forbidden module.
  - ADAPTERIMPORT-14: Reach is computed once per run as the union of grimp's `find_downstream_modules(m, as_package=True)`
    over the forbidden modules `m` the graph holds, as import-linter's own forbidden contract filters them; a forbidden module nothing imports is not in the graph and reaches nothing.
  - ADAPTERIMPORT-15: A name neither forbidden nor in the graph is clean.

  - ADAPTERIMPORT-16: One finding per statement, at the core test's `path:line`, is an `adapter import` row whose fact names each
    forbidden module the statement's names are or reach once, with the support modules that carried it (and `directly`
    where the statement is the module too), as in `imports shop.adapters (through tests/support/stores.py)`,
  - ADAPTERIMPORT-17: its fix to name the test after an adapter or composition module or to import no adapter; all
    findings print sorted by path and line.
  - ADAPTERIMPORT-18: A core test, or a support module one imports, that does not parse makes the `imports` row
    `✗ not checked` with the reason (exit `2`).
  - ADAPTERIMPORT-19: A `[[layout.allow]]` entry naming the rule is refused
    like any rule outside the layout rules.

  The imports area reads the config and builds the graph once
  per run; `import-contract`, `adapter-import` and `integration-marker` judge that one read.

  - ADAPTERIMPORT-20: A read that cannot judge
    refuses all three, a rule's own refusal only that rule,
    and grimp's warnings print once, as `import-contract`'s note rows (IMPORT-25).

  - ADAPTERIMPORT-21: The
    [fleet matrix](commands.md#quality-report) cell reads `enforced` when the repository's config holds the contract with
    the id, `no adapter contract` otherwise, and `not in <tag>` under a pin below v0.27.0.
    It first ships in v0.27.0.

  Ruled by the operator: a core test is exempt only through the registered module marker `imports_adapter`, counted per
  test directory in `[adapter_imports]` of `quality/baseline.toml`, a raise being a loosening.

  - ADAPTERIMPORT-22: A core test whose module-level `pytestmark` holds `pytest.mark.imports_adapter` gets no `adapter import` finding: the
    mark may be bare or called, alone, in a list or tuple, or reached through a module-level alias, read as the report's
    suite columns read it (`docs/commands.md`, FLEET-122); a mark on a class or a
    test exempts nothing.

  - ADAPTERIMPORT-23: It exempts only where the project's `pyproject.toml` registers it, as
    `imports_adapter` or `imports_adapter: <text>` in `[tool.pytest.ini_options] markers`; otherwise the finding stands and
    its fix is `register imports_adapter in [tool.pytest.ini_options] markers`.
    A registration only a `conftest.py` hook,
    pytest's native `[tool.pytest]` table, `pytest.ini`, `tox.ini` or `setup.cfg` makes is not read, so the test stays red.
  - ADAPTERIMPORT-24: Each exempted module counts once toward its direct parent directory, and parents are never summed.
  - ADAPTERIMPORT-25: A marked test module the rule would not report, a clean core test or a test that is no core test,
    registered or not, is `unused` at its `pytestmark` line with the fix `remove the imports_adapter mark`, so a stale mark
    cannot let a later adapter import in silently.

  - ADAPTERIMPORT-26: `[adapter_imports]` of `quality/baseline.toml` holds a positive count per test directory; a missing table or entry holds
    0.
    The rule judges the count: above the held one is `grew` at the directory (`3 marked modules import an adapter ·
    ceiling 2`), a directory with no entry is `new debt` (`… · ceiling 0`) with the fix `quality check --fix` while the file
    has no table and `import no adapter in one of them, or move it into the integration suite` otherwise, and below it is
    `can fall` at `quality/baseline.toml` with the fix `quality check --fix`.
  - ADAPTERIMPORT-27: Without the contract that
    carries the id the table is neither judged nor changed; with it, a baseline that cannot be read
    refuses the rule.

  Under `--fix --dry-run` the rule judges the table the fix would write. `quality check debt --fix` lowers or drops entries and never raises or adds one, except a carried move, which it
  writes under the new directory and reports as `moved` as it does for `[job_minutes]` (FIX-34); each report is a change
  of the written table against the held one, so a fix run again before the commit tells none. It records the measured
  counts (`recorded  K adapter import exceptions · quality/baseline.toml`, `to record` in a dry run) where the file has no
  table, a marked module exempts an import and git never held the table, as FIX-12, FIX-13 and FIX-46 say for every
  held table; a table git holds and the tree lost refuses with the restore step.

  - ADAPTERIMPORT-28: The fix reads the import contracts and
    builds the graph for the counts, and a read that cannot judge refuses it while the table is held.
  - ADAPTERIMPORT-30: The verdict and the door hold the head's table against the base's as "The baseline only falls" says, and a quality older
    than the release that knows the table is exit `2` on it, as for every unknown table.

  Named gaps, waiting for usage evidence: fixtures a
  `conftest.py` injects, string imports (`importlib.import_module`, `pytest.importorskip`), the `__init__` of an ancestor
  of a forbidden module, the `ignore_imports` of the contract (the raw graph is asked), `allow_indirect_imports = true`
  (a core module that reaches an adapter is in the reach set, so its own test is red for importing its subject), and a
  test-side fake, which is caught only through a forbidden module it imports.
- `integration-marker`: a test module outside the core that reaches an adapter carries the `integration` mark in its
  module `pytestmark`.
  - INTEGRATION-1: It judges where `adapter-import` judges, from the same contract read, and without the contract that
    carries the id it judges nothing and reads no file.
  - INTEGRATION-2: It judges the test modules of ADAPTERIMPORT-4 and 8 that are no core test (ADAPTERIMPORT-5 to 7); a
    core test belongs to `adapter-import` alone.
  - INTEGRATION-3: A module is adapter-bound when one of its own imports, or one in a support module it imports, is red
    as ADAPTERIMPORT-9 to 15 say, through the one reach, so a support module that imports an adapter binds every
    non-core test that uses it.

  - INTEGRATION-4: An adapter-bound module is clean only when its one direct module-level `pytestmark` binding, as
    `pytest_marks` reads it (`docs/commands.md`, FLEET-121), carries `integration`: bare, called, in a list or tuple, or
    through a module-level alias. pytest applies that mark to every item it collects under the module, so its own tests,
    inherited and `unittest` suites under any name, and tests it imports, star-imports or binds count with no reading of
    their own. A mark on a test, on a class or in a class `pytestmark`, and a mark that only a `conftest.py` hook,
    `pytest.param(marks=…)` or `-m` sets, does not clean the module; a module with no test is judged the same way.
  - INTEGRATION-5: The mark counts only where the project's `pyproject.toml` registers it, as ADAPTERIMPORT-23 reads
    `imports_adapter`; without the registration every adapter-bound module is unclean, marked or not, its fact ends
    `integration is not registered` and its fix is `register integration in [tool.pytest.ini_options] markers`.

  - INTEGRATION-6: One finding per unclean module, at the line of its first red statement, is an `unmarked` row whose
    fact names each forbidden module the module's red statements are or reach as ADAPTERIMPORT-16 words them, then
    `module not marked integration`, as in `imports shop.adapters · module not marked integration`; its fix is `put
    pytest.mark.integration in the module's one direct pytestmark binding`, which also tells the author of a module that
    marks its tests one by one, or binds its marks twice or under a condition, what the rule reads.
  - INTEGRATION-7: An `integration` mark on a module that is not adapter-bound is no finding.
  - INTEGRATION-8: A module that does not parse refuses the rules that judge it or are carried by it, and only those: a
    non-core test refuses `integration-marker`, a core test `adapter-import`, and a support module both rules reach
    both. The one read serves both rules and carries each rule's refusal, so one rule's refusal never silences the
    other.
  - INTEGRATION-9: The rule holds no baseline table and knows no exception mark, and a `[[layout.allow]]` entry naming
    it is refused.
  - INTEGRATION-10: The `integration` mark exempts nothing from `adapter-import`, and `imports_adapter` exempts nothing
    here.
  - INTEGRATION-11: The [fleet matrix](commands.md#quality-report) cell reads `enforced` when the repository's config
    holds the contract with the id, `no adapter contract` otherwise.

  Named gaps, waiting for usage evidence: fixtures a `conftest.py` injects, string imports, `allow_indirect_imports`
  (each red or outside what a static read sees); a `conftest.py` hook or plugin that adds or removes marks or builds
  items of its own; tests in a file pytest collects only through a project's `python_files`; and a write to the
  module's marks that no second binding shows (a star import after the binding that brings a `pytestmark`,
  `globals()`, `global pytestmark` in a nested body, a write through the value), which reads clean while pytest runs
  the tests unmarked. Any other shape of the mark reads as unmarked, so the module is red.
- `claude-md`:
  - CLAUDE-1: a tracked `CLAUDE.md`, in any case (`claude.md` is named as it is),
  - CLAUDE-2: holds exactly `@AGENTS.md` with or without a final line feed,
  - CLAUDE-3: so Claude, Codex and Grok read the same guidance, and the repository tracks an `AGENTS.md` beside it.
  - CLAUDE-4: No `CLAUDE.md` is clean, with or without an `AGENTS.md`.
  - CLAUDE-5: A symlink is a finding (a dangling one too),
  - CLAUDE-6: and so is a tracked `.claude/CLAUDE.md`, which Claude Code reads as project memory and Codex and Grok
    never read.
  - CLAUDE-7: Any other bytes are a finding, a Windows line ending included;
  - CLAUDE-8: a nested `docs/CLAUDE.md` is not judged.
  - CLAUDE-9: A finding's row names the file and says `claude only` (`.claude/CLAUDE.md`), `symlink`, `diverges` or `no
    AGENTS.md`,
  - CLAUDE-10: with a fixed fact and a fix in prose that never show its content;
  - CLAUDE-11: every fix is prose with no command, and for a file's own text it says to move the lines into `AGENTS.md`.
  - CLAUDE-12: A tracked file missing from disk is skipped;
  - CLAUDE-13: one the filesystem refuses to read makes the `files` row `✗ not checked` with the reason (exit `2`).

  No configuration switches the rule off.

  Ruled by the operator: `CLAUDE.md` and `AGENTS.md` never diverge (CLAUDE-7). A finding names only the file
  (CLAUDE-9, CLAUDE-10), and its fix is a sentence, never a command (CLAUDE-11).
- `agents-md`: the tracked file named exactly `AGENTS.md` at the root has the shape of the document set (write-docs'
  "Repository document set", which owns it).
  - AGENTS-1: Line 1 is a title, `# ` and the name of a GitHub repository a configured remote points at; any other first
    line, an empty file included, is a `title` finding at line 1,
  - AGENTS-12: and a title that is well formed but equals no such name is one too.
  - AGENTS-2: Between the title and the first `## ` heading only blank lines stand; anything else is a `section` finding
    at its first line, and a file with a title and no section is clean.
  - AGENTS-3: Every `## ` heading outside a fenced code block (backticks or tildes) is one of `Commands`, `Rules`,
    `Owners`, `Documents`, exactly spelled, at most once, in that order; another name, a repeat, a heading out of order
    and a second `# ` heading are `section` findings at their line, and each section may be missing. Headings of level 3
    and deeper are no sections.
  - AGENTS-4: The file has at most 100 lines (the last one counts without a line feed; `\n` alone separates lines) and
    at most 4096 bytes; each limit crossed is a `too long` finding at line 1.
  - AGENTS-5: No root `AGENTS.md` has no shape finding, and a tracked one missing from disk is skipped.
  - AGENTS-6: A finding names fixed words, line numbers, counts, for `missing` the path text of the span, for `title`
    the repository name a remote gives and, for `misplaced`, `misnamed` and `unused`, the path, never the title or a
    heading, and its fix is prose with no command.
  - AGENTS-7: A link that leaves the repository, text that is not UTF-8 and a read the filesystem refuses make the
    `files` row `✗ not checked` with the reason (exit `2`).
  - AGENTS-8: In `## Documents`, the first backticked span of each line is that document's path; later spans, lines
    without a span and other sections' backticks are never judged. The section runs to the next `# ` or `## ` heading or
    the end of the file; a line in a fence is no document line; a second `## Documents` heading starts another run.
  - AGENTS-9: A path passes if it is a tracked file, or a directory with at least one tracked file below it, spelled in
    exact case; one trailing `/` of a directory is optional and nothing else is normalised. A tracked file missing from
    disk passes.
  - AGENTS-10: Any other path is a `missing` finding at its line, whatever its form (absolute, `~`, `..`, a scheme, an
    `@` span, an anchor, an untracked file, a wrong case): fact `<path> is not tracked`, fix "point at a tracked
    document or delete the line".
  - AGENTS-11: The check compares text with the tracked listing alone, so no filesystem probe follows text from the file
    and nothing outside the repository is opened. It accepts any tracked directory; whether that is a directory of the
    document set whose files are named by their task or contract stays the reviewer's call.
  - AGENTS-13: The title, with blanks around it removed, equals the name part (after `owner/`) of any configured
    remote's GitHub repository, case ignored; otherwise the `title` finding's fix is "make line 1 `# <name>`" with each
    such name, joined by "or" (AGENTS-12). A remote counts when its URL, as configured and before any `insteadOf`, is
    `https://github.com/<owner>/<name>`, `ssh://git@github.com/<owner>/<name>` or `git@github.com:<owner>/<name>`, with
    at most one trailing `/` and then `.git`, in any case. When `AGENTS.md` is tracked and on disk and no remote counts,
    the `files` row is `✗ not checked` ("no GitHub remote names this repository", next step "add the repository's GitHub
    remote", exit `2`) before any finding, so the findings above show once a remote counts; no file, or a tracked one
    missing from disk, stays clean.
  - AGENTS-14: No printed line holds a remote URL; a URL with credentials counts as no remote.
  - AGENTS-15: The file sits at the root only: a tracked path whose last component is `AGENTS.md` in any case and that
    is not the root path is a `misplaced` finding at that path, fact "guidance outside the root file", fix "move what an
    agent needs into the root AGENTS.md or delete it, or declare it in quality/config.toml with [[layout.allow]], its
    path, the rule agents-md and a reason".
  - AGENTS-16: A tracked root file named `AGENTS.md` in another case is a `misnamed` finding, fact "the document set's
    path is exact", fix "rename it to AGENTS.md", and its content is not judged.
  - AGENTS-17: A `[[layout.allow]]` entry with the rule `agents-md` and a nested path stops AGENTS-15 for that path
    only; an entry for the root `AGENTS.md` is no finding and exempts nothing, and an entry for a root file in another
    case leaves AGENTS-16 standing.
  - AGENTS-18: An entry with the rule `agents-md` whose path no tracked file has is an `unused` finding as SUFFIX-12
    says.
  - AGENTS-19: Placement reads no file and needs no root file or remote: a nested file that is a symlink, gone from disk
    or not UTF-8 is `misplaced` all the same; when the root file is tracked and on disk and no remote counts,
    AGENTS-13's refusal covers nested findings too.
  - AGENTS-20: The rule is a layout rule, so its entry is accepted with `rule = "agents-md"`, and a malformed `[layout]`
    table or entry makes its row `✗ not checked` with the reason and the example, with or without a tracked `AGENTS.md`.

  No configuration switches the rule off.
- `root-layout`:
  - ROOT-1: every entry directly in the root of the tracked tree is on quality's base list or declared.
  - ROOT-2: An entry is a file or a directory (written `frontend/`); a directory with many files is one entry.
  - ROOT-3: Names compare in exact case, and the finding for a name that differs only in case names the base-list
    spelling.
  - ROOT-4: A file and a directory of one name are two entries,
  - ROOT-5: and a root symlink counts by its own name.
  - ROOT-6: Only tracked files count; a stray left on disk is not judged.
  - ROOT-7: The base list: `README.md AGENTS.md CLAUDE.md HEART.md LICENSE .gitignore .gitattributes .gitmodules
    pyproject.toml uv.lock .python-version package.json pnpm-lock.yaml package-lock.json Cargo.toml Cargo.lock
    rust-toolchain.toml Dockerfile .dockerignore compose.yaml compose.yml docker-compose.yaml docker-compose.yml
    sonar-project.properties .importlinter .pre-commit-config.yaml` and the directories `.github/ .aco/ quality/ src/
    tests/ docs/ scripts/`.
  - ROOT-8: It belongs to quality and changes with a quality version; a repository extends it, never shortens it. Each
    other entry is declared in `quality/config.toml`, one entry and one one-line reason, no pattern and no deeper path:

  ```toml
  [[root.allow]]
  entry = "frontend/"
  reason = "the web client, built apart from the Python package"
  ```

  - ROOT-9: A declaration that matches no tracked root entry is a finding;
  - ROOT-10: a declaration of a base-list entry that is tracked is clean.
  - ROOT-11: A finding's row names the entry and says `unlisted` (its fact adds the base list's spelling for a name that
    differs only in case)
  - ROOT-12: with both ways out in the fix, move it to its owner's directory or declare it with a reason, in prose with
    no command and no local path;
  - ROOT-13: a declaration that matches no tracked root entry says `unused` (its fact adds the tracked directory for an
    entry written without its slash).
  - ROOT-14: Findings are sorted by entry and all printed.
  - ROOT-15: An invalid declaration (a `/` inside the entry, `.`, `..`, `.git`, an empty entry, a multi-line reason, an
    unknown key, a missing key, an entry that is not text, a `[root]` key other than `allow`, `allow` that is not an
    array of tables, or one entry declared twice) makes the `files` row `✗ not checked` with the reason, and `next:`
    shows a `[[root.allow]]` example.

  Ruled by the operator: any pull request may add a declaration with a reason (ROOT-8); the review judges whether the
  reason holds. A declaration is no loosening, so `quality verdict` asks for no `[[gate.loosened]]` entry.

  - ROOT-16: The rule reads names, never content, and writes nothing.
  - ROOT-17: No configuration switches it off.

  Named gaps: a submodule in the root counts as a file entry and no case of it is tested; marketplace's refusal of every
  symlink in the tree stays that repository's own check.
- `file-suffix`:
  - SUFFIX-1: a tracked file whose basename has no suffix is a finding,
  - SUFFIX-2: unless it is executable (index mode 100755), a symlink (120000) or a gitlink (160000),
  - SUFFIX-3: its basename is a dotfile, its basename is on quality's list of tool-mandated names (`LICENSE Dockerfile
    Makefile CODEOWNERS`),
  - SUFFIX-4: or it is declared.
  - SUFFIX-5: The index (`git ls-files -s`) is the only source of a path's mode and a link is never followed;
  - SUFFIX-6: index data that cannot be read makes the `files` row `✗ not checked` with the reason (exit `2`).
  - SUFFIX-7: Names compare in exact case, in any folder.
  - SUFFIX-8: The list belongs to quality and changes with a quality version. A file is declared in
    `quality/config.toml`, a table of its own beside `[[root.allow]]`,
  - SUFFIX-9: one exact tracked path,

  the rule and a one-line reason:

  ```toml
  [[layout.allow]]
  path = "deploy/EDGE_SHA"
  rule = "file-suffix"
  reason = "the deploy script reads it by this name"
  ```

  - SUFFIX-10: The rule is `agents-md`, `file-suffix`, `script-name`, `script-header`, `test-layout` or `test-import`; a
    declaration covers only its own rule,
  - SUFFIX-11: and one `script-name` entry covers both the shape and the location of its path.
  - SUFFIX-12: A declared path that is not tracked is a finding of the declared rule, `unused`,
  - SUFFIX-13: whose fix deletes every `[[layout.allow]]` entry of that path (an untracked path makes each of its
    entries stale; the fix names no rule); a declaration for a file that already complies is clean.
  - SUFFIX-14: A suffixless file says `tracked`
  - SUFFIX-15: with both ways out, give it a suffix or declare it, in prose with no command;
  - SUFFIX-16: findings are sorted by path and all printed.
  - SUFFIX-17: The six rules' fixes that ask for an entry end with one clause naming the finding's own rule.
  - SUFFIX-18: A duplicate `(rule, path)`, a folder (a trailing `/`), a pattern (`*`, `?`, `[`), `..`, a leading `./`,
    an empty component, a blank path, an absolute path, a path that is not text, an unknown rule, an unknown key, a
    missing key or a reason that is not one-line text makes the `files` row `✗ not checked` with the reason, and `next:`
    shows a `[[layout.allow]]` example; a malformed `[layout]` table is the same.
  - SUFFIX-19: The rule writes nothing and no configuration switches it off.

  Ruled by the operator: quality checks in CI where files sit below the root (`tests/`, `scripts/`, file names).
- `script-name` and `script-header` judge a script:
  - SCRIPT-1: a tracked file with index mode 100755, whose content starts with `#!`, or that is a `.sh`, `.py` or `.mjs`
    file under a `scripts/` folder at any depth (`frontend/scripts/` too).
  - SCRIPT-2: A path with a `tests/` component is never a script,
  - SCRIPT-3: nor is a link (120000, 160000),
  - SCRIPT-4: nor data beside a script such as `scripts/BACKUP.md`.
  - SCRIPT-5: The mode comes from the index only; the content is read through the repository reader, and a file that
    cannot be read makes the `files` row `✗ not checked` with the reason (exit 2).
  - SCRIPT-6: Both rules compare names in exact case, print all findings sorted by path and have no off switch.
  - SCRIPT-7: A row says `misnamed` where the name's shape breaks the rule (its fact adds the place, when that breaks
    too), `misplaced` where only the place does, and `no header` for a script without a header (its fact names the
    window it checks).

  A declaration is a `[[layout.allow]]` entry as for `file-suffix`.
  - `script-name`:
    - SCRIPT-8: a script's basename without its extension is snake_case with at least two words (`run_static_checks.sh`,
      `verify_release.py`; `check.sh` and `prove-x.py` are red), lower-case letters and digits only,
    - SCRIPT-9: and the script lies under a `scripts/` folder.
    - SCRIPT-10: Tool-mandated basenames and dotfiles are exempt from both.

    There is no verb list: whether the verb fits stays a judgement for the structure audit.
  - `script-header`:
    - SCRIPT-11: a comment or docstring stands within the first three non-blank lines
    - SCRIPT-12: after the shebang and a PEP 723 block; blank lines do not count.
    - SCRIPT-13: Comments are `#`, and `//` or `/*` for `.mjs`.
    - SCRIPT-14: A tool directive (`# shellcheck`, `# -*-`, `# noqa`, `# type:`, `# pyright:`, `# ruff:`, `// eslint`)
      and code such as `set -euo pipefail` do not count.
    - SCRIPT-15: Presence only, never content.
- `test-layout` judges what may lie under a Python project's `tests/` and where a frontend test lies.
  - LAYOUT-1: A project is the directory of any tracked `pyproject.toml`, the root or a member at any depth;
  - LAYOUT-2: the file's content is never read, and one inside the `tests/` of another project, of either kind, makes no
    project.
  - LAYOUT-3: A project is judged when it has a package: a directory directly under `<project>/src/` that holds a
    tracked `.py` at any depth, with or without `__init__.py`.
  - LAYOUT-4: A project without one, and a `tests/` folder that belongs to no project, are not judged.
  - LAYOUT-5: Under a judged `<project>/tests/` every tracked path is a `test_*.py` (placed and named by the mapping
    below), a `conftest.py` at any depth, a file in a `support/` or `fixtures/` folder at any depth, or a file in the
    top-level `tests/e2e/`.
  - LAYOUT-6: An `__init__.py` under `tests/` is red anywhere, `support/` and `e2e/` included, and is data only inside a
    `fixtures/` folder.
  - LAYOUT-7: A finding names its case, as a `stray` row: an `__init__.py` (delete it, or declare it) or any other file
    (move it to a `support/` helper folder or a `fixtures/` data folder, or declare it).
  - LAYOUT-8: A declaration is a `[[layout.allow]]` entry with the rule `test-layout` on an exact tracked path. Paths
    come from the index and nothing is read from disk; a link or gitlink is judged by its own name and never followed;
    an index record that cannot be read makes the `files` row `✗ not checked` (exit `2`).
  - LAYOUT-9: Names compare in exact case,
  - LAYOUT-10: all findings print sorted by path and no configuration switches the rule off.

  Named gaps, waiting for usage evidence: a `tests/` folder elsewhere (`src/p/tests/`), a project with only `setup.py`,
  a project whose `src/` holds only modules, and a package named `support` or `fixtures`.

  - LAYOUT-11: Every `test_*.py` outside `fixtures/` and the top-level `e2e/` mirrors the module it tests, one
    direction.
  - LAYOUT-12: Its folder mirrors a folder of `src/<package>/` (`tests/api/` is `src/<package>/api/`;
  - LAYOUT-13: with several packages `tests/<package>/api/` is `src/<package>/api/`),
  - LAYOUT-14: and that folder holds a tracked `.py` at any depth.
  - LAYOUT-15: `tests/scripts/` mirrors `<project>/scripts/`
  - LAYOUT-16: and `tests/workflows/` mirrors the root's `.github/workflows/`,
  - LAYOUT-17: directly and only in the root project;
  - LAYOUT-18: both come before any package name.
  - LAYOUT-19: Each candidate of the mirrored folder has a key: `__x__` reads as `x`, otherwise one leading `_` is
    dropped;
  - LAYOUT-20: candidates are the `.py` files and the subfolders that hold a `.py`
  - LAYOUT-21: (in `scripts/` any file and subfolder,
  - LAYOUT-22: in `.github/workflows/` the `.yml` and `.yaml` stems with `-` read as `_`),
  - LAYOUT-23: and in the top folder of a package also its own name.
  - LAYOUT-24: The test's name, `test_<name>.py`, splits at `_` into words and a word prefix of it, ending at a word
    boundary, must equal a key exactly;
  - LAYOUT-25: the rest is the aspect, and a name whose first word is empty (`test_.py`, `test__x.py`) has no prefix.
  - LAYOUT-26: A `test_*.py` in a `support/` folder is red.
  - LAYOUT-27: A member's tests map only into the member's own `src/` and `scripts/`.
  - LAYOUT-28: Each case has its own fact and fix,
  - LAYOUT-29: a file that breaks the folder and the name condition together gets one finding with both facts, and every
    fix ends with declaring the path.
  - LAYOUT-30: A test module that mirrors nothing says `misplaced`,
  - LAYOUT-31: its fact joining the facts by `; `.

  Ruled by the operator: `tests/` mirrors `src/<package>/` by path (LAYOUT-12, LAYOUT-24), unit and integration tests
  are told apart by a pytest marker, and only `e2e` has its own folder (LAYOUT-5).

  Frontend tests.

  - LAYOUT-32: A frontend project is the directory of any tracked `package.json`, the root or a member at any depth;
  - LAYOUT-33: its content (`workspaces` included) is never read, a `package.json` inside the `tests/` of a project of
    either kind makes no project, and a test file belongs to its nearest enclosing frontend project.
  - LAYOUT-34: A repository without one is not judged by these rules.
  - LAYOUT-35: Judged files are tracked TypeScript and JavaScript files (the suffixes `size` counts, `.mjs`, `.js` and
    `.tsx` included)
  - LAYOUT-36: with a word between the first and the last `.` that is `test`, `spec`, `test-d` or `spec-d`;
  - LAYOUT-37: only the name decides, no `tests/` or `__tests__/` folder does, a link is judged by its name and a
    gitlink is a folder and never judged.
  - LAYOUT-38: A path under the `tests/` of a Python project with a package is judged by the Python cases only.
  - LAYOUT-39: Sibling: a judged file with no word `spec` is a test, and its base, its name up to the first `.`,
  - LAYOUT-40: or a non-empty prefix of the base that ends before a `-` or `_` or between a lower-case letter or digit
    and an upper-case letter,
  - LAYOUT-41: must equal the key of a subject in the same folder.
  - LAYOUT-42: A subject is any tracked file there that is no gitlink and carries no test word, whatever its suffix (a
    symlink counts by its name, a folder never);
  - LAYOUT-43: its key is its base without a leading `+`, so `page.test.ts` maps to `+page.svelte`.
  - LAYOUT-44: An empty base never matches, and `.test.ts` is red.
  - LAYOUT-45: Spec: a judged file with the word `spec` lies under its project's top-level `e2e/` folder at any depth;
  - LAYOUT-46: other files there are not judged as specs, and a `.test.*` there needs its sibling.
  - LAYOUT-47: A declaration exempts its path from every case.
  - LAYOUT-48: Each case has its own fact and fix: a test with no sibling (move it beside the file it tests and name it
    `<subject>[<Aspect>]` plus its own tail) and a spec outside `e2e/` (move it into the project's `e2e/`, or name a
    Vitest test `<subject>.test.<suffix>`); every fix ends with declaring the path; both say `misplaced`,
  - LAYOUT-49: and a test in the repository root names it as the repository root, and one in a nested project's root
    folder names that folder.

  `test-layout` judges no `.rs` file and reads no `Cargo.toml`, and Cargo's own layout stands (other rules still read
  tracked files for a shebang); a `package.json` or `pyproject.toml` inside a Cargo tree makes a project like any other.
  Named gaps, waiting for usage evidence: a subject that is a folder, an acronym run read as one word
  (`HTMLParser.test.ts` beside `HTML.ts` is red), a name holding both `test` and `spec` (judged as a spec), a test-named
  file outside any frontend project, where helpers, setup files and fixtures lie, a test whose subject lies outside its
  project (declare it), an `e2e/` folder that is its own workspace package (its specs are red), a `package.json` under a
  `fixtures/` folder outside any `tests/` (a project) and a judged file in a frontend `fixtures/` folder (it needs a
  sibling).
- `test-import` judges:
  - TESTIMPORT-1: every Python project, with or without a package, as `test-layout` finds projects.
  - TESTIMPORT-2: The importers are the tracked `.py` files under its `tests/`
  - TESTIMPORT-3: that are no link
  - TESTIMPORT-4: and lie in no `fixtures/` folder,
  - TESTIMPORT-5: `support/` modules and `e2e/` drivers included. The targets are the tracked `test_*.py` and
    `conftest.py` files there, outside `fixtures/`
  - TESTIMPORT-6: (a link is a target by its name),
  - TESTIMPORT-7: including a member's or the root's own.
  - TESTIMPORT-8: Every `import` and `from ... import` statement is read, inside a function or under `if TYPE_CHECKING:`
    too.
  - TESTIMPORT-9: An absolute name is red when its components equal the trailing components of a target's path without
    `.py`, which covers every `pythonpath` spelling without reading pytest's configuration (`import conftest`, `from
    tests.test_x import y`, `from e2e.conftest import z`);
  - TESTIMPORT-10: a `from M import a` checks `M` and `M.a`, so `from tests import conftest` is red and `from
    tests.support.x import test_helper` is not.
  - TESTIMPORT-11: A relative name resolves against the importer's folder and must equal a target's path.
  - TESTIMPORT-12: A plain `e2e/` driver may import another.
  - TESTIMPORT-13: One finding per statement, at `path:line`,
  - TESTIMPORT-14: says `test import` and names each target it imports;
  - TESTIMPORT-15: the fix is a `support/` module or a fixture in `conftest.py`, or a `[[layout.allow]]` entry with the
    rule `test-import` on the importer's path, which exempts all its imports
  - TESTIMPORT-16: (an untracked declared path is a finding; a `test-layout` declaration does not cover it). Importers
    are decoded as Python 3.14 reads source and parsed:
  - TESTIMPORT-17: a file that does not parse (`<path> is not valid Python (...)`)
  - TESTIMPORT-18: or cannot be read
  - TESTIMPORT-19: makes the `files` row `✗ not checked` with the reason (exit 2); no other file is read.
  - TESTIMPORT-20: All findings print sorted by path and line and no configuration switches the rule off.

  Not read, and waiting for usage evidence: string imports (`importlib.import_module`, `__import__`,
  `pytest.importorskip`), `pytest_plugins`, `sys.path` edits, `runpy`, source passed to a subprocess with `-c`, and
  `*_test.py` as a target. An import whose name matches a tracked test module is reported even when it means a
  third-party module; rename the test module or declare the importer.
- `python-floor` judges:
  - FLOOR-1: every tracked `pyproject.toml` that `test-layout` finds as a project (the root or a member at any depth,
    none inside another project's `tests/`)
  - FLOOR-2: and that holds a `[project]` table; one with only `[tool.*]` tables, or with `project` as a value, is not
    judged,
  - FLOOR-3: and a gitlink is never entered.
  - FLOOR-4: The floor is the highest lower bound among the `requires-python` clauses `>=`, `>`, `~=`, `==` and `===`,
    read with `packaging`;
  - FLOOR-5: a trailing `.*` is dropped (`==3.14.*` is 3.14, `==3.*` is 3),
  - FLOOR-6: `!=`, `<` and `<=` set none,
  - FLOOR-7: and `>3.13` counts as 3.13.
  - FLOOR-8: The file is clean from a floor of 3.14: `>=3.14`, `>=3.14,<3.15`, `~=3.14`, `==3.14.*` and `>=3.15` pass;
  - FLOOR-9: `>=3.12`, `>=3.11,<3.15`, `>3.13`, `>=3.14.0rc1`, `<3.15`, an empty value, `dynamic = ["requires-python"]`
    and a missing `requires-python` are red
  - FLOOR-10: (the last four name no floor).
  - FLOOR-11: One finding per file, at its path,
  - FLOOR-12: says `below floor` (its fact names the value and the floor)
  - FLOOR-13: or `no floor` (the value, which sets no lower bound),
  - FLOOR-14: with the fix: raise `requires-python` to `>=3.14` and move CI, `.python-version`, Dockerfiles and ruff's
    `target-version` with it, or declare the file.
  - FLOOR-15: A `[[python.allow]]` entry with exactly a `path` (the exact tracked path of the `pyproject.toml`, no
    folder, pattern or `..`)
  - FLOOR-16: and a one-line `reason`,
  - FLOOR-17: one per path,
  - FLOOR-18: makes that file clean; its own table, because the exception is meant to end.
  - FLOOR-19: Each allowed file stands as a `· <path> allowed <fact> · <reason>` [row](output.md#notes) under the files
    row, after the area's findings.
  - FLOOR-20: An entry whose path is no judged file (untracked, gone, without `[project]`) or whose file is already at
    the floor says `unused`;
  - FLOOR-21: its fix is to delete the entry.
  - FLOOR-22: A candidate that is not UTF-8 TOML, or whose `requires-python` is no string, makes the `files` row [`✗ not
    checked`](output.md#refusals) (exit 2) with its own reason.
  - FLOOR-23: A `requires-python` that is no PEP 440 specifier set or holds a `===` clause with a version that is not
    PEP 440 (`===foobar`) makes it `✗ not checked` with `<path> has a requires-python quality cannot read` (exit 2);
  - FLOOR-24: that reason holds no part of the value.

  No configuration switches the rule off. Only `requires-python` is read, because uv and pip refuse an interpreter
  outside it, so a leftover setter cannot install the project on old Python. Not read, and waiting for usage evidence:
  workflows, `.python-version`, Dockerfiles, ruff's `target-version`, `setup.py`, `setup.cfg` and Poetry's
  `[tool.poetry]`; `!=` as a floor (`>=3.13,!=3.13.*` is red although it admits only 3.14 and up); a job that never
  installs the project, which runs old Python unnoticed; `uv sync` behind an older setup-python, which fetches 3.14 and
  leaves a stale setter; and a Python project without a `pyproject.toml`.

  Ruled by the operator: every tracked `pyproject.toml` that `test-layout` finds as a project is judged, the root and
  each member alike (FLOOR-1).
- `naming` judges:
  - NAMING-1: the names a repository writes itself against its stem, the repository name of the remote called `origin`,
    as [naming.md](naming.md) rules.
  - NAMING-2: Only `origin` gives the stem, while `agents-md` keeps reading every remote; a remote counts as AGENTS-13
    says.
  - NAMING-3: Every finding's fact says `read from remote origin` after the stem, so a clone that a rename left behind
    shows it; re-pointing `origin` is its fix.
  - NAMING-4: A checkout with no GitHub `origin`, whatever files it tracks, makes the `files` row [`✗ not
    checked`](output.md#refusals) ("no GitHub remote called origin names this repository", next step "add the
    repository's GitHub remote as origin", exit `2`), because the repository's own name is always judged.
  - NAMING-5: Every tracked `pyproject.toml` that `test-layout` finds as a project (the root or a member at any depth)
    and that holds a `[project]` table is judged, a gitlink never entered; a file with `tests`, `fixtures` or
    `node_modules` among its folders is no project's own and is not judged.
  - NAMING-6: Its `name`, after PEP 503 normalisation, is `overnightworks-<stem>` or
    `overnightworks-<stem>-<component>`; under `[tool.uv] package = false` the bare `<stem>` or `<stem>-<component>` is
    also accepted.
  - NAMING-7: Each import package under its `src/` is `<stem>` or `<stem>_<component>`, each `-` of the stem written
    `_`;
  - NAMING-8: each name in `[project.scripts]` is `<stem>` or `<stem>-<component>`.
  - NAMING-9: A component is one or more words of lower-case letters and digits, joined by `-` or, for an import, `_`;
  - NAMING-10: imports, commands and npm names compare exactly.
  - NAMING-11: The `name` of each tracked `package.json` outside those folders is `overnightworks-<stem>[-<component>]`,
    `<stem>[-<component>]` or `slidev-addon-<stem>`;
  - NAMING-12: a manifest with no name has nothing to judge.
  - NAMING-13: The repository's own name is red with a capital or with a role suffix (`-runtime`, `-service`, `-client`,
    `-server`, `-lib`, `-sdk`, `-api`), at `.git/config`, whatever files it tracks.

  `sonar.projectKey` stays SONAR-59's.

  - NAMING-14: One finding per name, `misnamed`, at its file, says the kind (`distribution`, `import`, `CLI`, `npm` or
    `repository`), the name found and the stem, and its fix names the accepted forms;
  - NAMING-15: they print sorted by path.
  - NAMING-16: A deviation is an entry of `src/quality/naming_deviations.toml` (repository, kind, name, the item holding
    the operator's ruling) and covers exactly that name of that kind in that repository, a distribution compared after
    PEP 503.
  - NAMING-17: The file is quality's: no `quality/config.toml` table, `[[layout.allow]]` or `[[gate.loosened]]` declares
    one, and a new entry takes effect with a quality release;
  - NAMING-18: [naming.md](naming.md) lists the entries.
  - NAMING-19: A `pyproject.toml` that is not UTF-8 TOML, a `package.json` that is not UTF-8 JSON and a `name` that is
    no string make the `files` row `✗ not checked` with the file and its reason (exit `2`).
  - NAMING-20: Not read, and waiting for usage evidence: import clashes with a third party's package, plugin manifests,
    a Dockerfile's or a workflow's names; forks and archived repositories are outside the coverage and the rule detects
    neither, so a fork that calls the gate carries a deviation.

  No configuration switches the rule off. It first ships in v0.23.0, so a gate pinned below that release shows `not in
  <tag>` in the fleet matrix's column, and from v0.23.0 on the cell reads `enforced` (FLEET-18).

  Ruled by the operator: coverage is every non-archived repository of the organisation except forks, private ones
  included, and a new repository conforms from its first pull request.

  - NAMING-21: The rule ships as `Switch.ALWAYS` in its first release with no report-only phase, and no deviation entry,
    held baseline table or `[[gate.loosened]]` entry covers a name red when it ships.
- `python-security` judges:
  - SECURITY-1: every tracked regular `.py` file in any directory with the `S` codes of quality's own ruff (an exact pin
    in quality's `pyproject.toml` that serves its own lint too):
  - SECURITY-2: the ruff binary of quality's installation, never one on `PATH`,
  - SECURITY-3: at [the pinned version](commands.md#scanners), runs `ruff check --isolated --no-cache --ignore-noqa
    --select S,T20 --target-version py314 --output-format json -- <files>`, in batches below the argument limit,
  - SECURITY-4: and writes nothing into the checkout.

  That one run serves `python-print` too.

  - SECURITY-5: With no tracked `.py` file ruff is not started.

  A clean `code` row counts what ruff judged ([area rows](output.md#area-rows)).

  - SECURITY-6: Not judged: untracked files, a link, anything inside a gitlink, notebooks, `.pyi` files and suffixless
    scripts.
  - SECURITY-7: The repository's own ruff `select`, `ignore`, `per-file-ignores`, `exclude`, `extend` and noqa have no
    effect.
  - SECURITY-8: Each finding stands as a [row](output.md#rows) under the `code` row, `! src/a.py:12  insecure  S307 Use
    of possibly insecure function; ...`,
  - SECURITY-9: and its `fix:` names the line its noqa must stand on, ruff's `noqa_row`.
  - SECURITY-10: In the files `test-import` judges (every `.py` under a Python project's `tests/`, outside `fixtures/`)
    S101, S105 to S107, S603 and S607 are exempt;
  - SECURITY-11: S108 (a shared path breaks test isolation), S310 and every other code stay red there,
  - SECURITY-12: and `tests/fixtures/` and `src/` get no exemption.
  - SECURITY-13: A finding is green only when the line at ruff's `noqa_row` (the last line of a multi-line string)
    carries `# noqa: <CODE>[, <CODE>...] - <reason>` naming its code;
  - SECURITY-14: the reason is the text after ` - ` up to the next `#` or the end of the line, holds a letter and no
    comma (Sonar's noqa grammar splits on commas, so a comma raises python:S7632 for every caller that runs Sonar).
  - SECURITY-15: Every other form is red: no reason, a blanket `# noqa`, another directive as the reason (`# type:
    ignore[x]  # noqa: S607`, `# noqa: S607  # pragma: no cover`), `# noqa: S607: reason`, a wrong code, another line, a
    file-level `# ruff: noqa`,
  - SECURITY-16: and a second directive behind a reasoned one, bare or reasoned (`# noqa: S603 - why  # noqa: E501`),
    since ruff reads only a line's first directive.
  - SECURITY-17: ruff itself honours the reasoned form, so a caller's own ruff stays quiet.

  A caller whose ruff selects none of a family (S or T20) lists exactly that family in `lint.external`, which keeps its
  RUF100 from calling the gate's noqa unused; a caller whose ruff selects a family (for example `select = ["ALL"]`)
  leaves it out, because its ruff honours each noqa itself and ignores a file-level `# ruff: noqa` for a code listed in
  `external`.

  - SECURITY-18: A file ruff cannot parse makes the `code` row [`✗ not checked`](output.md#refusals) with `ruff cannot
    parse <path>` (exit 2);
  - SECURITY-19: a tracked `.py` file the checkout does not hold (deleted, left out by a sparse checkout, or a link to
    nowhere),
  - SECURITY-20: a file Python cannot decode,
  - SECURITY-21: or that reads as other text than ruff reads as UTF-8 (a PEP 263 cookie such as utf-7),
  - SECURITY-22: [a missing or mismatched ruff](commands.md#scanners), ruff exiting other than 0 or 1,
  - SECURITY-23: a `Failed to lint` warning,
  - SECURITY-24: a report quality cannot read,
  - SECURITY-25: a time-out,
  - SECURITY-30: two tracked files whose names ruff's report prints alike, as names that differ only in bytes that are
    not UTF-8 do,
  - SECURITY-26: or a file with a finding whose comments Python's tokenizer cannot read is `cannot judge` (exit 2).
  - SECURITY-27: One such file makes the whole rule `cannot judge` or `unparsable`, so the S findings of the other files
    are not printed in that run;
  - SECURITY-28: everything here but the tokenizer refuses the shared run, so `python-print` refuses with it.

  The other rules are judged either way. A deliberately invalid Python fixture takes another suffix, since a `.py` file
  is Python. No configuration switches the rule off, and a reasoned noqa asks `quality verdict` for no
  `[[gate.loosened]]` entry.

  - SECURITY-29: Not judged, and waiting for usage evidence: notebooks, `.pyi` files and suffixless Python; judging a
    non-UTF-8 source (a latin-1 file with non-ASCII bytes stays `cannot judge`), since ruff reads UTF-8 only; a stale
    reasoned noqa, which ruff's RUF100 finds.

  Ruled by the operator: the rules are red from the release that ships them, with no report-only phase; `assert` (S101)
  is exempt in tests (SECURITY-10); a rule-specific noqa with a reason is green (SECURITY-13).
- `python-print` judges:
  - PRINT-1: `T201` (`print`) and `T203` (`pprint`)
  - PRINT-2: from `python-security`'s run, over the same files, with the same deafness to the repository's ruff settings
    and the same refusals: a run quality cannot trust or a file ruff cannot parse leaves both rules `cannot judge` or
    `unparsable`.
  - PRINT-3: Each finding stands as a [row](output.md#rows) (``! scripts/sync_agents.py:12  print call  T201 `print`
    found``),
  - PRINT-4: a print to `sys.stderr` included,
  - PRINT-5: and tests get no exemption.
  - PRINT-6: A reasoned `# noqa: T201 - <reason>` at `noqa_row` clears one line, as for `python-security`.
  - PRINT-7: A program's one output owner declares itself with a file-level `# ruff: noqa: T201[, T203] - <reason>` on a
    line of its own,
  - PRINT-8: the form ruff honours too,
  - PRINT-9: clears the codes it names in its own file only;
  - PRINT-10: its reason holds a letter and no comma,
  - PRINT-11: and the comment holds no second `#`.
  - PRINT-12: A bare `# ruff: noqa`, one without a reason, a comma in the reason, one naming an S code (`# ruff: noqa:
    S603, T201 - reason`), one behind code on its line and `# flake8: noqa` clear nothing,
  - PRINT-13: and no file directive clears an S code.

  The owner line is a tool directive, so it does not satisfy `script-header`: a script keeps its comment or docstring
  first and the owner below it, as `scripts/verify_release.py` does.

  - PRINT-14: Each honoured owner stands as a [row](output.md#notes) under the `code` row, after its findings, `·
    scripts/verify_release.py:14  allowed  output owner · <reason>`,
  - PRINT-15: and a clean row counts it (`· 1 allowed`); an owner that names no code found in its file is an `unused`
    row on its line, to delete.
  - PRINT-16: python-print reads the comments of each file with a print finding or the text `# ruff:`, and one whose
    comments Python's tokenizer cannot read leaves python-print alone `cannot judge`.

  No configuration switches the rule off, and an owner or noqa asks `quality verdict` for no `[[gate.loosened]]` entry.
  Not judged, and waiting for usage evidence: `sys.stdout.write` and `sys.stderr.write` (ruff's TID251 would flag every
  reference to the streams, `logging.StreamHandler(sys.stderr)` included, for a bypass nobody has used; reopened when a
  landing outside `tests/` replaces a `print` with a direct stream write), and `print(..., file=<variable>)`, which ruff
  does not report as T201.

  Ruled by the operator: a program's one output owner declares itself in its code (PRINT-7), never in a path list.
- The baseline only falls:
  - BASELINE-1: on a pull request `quality verdict` holds the head's `quality/baseline.toml` against the base commit's,
    so growing a function and rewriting the baseline stays red.

  Each loosening is one line under the verdict's `loosenings` row, named with the baseline's own metric, path and symbol
  (a file entry has no symbol):
  - BASELINE-2: an entry raised: `size baseline raises function_lines src/quality/cli.py main 82 -> 90`;
  - BASELINE-3: an entry the base does not hold (a function or class under another name included; a moved file is judged
    as below): `size baseline adds ccn src/a.py f 16`;
  - BASELINE-4: a clone pair the base does not hold, when the base has a `[clones]` table: `clone baseline adds
    tests/test_a.py run <-> tests/test_b.py check`;
  - BASELINE-5: the base's `[clones]` table gone from a head that still has the file: `clone baseline removed`;
  - a job limit the base's `[job_minutes]` table does not hold or holds lower, when the base has the table:
    `job limit baseline raises .github/workflows/ci.yml e2e 40 -> 45`,
    `job limit baseline adds .github/workflows/ci.yml e2e 40` (TIMEOUT-18);
  - the base's `[job_minutes]` table gone from a head that still has the file: `job limit baseline removed`
    (TIMEOUT-18);
  - a held spec ID the base's `[spec_citations]` table lacks, when the base has the table, one line per ID in sorted
    order: `spec citation baseline adds ST-02`; the base's table gone from a head that still has the file:
    `spec citation baseline removed` (CITE-12);
  - a directory count the base's `[adapter_imports]` table holds lower, or lacks, when the base has the table:
    `adapter import baseline raises tests/api 2 -> 3`, `adapter import baseline adds tests/app 1`; the base's table gone
    from a head that still has the file: `adapter import baseline removed`;
  - BASELINE-6: the base's file deleted: `quality/baseline.toml removed`;
  - a `[[size.generated]]` or `[[spec_citations.implemented]]` declaration is judged as in [gate](gate.md), "Declared loosenings".

  Ruled by the operator: a file git recognises as renamed (BASELINE-11), a directory move included, is no loosening when
  its numbers do not rise (BASELINE-12).

  - BASELINE-11: The verdict reads the renames between the base and the head commit with `git diff -M50% -l1000`, git's
    default similarity, whatever the repository's git configuration says;
  - BASELINE-12: a file git does not pair is new as before. The base's entries and clone sides under the old path then
    move to the new path, so the head is compared with the entries its predecessor held and a raise or an added symbol
    still names the head's path;
  - BASELINE-13: an entry the base holds under the new path is a leftover and does not count.
  - BASELINE-14: A rename from a path that is not UTF-8 moves nothing, since no baseline key can name it, but still
    clears its new path of leftovers;
  - BASELINE-15: a rename to a path that is not UTF-8 is not read.
  - BASELINE-16: The allowance moves rather than being copied: while the head baseline still names the old path, nothing
    moves and the new path's entries are adds.
  - BASELINE-17: A pair whose files moved is the same pair, and a renamed function is a new entry.
  - BASELINE-18: Renames are read only when the base and the head both hold a baseline: without a base, or without a
    baseline on either side, nothing is read.
  - BASELINE-19: `merge` fetches the same two commits; it reads the same renames.

  How `check` and `check --fix` treat the renames is told in the `size` rule's `--fix` paragraph above.

  - BASELINE-7: A lowered, equal or removed entry or pair is green,
  - BASELINE-8: and so is adopting a baseline (the base has none) or neither side having one.
  - BASELINE-9: A `[clones]` table the base lacks is an adoption too: its pairs are not compared,
  - BASELINE-10: while the size entries are still held against the base's.

  A `[job_minutes]` table the base lacks is an adoption in the same way, whatever the base's `[clones]` table is, and
  the job lines stand after the size and clone lines in the file's order (TIMEOUT-18), and the spec citation lines stand
  last. A `[spec_citations]` table the base lacks is an adoption in the same way (CITE-12), and so is an
  `[adapter_imports]` table, whose lines stand last. A test module git renames from one directory to another that
  carries the registered `imports_adapter` mark at the head moves one count of the old directory's base entry to the new
  one, capped at the old held count, so modules that leave a directory together share it; only a count above the carried
  one is a line, and a directory moved whole moves every count. The mark and the project's registration are read from
  the head commit's files, never the working tree, so the verdict and the door agree (BASELINE-19). They read the marks
  only where the base holds the table, and a module they cannot read carries no count, so its raise shows. A `--fix`
  takes its counts from the committed table and the staged renames alone, so a second one before the commit moves no
  count the first one already moved while it still carries a newly staged move (FIX-34).

  - ADAPTERIMPORT-29: Each of these lines is declarable.
  - BASELINE-20: A malformed baseline on either side is exit `2`, naming the side (`quality/baseline.toml at the base
    commit <sha>: ...`, `quality/baseline.toml at the head: ...`).
  - BASELINE-21: So is a head baseline that links out of the repository,
  - BASELINE-22: a base file that is not a regular file (`... : not a regular file`),
  - BASELINE-23: and a base that git refuses to read, a base commit the clone lacks included (`... : git exited <n>`),
  - BASELINE-24: git's own words never printed.

  A `quality/config.toml` the verdict cannot read is told in [gate](gate.md): the side shows in its step, not in its
  fact.

  - BASELINE-25: Only the head's malformed baseline adds `restore quality/baseline.toml from git, or repair it by hand`
    (an unknown table adds `run the quality release your gate pins` instead): a pull request cannot rewrite its base.
  - BASELINE-26: The base is a commit sha, as [gate](gate.md) tells of the verdict's variables: `HEAD~1` or `-x` is exit
    `2` (`GATE_BASE_SHA is not a commit sha`) before git runs.
  - BASELINE-27: Outside a pull request (push, `merge_group`, `workflow_dispatch`) `gate.yml` passes no base,
  - BASELINE-28: so the base is never read and only `check` holds the tree.

  A lizard update that measures a function higher therefore turns the pull request that takes it red; a ruled raise is
  declared (see [Declared loosenings](gate.md#declared-loosenings)) and stays red until the operator's door merges it.
- The import contracts only tighten:
  - LOOSE-4: on a pull request `quality verdict` and the door compare the contracts of the three config places at the
    head with the base's, as import-linter parses them (never the raw options, so moving a config between
    `.importlinter`, `setup.cfg` and `pyproject.toml` changes nothing), without building an import graph.
  - LOOSE-5: A contract is the same on both sides when it has the same `name`; a rename is a removal plus a new
    contract.
  - LOOSE-6: Each loosening is one line under the verdict's `loosenings` row, sorted:
  - LOOSE-7: a contract the base holds and the head lacks: `import contract "layers" removed`, unless one of its lists
    names only modules the head no longer has and git does not read as renamed (below);
  - LOOSE-8: the base's config gone, whatever it held, or only its section gone from a file that stays, once:
    `import-linter config in .importlinter removed`;
  - LOOSE-9: an `ignore_imports` expression the base does not hold, one line each, compared as parsed (spacing around
    `->` does not count, a wildcard is a new expression): `import contract "domain" ignores app.domain -> app.web.*`;
  - LOOSE-10: any other field of a contract that differs, `broken_contract_guidance` excepted, unless the change only
    forbids more (below): `import contract "layers" changes allow_indirect_imports`
  - LOOSE-11: (a changed `type` reads `changes type`; a module dropped from a list reads as that list's field, unless
    the head no longer has it and git does not read it as renamed, or git reads it as renamed to a module the same list
    names (below));
  - LOOSE-12: `root_packages` (as a set of names), `include_external_packages` or `exclude_type_checking_imports`
    changed: `import-linter changes root_packages`;
  - LOOSE-36: the contract that carries the id `core-imports-no-adapter` (the contract of both `adapter-import` and
    `integration-marker`) stays at the head under the same name without the id: `import contract id
    core-imports-no-adapter removed`, or `import contract id core-imports-no-adapter moves "a" -> "b"` when contract `b`
    carries it at the head.

  A carrier that is gone at the head, renamed (LOOSE-5) or removed, gives LOOSE-7's line only, and a removed config
  LOOSE-8's. Two contracts carrying the id on a side are exit `2` like LOOSE-16.

  Ruled by the operator:

  - LOOSE-1: Each of these loosenings is red, can be declared with a `[[gate.loosened]]` entry whose `what` is its line
    exactly, and lands only through the operator's door.
  - LOOSE-2: The door reads the import contracts at the merge state and at the base itself and refuses an undeclared
    loosening, even when it was opened for Sonar's legacy findings.
  - LOOSE-3: Removing the whole config file can be declared as well and goes through the door.

  Ruled by the operator:

  - LOOSE-25: a change that can only forbid more is no loosening.
  - LOOSE-26: Per contract type: `allow_indirect_imports` turned from true to false (also by dropping the line)
  - LOOSE-27: and a module added to `source_modules` or `forbidden_modules` of a `forbidden` contract, the base's
    expressions all kept;
  - LOOSE-28: a module added to the `modules` of an `independence` contract, the base's all kept;
  - LOOSE-35: a base expression that is a plain module name counts as kept when a wildcard expression of the head's same
    list matches it by import-linter's own matcher, so base `a.x`, `a.y` against head `a.*` is green and base `a.*`
    against head `a.x` is red;
  - LOOSE-29: and a module newly placed in a `layers` contract: the head's `layers` are compared with every module name
    the base's `layers` do not name stripped, and a layer left empty dropped. Base layers `a`, `b` against head `a`,
    `new`, `b` is green.
  - LOOSE-30: A layer left with one module has no sibling, so its independence says nothing: base `(game)` against head
    `(game) : (agent)` is green, and base `a | b` against head `a : b : new` stays a loosening.
  - LOOSE-31: Everything else stays a loosening, except a struck module the head no longer has and git does not read as
    renamed (LOOSE-19), or git reads as renamed to a module the same list names (below) (LOOSE-22): a removed, renamed
    or replaced expression (a wildcard narrowed or replaced by names included), an expression added and another removed
    in one field,
  - LOOSE-32: `allow_indirect_imports` turned from false to true,
  - LOOSE-33: `as_packages`, `unmatched_ignore_imports_alerting`, `containers`, `exhaustive`, `exhaustive_ignores`
  - LOOSE-34: and every `protected` or `acyclic_siblings` field in either direction, a reorder, a merge into `a | b`, a
    module moved to another layer, `a | b` turned into `a : b`, a required `a` made optional `(a)`, and the session
    options (LOOSE-12).

  No import graph of the repository is built.

  - LOOSE-13: A new contract,
  - LOOSE-14: a removed `ignore_imports` entry,
  - LOOSE-15: a reworded `broken_contract_guidance` and adopting import contracts (the base has no config) are green.
  - LOOSE-16: Two contracts of one name on a side,
  - LOOSE-17: two config places holding a section,
  - LOOSE-18: or a config that cannot be read are exit `2`, naming the side: the verdict says `import-linter config at
    the base commit <sha>: ...` or `import-linter config at the head: ...`, the door names the base commit by its SHA
    alone, `import-linter config at <sha>: ...`.

  - LOOSE-19: A module a pull request strikes from a list of a contract (`layers`, `containers`, `source_modules`,
    `forbidden_modules`, `modules`, `protected_modules`) is no loosening when the head no longer has it: no tracked
    `<path>.py`, no `<path>/__init__.py` and no file under `<path>/`, below the directory of its root package.
  - LOOSE-20: Every other strike stays `changes <field>`: a module still present, a wildcard expression, a name outside
    the root packages, a root package the head's files, or the base's, cannot place.
  - LOOSE-21: A module whose file, or a file under whose folder, git reads as renamed between the base and the merge
    commit, to a file that is not empty, is no deletion: striking it stays a loosening.
  - LOOSE-22: It is no loosening when the same field at the head names the module at its renamed path.

  The name is exact: a renamed folder needs the package's own name, a listed submodule does not count, and several
  renamed files must agree on one module. In `layers`, `containers` and `protected_modules` the module stands where the
  struck name stood: another layer, a `:` for a `|`, another position or another order is `changes <field>`.

  - LOOSE-23: A contract the head lacks is no loosening when one of its lists names only modules the head no longer has
    and git does not read as renamed, except that `layers` does not count when the contract is `exhaustive` and one of
    its containers still has a file at the head or git reads a file of it as renamed.
  - LOOSE-24: Only file names and sizes are read, at the checked-out merge commit by the verdict and at the pull
    request's merge commit by the door, and only when a module is struck or a contract is removed; a read that fails, or
    a rename list that came with a warning, is exit `2`.

- SECRET-1: The `secret` rule scans the whole history reachable from `HEAD` with gitleaks (a key removed later is still found;
- SECRET-2: other branches the checkout fetched are not scanned),
- SECRET-3: always with quality's own config
  (`src/quality/gitleaks.toml`): the repository's `.gitleaks.toml`, `.gitleaksignore` and `gitleaks:allow` comments have
  no effect.
- SECRET-28: A repository with no commit has no history to scan, so it is exit `2`.
- SECRET-16: A known false positive is allowed by the value, not the place: the repository's
  `quality/config.toml` names the gitleaks rule, the file and the SHA-256 of the finding's secret (UTF-8, lowercase hex)
  with a reason:

```toml
[[secrets.allow]]
rule = "aws-access-token"
file = "tests/fixture.py"
secret_sha256 = "<64 hex>"
reason = "fixture value"
```

- SECRET-17: A file that lists `allow = [...]` inline reads the same and takes a new entry as one more inline table,
  because TOML refuses a `[[secrets.allow]]` block after an inline list.

- SECRET-18: One entry allows that value in that file at any line and in every commit that holds it.
- SECRET-19: Another value in
  the same place is red,
- SECRET-20: and the same value in another file is red.

A red finding is
[the row](output.md#rows) `! <path:line>  secret  gitleaks rule <rule> · commit <sha8> · value not shown` (in the
staged change without the commit). Under it stands
`fix: rotate it; if it is no secret, allow it in quality/config.toml with a reason:` and the entry's lines,
[the sha256 whole on its line](output.md#length), so the entry is copied from the output with a reason added.

- SECRET-21: An entry
  without a reason,
- SECRET-22: a hash that is not 64 lowercase hex,
- SECRET-23: a duplicate `(rule, file, secret_sha256)`
- SECRET-24: and the old `fingerprint` form are exit `2`.
- SECRET-25: [The clean row](output.md#area-rows) counts the
  commits reachable from `HEAD` that gitleaks scans (`✓ secrets  clean  412 commits`); a count git cannot give is exit
  `2`.

- SECRET-4: A missing `quality/config.toml` means no allowances: the scan still runs and its findings are red.
- SECRET-5: Only a
  file that exists and cannot be read or parsed,
- SECRET-6: or links out of the repository,
- SECRET-7: is exit `2`; a link
  that leads nowhere or loops exists, so it is exit `2` too.

- SECRET-8: Secret handling: the secret scan runs gitleaks without `--redact` so the report carries each `Secret` to be hashed.
- SECRET-9: gitleaks writes that report to a `tempfile` scratch directory (mode 0700); quality reads it, keeps only the
  hash, and deletes the directory on every exit path (success, gitleaks failure, parse error).
- SECRET-10: The value
  reaches no stdout, stderr, log, exception text or retained file;

gitleaks itself runs without its `-v`.
While the scan runs, the value exists in that report file and in quality's process memory. The boundary is the deletion
and the absence of any other sink, not memory-only handling. The hash is safe to print: it appears only where the
history already shows the content, and a real secret must be rotated anyway. gitleaks' scan stderr may echo scanned
values, so it is shown only as the vet allows ([the vet](output.md#the-vet)); the missing-report and unexpected-exit
[refusals](output.md#the-vet) keep their exit code without those diagnostics.

- SECRET-11: The scan runs git without any attributes (not the repository's `.gitattributes`,
- SECRET-12: not `info/attributes`,
- SECRET-13: not the system or global file), so a `-diff` or `binary` line cannot hide a key.
- SECRET-14: That needs
  git 2.41 or newer; an older git, or one whose version cannot be read, is exit `2`.
- SECRET-15: The system file is
  excluded by `GIT_ATTR_NOSYSTEM`, git's documented switch; that is pinned as an environment contract, not by a root-only
  real-file test.

- SECRET-26: Every allowed finding is a row `· <path:line>  allowed  <rule> · <reason>`, and
  [the clean row](output.md#notes) counts them (`· 1 allowed`).
- SECRET-27: An allowance that matched nothing is a row too, so a
  stale entry can be deleted:
  `· quality/config.toml  unused  [secrets] allow entry <n> matches no secret · <rule> in <file>`.

[`quality check secrets --staged`](commands.md#quality-check-secrets---staged) prints the allowed rows and no unused
row: one change cannot tell a stale entry from one the history still needs.

- WORKFLOW-1: The `workflow-lint` rule (actionlint) and the `workflow-security` rule (zizmor, `--offline`) judge every tracked `.yml`
  and `.yaml` file directly under `.github/workflows/` that the tree holds,
- WORKFLOW-2: with quality's own configs: a
  repository's scanner config and ignore comments change nothing;
- WORKFLOW-3: quality's configuration adds the hosted
  runner labels `ubuntu-26.04` and `ubuntu-26.04-arm`, which actionlint does not know yet,
- WORKFLOW-4: and accepts
  GitHub's `uses: $/…` form of a local action
- WORKFLOW-5: or reusable workflow, which actionlint does not
  know yet either.
- WORKFLOW-6: `workflow-security` also judges every tracked `action.yml` and `action.yaml`, wherever it lies;
- WORKFLOW-7: actionlint is never handed one,
- WORKFLOW-8: and the clean row counts the workflows alone.

A lint finding is `! <path:line>  invalid  actionlint`, the message under it behind `| `, then
`fix: correct the workflow as the message says`. A security finding is
`! <path:line>  insecure  zizmor <audit> · <description>` with `fix: see <url>` ([rows](output.md#rows)).

Ruled by the operator: the clean row counts those files and says in the row itself, and in no note of its own, that only
offline audits ran (ROW-38), so a workflow that only an online audit would flag is not read as fully audited:
`✓ workflows  clean  5 workflows · offline audits only`.

- CONCURRENCY-1: The `pr-concurrency` rule judges every tracked workflow that a pull request triggers: its `on:` is the string
  `pull_request`, `pull_request_target` or `pull_request_review`, a list that holds one of them, or a mapping with one as
  a key; the mapping's values are not read. A workflow with none of them (`push` alone, `schedule`,
  `workflow_dispatch`, `merge_group`, `workflow_call` alone) is outside; a `workflow_call` file is no root, because its
  caller's `concurrency` governs.
- CONCURRENCY-2: Only the top-level `concurrency` counts, never a job's.
- CONCURRENCY-3: The rule reads
  no expression: after trimming and collapsing the whitespace inside each `${{ }}` and setting `||`, `==` and `!=` off by
  one space, the `group` and `cancel-in-progress` values are compared with a closed list; a form outside it is a finding,
  however equivalent.

Only space, tab, carriage return and line feed fold inside an expression, so a
no-break space keeps a form outside the list, as actionlint refuses such an expression. A block scalar (`|`, `>`, any
chomping) is read by its content, its final line breaks dropped; a quoted scalar with an escaped line break stays
outside the list:

- G1: `${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}`, always
- G2: `${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.run_id }}`, always
- F1: `${{ github.event.pull_request.number != null }}`, always
- F2: `${{ github.event_name == 'pull_request' }}`, only without a target or review trigger; with one, those runs stay
  uncancelled

A pull-request run is grouped by its number and cancels; any other run gets its run id and does not, so a run on main
always finishes. A group that falls back to the ref or the commit is shared by runs on main, and GitHub drops a pending
run when a newer one queues in the same group, whatever `cancel-in-progress` says. G1 puts the runs of one pull request
under two events of a workflow that names more than one pull-request trigger in one group, whichever two events they
are, so one can cancel the other; G1 passes there and the fix names G2.

- CONCURRENCY-4: The block every finding shows as its fix, and
  a repository copies:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event.pull_request.number != null }}
```

- CONCURRENCY-5: A finding is `missing` (no `concurrency`, or none with a `cancel-in-progress`; a group alone, a string included),
  `shared group` (the `group` is not a listed form, naming `github.sha`, `github.ref`, `github.head_ref` or
  `github.ref_name` where it holds one; a `concurrency` mapping without a `group` is `no group`, at the `concurrency`
  key's line) or `cancels main` (the `cancel-in-progress` is not a listed form), each with the
  block as its fix.
- CONCURRENCY-6: A workflow whose YAML or file cannot be read, a `group` or `cancel-in-progress` that
  is no string, and an alias or a merge key where it can hide the top mapping, `on:`, the `concurrency` or either value
  are exit `2` naming the file.
- CONCURRENCY-7: The clean row of `workflows` keeps its words.

quality's own checkout carries the block in `ci.yml` and `code-scanning.yml`. No configuration
switches the rule off.
It first ships in v0.11.0, so a dependent meets it at its first bump to that release.

- PRWORKFLOW-1: The `pr-workflows` rule judges the same workflows as `pr-concurrency` (a `pull_request`, `pull_request_target` or
  `pull_request_review` trigger, read as CONCURRENCY-1 reads it): each is the gate's caller (a job whose `uses:` is the
  repository's own `gate.yml` or quality's pinned `gate.yml`), CodeQL's caller (the same for `codeql.yml`), or declared in
  `quality/config.toml` with its reason.
- PRWORKFLOW-2: A workflow that is none is `unlisted`, at the workflow with no
  line, and its fix names both ways out with the block to copy; the reason is the person's to write:

```toml
[[workflow.allow]]
file = ".github/workflows/preview.yml"
reason = "it deploys the pull request preview and no check belongs to it"
```

- PRWORKFLOW-3: `file` is the exact tracked path of one workflow, no folder, pattern, `..` or absolute path; `reason` is one non-empty
  line; one entry per file.
- PRWORKFLOW-4: A workflow no pull request triggers (`push` alone, `schedule`,
  `workflow_dispatch`, `merge_group`, `workflow_call` alone) is outside, so a workflow reached only through a reusable call
  is judged as its caller.
- PRWORKFLOW-5: A declaration whose file is no such workflow, is absent, or is the gate's or
  CodeQL's caller is `unused`.

A declaration is no loosening: moving a mandatory job out of the gate's
caller into a declared workflow leaves that job missing from the run (VERDICT-7), and dropping it from `mandatory_jobs`
is a loosening (VERDICT-13).

- PRWORKFLOW-6: A workflow whose YAML or file cannot be read, and an alias or a merge key where it can hide
  the top mapping or `on:`, are exit `2` naming the file.

The clean row of `workflows` keeps its words, and
the rule's extent is the number of pull-request workflows. No configuration switches the rule off. It first ships in v0.23.0, so a gate pinned below that release shows
`not in <tag>` in the fleet matrix's column, and from v0.23.0 on the cell reads `enforced` (FLEET-18).

- PYTEST-1: The `pytest-workers` rule judges every pytest call that runs tests in a `run:` step of every tracked workflow, whether or
  not a pull request triggers it; only the `run:` of a step is read, as POSIX shell whatever its `shell:` says, and a composite
  action is not.
- PYTEST-2: The script is never run. The value is read line by line, as the shell reads it: a literal
  block scalar at each file line, a folded or quoted one with each line break kept at the scalar's first line; each `${{
  }}` on a line becomes one dynamic word; a line ending in a backslash joins the next, and one that ends the
  script joins nothing; a `#` that starts a word outside
  quotes ends the command text; a pair of backticks that no quote or backslash protects is read as `$(` and `)`, and a
  last backtick left without a partner on the line is read as `$(` when a word follows it (a substitution that spans
  lines) and dropped otherwise; the words are split by Python's `shlex`, and `&&`, `||`, `;`, `|`, `&`, `(` and `)`,
  also one glued to `<` or `>`, end a segment. A word that holds `$(` is split again the same way,
  so `"$(pytest -q)"` reads as `$(pytest -q)`. The string after the `-c` flag of `sh`, `bash`, `dash`, `zsh` or
  `ksh` is taken whole, substitutions included, before it is split, and is read as a script of its own by the same reader;
  its segments stand after the shell's. The shell's flags are read only before its first operand: a flag is a word that
  starts with a dash or a plus sign, and `-o`, `+o`, `-O`, `+O` and `--rcfile` and `--init-file` take the next word as their
  value (so does any flag that ends in `o` or `O`, as `-eo`).
  A lone `-` or `--` ends the flags, and the word after it is the first operand. A `-c` flag is a single-dash word that
  holds the letter `c`, so `-c`, `-ec` and `-lc` count, and the first operand after one is the string. A string the reader
  cannot split to its end is `invalid` where the run holds the word `pytest` (PYTEST-6); `bash run.sh -c 'pytest -q'`
  reads no code, since its first operand is the script's name, and `bash -- -c 'pytest -q'` reads none either, since its
  first operand is `-c`.

- PYTEST-3: A segment is a pytest call when its command word, after assignments (`VAR=value`), `env`, `sudo`, `timeout`, `nice` and
  `xargs` with their flags and operands, `time`, `exec`, `command`, `nohup` and the shell keywords `if`, `then`, `elif`,
  `else`, `do`, `while`, `until`, `!` and `{`, is `pytest` or `py.test`; a Python interpreter (`python`, `python3`,
  `python3.<n>`, `python3.<n>t`, `pypy`, `pypy3`, `pypy3.<n>`) with only flags before `-m pytest`, where `-W`, `-X` and
  `--check-hash-based-pycs` take a value; or a runner (`uv run`, `uvx`, `poetry run`, `pipenv run`, `pdm run`, `hatch
  run`, `coverage run`, `npx`, `bunx`, `pnpm exec`, `pnpm dlx`, `npm exec`, `yarn`) that starts either, past its flags and
  their values. `yarn dlx`, `yarn exec`, `pnpm <bin>` and `bun x` are named gaps. The flags that take a value
  are `env` `-u`, `--unset`, `-C`, `--chdir`, `-a` and `--argv0`, and `sudo` `-u`, `-g`, `-D`, `-C`, `-h`, `-p`, `-r`,
  `-t`, `-T`, `-U` and `-R`, when the value follows as its own word; a long `sudo` flag with its value after a space is a
  named gap. The wrappers `timeout`, `nice` and `xargs` are read through with their flags: `timeout` takes `-k`, `-s`,
  `--kill-after` and `--signal` with a value as the next word, then one duration word; `nice` takes `-n` and
  `--adjustment`; `xargs` takes `-I`, `-n`, `-P`, `-d`, `-E`, `-L`, `-s`, `-a` and the long forms `--replace`,
  `--max-args`, `--max-procs`, `--delimiter`, `--eof`, `--max-lines`, `--max-chars` and `--arg-file`, each with its value
  as the next word when that is separate. Only the words on the line are read: `echo x | xargs apt-get install` is a call
  of `apt-get install`, and `xargs` with no command word behind its flags is no call. The tool word after `uvx` loses a
  trailing `@version`, as does the tool word after `npx`, `bunx`, `pnpm exec`, `pnpm dlx`, `npm exec` and `yarn`, so `uvx
  pytest@8 -q` reads as `uvx pytest -q`. The value of `env -S` (also `-S'…'` and `--split-string=…`) is read as
  the words it splits into, in its place.
- PYTEST-4: The word `pytest` anywhere else is no call: `pip install pytest`,
  `echo "run pytest"`, a path, a comment. A script, `make`, `tox`, `nox`, `xvfb-run` or `docker run` starting pytest is
  the rule's named gap and reads as no call.

- PYTEST-5: The options before the first `--` are read as pytest reads them, by the reader `pytest-random` shares (RANDOM-7): the
  last of `-n N`, `-nN`, `-n=N`, `--numprocesses N` and `--numprocesses=N` counts, as argparse reads repeated flags, also
  behind other short flags in one word (`-qn4`, `-qn 4`, `-qn=4`). `N` is `auto`, `logical` or a whole number above zero: the
  call passes. No such flag, `-n 0` (`00` too, `-qn0` too) or an abbreviated flag (`--numproc 4`, which pytest refuses) is
  `serial`; any other `N`, a missing one or a dynamic one (`$WORKERS`, `$(nproc)`, `${{ matrix.n }}`) is `invalid`. The
  words after the first `--` are paths for pytest, not its flags, so `pytest tests -- -n auto` is `serial`. A call that only
  lists tests, with any spelling pytest declares for `--collect-only` (`--collect-only`, `--collectonly`, `--co`) before the
  first `--`, runs none and is not judged (`pytest --collect-only` is clean, `pytest -- --collect-only` is `serial`). A quoted lone `--` (`pytest -k "--" -n 4`) reads as that same word, because the reader does not keep the
  quote, so it is a false `serial` that the marker clears; pytest itself refuses `-k "--"`.
  `PYTEST_ADDOPTS` and a `-n` in `pyproject.toml` are not read.
- PYTEST-6: A line `shlex` refuses that holds the word `pytest`, and a
  command word built at run time (`eval`, or a word starting with `$`) in a `run:` that holds the word `pytest` outside
  comments, are `invalid`, because the rule sees a call it cannot read; without the word `pytest` in the `run:` they are
  no call. A command word that is a substitution (`$(which pytest) -q`, a backtick pair in its place, `"$(which pytest)"
  -q`, `if $(which pytest) -q; then`) in such a `run:` is `invalid` with the same fact, while `$(pytest -q)` is its pytest
  call, judged once.

A second substitution on a command line (`cp $(a) $(b) dest`, `echo "$(date)" "$(uname)"`)
is no command word; the same `$(…)` after the `)` of a `case` pattern is read the same way, a named gap.

- PYTEST-7: A comment `# pytest-workers: <reason>`, with a reason that holds a letter, clears every call of the logical line it
  stands on, or that stands on any line of the unbroken run of comment-only lines directly above the call's first line (a
  plain comment inside the run does not break it, so two markers stand on two lines); the first line that is not
  comment-only ends the run, and so does a block scalar's `run:` line that carries a comment; a marker that trails another
  command's line is not in the run and clears that command only, so the unmarked call of the next line is reported. The
  marker stands, for a plain or quoted value, as a YAML comment above `run:` or after its value; for a block scalar (`|`,
  `>`), a YAML comment above the `run:` key clears nothing, so the marker goes on the `run: |` line (it covers the
  script's first line) or on a block line directly above the line it clears; for a command joined by backslashes, above
  its first line or on any of its lines. Without the id, without a reason, with a reason of no letter, or separated from
  the call by a line that is not comment-only, it clears nothing; a marker that clears no call is not reported.
- PYTEST-13: A pytest line in a heredoc body is read as a shell line, since the reader does not parse heredocs: without a readable flag it is `serial`,
  and a marker above it clears it.
- PYTEST-8: A finding is at the call's first line, `serial` with the fact `pytest runs
  without -n` or `pytest runs with -n 0`, or `invalid` with the fact `-n count is not a number above zero or auto`,
  `command cannot be read to its end` or `command is built at run time, pytest -n cannot be read`, its fix `pass -n auto,
  or explain on the line above: # pytest-workers: <reason>`.
- PYTEST-9: A workflow whose YAML or file cannot be read, a
  `run:` that is no string, a `jobs` or a job that is no mapping, and an alias or a merge key where it can hide `jobs`, a
  job, `steps`, a step or a `run:` are exit `2` naming the file; a step with no `run:` is skipped.
- PYTEST-10: The clean row of `workflows` keeps its words, and
  the rule's extent is the count of workflows it read.
- PYTEST-11: quality's own checkout needs no workflow edit.

No configuration switches the rule off. It first ships in v0.11.0, so a dependent meets it
at its first bump to that release.

- RUNTIME-1: The `runtime-install` rule judges every `run:` step of every tracked workflow, whether or not a pull request triggers
  it: a step installs nothing through an operating-system package manager and pipes no download into a shell, or it
  carries the marker below. Only the `run:` of a step is read, as POSIX shell whatever its `shell:` says. Scripts a `run:`
  step calls, composite actions, the steps of `uses:` (an action that runs a command from an input, `with: command:`,
  included) and Dockerfiles are not read.
- RUNTIME-2: The script is read by the reader of `pytest-workers` (PYTEST-2), and
  every segment also keeps the separator that opened it: `|`, `|&`, `&&`, `||`, `;`, `&`, `(`, `-c` for the segments of a
  shell's `-c` string, or none at the line's start.
- RUNTIME-3: A segment's command word is found as `pytest-workers`
  finds it, behind assignments, `env`, `sudo`, `timeout`, `nice`, `xargs` and the prefix words, by the basename of a path,
  and a shell's `-c` string is read as a script of its own. `playwright` is read as `pytest` is: on its own, behind
  `python -m`, or behind a runner (`uv run`, `uvx`, `poetry run`, `pipenv run`, `pdm run`, `hatch run`, `coverage run`,
  `npx`, `bunx`, `pnpm exec`, `pnpm dlx`, `npm exec`, `yarn`), whose tool word loses a trailing `@version`.

- RUNTIME-4: A call is an install when its arguments, the words after its command word to the end of the segment, hold a word of its
  manager's row as an exact word: `apt-get` and `apt` (`install`, `reinstall`, `satisfy`, `build-dep`, `upgrade`,
  `dist-upgrade`, `full-upgrade`); `dpkg` (`-i`, `--install`); `dnf` and `yum` (`install`, `reinstall`, `localinstall`,
  `groupinstall`, `upgrade`, `update`); `apk` (`add`, `upgrade`); `brew` (`install`, `reinstall`, `bundle`, `upgrade`);
  `snap` (`install`, `refresh`); `playwright` (`install` together with `--with-deps`, in any order, or `install-deps`);
  and, as a closed reading of "an operating-system package manager", `pacman` (`-S`, `-Sy`, `-Syu`, `--sync`, `-U`),
  `zypper` (`install`, `in`, `update`, `up`, `dist-upgrade`, `dup`), `choco` and `winget` (`install`, `upgrade`) and
  `port` (`install`, `upgrade`). `update` of `apt-get`, `apt`, `apk` and `brew` refreshes an index and installs nothing,
  alone or before an install; an `update` alone still reaches the mirror at run time and reads green. Any other word is no
  install (`remove`, `purge`, `clean`, `list`, `playwright install chromium`, `playwright test`). The finding is
  `installs` with the command's basename and the matched word as its fact (`apt-get install`, `dpkg -i`, `playwright
  install --with-deps`), one per call, at its logical line's first line.
- RUNTIME-5: A segment is a download into a shell
  in these three forms and no other, a downloader being `curl` or `wget` and a shell `sh`, `bash`, `dash`, `zsh` or `ksh`,
  both by command word: the shell is opened by `|` or `|&` and an earlier segment of its pipeline is a downloader (`curl
  url | sh`, `wget -O- url | sudo bash -s -- -y`, `curl url | tee f | sh`); the shell holds the word `<(` directly
  followed by a downloader (`bash <(curl url)`); the shell's `-c` string is a command substitution whose first command is
  a downloader (`sh -c "$(curl url)"`). The shell's other arguments are not read. The finding is `installs` with the fact
  `a download runs as a shell script`.

- RUNTIME-6: What the reader cannot read is `invalid`, narrowed so that it stays rare. A line `shlex` refuses, or a `-c` string it
  cannot split, is `invalid` with the fact `command cannot be read to its end` only when the line holds a manager's name
  and one of that manager's install words (for `playwright`, `--with-deps` or `install-deps`), each as a word of its own.
  A command word built at run time (`eval`, a word that starts with `$`, a substitution) is `invalid` with the fact
  `command is built at run time, an install cannot be read` only when the `run:` holds such a pair outside comments; when
  the substitution's first command is itself an install, that call is judged and there is no second finding. `curl` and
  `wget` are no manager names, so a downloader or shell built at run time is a named gap.

- RUNTIME-7: A comment `# runtime-install: <reason>`, with a reason that holds a letter, clears findings in two places. The step
  form: the marker stands on the line directly above a step's first line, the line of its `-`, with its `#` in the
  column of that `-`, and clears every finding of the step's `run:`; a `#` in another column clears nothing, a comment
  in the `-` column that follows the previous step and stands directly above the next step clears the next step, and a
  step whose `-` stands alone on its line or that stands in a flow sequence (`steps: [{run: x}]`) has no step form. The
  per-line form is that of `pytest-workers`: the marker on a call's logical line, or on a line of the unbroken run of
  comment-only lines directly above its first line, clears that call (a marker trailing another command's line clears that
  command only), for a block scalar on the `run: |` line (it covers the first line) or on a block line directly above the
  call; the whole comment run above a step whose `run:` stands on the step's own first line (`- run: x`, a flow step) belongs
  to the step form alone, so that step takes a marker on its own line only, while a marker on the `-` line of a step
  whose `run:` stands on the next line (`- # runtime-install: reason`) clears that call; a marker between a step's
  `name:` and its `run: |` clears nothing. A marker that clears nothing is not reported.
- RUNTIME-8: A
  `pytest-workers` marker clears no `runtime-install` finding and a `runtime-install` marker clears no `pytest-workers`
  finding: each rule reads only its own id, and `pytest-workers` keeps its per-line placement. The marker is for a step
  that proves an install on a fresh system, and for nothing else: the reader cannot see a step's purpose, so the review of the dependent's diff judges it, and any other use is a deviation from
  the fleet standard, which only the operator rules. The findings stand in the `workflows` area, their fix `take the
  tool from the lockfile, the runner image or an image pinned by digest, or explain on the line above: #
  runtime-install: <reason>`.
- RUNTIME-9: A workflow whose YAML or file cannot be read, a `run:` that is no string, a
  `jobs` or a job that is no mapping, and an alias or a merge key where it can hide `jobs`, a job, `steps`, a step or a
  `run:` are exit `2` naming the file; a step with no `run:` is skipped and a tracked name gone from the tree is skipped.
- RUNTIME-10: The clean row of
  `workflows` keeps its words, the rule's extent is the count of workflows it read.

No configuration
switches the rule off. It first ships in v0.12.0, so a dependent meets it at its first bump to that release.
quality's own checkout needs no workflow edit.

- LIVE-1: The `live-layer` rule keeps the live layer out of CI. A repository that tracks a path under a `tests/e2e/live/` folder,
  at its root or in a judged project's `tests/`, has a live layer; without one the rule is clean with extent `0` and reads
  no workflow.
- LIVE-2: With one, every pytest call in a `run:` of every tracked workflow is judged, whatever triggers the
  workflow (a push or a schedule is CI too), read as `pytest-workers` reads it (PYTEST-2, PYTEST-3) and with its options
  as `pytest-random` reads them (RANDOM-7); a call that only lists tests (PYTEST-5) is not judged, and the extent is the
  number of workflows read.
- LIVE-3: A call passes when its last `-m` before the first `--` is an expression in pytest's
  marker grammar (`and`, `or`, `not`, parentheses, names) that is false whenever `live` is true, whatever any other marker
  is. `addopts` and `PYTEST_ADDOPTS` are not read: pytest reads them before the call's own words, so the call's own last
  `-m` wins. A `live(...)` with arguments counts as another name, and an empty `-m ""` selects every test.
- LIVE-4: A call
  with no `-m`, or with one that can select a test marked `live`, is `runs live` at the call's first line; its fact is
  `pytest can select tests marked live`, its fix `add -m "not live", or "and not live" to its -m expression`.
- LIVE-5: An
  `-m` built at run time (`${{ }}`, `$VAR`, `$(...)`) or not readable in pytest's grammar is `invalid` with the fact `-m
  expression cannot be read`; an unreadable shell line is `invalid` as `pytest-workers` reports it, and a command built at
  run time in a run holding the word `pytest` is `invalid` with the fact `command is built at run time, pytest -m cannot
  be read`.
- LIVE-6: A workflow that cannot be read is exit `2` as for `pytest-workers`.
- LIVE-7: No marker comment,
  configuration or declaration switches the rule off; only the operator rules a deviation ([proof](proof.md)). It first
  ships in v0.25.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column (FLEET-18).
- LIVE-8: Named gaps: a call behind a repository script, `make`, nox, tox or a mise task is not seen, since the live
  layer's own boundary covers it (a journey without platform's storage-state file fails loud, and CI never has that file);
  `-k`, `--deselect` and `--ignore` are not read as an exclusion, so a call that relies on them alone is `runs live`; a
  Playwright live layer under `frontend/e2e/live` is not read.

The marker grammar is read by `marker_expression`,
since pytest is no runtime dependency of quality; a test holds it against pytest's own parser.

Named gaps, each reading as no call: `sudo` flags that combine and take a value (`sudo -nu ci apt-get install`) and a
nested `env -S`; a download and its run in two steps (`curl -o f url && sh f`); `eval "$(curl url)"` and `source
<(curl url)`; a pipe into `python` or `perl`; a pipe that ends a line, since the reader joins lines at a backslash
only; a downloader or shell built at run time; subcommands that fetch packages and are not in the table (`downgrade`,
`distro-sync`, `builddep`, `swap`, `apt-get source`, `apt-get download`); `dpkg` flags glued to others (`-iE`);
`dnf5`, `microdnf` and `apt-fast`; wrappers that run something else (`xvfb-run`, `docker run`, `make`, `tox`, `nox`);
the runners `yarn dlx`, `yarn exec`, `pnpm <bin>` and `bun x`. A shell's `-c` string is a pipeline of its own, so a
pipe after it does not reach the string's downloader (`bash -c 'curl u' | sh`). Unlocked tool installs (`pip install`,
`uv pip install`, `npm install -g`, `cargo install`, `go install`) and lockfile or tool-manager installs (`uv sync`,
`npm ci`, `mise install`) are no finding; the first are the item "an unlocked tool install in a CI job is reported".
Known false alarms, reported and not cleared by the marker, which is for fresh-system proofs only: a heredoc body is
read as shell lines, so a heredoc that writes a Dockerfile with a chained `RUN apt-get update && apt-get install -y x`
is reported (its fix is a tracked Dockerfile), while a simple `RUN apt-get install -y x` is no call; a dry run
(`apt-get -s install`, `--simulate`, `--dry-run`, and `apt-get --print-uris`, which prints download addresses and
installs nothing) reads as the real call.

- CATALOG-1: The `catalog-check` rule, in the `workflows` area, requires a covered repository's pull-request workflows to call the
  generator's check. A repository is covered when its tracked root `pyproject.toml` declares `overnightworks-design` with the
  extra `catalogs` in `[project] dependencies`, in a group of `[project.optional-dependencies]` or in a group of
  `[dependency-groups]`; the name and the extra are compared canonicalised, a version, a marker or a URL does not count, and
  an `{include-group}` entry is skipped.
- CATALOG-2: Only the root file counts, so a nested project is a named gap,
- CATALOG-6: and
  `[tool.design-catalogs]` without the declaration does not cover.
- CATALOG-3: A repository that is not covered sees no row
  and no workflow is read.
- CATALOG-4: A `pyproject.toml` that is not UTF-8 TOML, whose dependency list is no list,
  whose group table is no table or whose
  entry is neither a requirement string nor an `{include-group}` table is exit `2` naming the file. The whole
  `[tool.design-catalogs]` table is design's to judge: a covered repository whose table design's `catalog_directory` refuses
  (any key, not only `catalogs`) is exit `2` naming `pyproject.toml` for each rule that needs the directory, `render-pass`
  and `locator-text`, and for no other rule.

- CATALOG-8: A covered repository's `run:` steps are read in every workflow a pull request triggers.
- CATALOG-7: A step counts when
  its script calls `design-catalogs` with `--check` in a form the shared shell reader reads as a call of a named tool, so
  the forms that `pytest-workers` reads stand.
- CATALOG-12: A call through `make` counts when the goal of a counted step
- CATALOG-21: reaches a
  recipe line of the tracked root `Makefile` that calls the check.
- CATALOG-13: The file is read only when no
  counted step calls the check directly and a counted step has a `make` goal, and only when it is a tracked file on disk; a
  tracked `GNUmakefile` or `makefile` makes the reading no call.

A `make` call is followed only with literal
goal words (a `VAR=x` word is skipped), with no flag at all (so no `-C`, `-f` or `-j`), and with a goal (a `make` call
with no goal, which builds the default goal, is not followed); target and prerequisite names are literal too, so a rule
whose name or prerequisite holds `$(VAR)` or `%` defines nothing and is not followed.

- CATALOG-15: The recipe is followed as make
  reads a plain file:
- CATALOG-14: a goal's recipe, then its prerequisites' recipes, then the recipes of the `make` calls in them,
- CATALOG-18: each
  target once.
- CATALOG-17: A `-` prefixed line,
- CATALOG-16: a line of a conditional or of a define block,
- CATALOG-19: a target
  whose recipe is given by two rules or by a double-colon rule,
- CATALOG-20: and a file with `.IGNORE`, `.RECIPEPREFIX`, `.ONESHELL`,
  an include or a `MAKEFLAGS` assignment are no call.
- CATALOG-23: A `Makefile` that
  is not UTF-8, or is a symlink that resolves outside the repository, is exit `2` naming the file, when a counted step has
  a `make` goal and no step calls the check directly.
- CATALOG-9: A word in an `echo`, a script, `tox`, `nox`, a
  composite action or a reusable workflow called through `uses:` is no call;
- CATALOG-22: every other form the reading does
  not follow is no call too.

A step with `continue-on-error: true` or with a `working-directory` other than
`.` does not count.

- CATALOG-11: An unreadable workflow, a `run:` that is no string, a `jobs` or a job that is no mapping, or an alias
  or a merge key where it can hide a step is exit `2` naming the workflow.

- CATALOG-24: A covered repository is judged in this order. Any counted step with a call, directly or through `make`, makes the
  rule clean: no row, whatever else the workflows hold. Otherwise each unreadable line and each command built at run time is an
  `invalid` finding. Otherwise the repository gets one `missing` finding, and exit 1 follows either:

  - CATALOG-5: `! .github/workflows  missing  no workflow step runs design-catalogs --check on a pull request, directly or through make`,
  - CATALOG-25: at the folder with no line;
  - CATALOG-26: `! <workflow or Makefile>:<line>  invalid  command cannot be read to its end`, for a logical line of a counted step's script, or of a
    recipe a counted `make` goal reaches, that the shared reader refuses to its end when the line's own text holds the word
    `design-catalogs` or `design.catalogs.command` as a whole word, not inside a longer name; a refused line without the word
    is no problem and reads as no call;
  - CATALOG-27: `! <workflow or Makefile>:<line>  invalid  command is built at run time, design-catalogs --check cannot be read`, for a command word
    built at run time (a lone `$`, `eval`, a word starting with `$`) in a script, or in the reached recipes, whose text as a
    whole holds either word. A make variable in the command word (`$(UV) run design-catalogs --check`) is such a command; the
    `$(MAKE)` and `${MAKE}` substitutions are not. "The script" is a counted step's `run:`; the reached recipe lines are one
    text, so once any of them names the word, every reached line whose command word is a make variable is its own row, even
    one that does not mention the generator: this reading errs red and is no gap of the list below.

- CATALOG-28: A workflow row stands at the workflow's path and the first line of the logical line; a recipe row at `Makefile` and the first
  physical line of the joined recipe line. Workflow rows come in the shared walk's order, then the `Makefile` rows in the order
  the scan reaches them.
- CATALOG-10: Every row has the fix `add a step to a pull-request workflow, or a make target that make
  lint runs: uv run design-catalogs --check, from the repository root`.

- CATALOG-31: The reading errs red in every form of the closed make reading that it does not follow: those read as `missing`, or as
  `invalid` when a command word is a make variable. These gaps err green, each reading as a counting call:

  - a here-document body line, a call inside `if false; then ... fi`, a call behind `cd sub &&`, and `design-catalogs` as an
    option value of `uv run`, which are properties of the shared reader that `pytest-workers` and `runtime-install` share;
  - a step with an `if:`, a call ending in `|| true` in a step or in a recipe line, and a job that is not mandatory;
  - `pull_request_target` and `pull_request_review` as triggers, trigger filters (`types`, `branches`, `paths`), a job-level
    `if:`, a step's or a default `shell:`, and `defaults.run.working-directory`;
  - `MAKEFLAGS` from the environment, and a `SHELL` or `.SHELLFLAGS` assignment in the `Makefile`;
  - a followed target that is not `.PHONY` while a file of its name is tracked, and a rule line with a `;` recipe after a tab
    recipe of the same target.
  - `uv run --directory sub design-catalogs --check`, `uv run --project sub design-catalogs --check` and
    `uvx design-catalogs --check`, which run in a subproject or without the locked dependency.

A repository that runs the generator without declaring it (`uvx --from 'overnightworks-design[catalogs]' design-catalogs
--check`, uv's legacy `[tool.uv] dev-dependencies`) is not covered and sees nothing; that is a named gap. No configuration
switches the rule off.

- FLEET-98: It first ships in v0.12.0, which only reports it: a dependent meets the enforcement at its first bump
  to v0.13.0, the release that names the switch, and its fleet cell reads `report-only` until then (a gate pinned at v0.12.0
  reads `report-only`; from v0.13.0 on the cell reads as on main).
- FLEET-99: On main the cell of the gate's own
  clone reads `enforced` when the clone declares the dependency and `no catalogs declaration` when it does not.

The `render-pass` rule, in the `workflows` area, requires a covered repository that tracks two or more catalogs to name the
job that renders each page once per language. It judges presence and gating, never what the job does; the three assertions
of the pass (no key shown as text, `lang` equal to the language, no overflow) stay the app's test.

- RENDER-1: **Who is judged.** A repository covered as for `catalog-check` (CATALOG-1) that tracks two or more catalogs. A
  repository that is not covered, or has fewer than two catalogs, sees no row and no exit `2` from this rule, and no
  workflow is read for it.
- RENDER-2: **Where the catalogs lie.** The directory is design's `catalog_directory` over the text of the tracked root
  `pyproject.toml` that quality has read: `[tool.design-catalogs] catalogs`, else `frontend/src/lib/catalogs`, the default
  design owns. It is read only when the repository is covered. A table design refuses is exit `2` naming `pyproject.toml`
  with design's sentence: a `catalogs` that is absolute, has no parts (`""` and `"."`), starts with `..` or is no string, a
  `python`, `typescript` or `source` that is no string, an unknown key, and a `tool.design-catalogs` that is no table. The
  app's own `design-catalogs --check` refuses the same table. A catalog is a tracked file `<directory>/<name>.po` directly in
  that directory; a `.po` file elsewhere does not count.
- RENDER-3: **The declaration.** `quality/config.toml` takes the optional table `[languages]` with the one key
  `render_pass`, the name of a job as `mandatory_jobs` names it (the job's `name:`, else its id; a `name:` that holds an
  expression is no name). Another key, or a value that is not a non-empty string, is exit `2`. A quality older than the
  release that ships the table refuses it as an unknown table.

Each finding is `missing` at `quality/config.toml` with no line, and the first that applies stops the rule:

- `! quality/config.toml  missing  N catalogs and no render pass declared`, fix `name the job that renders each page once per
  language in [languages] render_pass` (also where the file is missing);
- `! quality/config.toml  missing  render pass "x" is no job of a tracked workflow`, fix `name a job a tracked workflow holds`;
- `! quality/config.toml  missing  render pass "x" is not a mandatory job`, fix `add "x" to [gate] mandatory_jobs`.

With all three held the rule has no row. Any tracked workflow counts, a root or not. Named gaps: a matrix render pass cannot
be green, since the file names the job `render` and the run lists its legs as `render (de)`; a job of a called workflow is
listed as `caller / job`; pointing `render_pass` at any other mandatory job passes; a nested project's catalogs do not count
(CATALOG-6). No configuration switches the rule off. It first ships in v0.17.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column, and from
v0.17.0 on, and on main, the cell reads `enforced` where the clone declares the catalogs and `no catalogs declaration` where it does not (FLEET-99).

- LITERAL-15: The `literal-text` rule, in the `files` area, turns every literal text of a covered repository's pages into a finding: the
  texts a person reads that the markup holds itself instead of taking them from a catalog. A repository is covered as for
  `catalog-check` (the same read of the root `pyproject.toml`, CATALOG-1), so a repository that is not covered sees no row
  and no new verdict, and its `debt` area holds exactly the verdicts of `size`, `clone`, `job-timeout`, `test-twin` and `spec-citation`; the root
  `pyproject.toml` is read once for the area, and one that cannot be read refuses the rule with the reason `python-floor` gives,
  `pyproject.toml is not UTF-8 TOML that Python reads (<error>)`, which `catalog-check` and `render-pass` give too.
- LITERAL-1: The rule reads every tracked `*.svelte` file the tree holds, with the standard library's `html.parser` and a fold of the
  `{...}` holes; a `.vue`, `.jsx`, `.tsx` or `.html` file is not read.

- LITERAL-2: A text counts once per text node and once per attribute value, not per word: the text nodes of a page, the values of
  `title`, `placeholder`, `aria-label`, `alt`, `label`, `aria-description`, `aria-roledescription` and `aria-valuetext`, a
  `value` on an `<input>` whose `type` is `submit`, `button` or `reset` and on a `<button>` whose `type` is `submit` or absent,
  and `content` on `<meta name="description">`, on elements and on components alike (the parser lowercases names, so
  `<Dialog title="x">` is read like `<dialog title="x">` and a prop written `Label="x"` counts).
- LITERAL-3: Not text: a
  `<script>` or `<style>` block, a hole that is not a bare string literal, a logic block or a hole starting with `#`, `:`, `/`
  or `@` (`{@html '...'}` included), and any content with no letter. A hole that is one string literal, or a template literal
  without a substitution, counts as the text it holds, in a text position and as a whole attribute value; there is no
  exemption mechanism, a brand name lives in the catalog.

- LITERAL-14: Each literal text is one `literal text` finding at `<page>:<line>`, whatever `quality/baseline.toml` holds: no table and
  no exemption turn it off. Its fact is the text in single quotes with its whitespace collapsed (`'Log in'`), and its fix is
  `read it through the generated module and keep it in the catalog`. A text node stands at the line of its first letter and
  an attribute value at the line of its tag; the fold leaves the newlines of a dropped `<script>` or `<style>` block and of a
  folded hole behind, so the line is the page's.

A page the index tracks and the disk lacks is not read.

- LITERAL-16: A `[literal_text]` table in `quality/baseline.toml`, which an earlier release wrote, is read and ignored whatever it holds,
  malformed included, so it is no exit `2`, no finding and no loosening: a base that holds it and a head that does not is
  green. `quality check debt --fix` rewrites the file without it (`rewritten`, FIX-16).

Named gaps, each a fact of the parser or of the scan: a literal inside a larger expression (`{ok ? 'Saved' : x}`) is not
counted; a component prop outside the attribute list (`<Hero heading="Welcome" />`) is not counted; a string in a `<script>`
is not counted, and the scheduled audit finds those under LANG-24; an unpaired quote in a hole swallows the markup up to the
next quote (`{x // don't}` followed by `<b>Real text</b>` and a hole with another apostrophe counts nothing), and with one
apostrophe only the hole never closes (exit `2`); `a < b` reads two text nodes; `title="{n} items"` counts the fragment
`items`, because the fold leaves the literal part beside a hole; a text holding an apostrophe prints inside single quotes
unescaped (`'Don't'`); an attribute of a tag spread over several lines names the line of the tag's first. Python 3.14's
parser reads the body of `title`, `textarea`, `iframe`, `xmp`, `noembed` and `noframes` as one text up to the end tag,
whatever the case, so `<Title><b>One</b><i>Two</i></Title>` is one text and a self-closing `<Title />` holds none.

- LITERAL-9: Exit `2` names the file: a page that is not UTF-8; a `<script>`, `<style>`, `<textarea>`, `<title>`, `<iframe>`, `<xmp>`,
  `<noembed>` or `<noframes>` that is opened and never closed, because the parser then reads the rest of the page as one text
  and every text after it would be missed in silence; and a `{` hole that is never closed, an unbalanced `{` in an HTML comment
  included. The first page in the tracked order that cannot be read to its end stops the rule, so one fact prints, a page that is not UTF-8 is named before any other, and the fold runs
  before the parser, so an unclosed hole is named before an unclosed element. A missing directory, no Svelte file and a
  tracked page deleted from the tree are no finding.
- FLEET-102: It first ships in v0.13.0, so a gate pinned below that release shows `not in <tag>`
  in the fleet matrix's column. v0.16.0 is the release that names the table as its switch:
- FLEET-101: a gate pinned at v0.13.0 to v0.15.0
  reads `report-only`, from v0.16.0 on the cell reads `enforced` where `quality/baseline.toml`
  holds `[literal_text]` and `report-only` where it does not, until the release that names the catalogs declaration as the switch
  (`SwitchChange` in `src/quality/rules.py`); from that release on and on main the cell reads `enforced` where the clone declares
  the catalogs and `no catalogs declaration` where it does not, as for `catalog-check` (FLEET-99).

No
configuration switches it off.

The `locator-text` rule, in the `files` area, turns a catalog text in a locator position of a covered repository's Python,
TypeScript and JavaScript tests into a finding: a test finds an element by its key through the generated module, never by the words a person reads.
It is not a second target of `literal-text`, which reads page files; this rule reads test files and judges only a text a
catalog holds, in a position that finds an element. There is no baseline, no ceiling and no exemption: a brand name lives
in the catalog like any text (LITERAL-3), so a test reads it by key.

- LOCATOR-9: **Who is judged.** A repository covered as for `catalog-check` (CATALOG-1). A repository that is not covered
  sees no row and nothing is read for it.
- LOCATOR-8: **Test files.** Every tracked `*.py` file the tree holds under a directory named `tests`, and every tracked
  `*.ts` or `*.js` file named `*.test.*` or `*.spec.*` (`x.test.browser.ts` too) or held under a directory named `e2e` or
  `test-utils` (`.tsx`, `.jsx`, `.mjs`, `.cjs` and `.mts` files are not read). A file that cannot be read refuses the rule
  naming the file (LOCATOR-10).
- LOCATOR-11: **Reading a script.** A script is read as tokens, not parsed: strings with their escapes decoded (`\'`,
  `\n`, `\xHH`, `\uHHHH`, `\u{...}`, a line continuation), templates, regex literals, names, numbers and punctuation,
  with comments skipped. A `/` starts a regex after nothing, after punctuation but `)` and `]`, and after `return`,
  `typeof`, `case` and the other keywords that take an operand, but not after a property or a private field of that
  spelling (`cfg.default / 2` and `this.#default / 2` divide), and after the `)` that closes the head of an `if`, `while`,
  `for` or `with` (`if (x) /'/.test(y);`); after anything else it divides. A `/` right after a postfix `++` or `--` divides (a regex
  never follows a prefix one), and so does one after a `!` that follows an identifier, `)` or `]`, the TypeScript
  non-null mark (`v! / 2`); a `!` after anything else is a prefix not and a regex may follow it (`!/x/.test(y)`).
  A template with a hole is no site and its holes are read like the rest, so a locator inside one is judged.
  A literal's line is the line it starts on, a line ending at `\n`, `\r\n`, `\r`, U+2028 or U+2029. JSX is not read.
- LOCATOR-4: **Catalog texts.** The directory is design's `catalog_directory` (RENDER-2). Every tracked `<name>.po` file
  directly in it is read with design's `parse_catalog` over the bytes `repository.py` read, so the reading is design's own:
  strict UTF-8, closed strings, obsolete `#~` entries left out. A text is every msgid and every non-empty msgstr form, so an
  English translation (`en.po`, msgstr `Settings`) is surface text too; a fuzzy entry gives its msgid only, since gettext
  does not show its msgstr.
  - LOCATOR-5: The key a finding names is the entry's msgctxt (the msgid for an entry that has none), and a
    text several keys hold names the first in sort order.
- LOCATOR-1: **Locator positions.** An argument of a call named `getBy<Upper...>` or `get_by_<lower...>` (an app's
  `getByRoleButton` counts, `getByteTimeDomainData` does not); an argument of `to_have_text`, `to_have_accessible_name`,
  `to_have_title`, `to_have_accessible_description` and their camelCase forms; the value argument of `to_have_attribute`
  and `toHaveAttribute` (its second argument, in Python also `value=`) when its first argument (in Python also `name=`) is a
  string literal naming an attribute a person reads (`title`, `placeholder`, `aria-label`, `alt`, `label`,
  `aria-description`, `aria-roledescription`, `aria-valuetext`), so a `data-state` value is no position; and a `has_text`,
  `has_not_text`, `hasText` or `hasNotText` keyword or property of any call. The role argument of `get_by_role` and
  `getByRole` (its first argument, in Python also `role=`) and the argument of `get_by_test_id` and `getByTestId` are
  protocol tokens and test ids, never surface text, so they are no position.
- LOCATOR-2: **Sites.** A literal in such a position whose whole value is a catalog text: a string, in a script also a
  template without a `${` hole, including one inside a helper call that is itself an argument
  (`get_by_role("button", name=label("Abbrechen"))`), one in a list and, in a script, one a property of an options object
  holds (`{ name: 'Abbrechen' }`); and a regex literal anchored `^...$` whose body, regex escapes undone, is exactly such a
  text, whatever its flags (case-sensitive), or the same anchored text in a string that `re.compile(` or `RegExp(` holds. A
  plain string is never read as a regex: `"^Abbrechen$"` is compared whole. An f-string, a template with a hole, a
  concatenation, a ternary branch and a comparison are no site. In a script a value is a site only when it stands alone:
  it follows `(`, `,`, the `[` of an array (a `[` after a name, `)` or `]` is an index) or a `key:`, and it is followed by
  `)`, `,`, `]` or `}`.
- LOCATOR-3: **Constants.** A name bound exactly once at module level to a plain string literal in the same file is
  resolved to it; a name bound twice, extended, unpacked or imported from another module is not. In a script it is a
  top-level `const` (optionally `export`) set to a string or a template without a hole and ended by `;`, `as const`, the
  end of the file or a line that starts with a name, and it is the only top-level `const`, `let`, `var`, `function` or
  `class` of that name. An assignment to a const is not looked for, since TypeScript refuses it and it throws when it
  runs, so code that type-checks has none. A `const` inside a function is no constant,
  and a function-local name that shadows a top-level one resolves to the top-level value, so a false finding there is fixed
  by renaming.
- LOCATOR-7: **Out of scope.** `toBe`, `toContain`, `toEqual`, `to_be`, `to_contain_text`, `toContainText` and the other
  substring assertions, `assert x == "..."` and every other position: about four
  of five such sites are scenario data, and the scheduled audit judges them.

- LOCATOR-6: Each site is one `catalog text` finding at `<test file>:<line>` (the line of the literal), whatever `quality/baseline.toml`
  holds. Its fact is `'<text>' is the catalog text of <key>`, with `, and N more keys` when several keys share the text, and
  its fix is `find it by its key through the generated module`.

The extent of the rule's row is the number of test
files read.

It first ships in v0.21.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column, and on main and
from v0.21.0 on the cell reads `enforced` where the clone declares the catalogs and `no catalogs declaration` where it does not (FLEET-99).

- LOCATOR-10: Exit `2`: a Python test file that is not valid Python or not UTF-8; a script test file that is not UTF-8, has a
  string that reaches a raw newline or the end of the file, a template or `${` hole that is never closed, a regex that reaches
  a line ending or the end of the file, or a block comment that is never closed, named with the file and the `line N` of the
  construct; a catalog design's strict reading refuses, named `<file>:<line>`; a catalog that is a symlink resolving outside
  the repository, which is never read; a `[tool.design-catalogs]` table design refuses (RENDER-2), named `pyproject.toml`.
  Unbalanced brackets, a stray `}`, any other character and a valid file nested deeper than the interpreter recurses refuse
  nothing: a valid file is judged like any other, and no site of it is lost. A missing catalogs directory is no finding here:
  it is held by `catalog-check`, since the app's own `design-catalogs --check` turns red when the source catalog is missing.

Named gaps: a msgid with a gap (`Hello {name}`) and a `msgid_plural`; a regex locator that is not an anchored exact text
(`/shuffle/i`), a case-insensitive one that differs in case; a later
typed parameter such as `name: string`, which reads as a key with an identifier site that only a top-level const of that
name could make red; a constant imported from another module, one declared `const A: string = '...'` or `const A =
'...', B = '...'`, and a dynamic string, a concatenation and a ternary; a helper named other than `getBy...`,
testing-library's bare `getByRole(container, role)`, `to_have_value` and `toHaveValue` (a value is mostly scenario
data), and an attribute named by a variable or `value` or `content`, which `literal-text` reads only conditionally;
`.tsx`, `.jsx`, `.mjs`, `.cjs` and `.mts` files; helper modules outside `tests/` directories; a nested project's
catalogs.

- TIMEOUT-1: The `job-timeout` rule, in the `debt` area, judges every job of a workflow that runs on a pull request or on a push to
  main, and of the reusable workflows those call through a local `uses:`. Every tracked workflow is parsed, a root or not,
  because a root is known only after the parse; a tracked file gone from the tree is skipped and a composite action is not
  read.
- TIMEOUT-2: A workflow is a root when its `on:` (a string, a list or the keys of a mapping) names
  `pull_request`, `pull_request_target` or `pull_request_review`, or names `push`: bare or with an empty, `~` or `null`
  value, with filters that name no `branches`, `branches-ignore` or `tags`, with `branches` of which a pattern that does
  not start with `!` matches `main`, or with `branches-ignore` of which none matches it. A pattern matches as Python's
  `fnmatch` reads it with `**` read as `*`; a `!` pattern is not read, so the reading errs toward a root. A `push` with
  only `tags` is no root, and `schedule`, `workflow_dispatch`, `merge_group`, `workflow_run` and `workflow_call` alone are
  none.

- TIMEOUT-3: A job whose `uses:` starts with `./` or `$/` and names `.github/workflows/<file>.yml` or `.yaml`, a tracked workflow,
  makes that file judged, followed from every root and judged once, so a cycle ends where it closes and is no finding. A
  remote reusable workflow is not followed: it is judged in its own repository when a root there reaches it through a
  local call, and one that only another repository's root reaches is judged nowhere, a gap this rule keeps. A workflow no
  root reaches is outside, which is how release and image workflows stay outside; a local call in a file that is not
  judged is not read.
- TIMEOUT-4: A judged job is an entry of
  `jobs:` with no `uses:`; a job with `uses:` is not judged itself, and a matrix job is one job.

- TIMEOUT-5: The limit is read in this order: no `timeout-minutes` key is `missing`, never debt; a plain unquoted decimal number
  above zero is that many minutes; any other value is `invalid` (an expression, a quoted number, `0`, a negative, signed,
  hex or exponent form, an empty value, `~`, `null`, a word, a sequence or a mapping); a limit above 15 is `new debt`,
  `15.5` included, unless the baseline holds it.
- TIMEOUT-6: The findings stand in the `debt` area at the file that
  defines the job, a reached workflow under its own path, at the `timeout-minutes` line or the job id's line for
  `missing`, in the listing's order and the file's job order. The facts are `job <id> has no timeout-minutes`, `job <id>
  has a timeout-minutes that is not a number above zero` and `job <id> has timeout-minutes <value> · limit 15`; the fix is
  `set timeout-minutes to a number of at most 15` for the first two and `lower timeout-minutes to 15 or less` for the last.

Where the baseline has no `[job_minutes]` table, no ceiling file included, the last reads `lower
timeout-minutes to 15 or less, or run quality check --fix to record it`; where it holds the table, an empty one included,
it stays as it is. `check` reads no git history for these words, so a table git held and the file lost reads as the
first, and `--fix` refuses it (TIMEOUT-29).

- TIMEOUT-12: A job above 15 may be held as debt in a `[job_minutes]` table of `quality/baseline.toml`: one subtable per workflow
  file, keyed by the repository-relative path of the file that defines the job, and one key per job id with a positive
  whole number of minutes (`[job_minutes.".github/workflows/ci.yml"]`, then `e2e = 40`). A table with no subtable is held
  and empty; a value that is no positive integer, or a subtable or key that is no table, is a malformed baseline
  (`needs <where> as a positive count, not <value>`, `needs <where> as a table`),
- TIMEOUT-33: where `<where>` joins the
  table, the path and the job as a TOML dotted key, the path and a job id that is no bare key quoted
  (`job_minutes.".github/workflows/ci.yml".e2e`).
- TIMEOUT-13: It renders after the size and
  `[clones]` tables, subtables sorted by path and keys by job id, and a file that already renders so stays untouched by
  `--fix`, and the unknown-table message lists `job_minutes` among the tables quality knows.
- TIMEOUT-14: An older quality
  refuses the table as unknown. The debt a limit counts as is its rounded-up whole
  minutes, so `17.5` is held by an entry of `18`; a fact prints the limit as written.

- TIMEOUT-15: Against the table a judged job with a readable limit is compared by its rounded-up value `d`, with or without a ceiling
  file. An entry of 15 or less is no debt and reads as no entry. With no entry and `d` above 15 the job is `new debt`; an
  entry above 15 and `d` above it is `grew` (`job e2e has timeout-minutes 45 · ceiling 40`, fix: bring it back to 40 or
  below); an entry above 15 and `d` below it, or `d` of 15 or less, is `can fall` (`job e2e has timeout-minutes 20 ·
  ceiling 40`, fix: quality check --fix); `d` equal to the entry, and no entry with `d` of 15 or less, are green. A job
  without a readable limit keeps its `missing` or `invalid` finding and its entry stands.
- TIMEOUT-16: After the measured
  findings, sorted by path and job and with no line, each entry that can fall with nothing to measure is `can fall`: its
  path is not tracked or no root reaches the file any more, the file has no such job or the job calls a workflow (`job e2e
  is gone · ceiling 40`), or the entry is of 15 or less (`job e2e has no debt · ceiling 10`);
- TIMEOUT-24: the fix of each
  `can fall` form is `quality check --fix`.
- TIMEOUT-34: The rule's extent is the jobs the held table holds, and a clean
  `debt` row over a table holding at least one job reads `ceiling held · N entries, M clone pairs, K job limits`; an empty or
  absent table adds nothing.

A baseline that cannot be read
makes the rule `cannot judge` and names the baseline, as it does for `size` and `clone`; one that is absent leaves the rule judging with no entry.

- TIMEOUT-17: A renamed workflow carries its base entries to its new path in the verdict as a renamed source file carries its size
  entries, a leftover under the new path and an entry under a path the head still names moving nothing; a job id renamed
  inside a file is a new entry.

- TIMEOUT-18: `--fix` follows a renamed workflow for the `[job_minutes]` table as it follows a renamed source file (FIX-34 to FIX-45
  apply). From the ceiling's last commit, the workflow's held job entries move to the new path at their held values
  (`moved`, `job e2e 40 minutes from .github/workflows/old.yml`), what the table held under the new path before the move,
  and the carry of the last commit lacks, is dropped (`dropped`, `job e2e 25 minutes · left from before the move`), and an entry
  the carry already holds, no higher, stays unless a moved entry takes its key; each entry is judged on its own. An entry under a renamed path that the last commit's table does not explain
  refuses with the restore step before anything is written, naming the workflow. Where the last commit holds no
  `[job_minutes]` table the verdict compares nothing,
- TIMEOUT-19: so held entries under a renamed workflow move, no
  leftover is judged, and only an entry a moved one takes the key of is dropped. A held ceiling without the table stays without it, a held empty table stays held, and entries under
  a path no rename touches are written as they were. A moved job entry counts as moved in the debt row and the verdict
  line.

- TIMEOUT-20: `--fix` then lowers and drops the held job entries, from the jobs the rule judges (TIMEOUT-1 to TIMEOUT-4) under the
  paths it followed. An entry above 15 falls to its job's rounded-up limit when that is above 15 and below the entry
  (`lowered`, `job e2e 40 → 18 minutes` for `timeout-minutes: 17.5`).
- TIMEOUT-21: An entry that is no debt is dropped
  (`dropped`) when the job's rounded-up limit is 15 or less (`job e2e 40 → 12 minutes · debt from 16 minutes`), when the
  entry itself is 15 or less (`job e2e has no debt · was 10 minutes`), and when its job is gone, because its workflow is
  not tracked or no root reaches it, the file has no such job or the job calls a workflow (`job e2e is gone · was 40
  minutes`); dropping the last entry leaves a held empty table.
- TIMEOUT-22: Nothing rises and nothing enters: a job above
  its entry stays `grew`, a job above 15 without an entry stays `new debt`, and an entry whose job has a `missing` or
  `invalid` limit stands.
- TIMEOUT-23: Where the table holds an entry and a workflow the rule must read cannot be read
  (TIMEOUT-7), the whole fix is refused with the rule's reason, `✗ not fixed`, exit `2`, and nothing is written, the size
  entries and clone pairs included, since a half-read set of jobs would drop the entries of the jobs it did not see; a
  file without the table, a held empty table and a first recording lower nothing and refuse nothing for a workflow they
  cannot read.
- TIMEOUT-32: Whether a held entry can fall is decided once, for the rule's `can fall` findings and for
  this lowering alike.
- TIMEOUT-25: A lowered or
  dropped job entry counts like a size entry in `fell`, the `debt` row and the verdict line, and a `✓` row per entry names
  the workflow;
- TIMEOUT-26: `--dry-run` makes the same table and words each change as what the fix would do, row for row,
  and refuses where the fix refuses.

- TIMEOUT-27: `--fix` records the `[job_minutes]` table where the file has none and no version of it in git's history ever held one,
  when at least one judged job has a readable limit whose rounded-up value is above 15: exactly the judged jobs above 15,
  each at its rounded-up limit under the path of the file that defines it (`17.5` as `18`), and no table, not even an
  empty one, where no job is; a first `--fix` with no ceiling file records it with the size entries and clone pairs
  under the same condition, and a second `--fix` changes nothing.
- TIMEOUT-28: The question to git is asked only where a
  recording would happen: a ceiling that lost the table although a version of it held one refuses the whole fix as
  `[clones]` does (`quality/baseline.toml lost its [job_minutes] table, which git holds`, exit `2`, the restore step,
  nothing written), and a version no TOML parser reads that names the table (FIX-13) or a shallow clone that cannot prove the table never held names
  its step, `--dry-run` alike; a table-less file with no job above 15 reads no history for it.
- TIMEOUT-29: A recording
  is not a raise: after the table exists `--fix` adds no entry to it, so a job above 15 with no entry stays `new debt`.
- TIMEOUT-30: A job whose limit cannot be read is never recorded, and where a workflow the rule must read cannot be
  read no job table is written, not even a partial one, while the size and clone fix proceeds and the rule reports the
  file with exit `2`.
- TIMEOUT-31: The debt row is `recorded  K job limits · quality/baseline.toml` with a `recorded  K
  job limits` row under it (`1 job limit` for one); beside clone pairs it names both (`M clone pairs, K job limits`), a
  first recording names the job limits after the pairs (`N entries, M clone pairs, K job limits · quality/baseline.toml is new`), and
  `--dry-run` words the rows `to record` and `record`; the verdict line is `ceiling recorded`, under the dry run `ceiling
  would be recorded`, and a recording adds nothing to the entries that fell.

- TIMEOUT-7: A workflow whose YAML or file cannot be read, an alias or a merge key where it can hide the top mapping, `on:`, the
  `push` value, its `branches`, `branches-ignore` or `tags`, or in a judged file `jobs`, a job, a `uses:` or a
  `timeout-minutes` value, a `push` that is a word, a number or a sequence, a filter of another shape, a `jobs` or a job
  that is no mapping, a local call in a judged file that names no tracked workflow of the workflow folder, and a called
  workflow gone from the tree are exit `2` naming the file; the other rules still judge. A workflow with no `jobs:` has no
  job.
- TIMEOUT-8: The rule is judged beside `size` and `clone` and independent of both, and the debt area counts only the
  rules a ceiling file switches as measured, so the verdict reads `debt ceiling measured, not enforced` where no ceiling file holds the debt, even when
  `job-timeout` finds.
- TIMEOUT-10: A clean `debt` row keeps its words, and no configuration
  switches the rule off.
- TIMEOUT-11: It first ships in v0.12.0, so a gate pinned below that release shows `not in <tag>`
  in the fleet matrix's column.

A repeated job id or a repeated `timeout-minutes` key is read as the loader
reads it, the last winning; actionlint, which `workflow-lint` runs, refuses the repetition. Named gaps: `merge_group`
and `workflow_run` as roots, the negation and the `+` of a push filter, a limit set only by a `workflow_call` input, a
step's `timeout-minutes`, and a default branch other than `main`; the jobs of a remote reusable workflow that only
another repository calls are judged by no rule yet.

The `test-twin` rule, in the `debt` area, counts two kinds of copying in the tracked Python test files and reports them as one
`open` [note](output.md#notes); it only reports and holds no count.

- TWIN-1: **Which functions.** A file `Language.is_test_file` calls a test is read. A function whose name starts with `test`
  is a test wherever it stands, a method of any class and a nested function included; every other function of such a file is a
  helper (a fixture, a function of a support module or `conftest.py`, a method of a fake, a function nested in a test). The
  token stream of a function is the one `clone` reads (`clones.tokens`): arguments and statements, without the docstring and
  the decorators.
- TWIN-2: **Test twins.** Tests of one file whose streams are equal with names kept and literals folded, from 12 tokens, form
  a group. A case table stands in a decorator and is not compared, so two parametrized tests with one body and two tables are a
  group; the fix is one table whose case ids name the behaviours.
- TWIN-3: **Copied helpers.** Helpers of two or more files whose streams are equal with names and literals kept, from 8
  tokens, form a group, whatever the functions are called. A copy that differs by one literal is no copy here.
- TWIN-4: **The note.** The unit of both counts is the group. One `open` note under the `debt` area at the location `.` reads
  `24 groups of test twins · 20 groups of copied test helpers`; a count of zero leaves its half out, and no note stands when both
  are zero. The rule holds no finding, its extent is 0 and it is never measured only, so every other row, the closing line and the
  exit are what `size` and `clone` make them, in every state of the baseline, a red area included.
- TWIN-5: **Refusals.** The rule reads the sources the `debt` area read; when they cannot be read the area is refused as
  before and no note stands. It needs no baseline, so a baseline that cannot be read refuses `size` and `clone` (CLONE-9) and the
  note still stands. A file Python cannot parse is skipped without a refusal; `size` and `clone` refuse it.
- TWIN-6: **Report only.** The rule is `report-only` in the fleet matrix and changes no verdict. It first ships in v0.23.0, so a gate
  pinned below that release shows `not in <tag>` in the fleet matrix's column, and from v0.23.0 on the cell reads `report-only` (FLEET-18).

The `spec-citation` rule, in the `debt` area, reads the spec IDs of a repository's Markdown files and the IDs its tests cite.
Without a `[spec_citations]` table in `quality/baseline.toml` it reports three counts as one `open` [note](output.md#notes)
and holds no list; with the table it holds the repository's list so that it only falls: a new uncited ID, a citation of an ID
no spec defines or one that is retired, and a duplicate definition are red, and the table holds the uncited IDs.

- CITE-1: **Definitions.** Every tracked `.md` file the tree holds is read. A list item (`-`, `*`, `+`, `1.`, `1)`, at any
  indent; a number is ASCII digits only) that opens with `ID:` defines a live ID, and `ID (retired).` a retired one, which needs
  no test. A table row defines its first cell when that cell is exactly an ID and either the table's header row has `ID`
  as its exact first cell, wherever the table stands, or the table follows a rule's list item with no heading or
  unindented prose between them. A fenced code block, prose, `(ID)` and a list item without the colon define nothing.
  An ID is `[A-Z][A-Z0-9]*(-[A-Z][A-Z0-9]*)*-[0-9]+`, and IDs compare as exact tokens: `CSRF-01` is not `CSRF-1`.
- CITE-2: **Python citations.** The docstring of a function whose name starts with `test` in a Python test file (TWIN-1)
  cites. Its head is the run of IDs at the docstring's start, after an optional `Spec:`, joined by `,`, `and` or `, and`, and every
  ID-shaped token in the head counts, so a mistyped prefix is caught. A module or class docstring, a helper, a comment and a
  later mention do not cite.
- CITE-3: **TypeScript and JavaScript citations.** In a file `Language.is_test_file` accepts, a bare `it(` or `test(` whose
  first token is a string, or a template without a hole, cites by its head as in CITE-2. `describe`, `.each`, `.skip` and other
  modifiers are not read (a named gap).
- CITE-4: **The note.** Without the table one `open` note under the `debt` area at the location `.` reads
  `16 spec IDs uncited · 2 cited IDs undefined or retired · 1 spec ID defined twice`. The undefined count is distinct IDs, not
  citations, and a cited retired ID counts with them. A count of zero leaves its part out, and with all three at zero no note
  stands.
- CITE-5: **What cannot be read.** Sources the `debt` area cannot read refuse the area as before, and no note stands. Without
  the table a tracked `.md` file that cannot be read, is absent from the tree or is not UTF-8 and a TypeScript or JavaScript test
  file that is absent, is not UTF-8 or that the lexer refuses are left out and named in the note's last part (`· 1 file not read: docs/x.md`), so the rule
  changes no exit code; with only such files the note is that part alone. A Python test file that does not parse is skipped,
  since `size` and `clone` refuse it.
- CITE-6: **Report only.** The rule is report-only exactly where the baseline has no `[spec_citations]` table, a missing
  baseline file and a baseline that cannot be read included (the latter refuses `size` and `clone`, CLONE-9, and the area). Then
  it holds no finding, its extent is 0, it is never measured only, and it never decides whether the `debt` area is partly measured,
  so a repository that has not adopted the table keeps its row. It first ships in v0.25.0, so a gate pinned below that release
  shows `not in <tag>` in the fleet matrix's column (FLEET-18).
- CITE-7: **The held list.** `[spec_citations] uncited` holds the live IDs no test cites, sorted, one per line. The ID alone
  is the key, since IDs are unique in their repository, so a rule moving between files moves nothing; an ID is held wherever it
  is defined. A table with no ID stands as `[spec_citations]` alone, and any other key is a malformed file.
- CITE-8: **With the table.** A live ID no test cites and the table lacks is `uncited` at its definition's path and line
  (`spec ID ST-02 is cited by no test`). A citation of an ID defined nowhere is `undefined`, of a retired ID `retired`, at the
  citing test's path and line, one finding per citing test. Every definition after an ID's first is `duplicate` at its own path
  and line, naming the first (`spec ID ST-01 is defined again, first at docs/spec.md:12`). None of these three is held: no entry of the
  table silences them. A held ID that is cited, retired or no longer defined is
  `can fall` at `quality/baseline.toml` (`spec ID ST-01 is cited`), fix `quality check --fix`. The debt row counts the held
  IDs (`ceiling held · 0 entries, 0 clone pairs, 16 uncited spec IDs`, nothing for an empty table), the rule's extent is that
  count, it is never measured only, and no note stands.
- CITE-9: **What cannot be read with the table.** A tracked `.md` file or a TypeScript or JavaScript test file that cannot be
  read, or that the tree lacks (a sparse checkout, a deletion), can hide a definition or a citation, so with the table the rule is `cannot judge`, naming the files (`spec IDs cannot be
  judged, 1 file not read: docs/x.md`), exit `2`, as for `clone` (CLONE-9). A Python test file that does not parse is skipped as
  before.
- CITE-10: **Dropping under `--fix`.** `quality check --fix` drops each held ID that is now cited, retired or undefined (a
  `dropped` row, `spec ID ST-01 is cited`, counted among the entries that fell) and never adds one: a new uncited ID stays a
  finding, and a raise is a hand edit with a declared loosening. While any `.md` or script test file is unread it neither drops nor
  records: with the table the rule reads `cannot judge`, naming the unread files, exit `2`, writing nothing, in the dry run too;
  without it nothing is recorded, the report-only note stays and the exit is unchanged. An ID defined in an unread file would
  otherwise look undefined and be dropped, then return as a new uncited ID only the operator's door could merge. Named limit: a
  tracked `.md` file that is not UTF-8 keeps its repository from recording until it is fixed.
- CITE-11: **Recording under `--fix`.** `quality check --fix` records the table, the uncited IDs of the tree, only where the
  baseline has none, git never held one, the tree defines a live ID, no citation is undefined or retired (a declared foreign
  citation is not undefined, CITE-14; an implemented document's IDs are not recorded, CITE-15) and no ID is defined
  twice; the row reads `recorded  K uncited spec IDs · quality/baseline.toml` (`1 uncited spec ID`, a first recording
  `N entries, M clone pairs, K uncited spec IDs · quality/baseline.toml is new`), and the dry run words it `to record`. Otherwise
  nothing is recorded, not even an empty table, and the note stays, so a routine `--fix` in a repository that has not converted its
  documents never turns it red. Where the fix would record and a version of the baseline in git held the table, it refuses
  through the same question as `[job_minutes]` (TIMEOUT-28): `quality/baseline.toml lost its [spec_citations] table, which git
  holds`, exit `2`, the restore step, nothing written; where it would not record, git is not asked.
- CITE-12: **The loosenings.** On a pull request the verdict holds the head's table against the base's: each held ID the base's
  table lacks is `spec citation baseline adds <ID>` (a set has no raise, so an add is the raise), and the base's table gone from
  a head that still has the file is `spec citation baseline removed`; a base without the table is an adoption and green. Both
  are declarable as in [gate](gate.md#declared-loosenings).
- FLEET-118: **The matrix cell.** The cell reads `not in <tag>` below the first release, `report-only` without the table,
  `enforced` with it from v0.26.0, the release that holds the list (`SwitchChange` in `src/quality/rules.py` names it), and
  `cannot read quality/baseline.toml` for a baseline that cannot be read.
- CITE-13: **The unknown-table message.** `quality/baseline.toml has an unknown table [future]; quality <version> knows ccn,
  file_lines, function_lines, clones, job_minutes, spec_citations` (CEILING-3). A quality that predates the table meets it as an
  unknown table and exits `2` (BASELINE-25), so a repository records the table only once its gate pins v0.26.0, the release that holds it.

- CITE-14: **Foreign prefixes.** An implementer declares each prefix whose IDs its tests cite, one entry per prefix, in
  `quality/config.toml`; `owner` is the repository that defines them:

  ```toml
  [[spec_citations.foreign]]
  prefix = "LANG"
  owner = "overnightworks/design"
  ```

  A prefix is an ID's part before its last `-<number>`, compared exactly (`REQ` does not cover `REQ-AUTH-3`). A citation of an ID
  the tree defines nowhere whose prefix is declared is not judged: no `undefined` finding, not in the note (CITE-4), and it does
  not keep `--fix` from recording (CITE-11). Where the tree defines the prefix, live or retired, the declaration exempts
  nothing and every ID of it is judged as any other (CITE-19). The declaration is no loosening. A foreign citation is not
  resolved against its owner, so a mistyped foreign ID or a typo onto a declared prefix is not caught; CITE-2's caught typo
  holds for undeclared prefixes only.
- CITE-15: **Implemented documents.** An owner declares its document, a tracked `.md` file, in `quality/config.toml`:

  ```toml
  [[spec_citations.implemented]]
  path = "docs/calling.md"
  item = "overnightworks/agents#312"
  date = 2026-11-23
  reason = "the implementers cite its rules, so no test of this repository does"
  ```

  A live ID whose first definition stands in a declared document is not `uncited`, not in the note and not recorded by `--fix`;
  a held one `can fall` (`spec ID CALLING-3 is left to its implementers`) and `--fix` drops it (CITE-10). A citation by the
  repository's own tests still counts as cited, and a held ID both cited and in a declared document falls as `is cited` first.
  Undefined and retired citations and duplicates are judged there as anywhere.
- CITE-16: **Malformed declarations.** Exit `2` naming the entry with its example. For `foreign`: a missing or extra key, a
  `prefix` outside the ID pattern, an `owner` that is not `<owner>/<repo>`, or two entries for one prefix. For `implemented`: a
  missing or extra key, a `path` that is not the exact path of one `.md` file (no folder, pattern, `..` or absolute path), a
  malformed `item` or `date`, a `reason` that is not one line, or two entries for one path. Either key as anything but an array
  of tables is refused too. It shows in `check` and `check --fix` as the `debt` row `✗ not checked`, and in the verdict and the
  door naming the side, as GENERATED-6 to GENERATED-8; `check secrets --staged` reads only `[secrets]`. A quality older than
  v0.26.0 refuses `[spec_citations]` in `quality/config.toml` as an unknown key (the baseline's table of that name is
  CITE-13's), so a repository declares only once its gate pins v0.26.0, the release that holds the list.
- CITE-17: **Unused declarations.** With the baseline table, a foreign prefix that matches no citation of an ID the tree
  defines nowhere is `unused` at `quality/config.toml` (`[[spec_citations.foreign]] prefix LANG matches no citation`), and an
  implemented path that is not tracked or holds no live ID's first definition is `unused` at that path
  (`[[spec_citations.implemented]] matches no tracked file that defines a live spec ID`). Both fixes read
  `delete its <heading> entry from quality/config.toml`. Without the table neither is judged (CITE-6).
- CITE-18: **The owner's loosening.** On a pull request a head `[[spec_citations.implemented]]` path the base does not
  declare prints `spec citation leaves <path> to its implementers · <item> of <date>`, declared by the entry's own `item` and
  `date` and red until the operator's door merges it ([gate](gate.md#declared-loosenings)). A base without
  `quality/config.toml` makes every entry new; an inherited or deleted entry and a changed `reason`, `item` or `date` loosen
  nothing; a changed `path` is new; a `[[gate.loosened]]` entry with this text matches nothing and is red; without a base
  nothing is judged. An implementer's foreign prefix is no loosening.
- CITE-19: **A foreign prefix defined here.** With the baseline table, a declared prefix that the tree also defines, live or
  retired, is `conflict` at `quality/config.toml` (`[[spec_citations.foreign]] prefix ST is defined here, first at
  docs/spec.md:3`), fix `delete its [[spec_citations.foreign]] entry from quality/config.toml, or give the local IDs a prefix of
  their own`. Without it a repository could silence every undefined citation of its own prefix through a declaration that is
  no loosening.

- ADVISORY-30: The `advisory` rule judges every tracked `uv.lock`, `pnpm-lock.yaml` and `requirements*.txt` (a name that
  starts with `requirements` and ends in `.txt`; `requirements.in` and `dev-requirements.txt` are not),
- ADVISORY-1: root and nested, with osv-scanner against the OSV advisory database (dev dependencies count).
- ADVISORY-31: `uv.lock` and the requirements files are judged against the PyPI database, `pnpm-lock.yaml` against the npm
  database.

A `package-lock.json`, `yarn.lock` or `bun.lock` is not judged yet.

- ADVISORY-32: A repository with no tracked lock [needs no osv-scanner](commands.md#scanners) (and none is located); a
  database is fetched only for an ecosystem with a tracked lock,
- ADVISORY-33: once per run however many locks it has.
- ADVISORY-2: A lock that is tracked but missing on disk is skipped;
- ADVISORY-3: one that links out of the repository,
- ADVISORY-4: is not a regular file
- ADVISORY-5: or is empty (zero bytes) is exit `2`.
- ADVISORY-6: A lock listed in `.gitignore` is still judged.

The [clean row](output.md#area-rows) counts the lock files it judged and the packages osv-scanner lists for them: `✓
dependencies  clean  1 lockfile · 63 packages`, and `0 lockfiles · 0 packages` with none.

- ADVISORY-20: A package counts once per tracked file, by its ecosystem, its name as the ecosystem compares it (Python's rule
  for PyPI, exact for npm, so `lodash.merge` and `lodash-merge` are two) and its version,
- ADVISORY-21: so a requirements file's pin and the wheel line for the same package are one;
- ADVISORY-22: every tracked name counts, so a lock reached by two names counts for each.
- ADVISORY-23: A finding is one per advisory group, package and lock file,
- ADVISORY-24: named by the tracked relative path: `! uv.lock  affected  pyyaml 5.3.1 · GHSA-8q59-q68h-6hv4 · fixed in 5.4`
  with `fix: uv lock --upgrade-package pyyaml`.

[The row](output.md#rows) names the group's first id; an allowance matches every id and alias of the group. For a nested lock
the [fix](output.md#rows) names its directory (`in speech/: uv lock --upgrade-package ...`).

- ADVISORY-25: Several fixed versions are listed as osv lists them.
- ADVISORY-26: An advisory with no listed fixed version, a package listed as malicious included, says `no fixed version
  listed` with `fix: remove or replace <package>; see https://osv.dev/vulnerability/<id>`.
- ADVISORY-27: Where osv's record names no such package the row says nothing of a fix, with `fix: see <url>`.

The fix prints a command only where it works as printed: for `pnpm-lock.yaml` it says to upgrade each listed package, or its
parent when it is transitive, or set a pnpm override in `package.json`; for a `requirements.txt` it says to raise each listed
package's pin, and to compile the file again when it is generated.

- ADVISORY-28: A package name that needs quoting prints quoted in the command;
- ADVISORY-29: one that no shell word can hold (a control or format character) gets `fix: see <url>`, or `in <dir>/: see
  <url>` for a nested lock, so no command is printed that would not work.
- ADVISORY-7: npm names match as written (`lodash.merge` is not `lodash-merge`); Python's name rule applies to PyPI only.

A repository's own `osv-scanner.toml` (ignored advisories, package overrides), its `package.json` `pnpm.auditConfig`, its
`.npmrc` and its `.gitignore` change nothing: the gate passes its own empty `src/quality/osv-scanner.toml` and each tracked
lock explicitly, so only a real fix in the lock (an upgrade or an override) turns a finding green.

- ALLOW-1: An advisory the repository cannot fix is allowed in its own `quality/config.toml`, one entry per advisory and lock:

```toml
[[advisories.allow]]
id = "GHSA-462x-pgg6-55fp"
lock = "speech/providers/qwen/uv.lock"
until = 2027-01-31
date = 2026-10-21
item = "overnightworks/agent-presentator#371"
reason = "gradio caps pillow<13; no image input"
```

- ALLOW-2: `id` is any id of the advisory group, GHSA, PYSEC or CVE alike.
- ALLOW-7: `lock` is the tracked relative path; the entry allows the advisory on every package it affects in that lock and in
  no other, so a lock reached by two tracked names needs an entry per name.
- ALLOW-9: Two entries matching one group in one lock (its GHSA and its CVE id) are a finding at `quality/config.toml` naming
  both, and the group is allowed by neither: `! quality/config.toml  conflict  <id> and <id> in <lock> match one advisory ·
  none allows it`.
- ALLOW-3: `until` is the re-check date and `date` the day of the ruling, both TOML dates without a time; `item` and `reason`
  are as in a [declared loosening](gate.md#declared-loosenings).
- ALLOW-4: The allowance holds through `until` (in UTC) and the advisory is red again from the next day: its row ends in `·
  allowance ran out on <until> (<item>)`.
- ALLOW-10: quality sets no latest `until`,
- ALLOW-19: and an advisory with no fixed version is allowed like any other.
- ALLOW-6: Each allowed advisory stands as a row under the dependencies row, after the area's findings and their `fix:`, and
  the rule stays clean: `· <lock>  allowed  <package> <version> · <id> · fixed in <versions> · until <until> (<item>) ·
  <reason>`.
- ALLOW-20: The fact says what the `affected` row says of a fix. [A clean row](output.md#notes) counts them (`· 1 allowed`).
- ALLOW-5: An entry no reported advisory matches is a finding: `! quality/config.toml  unused  <id> in <lock> matches no
  advisory`. [Deleting it is no loosening](gate.md#declared-loosenings).
- ALLOW-11: With no tracked lock every entry matches nothing.
- ALLOW-12: A malformed entry (a missing or extra key,
- ALLOW-13: an empty or multi-line `id`, `lock` or `reason`,
- ALLOW-14: an `item` that is not `#<n>` or `<owner>/<repo>#<n>`,
- ALLOW-15: an `until` or `date` that is text or carries a time,
- ALLOW-16: two entries for one `id` and `lock`,
- ALLOW-17: or an unknown key in `[advisories]`)
- ALLOW-18: is exit `2` in every rule that reads the config and in `verdict`, [naming the entry by its
  position](output.md#refusals) (`[[advisories.allow]] entry 2`), except the last two, which name the `id` and `lock` and the
  key; [`check secrets --staged` reads only `[secrets]`](gate.md#declared-loosenings).
- A new allowance, and one whose `until` moves later, is a [declared loosening](gate.md#declared-loosenings) the allowance
  makes itself, so only the operator's door merges it. The pull request writes the allowance, so its item, date and
  reason prove nothing alone: the verdict's red and the door are the gate. An `id` that is an alias covers every
  group carrying it in that lock, while the door names the `id` and the lock, not the packages.
  Why only the operator's door merges it stands in [gate.md](gate.md#declared-loosenings). The entry's keys, the alias,
  the date format, the expiry line, the unmatched allowance, [the adoption](gate.md#declared-loosenings), no latest
  `until`, the printed lines, an advisory without a fixed version and the reach of an allowance are defaults the
  operator never answered.

Ruled by the operator:

- An exception stands per advisory in `quality/config.toml`, with a reason, an item and a date (ALLOW-1).
- A new exception is a loosening: the pull request stays red until the operator's door merges it (VERDICT-64).
- ALLOW-8: From the merge on, the advisory is green until the date and red again after it.
- No agent can silence an advisory alone.
- A pull request cannot move the re-check date later without the operator: a later `until` for an existing exception is
  a loosening like a new one (VERDICT-64).
- A new exception declares itself: it needs no separate `[[gate.loosened]]` entry (VERDICT-67).

- ADVISORY-8: quality gets each needed database itself on every run (PyPI about 37 MB, npm about 219 MB;
  `https://osv-vulnerabilities.storage.googleapis.com/<ecosystem>/all.zip`), checks it, places it in the scan's own 0700
  scratch directory and runs osv-scanner `--offline` on it, so osv-scanner makes no network call and reads no cache of its
  own.

A request that fails transiently (HTTP 500 to 599 or 429, a refused or reset connection, a timeout, a DNS failure, an
unreachable network or host, a body cut short) is tried again after 3, 9 and 27 seconds, each wait cut to the time left.

- ADVISORY-42: A request that waits 30 seconds without a byte is cut and tried again.
- ADVISORY-9: A database that cannot be fetched within 300 seconds in total for all databases together (the retries and their
  waits included; connect, headers and body, however the server drags them out; reading and hashing a local copy not
  included; the run's end caps the 300 seconds), or still not after the fourth attempt (`cannot download the <ecosystem>
  advisory database from <url>: <error> after 4 attempts`),
- ADVISORY-10: cannot be placed in the scratch directory,
- ADVISORY-11: is not a valid zip,
- ADVISORY-12: or holds no advisory
- ADVISORY-13: is exit `2`, never clean, naming the database; so are an osv-scanner [that is missing, at another
  version](commands.md#scanners), exits other than `0` or `1`,
- ADVISORY-14: runs past the run's deadline,
- ADVISORY-15: or writes a report that is not the expected JSON,
- ADVISORY-16: disagrees with its exit,
- ADVISORY-17: or names a lock file quality did not pass.

Trust boundary: the bucket's answer over TLS, its ETag and its unsigned zip, is what quality trusts, from the same
source osv-scanner itself uses. quality keeps a copy of each archive it fetched, so that a run downloads only what
changed, and trusts no file in the cache directory, its name, its time stamp or any stored hash:

- ADVISORY-43: The copy of an ecosystem lives at `<cache>/advisories/<ecosystem>/all.zip`, where `<cache>` is
  `overnightworks-quality` below `$XDG_CACHE_HOME` where that is an absolute path, else below `~/.cache`; with neither
  there is no copy. The door finds `<cache>` once from the environment it is given. quality creates each missing level with
  mode 0700. One archive stays per ecosystem a tracked lock names, replaced in place and never pruned. The copy is read
  once, only as a regular file (a directory, a FIFO or a file that cannot be opened or read counts as no copy).
- ADVISORY-44: With a copy, the request is one conditional GET with the md5 of the copy's own bytes as `If-None-Match`;
  without one, a plain GET. The copy is used only on a `304` whose ETag is that md5. A `304` that names another md5 costs one
  of the four attempts and is followed by a plain GET. A `200` is used only when its body's md5 is the md5 its ETag states,
  and then replaces the copy through a temporary file `all.zip.tmp-*` beside it and an atomic rename; an archive that is not
  a valid zip with an advisory is kept nowhere. osv-scanner reads a private copy in the scratch directory of the bytes that
  were checked, never the cache file. Two runs at once need no lock: each reader checks the bytes it read, and a writer
  renames a whole file.
- ADVISORY-45: Exit `2`, never clean, naming the database: a `304` without an md5 ETag, a `304` to a request that sent no
  copy, a `200` whose body is not the md5 of its md5 ETag, a `200` without an md5 ETag (weak, not 32 hex digits or missing)
  while a copy exists, and a server that does not answer, though a copy exists. A copy that fails the comparison is never
  scanned. A `200` without an md5 ETag and no copy is scanned and kept nowhere.
- ADVISORY-46: A cache directory quality cannot create, write or read is a run without a copy: it judges from the verified
  download and prints nothing.
- A local green means the server's state at run time. `gate.yml` and `ci.yml` restore no copy, a hosted runner starts
  without one, and a self-hosted runner keeps a verified copy under its user's cache directory, which
  `XDG_CACHE_HOME` moves.

A doctored or rolled-back copy is caught by its md5: the server answers a request that names it with the genuine archive. md5
is an integrity check against the server's own statement and no signature; the server states no other hash that an attacker
could not forge, and a collision does not help against an archive the attacker did not produce.

- ADVISORY-18: Records the pinned osv-scanner cannot read are dropped by it silently; the live-database test for `pyyaml
  5.3.1` on every quality pull request is the canary.

osv-scanner judges the lock as written; the caller's own `uv sync --locked` guards against a hand-edited lock. A
`pnpm-lock.yaml` entry pinned by URL without `integrity` is refused by the caller's own `pnpm install --frozen-lockfile` from
pnpm 10.34.1 and 11.4.0 on (`ERR_PNPM_MISSING_TARBALL_INTEGRITY`); quality does not read it. osv-scanner exits `127` on a
`uv.lock` or `pnpm-lock.yaml` it cannot parse, which is `cannot judge`, never clean.

The check job in `gate.yml` stops at a limit that setup, the scanner install step's limit, the run's deadline and a margin
add up to, so quality's own `cannot judge` shows before GitHub cancels the job; the retries spend the 300 seconds and add
none.

- ADVISORY-34: A test adds them up from the workflow and the constants.

Measured with osv-scanner 2.7.0 on 12 cores (the download at load 17 to 20): the npm archive is 219,026,863 bytes (230,735
files), the PyPI archive 37,064,024 bytes (26,612 files); downloading npm took 34 s; a whole `check` run over a repository
with one `pnpm-lock.yaml` and one hashed `requirements.txt` (one pin) under `--all-packages`, both databases included, took
63 s and 69 s wall and 640 MB resident (load 3 to 5). A `uv.lock` is judged against the same PyPI database; that setup was
not measured.

A requirements file is read by quality itself (`pip_requirements.py`), because osv-scanner skips a line it cannot parse and
still reports clean.

- ADVISORY-35: Exactly two logical forms are accepted, after comments are dropped and `\` continuations joined:
- ADVISORY-36: `name[extras]==version`, with an optional `; marker` and `--hash=` tokens,
- ADVISORY-37: and `name[extras] @ https://.../<name>-<version>-....whl` with at least one `--hash`, where the wheel's
  distribution name equals `name` (PEP 503).
- ADVISORY-38: Any other line (`-e`, `>=`, `~=`, a bare name, `-r`, `-c`, `--index-url`, any other option, a git or sdist
  URL, a wheel without a hash or named for another package) is exit `2`, naming the file and the line
  (`.github/requirements-build.txt: line 4 is neither ...`); a minimum version is refused because pip installs the newest
  one, so judging the lowest could hide a hole.
- ADVISORY-39: osv-scanner reads the file as written under `--all-packages`; the wheel lines, which it skips, reach it as a
  scratch file of `name==version` lines judged under the same tracked name, so a finding on a wheel is named by the tracked
  file and an allowance's `lock` is that file's path.
- ADVISORY-40: The packages osv-scanner lists for each file must equal the pins quality read: a pin it did not list is exit
  `2` (`osv-scanner did not read line 4 of <file>`),
- ADVISORY-41: and so is a package it lists that quality did not read.

The grammar is an allowlist; `-r`, `-c`, `--index-url`, `-e`, git and sdist URLs and ranges wait for a caller that needs
them.

- ADVISORY-19: An advisory database zip with an entry that is not valid JSON is exit `2` too.

Every other download failure is exit `2` at once, without a retry: another 4xx (a missing database is permanent), a missing
file, an error no list above names, and every error after the body is whole (a bad zip, no advisory, not JSON, a scratch
write) (ADVISORY-42).

Ruled by the operator:

- NPM-1: An `[[advisories.allow]]` entry for an npm finding is the same entry with `lock` set to the file's tracked path (`id
  = "..."`, `lock = "frontend/pnpm-lock.yaml"`); it is a declared loosening and merges only through the operator's door like
  any other.
- NPM-2: Nothing in the repository's own files silences an advisory; only a real fix in the lock, an upgrade or an override,
  counts.
- Open npm advisories keep the bump pull request red (ADVISORY-31).

When a repository's frontend and its lock are gone, the npm database is no longer downloaded and an
entry for it is a finding until it is deleted. A repository that falls back to an older quality leaves such an entry
unmatched and red likewise. The predecessor `pnpm audit --prod` steps are retired by each repository's own head.

`fleet-source` reads every tracked `uv.lock`, the root one and each nested one, offline.

- SOURCE-1: A `[[package]]` whose canonical name starts with `overnightworks-` and whose `source` is `registry` is a `from
  index` finding at the lock's tracked path, one per package and lock, its fact the package, its version and the index, its
  fix the git tag pin ([the standard](release.md#depending-on-a-fleet-package)).
- SOURCE-2: A `git`, `url`, `editable`, `virtual`, `directory` or `path` source is allowed,
- SOURCE-3: and a package outside the prefix is not read further.
- SOURCE-4: A lock that is not UTF-8 TOML or whose packages are not a list of tables, a fleet package without a `source` and
  a source of another kind are exit `2`, so a future uv never reads green.
- SOURCE-5: A tracked lock the tree no longer holds is skipped, and one that links outside the repository or is no file is
  exit `2`.
- SOURCE-6: A lock tracked under several names is judged once, under the first.
- SOURCE-7: The row's extent is the locks read, and a repository without a `uv.lock` is clean at `0`; the rule is on for
  every repository, and the lock's content decides whether it judges anything.

It does not check that a `git` source is a tag rather than a branch, nor the host of a `url` source: the lock pins the
resolved commit or a hash either way, and a reviewer reads the rest.

- SOURCE-8: It judges independently of `advisory`, so neither refuses the other.

It first ships in v0.21.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column (FLEET-18).

`pytest-random`, in the `dependencies` area, holds the unit and integration layers to the fleet standard's "random order
with the seed printed" (pytest-randomly). Three readers judge that one decision.

- RANDOM-1: **The lock.** Every tracked `uv.lock`, found and read as `fleet-source` finds and reads it (SOURCE-5, SOURCE-6):
  a lock that holds the package `pytest` and not `pytest-randomly`, names compared canonicalised, is one `missing` finding at
  the lock (`pytest 9.2.0 is locked without pytest-randomly`), its fix adding the plugin to the dev dependency group, then
  `uv lock`. A lock with neither package, or with the plugin alone, is clean.
- RANDOM-2: The row's extent is the locks read, and a repository without a lock, a `pyproject.toml` or a workflow is clean at
  `0`. The rule reads the lock, not a layer: R6 binds the unit and integration layers, and the marker (RANDOM-8) is the way
  out of a flow layer on the workflow side only.
- RANDOM-4: **The options.** Three sources hold a list of pytest options: `addopts` in
  `[tool.pytest.ini_options]` or in the native table `[tool.pytest]` (pytest refuses both in one file) of each
  `pyproject.toml` the `python-floor` rule reads (a list, or a string split as shell words; so a gitlink and a fixture
  project under another project's `tests/` stay out), a pytest call of a workflow `run:` script (the calls
  `pytest-workers` finds, PYTEST-3) and `PYTEST_ADDOPTS` (RANDOM-11). Each source is judged alone, and a source is one
  finding, the no-shuffle problem first. **Deliberate simplification:** pytest joins the verbosity of `addopts`, then
  `PYTEST_ADDOPTS`, then the call, and the rule does not; it is stricter than R6 only where a `-q` in one source meets a
  `-v` in another, or where the plugin is loaded again from another source than the one that switched autoload off, a
  case no fleet repository has.
  - RANDOM-5: *No random order, `unshuffled`:* `-p no:randomly` or `-pno:randomly`, `--randomly-dont-reorganize`, a
    fixed seed (`--randomly-seed=<integer>` or `--randomly-seed=last`, the last of them counting) and
    `--disable-plugin-autoload` unless the source also holds `-p randomly` or `-p pytest_randomly`, which loads the
    plugin again. `--randomly-seed=default` draws a fresh seed and is clean. `last` is `unshuffled` although a runner
    without a restored cache draws a new seed: it exists to reproduce a run, and it fixes the order as soon as a cache
    persists.
  - RANDOM-6: *No seed in the log, `unseeded`:* the plugin prints its seed only as a report-header line, and pytest
    prints header lines only from verbosity 0 up. `-v` adds one to the level, `-q` and `--quiet` take one,
    `--verbosity=<n>` and `--verbosity <n>` set it, read in order of appearance; a level below zero, or `--no-header`, is
    `unseeded`. So `-q`, `-qq`, `-qn4` and `--verbosity=-1` hide the seed, `-q -v`, `-qv`, `-q --verbosity=0` and
    `-n auto` show it, and `--verbosity=0 -q` hides it.
  - RANDOM-7: *Reading:* the options are read by the reader `pytest-workers` shares (PYTEST-5), and only the words
    before the first `--` are options. In a short cluster (`-abc`) each letter is read from the left, and a letter that
    takes a value (the set pytest's and xdist's parsers declare) ends the cluster with the rest of the word, or the next
    word, as its value: `-kquick`, `-rfq` and `-n4 -k q` are clean, `-qn4` and `-qk quick` are `unseeded`. A long option
    is read by its full name only. A `-p` counts only as a word of its own (`-p X`, `-pX`), as pytest reads the plugin
    words: `-vpno:randomly` is clean, and `--disable-plugin-autoload -qp randomly` is `unshuffled`, since no plugin is
    named. The value-taking letters and the spellings of the options the rules read are declared, never read from pytest
    at run time, and a test holds them against the installed parsers. An option a plugin of the repository adds is not
    known. A `--randomly-seed` value built with `${{ }}` is chosen at run time and is clean (a
    named gap), and so is a value of `PYTEST_ADDOPTS` that holds `${{ }}`. `PYTEST_ADDOPTS` is split as shell words, as
    pytest splits it, so `-p "no:randomly"` is read as `-p no:randomly`.
  - An `addopts` finding stands at the key's line in the `pyproject.toml` (no line where the key is not a plain
    `addopts =` line of its table), and nothing clears it, since `addopts` holds for every layer (RANDOM-8).
- RANDOM-9: **The workflow.** A call is read with the reader of `pytest-workers` (PYTEST-2, PYTEST-3), through `uv run`,
  `python -m pytest` and the other runners, at the call's first line.
- RANDOM-11: `PYTEST_ADDOPTS` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD` are read where a workflow, a job or a step sets them in
  `env:`, at the value's line, and as an assignment word of a script, leading a command (`PYTEST_ADDOPTS=-q pytest`, `env
  PYTEST_ADDOPTS=-q pytest`) or after `export`. Any value of `PYTEST_DISABLE_PLUGIN_AUTOLOAD` that is not empty (a blank one
  included) switches the autoload off, as pytest reads it, so it is `unshuffled`, whatever else `env:` holds. A call behind a
  script, `make` or a mise task, a `pytest.main([...])`, a flag in `pytest.ini`, `.pytest.ini`, `pytest.toml`,
  `.pytest.toml`, `tox.ini` or `setup.cfg` and a plugin disabled in a `conftest.py` are not read. A call that only lists
  tests, as `pytest-workers` reads it (PYTEST-5: `--collect-only`, `--collectonly` or `--co` before the first `--`), runs
  none and is not judged (`pytest --collect-only -q` is clean, `pytest -q -- --collect-only` is `unseeded`).
- RANDOM-8: **The marker.** A comment `# pytest-random: <reason>`, with a reason that holds a letter, on the line of a
  workflow call, assignment or `env:` value, or on a line of the unbroken run of comment-only lines directly above it,
  clears the finding (the form of `# pytest-workers:`, PYTEST-7), so `# pytest-workers:` and `# pytest-random:` stand on
  two lines above one call; a marker that trails another command's line clears that command only. It is the way out of a flow layer (end-to-end, browser, image); it clears nothing in `addopts`, and it has
  no other form.
- RANDOM-10: **Facts and fixes.** `unshuffled`: `pytest-randomly is switched off or its order fixed by <source>`, its
  fix naming what to drop (`-p no:randomly`, `--randomly-dont-reorganize`, `--randomly-seed=<integer or last>`,
  `--disable-plugin-autoload` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD`). `unseeded`:
  `pytest-randomly's seed header is hidden by <source>`, its fix
  `drop -q, --verbosity=-1 and --no-header so the log prints "Using --randomly-seed=N"`. The `<source>` is `addopts`,
  `the call`, `PYTEST_ADDOPTS` or `PYTEST_DISABLE_PLUGIN_AUTOLOAD`; a workflow finding adds
  `, or explain on the line above: # pytest-random: <reason>`.
- RANDOM-3: **What cannot be read.** A lock or `pyproject.toml` that is no UTF-8 TOML, a lock whose packages are no list of
  tables and an `addopts` that is neither a string of shell words nor a list of strings are exit `2` naming the file.
- RANDOM-12: A workflow whose YAML cannot be read, and an alias or a merge key where it can hide `jobs`, a job, `steps`, a
  step or an `env:`, and a `jobs` or a job that is no mapping, are exit `2` naming the file, as for `pytest-workers`
  (PYTEST-9), and so is an `env:` value of the two variables, or a script assignment of `PYTEST_ADDOPTS`, that is no shell
  words.
- RANDOM-13: A pytest line the shared reading cannot read or finds built at run time is `invalid` for `pytest-workers`
  (PYTEST-6) and no finding of this rule, so it is red once.
- RANDOM-14: **No release, no switch.** No configuration switches the rule off and no declaration table exists. The rule
  first ships in v0.24.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column (FLEET-18)
  and a dependent meets it at its bump to it. quality's own tree holds no finding of
  it. Not covered, named: pytest and pytest-randomly locked in different dependency groups where a job installs one group
  only, a test environment installed from a requirements file instead of the lock, and an option or flag built with
  `${{ }}`.

`vitest-random`, in the `dependencies` area beside `pytest-random`, holds the unit and component layers of a TypeScript
project to the same R6. Vitest runs files and tests in a fixed order unless `test.sequence.shuffle` is set, and prints
`Running tests with seed "N"` whenever it shuffles, so the config decides it for every run, CI, local and IDE.

- VITEST-1: **The project.** The directory of each `package.json` that `frontend_projects` yields (none under a project's
  `tests/`) and whose `dependencies` or `devDependencies` names `vitest`. Its config is the first tracked file among
  `vitest.config.<ext>` and then `vite.config.<ext>` (`.ts`, `.mts`, `.cts`, `.js`, `.mjs`, `.cjs`), vitest's own order. The
  row's extent is the projects read.
- VITEST-2: **No shuffle, `unshuffled`.** One finding per project, at the config or, with none tracked, at the
  `package.json`, the fact `vitest runs its files and tests in a fixed order`. The config shuffles when the exported object
  (the object after `export default`, the argument of `defineConfig(` and the arrow form `=> ({` included; only a file
  without `export default` falls back to its first `defineConfig(` argument; an `export default` that names no object, as in
  `export default c`, reads none and is `unshuffled`) has a direct `test` key whose object has a direct `sequence` key whose
  object holds `shuffle: true` or `shuffle: { files: true, tests: true }`. A `sequence` anywhere else counts for nothing,
  since vitest 4 ignores a project's own; `{ files: true }` or `{ tests: true }` alone leaves the other order fixed, so it is
  no shuffle. Any `shuffle: false` elsewhere in the file is a finding too, since a vitest 5 project can turn the root's
  shuffle off.
- VITEST-3: A `seed:` with a number literal in that `sequence` is `unshuffled` as well (a fixed seed, as RANDOM-5 treats
  one); a seed set from an expression is clean. The fix is `set test.sequence.shuffle: true in <config>; vitest then prints
  Running tests with seed "N"`, and for a fixed seed `draw test.sequence.seed at run time or drop it in <config>; vitest then
  prints Running tests with seed "N"`.
- VITEST-4: **Reading.** The config is read as tokens (`typescript_tokens`), never run. A config with a string, template,
  regex or block comment that does not close, or that is no UTF-8, and a package.json that is no UTF-8 JSON (`unparsable`, as
  `naming` reports it, through the reader both share) are exit `2` naming the file. A key set in an imported module, a
  spread or `mergeConfig` does not count, which is stricter than vitest.
- VITEST-5: **No release, no switch.** No configuration switches the rule off, and no marker exists: vitest has no flow
  layer, since the fleet's flow layer is Playwright. It first ships in v0.25.0, so a gate pinned below that release shows
  `not in <tag>` in the fleet matrix's column (FLEET-18). Not covered, named: negatives in scripts and workflow calls
  (`--sequence.shuffle=false`, `--sequence.seed=N`), `--config` pointing elsewhere, `extends: false`, a `reporters` list
  without a reporter that prints the seed, 4.1.12's parent-directory config lookup (errs stricter). R6 under node's own test
  runner is `node-test-random`'s.

`node-test-random`, in the `dependencies` area beside `vitest-random`, holds a `node --test` call of a workflow to the same
R6. Node's runner runs its files and tests in a fixed order unless `--test-randomize` or a `--test-random-seed` says
otherwise (node 24.16 or newer; an older node refuses the flag), and prints `Randomized test order seed: N` through its
spec, tap and junit reporters.

- NODETEST-1: **The call.** A command in a `run:` script of a tracked workflow, read by the reader of `pytest-workers`
  (PYTEST-2, PYTEST-3: wrappers, assignments, a shell's `-c` string, and the runners `npx`, `npm exec`, `pnpm exec` and
  `yarn`), whose command word is `node` and whose words before the first `--` hold `--test`. The row's extent is the
  workflows read; a repository without such a call is clean.
- NODETEST-2: **No shuffle, `unshuffled`.** One finding at the call's first line, the fact `node --test runs its files and
  tests in a fixed order`. Among the words before the first `--`, a call shuffles when the last of `--test-randomize` and
  `--no-test-randomize` is `--test-randomize` (`--test-randomize=<anything>` counts as set, as node reads it), or when a
  `--test-random-seed` stands; a seed whose value (`=n` or the next word) is a whole number fixes the order and is
  `unshuffled` as well (as RANDOM-5 treats one), while a seed built at run time (`${{ }}`, `$VAR`, `$(...)`) is clean. The fix
  is `pass --test-randomize before the test files and drop --no-test-randomize and a fixed --test-random-seed (node 24.16 or
  newer); node then prints "Randomized test order seed: N"`.
- NODETEST-3: **No seed in the log, `unseeded`.** A call that shuffles and has `--test-reporter` words, none of them a
  `spec`, `tap` or `junit` reporter whose destination is `stdout` or `stderr`: the n-th `--test-reporter-destination` goes
  with the n-th `--test-reporter`, and a reporter without one writes to stdout. `dot`, `lcov` and a reporter named by
  module path or package do not count; a call without `--test-reporter` is clean. The fact is `no reporter of the call prints
  the seed to the log`, the fix `add --test-reporter=spec --test-reporter-destination=stdout so the log prints "Randomized
  test order seed: N"`. A call that does not shuffle has the first finding only, so each call has at most one.
- NODETEST-4: **Reading.** The words are read, never run. A line the reader cannot finish that holds the words `node` and
  `--test` is `invalid` (`command cannot be read to its end`), and so is a command built at run time in a script holding
  them (`command is built at run time, node --test cannot be read`). A workflow that cannot be read is exit `2` naming the
  file, as PYTEST-9 says.
- NODETEST-5: **No release, no switch.** No configuration switches the rule off, and no marker exists, as for VITEST-5. It
  first ships in v0.26.0, so a gate pinned below that release shows `not in <tag>` in the fleet matrix's column (FLEET-18).
  Not covered, named: a call behind a package.json script (`npm test`, `pnpm test`, `node --run`), `make` or a mise task,
  `run()` from `node:test` inside a script, `NODE_OPTIONS` (a randomizing value there leaves the finding, a fixing one goes
  unseen), a config file passed with `--config-file`, an option written after the first test file (it reads as set), and
  subtests awaited one by one, which keep their order as node documents. A custom reporter counts as printing no seed,
  which is stricter.

The `logging` rule, in the `code` area, judges a repository against the machine rules of the fleet standard "Logging" with
logline's own checker, `logline.check.check` of the logline release quality pins. quality holds no copy of the standard's
values and runs no logging analysis of its own; a rule's wording and its gaps are logline's README's.

- LOGGING-1: **Who is judged.** A repository whose tracked root `pyproject.toml` declares `overnightworks-logline` in
  `[project] dependencies`, a group of `[project.optional-dependencies]` or a group of `[dependency-groups]`, the name
  compared canonicalised. A repository that does not declare it sees no row, and no file of it is judged. This is the
  declaration, not the standard's "Who is covered": a deployed process that has not adopted logline is not judged, and
  quality, which declares logline, is, although the standard excludes a pure command-line tool.
  The declaration is checked before logline judges any file, so a root `pyproject.toml` that cannot be read or parsed
  refuses the rule, and the `code` area with it, in every repository.
- LOGGING-2: **Files.** Every tracked regular `.py` file that `Language.is_test_file` does not call a test file, the
  definition `size` uses; the texts are the ones the `code` area's lint read, so no file is read twice and a text that is not
  valid Python is the lint's refusal.
- LOGGING-3: **Notes.** Each finding is one `open` [note](output.md#notes) at `<path>:<line>` whose fact is the logline rule
  value (`event_name_shape`), in path and source order; no finding row stands and the exit stays `0`. The row's extent is
  the files judged.
- LOGGING-4: **Exit 2.** A text logline cannot parse (`SyntaxError`) or exhausts the interpreter on (`RecursionError`),
  named with the file. Any other exception of the check is not caught. The check runs in quality's process without a time
  limit.
- LOGGING-5: **The pin.** quality depends on logline by the full commit of a release tag, the tag named in a comment, as
  [the fleet standard](release.md#depending-on-a-fleet-package) says for a tool installed without a lock. logline's own
  dependencies, structlog and opentelemetry-api, enter quality's lock with it, and an advisory against either turns
  quality's own gate red although quality never runs their code.
- LOGGING-6: **Report only.** The rule is `report-only`: it changes no verdict. It first ships in v0.23.0, so a gate
  pinned below that release shows `not in <tag>` in the fleet matrix's column, and from v0.23.0 on the cell reads `report-only` (FLEET-18).
  The matrix has no coverage signal for it: every repository whose pin has the rule reads `report-only`, covered or not.

Not judged: the standard's "a released logline tag" (`fleet-source` tells no tag from a branch, SOURCE-2).

- CHECK-4: Every rule is judged, even when another cannot be,
- CHECK-5: and gives one verdict: clean, findings, unparsable, or
  cannot judge.

Each finding names the file, a fact and a fix. What `quality check` prints, its frame, the
masking, `-v` and the vet, is [the output contract](output.md).
