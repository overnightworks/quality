# Rules

Reference for the engineer or operator who needs the exact behaviour of `quality check`.

`quality check` reads git's view of the repository it is started in, from its top level, so a run from `src` judges
the same files and the same `quality/baseline.toml` as one at the top, and names every file relative to the top (`size
src/big.py`) (CHECK-1). There is no path argument; in a submodule or a linked worktree a run judges that work tree's own
top (CHECK-2).

`quality check [AREA ...]` judges only the areas it names, each whole, in the order of `quality check --help` and each
once, however they are named; with none it judges all seven (CHECK-3). An area not named starts nothing and has no
row (CHECK-6); the verdict counts only the named areas (`✓ clean · 1 area`) (CHECK-7) and the exit is the worst of
theirs (CHECK-8), so a named run says nothing of the rest. The gate runs all seven ([gate](gate.md#gateyml)).

It applies these rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass) (ENV-1).
  The fix is `git --literal-pathspecs rm --cached -- <name>, then add <name> to .gitignore`, the first name quoted for
  the shell, so a copy untracks exactly that file (ENV-2); the second stands only where the name needs no quoting, else
  it reads `it` (ENV-3). A name without a slash, such as `.env`, also matches that name in every folder
  (`gitignore(5)`). A name that is not printable UTF-8 gets the hint to remove it by its bytes (`git ls-files -z` shows
  them) instead of a command (ENV-4). For an app repository it checks the rule of
  [platform's app standard](https://github.com/overnightworks/platform/blob/main/docs/app.md) that env files pass
  quality's rules.

  Ruled by the operator: the command runs exactly as printed, with no home-made encoder for the name.
- `compose-port`: every published port in a tracked compose file binds loopback, `127.0.0.1` or `::1` (`"[::1]:HOST:CONTAINER"`,
  `host_ip: ::1`); `[::]` and `0.0.0.0` are findings (COMPOSE-1). For an app repository it checks the rule of
  [platform's app standard](https://github.com/overnightworks/platform/blob/main/docs/app.md) that published compose
  ports pass quality's rules. A compose file is a `.yml` or `.yaml` file whose name holds the word `compose` between
  `.`, `-` or `_`, in any case: `docker-compose.ci.yml` and `keycloak/bootstrap.compose.yaml` are judged,
  `composer.yml`, `compose2.yaml` and `compose.yaml.example` are not (COMPOSE-2). Only tracked files count: an untracked
  `compose.yaml` is never judged, one added with `git add` is (COMPOSE-3). Its rows say `published port`,
  `unreadable port`, `host network` or `unused` (a stale `[[compose.allow]]`) (COMPOSE-4). A tracked file missing from
  disk (deleted, or a symlink to nowhere or in a loop) is skipped (COMPOSE-5); one the filesystem refuses to read makes
  the `files` row `✗ not checked` with the reason (exit `2`) (COMPOSE-6).
  A file is read as Compose reads it: `- 22:22` is the text `22:22` (COMPOSE-7), and `!override` and `!reset` are read
  as the value they wrap (COMPOSE-8); any other tag (`!foo`) and any YAML error is `cannot judge` (COMPOSE-9), in one
  line with its line and column (or its position for a character YAML refuses, `nested too deeply`, `a value does not
  fit its tag`, or `not valid YAML`) (COMPOSE-10).
  A port that is neither text, a number nor a mapping whose `published` (or, without one, `target`) is text or a number
  is a finding (COMPOSE-11). A `ports` that is neither a list nor empty, `0` or `false` is a finding too (COMPOSE-12). A
  service with `network_mode: host` publishes whatever its process binds, so it is a finding too (COMPOSE-13), and so is
  a `network_mode` that holds a Compose interpolation (`${...}`), which may resolve to `host` (COMPOSE-14); `none`,
  `service:x` and any other literal are clean (COMPOSE-15). A service is declared in `quality/config.toml`, a table of
  its own beside `[[layout.allow]]`, with the exact tracked path of its compose file, the service name and a one-line
  reason (COMPOSE-16):

  ```toml
  [[compose.allow]]
  file = "compose.yaml"
  service = "traefik"
  reason = "it terminates TLS on the host's own interfaces"
  ```

  A declared service passes the host check only; its ports are still judged (COMPOSE-17). A declaration that matches
  nothing is a finding on its file: the file is not tracked or not a compose file, it lacks the service, or the service
  is neither host-networked nor interpolated there; the fix is to delete the entry (COMPOSE-18). A duplicate
  `(file, service)`, a folder (a trailing `/`), a pattern (`*`, `?`, `[`), `..`, an absolute path, an unknown key, a
  missing key or a service or reason that is not one-line text makes the `files` row `✗ not checked` with the reason,
  and `next:` shows a `[[compose.allow]]` example (COMPOSE-19), and a malformed other table in `quality/config.toml` is
  forwarded with its validation error. A declaration is no loosening, so `quality verdict` asks for no
  `[[gate.loosened]]` entry. A config reader older than the rule refuses `[compose]` as an unknown key.

  Ruled by the operator: a service with `network_mode: host`, or one whose `network_mode` holds a Compose interpolation,
  is a finding unless `quality/config.toml` declares its file and service.
- `size`: every tracked file of Python, TypeScript (`.ts` `.tsx` `.mts` `.cts`), JavaScript (`.js` `.jsx` `.mjs`
  `.cjs`), Svelte or Vue is measured by its lines (SIZE-1), and no other file is (SIZE-2), and every tracked `*.py`
  file also with [lizard](https://github.com/terryyin/lizard): a file from 800 lines, a Python function from 60 lines
  and a Python function with a CCN above 15 are debt (SIZE-3), [counted apart](commands.md#quality-report) for
  production and tests (a path under `test/` or `tests/`; Python also `conftest.py`, `test_*.py`, `*_test.py`;
  TypeScript and JavaScript also `__tests__/`, `*.test.*`, `*.spec.*`, `*.test-d.*` and `*.spec-d.*`).

  Ruled by the operator: a file that `quality/config.toml` declares generated is left out of the `size` rule; a minified
  or generated file the repository does not declare is counted by its lines like any other:

  ```toml
  [[size.generated]]
  path = "frontend/src/lib/api/types.ts"
  job = "Backend (Python 3.14)"
  item = "overnightworks/songmaker#1784"
  date = 2026-10-30
  reason = "written by scripts/generate_types.py, proven by its --check"
  ```

  A declared file's lines, functions and CCN are neither judged (GENERATED-1), counted in the not-recorded row's count
  (GENERATED-2) nor recorded by `quality check --fix` (GENERATED-3). A declared Python file that Python cannot parse
  is left out likewise: the `size` rule names no note for it and is not refused by it (GENERATED-4). `path` is the
  exact tracked path of one file, `job` one of `[gate] mandatory_jobs`, the job whose generator check proves the file
  (GENERATED-5). A missing or extra key, a folder, a pattern, `..`, an absolute path, a `job` the gate does not list
  (a config without `mandatory_jobs` included), a malformed `item` or `date`, a reason that is not one line or two
  entries for one path is exit `2` naming the entry with this example (GENERATED-6): in `check` as the `debt` row
  `✗ not checked` with the [example under `next:`](output.md#refusals), in `quality check --fix` as the same row
  (GENERATED-7), and in the verdict (GENERATED-8) and the door naming the side;
  [`check secrets --staged` reads only `[secrets]`](gate.md#declared-loosenings). A declaration whose path holds no
  tracked file in a measured language is an `unused` row
  (`[[size.generated]] matches no tracked file in a measured language`) on that path, fixed by deleting the entry from
  `quality/config.toml` (GENERATED-9). Only `quality/config.toml` declares: `.gitattributes`, `linguist-generated`
  included, plays no part.

  A baseline entry left under a declared file's path can fall (`file is left out as generated · ceiling 850 lines`)
  until `quality check --fix` drops it (`dropped  file is left out as generated`) (GENERATED-10). Only the `size`
  rule leaves the file out: `clone` and the report still read it. quality cannot see what the declared job runs; the
  operator checks it once, at the door.

  Functions of the other languages are not measured (SIZE-4): lizard misplaces their boundaries
  ([lizard's issue tracker](https://github.com/terryyin/lizard/issues/521)). Functions are named by their qualified
  symbol (`Class.method`); a second one of the same name, such as a property setter, is `Class.method#2` (SIZE-5).
  Without `quality/baseline.toml` the [debt row](output.md#area-rows) is
  `· debt  not recorded  N entries over the thresholds · measured only`, N the size entries over the thresholds that
  quality measured (a declared file left out); it equals what a first `quality check --fix` records only when every
  Python file parses, otherwise that fix refuses (SIZE-6). The size debt leaves the exit unchanged (SIZE-7). The
  per-class and language counts leave `check`; the report keeps them.
  A `.py` file is read the way Python reads source (a PEP 263 coding cookie or a UTF-8 BOM decides the encoding)
  (SIZE-8). One Python 3.14 cannot decode or parse is named as not valid Python: without a baseline it stands as a
  [note row](output.md#notes) under the debt row (`· bad.py  not measured  not valid Python`) and the size verdict is
  unchanged (SIZE-9), while `python-security` judges the file unparsable or cannot judge it (exit `2`); with a baseline
  the `debt` row is `✗ not checked` with `bad.py is not valid Python (...)` (SIZE-10), and `quality check --fix`
  refuses the same way, writing nothing; both exit `2`. Only where no baseline is held does `--fix` refuse with
  `✗ not fixed`, since only recording the first ceiling is refused (SIZE-11). A tracked file the operating system
  refuses to read makes the `debt` row `✗ not checked` with or without a baseline, and `quality check --fix` refuses
  likewise, writing nothing; both exit `2` (SIZE-12). The reason names the case: a file without read permission is
  `cannot read locked.py: [Errno 13] ...`, a file behind a directory without permission is
  `cannot reach pkg/m.py: [Errno 13] ...` (SIZE-13). A tracked file missing from disk
  (deleted but not staged, left out by a sparse checkout, or a dangling symlink) is skipped, and its entries stand,
  unjudged, while the index tracks it (CEILING-1). A `quality/baseline.toml` that is not UTF-8 is malformed like invalid
  TOML and says to restore it from git or repair it by hand (CEILING-2). A baseline with a table this quality does not
  know (`has an unknown table [future]; quality <version> knows ccn, file_lines, function_lines, clones`) is not told
  that: a newer quality may have written it, so the head is told to run the quality release its gate pins (CEILING-3).
  With `quality/baseline.toml`, the tree on disk must hold the baseline: a new violation, an entry that grew and an
  entry above its measured value are findings, each a row under the [debt row](output.md#area-rows): `new debt`
  (`file has 800 lines · debt from 800 lines`, fix: split the file below 800 lines) (CEILING-4), `grew`
  (`function read_rows has 71 lines · ceiling 64`, fix: bring it back to 64 lines or below) (CEILING-5) and `can fall`
  (`file has 742 lines · ceiling 766`, fix: `quality check --fix`) (CEILING-6); an entry whose path the index no longer
  tracks, or whose Python file is measured without the function, can fall after the measured rows, with no line
  (`file is gone · ceiling 802 lines`, `function total is gone · ceiling CCN 19`, fix: `quality check --fix`)
  (CEILING-7); an entry with no measurement otherwise (a tracked file missing from disk, a function entry under a file
  of another language) stands (CEILING-8) and counts in the [clean row](output.md#area-rows)'s
  `ceiling held · N entries, M clone pairs`.
  `quality check debt --fix` (`--fix` takes no `--staged` and needs `debt` among the named areas, or none named; `--dry-run` needs `--fix`; no workflow runs either)
  changes `quality/baseline.toml` at the top of the repository it is started in (a stray `src/quality/baseline.toml` is
  neither read nor removed) and then judges the tree against the new file. It only lowers: each held entry falls to its
  measured value (`lowered`, `function total CCN 19 → 17`) and goes where the measured value is no longer debt (`dropped`,
  `file 805 → 790 lines · debt from 800 lines`); an entry that grew stays a finding, its `fix:` adding `a raise is
  a hand edit and a declared loosening`, a new one stays a finding with its usual `fix:`, and the rest still falls. An entry `check` calls gone or left out as generated goes
  (`dropped  file is gone`), counted among the lowered; a held pair the tree lost goes (`dropped`, `pair ... is gone`). It
  records a ceiling (`recorded`, `N entries, M clone pairs · quality/baseline.toml is new`) only where none lies on disk,
  in the index or in any commit `HEAD` reaches; a ceiling or a `[clones]` table git holds and the tree lacks is `✗ not
  fixed` with `restore quality/baseline.toml from git, or repair it by hand` (a version no TOML parser reads refuses
  likewise), and a shallow clone that cannot prove it names `git fetch --unshallow`. A file without `[clones]` that git
  never held it in records the pairs and keeps its entries. It also rewrites a file whose text is not what it would render (an older header, other blank lines or order, a comment), though no entry changes: `rewritten`, `quality/baseline.toml`, or beside a finding `rewritten · quality/baseline.toml`. The verdict line adds `ceiling lowered by N entries`,
  `ceiling recorded`, `ceiling rewritten` or `nothing to lower`. Under `--dry-run` the same ceiling is made, judged and rendered and nothing is written: each change is a `~` row worded as what the fix would do, the `~` debt row (`N to lower`, `N to move`, `to record` or `to rewrite`, `text differs · quality/baseline.toml`) and the verdict (`~ dry run · N ceiling entries would fall`, `N ceiling entries would move`, `ceiling would be recorded` or `ceiling would be rewritten`, then `· nothing written`), the exit `1` while the fix would change the file.
  The file is written whole or not at all: a hidden sibling takes the text and replaces the file in one step, so whatever
  stops the process leaves the complete old file or the complete new one, the replacement being the commit point, so a
  Ctrl-C just after it keeps the new file, and a killed process can leave that sibling. It keeps the file's mode,
  stays refused on a read-only file and needs a writable folder; a symlink on its way is never written through. A fix
  writes only when an entry changes or the file's bytes are not its rendering; a file that renders byte for byte stays
  untouched. A `check` without `--fix` says nothing of the text. It promises nothing against a power loss.
  A config or baseline the check cannot read, or a file Python cannot parse (a declared file included (GENERATED-11),
  because the clone pairs of the whole tree must be measured), refuses it (CEILING-9): the `debt` row is
  `✗ not checked` or `✗ not fixed`, exit `2`, and nothing is written. A refused fix leaves each rule the verdict it
  reached against the unchanged file. Another area's refusal does not undo a finished fix: the file is written, the
  `✓` row stands, the exit is `2`.
  `--fix` follows renames as the verdict carries them: from the ceiling's last commit it reads what git renamed into the index (`git diff --cached -M50% -l1000`), moves a renamed file's entries and pairs to the new path at their values (`moved`) and drops what the ceiling held under that path before the move (`dropped ... left from before the move`); a ceiling it already moved stays, an entry it cannot place refuses with the restore step, a shallow clone whose ceiling's last commit is its edge, which is every depth-1 clone that holds a ceiling, refuses `--fix` with `git fetch --unshallow`, and a move the index does not record is not followed. Renames are followed before anything is judged gone, so a renamed file's entries move and never fall; with no committed ceiling a moved file's entries are gone. `check` reads no history: after a staged move it names the new path `new debt` and the old one `can fall … is gone`, and one `--fix` clears both.
- `clone`: a pair is two functions (methods and nested functions too) in two different tracked `*.py` files, each of at
  least 40 tokens without its docstring, whose structure is at least 90 % the same once every name and literal is
  renamed (CLONE-1); tests count like code (CLONE-4), and a copy inside one file is no pair. A pair is named by the
  path and qualified symbol of both sides, as `size` names a function (CLONE-2); a pair counts as tests only when both
  files are tests (CLONE-3). Without a `[clones]` table in `quality/baseline.toml` the rule only measures (CLONE-5):
  where the file exists, the pairs stand in the [partly row](output.md#area-rows)
  (`· debt  not recorded  2 clone pairs · partly measured only`). With one, even an empty one, a new pair is a
  `near copy` row at its second side (`function check and tests/test_a.py run`, fix: give the shared body one owner and
  call it, or make the cases one parametrized test) (CLONE-6) and so is a held pair the tree lost, even when a side
  was deleted (`can fall` at its first side: `pair tests/test_a.py run <-> tests/test_b.py check is gone`, fix:
  `quality check --fix`). A Python file that cannot be parsed may hide a pair, so with a `[clones]` table the `debt` row is `✗ not checked`
  (exit `2`), as for `size`. The baseline is read once for both rules: one that cannot be read leaves both
  unjudged (the `debt` row names the reason once, exit `2`), also when it has no `[clones]` table, since the mode is then unknown.

  Ruled by the operator: quality owns this detector instead of calling jscpd or lizard; it is the detector taken over
  from aco.

  The detector compares the functions' name-folded token streams and judges Python only. The threshold, what makes a
  pair, how it is named, that tests count, the report-only mode and the printed lines are defaults the operator never
  answered. A renamed file or function orphans the held pair and names a new one. Only the verdict's comparison with the
  base treats a pair whose files git renamed as the same pair (see the baseline paragraph below).
- `import-contract`: the repository's [import-linter](https://github.com/seddonym/import-linter) contracts must be
  kept. The config is the tracked `.importlinter` or `setup.cfg` (`[importlinter]`) or `pyproject.toml`
  (`[tool.importlinter]`), counted once its name exists in the tree: a tracked one deleted from disk is none, a symlink to
  nowhere cannot be read (exit `2`).

  Ruled by the operator: without one the rule only measures; the `imports` row is a grey note, the area is not counted
  among the clean areas, and the verdict names it.

  The `imports` row reads `· imports  not set up  no import-linter config`, dim at a terminal, the exit stays `0`, and the verdict line says `imports not enforced`
  before the duration, after debt's note. A config with contracts that are kept is `✓ imports  clean  N contracts` (a config without a contract: `0 contracts`). import-linter 2.15 runs inside quality as a library, never its CLI, and writes nothing to
  the checkout (no `.import_linter_cache`). A broken contract is one finding `! <config>  broken  contract <name>`, with the lines import-linter renders for it (the chain, `shop.db -> shop.web (l.1)`, and the contract's guidance) under the row behind `| `, fix `change the import or the contract`, exit `1`. An `ignore_imports` entry no
  import matches follows the contract's `unmatched_ignore_imports_alerting`: `error` (the default) is a finding `unused  contract <name> ignores a -> b`, fix `delete it from the contract's ignore_imports`, `warn` a note row `· <config>  unused  contract <name> ignores a -> b`, `none` nothing. Each root
  package is the one tracked `<dir>/<name>/__init__.py` outside the tests. The rule cannot judge (exit `2`, before any
  caller code runs or any file outside is read) two configs that each hold a section, a `contract_types` option
  (plugins would be imported), a type other than forbidden, layers, independence, protected and acyclic_siblings,
  invalid contract options, a config value of the wrong type (`... in pyproject.toml cannot be judged: ...`), a
  dotted root package, a root package with no or several such directories, one Python finds in another place (`root
  package yaml is src/yaml/__init__.py, but Python finds it outside the repository at site-packages/yaml/__init__.py`, a
  place outside named from the `sys.path` entry it lies under; a place inside is named repository-relative), a symlink
  under a root package that leaves the repository, directly or through a link that stays inside, and a symlink loop, and a `.py` file that "cannot be examined" (no regular file, a name or content that is not valid
  UTF-8, a coding cookie in any spelling but utf-8, utf8, unicode-1-1-utf-8, unicode11utf8, x-unicode20utf8, ascii, us-ascii or ansi_x3.4-1968 (accepted in any letter case), or one the system will not let it read). A
  module grimp cannot parse makes the `imports` row `✗ not checked` with the reason (exit `2`); a warning grimp gives while building the
  graph is a note row `· <config>  warning  <grimp's message>`, before the contracts' notes. No message names an absolute local path.
  On a pull request `quality verdict` and the door also hold the head's import contracts against the base's (see
  "The import contracts only tighten" below).
- `claude-md`: a tracked `CLAUDE.md`, in any case (`claude.md` is named as it is) (CLAUDE-1), holds exactly `@AGENTS.md`
  with or without a final line feed (CLAUDE-2), so Claude, Codex and Grok read the same guidance, and the repository
  tracks an `AGENTS.md` beside it (CLAUDE-3). No `CLAUDE.md` is clean, with or without an `AGENTS.md` (CLAUDE-4). A
  symlink is a finding (a dangling one too) (CLAUDE-5), and so is a tracked `.claude/CLAUDE.md` (CLAUDE-6), which
  Claude Code reads as project memory and Codex and Grok never read. Any other bytes are a finding, a Windows line
  ending included (CLAUDE-7); a nested `docs/CLAUDE.md` is not judged (CLAUDE-8). A finding's row names the file and
  says `claude only` (`.claude/CLAUDE.md`), `symlink`, `diverges` or `no AGENTS.md` (CLAUDE-9), with a fixed fact and a
  fix in prose that never show its content (CLAUDE-10); every fix is prose with no command (CLAUDE-11), and for a
  file's own text it says to move the lines into `AGENTS.md`. A tracked file missing from disk is skipped (CLAUDE-12);
  one the filesystem refuses to read makes the `files` row `✗ not checked` with the reason (exit `2`) (CLAUDE-13). No
  configuration switches the rule off.

  Ruled by the operator: `CLAUDE.md` and `AGENTS.md` never diverge. A finding names only the file, and its fix is a
  sentence, never a command.
- `root-layout`: every entry directly in the root of the tracked tree is on quality's base list or declared (ROOT-1). An
  entry is a file or a directory (written `frontend/`); a directory with many files is one entry (ROOT-2). Names
  compare in exact case, and the finding for a name that differs only in case names the base-list spelling (ROOT-3). A
  file and a directory of one name are two entries (ROOT-4), and a root symlink counts by its own name (ROOT-5). Only
  tracked files count; a stray left on disk is not judged (ROOT-6). The base list: `README.md AGENTS.md CLAUDE.md
  HEART.md LICENSE .gitignore .gitattributes .gitmodules pyproject.toml uv.lock .python-version package.json
  pnpm-lock.yaml package-lock.json Cargo.toml Cargo.lock rust-toolchain.toml Dockerfile .dockerignore compose.yaml
  compose.yml docker-compose.yaml docker-compose.yml sonar-project.properties .importlinter .pre-commit-config.yaml` and
  the directories `.github/ .aco/ quality/ src/ tests/ docs/ scripts/` (ROOT-7). It belongs to quality and changes with
  a quality version; a repository extends it, never shortens it. Each other entry is declared in `quality/config.toml`,
  one entry and one one-line reason, no pattern and no deeper path (ROOT-8):

  ```toml
  [[root.allow]]
  entry = "frontend/"
  reason = "the web client, built apart from the Python package"
  ```

  A declaration that matches no tracked root entry is a finding (ROOT-9); a declaration of a base-list entry that is
  tracked is clean (ROOT-10). A finding's row names the entry and says `unlisted` (its fact adds the base list's
  spelling for a name that differs only in case) (ROOT-11) with both ways out in the fix, move it to its owner's
  directory or declare it with a reason, in prose with no command and no local path (ROOT-12); a declaration that
  matches no tracked root entry says `unused` (its fact adds the tracked directory for an entry written without its
  slash) (ROOT-13). Findings are sorted by entry and all printed (ROOT-14). An invalid declaration (a `/` inside the
  entry, `.`, `..`, `.git`, an empty entry, a multi-line reason, an unknown key, a missing key, an entry that is not
  text, a `[root]` key other than `allow`, `allow` that is not an array of tables, or one entry declared twice) makes
  the `files` row `✗ not checked` with the reason, and `next:` shows a `[[root.allow]]` example (ROOT-15). Any other
  malformed table or allowance elsewhere in `quality/config.toml`, which plain `quality check` reads for this rule,
  refuses it too, with that table's own message; the values of `[gate]` stay the verdict's to judge.

  Ruled by the operator: any pull request may add a declaration with a reason; the review judges whether the reason
  holds. A declaration is no loosening, so `quality verdict` asks for no `[[gate.loosened]]` entry.

  The rule reads names, never content, and writes nothing (ROOT-16). No configuration switches it off (ROOT-17). A
  config reader older than the rule refuses `[root]` as an unknown key, so a declaration lands in the pull request that
  moves the repository to a quality version that knows it. Named gaps: a submodule in the root counts as a file entry
  and no case of it is tested; marketplace's refusal of every symlink in the tree stays that repository's own check.
- `file-suffix`: a tracked file whose basename has no suffix is a finding (SUFFIX-1), unless it is executable (index
  mode 100755), a symlink (120000) or a gitlink (160000) (SUFFIX-2), its basename is a dotfile, its basename is on
  quality's list of tool-mandated names (`LICENSE Dockerfile Makefile CODEOWNERS`) (SUFFIX-3), or it is declared
  (SUFFIX-4). The index (`git ls-files -s`) is the only source of a path's mode and a link is never followed (SUFFIX-5);
  index data that cannot be read makes the `files` row `✗ not checked` with the reason (exit `2`) (SUFFIX-6). Names
  compare in exact case, in any folder (SUFFIX-7). The list belongs to quality and changes with a quality version. A
  file is declared in `quality/config.toml`, a table of its own beside `[[root.allow]]` (SUFFIX-8), one exact tracked
  path (SUFFIX-9), the rule and a one-line reason:

  ```toml
  [[layout.allow]]
  path = "deploy/EDGE_SHA"
  rule = "file-suffix"
  reason = "the deploy script reads it by this name"
  ```

  The rule is `file-suffix`, `script-name`, `script-header`, `test-layout` or `test-import`; a declaration covers only
  its own rule (SUFFIX-10), and one `script-name` entry covers both the shape and the location of its path (SUFFIX-11).
  A declared path that is not tracked is a finding of the declared rule, `unused` (SUFFIX-12), whose fix deletes every
  `[[layout.allow]]` entry of that path (an untracked path makes each of its entries stale; the fix names no rule); a
  declaration for a file that already complies is clean (SUFFIX-13). A suffixless file says `tracked` (SUFFIX-14) with
  both ways out, give it a suffix or declare it, in prose with no command (SUFFIX-15); findings are sorted by path and
  all printed (SUFFIX-16). The five rules' fixes that ask for an entry end with one clause naming the finding's own rule
  (SUFFIX-17). A duplicate `(rule, path)`, a folder (a trailing `/`), a pattern (`*`, `?`, `[`), `..`, a leading `./`,
  an empty component, a blank path, an absolute path, a path that is not text, an unknown rule, an unknown key, a
  missing key or a reason that is not one-line text makes the `files` row `✗ not checked` with the reason, and `next:`
  shows a `[[layout.allow]]` example; a malformed `[layout]` table is the same (SUFFIX-18), and another malformed table
  in `quality/config.toml` is forwarded with its validation error. A declaration is no loosening, so `quality verdict`
  asks for no `[[gate.loosened]]` entry. The rule writes nothing and no configuration switches it off (SUFFIX-19). A
  config reader older than the rule refuses `[layout]` as an unknown key.

  Ruled by the operator: quality checks in CI where files sit below the root (`tests/`, `scripts/`, file names).
- `script-name` and `script-header` judge a script: a tracked file with index mode 100755, whose content starts with
  `#!`, or that is a `.sh`, `.py` or `.mjs` file under a `scripts/` folder at any depth (`frontend/scripts/` too)
  (SCRIPT-1). A path with a `tests/` component is never a script (SCRIPT-2), nor is a link (120000, 160000) (SCRIPT-3),
  nor data beside a script such as `scripts/BACKUP.md` (SCRIPT-4). The mode comes from the index only; the content is
  read through the repository reader, and a file that cannot be read makes the `files` row `✗ not checked` with the
  reason (exit 2) (SCRIPT-5). Both rules compare names in exact case, print all findings sorted by path (SCRIPT-6) and
  have no off switch. A row says `misnamed` where the name's shape breaks the rule (its fact adds the place, when that
  breaks too), `misplaced` where only the place does, and `no header` for a script without a header (its fact names
  the window it checks) (SCRIPT-7). A declaration is a `[[layout.allow]]` entry as for `file-suffix`.
  - `script-name`: a script's basename without its extension is snake_case with at least two words
    (`run_static_checks.sh`, `verify_release.py`; `check.sh` and `prove-x.py` are red), lower-case letters and digits
    only (SCRIPT-8), and the script lies under a `scripts/` folder (SCRIPT-9). Tool-mandated basenames and dotfiles are
    exempt from both (SCRIPT-10). There is no verb list: whether the verb fits stays a judgement for the structure
    audit.
  - `script-header`: a comment or docstring stands within the first three non-blank lines (SCRIPT-11) after the shebang
    and a PEP 723 block (SCRIPT-12); blank lines do not count. Comments are `#`, and `//` or `/*` for `.mjs`
    (SCRIPT-13). A tool directive (`# shellcheck`, `# -*-`, `# noqa`, `# type:`, `# pyright:`, `# ruff:`, `// eslint`)
    and code such as `set -euo pipefail` do not count (SCRIPT-14). Presence only, never content (SCRIPT-15).
- `test-layout` judges what may lie under a Python project's `tests/` and where a frontend test lies. A project is the
  directory of any tracked `pyproject.toml`, the root or a member at any depth (LAYOUT-1); the file's content is never
  read, and one inside the `tests/` of another project, of either kind, makes no project (LAYOUT-2). A project is judged
  when it has a package: a directory directly under `<project>/src/` that holds a tracked `.py` at any depth, with or
  without `__init__.py` (LAYOUT-3). A project without one, and a `tests/` folder that belongs to no project, are not
  judged (LAYOUT-4). Under a judged `<project>/tests/` every tracked path is a `test_*.py` (placed and named by the
  mapping below), a `conftest.py` at any depth, a file in a `support/` or `fixtures/` folder at any depth, or a file in
  the top-level `tests/e2e/` (LAYOUT-5). An `__init__.py` under `tests/` is red anywhere, `support/` and `e2e/`
  included, and is data only inside a `fixtures/` folder (LAYOUT-6). A finding names its case, as a `stray` row: an
  `__init__.py` (delete it, or declare it) or any other file (move it to a `support/` helper folder or a `fixtures/`
  data folder, or declare it) (LAYOUT-7). A declaration is a `[[layout.allow]]` entry with the rule `test-layout` on an
  exact tracked path. Paths come from the index and nothing is read from disk; a link or gitlink is judged by its own
  name and never followed (LAYOUT-8); an index record that cannot be read makes the `files` row `✗ not checked` (exit
  `2`). Names compare in exact case (LAYOUT-9), all findings print sorted by path (LAYOUT-10) and no configuration
  switches the rule off. Named gaps, waiting for usage evidence: a `tests/` folder elsewhere (`src/p/tests/`), a project
  with only `setup.py`, a project whose `src/` holds only modules, and a package named `support` or `fixtures`.
  Every `test_*.py` outside `fixtures/` and the top-level `e2e/` mirrors the module it tests, one direction
  (LAYOUT-11). Its folder mirrors a folder of `src/<package>/` (`tests/api/` is `src/<package>/api/` (LAYOUT-12); with
  several packages `tests/<package>/api/` is `src/<package>/api/` (LAYOUT-13)), and that folder holds a tracked `.py`
  at any depth (LAYOUT-14). `tests/scripts/` mirrors `<project>/scripts/` (LAYOUT-15) and `tests/workflows/` mirrors
  the root's `.github/workflows/` (LAYOUT-16), directly and only in the root project (LAYOUT-17); both come before any
  package name (LAYOUT-18). Each candidate of the mirrored folder has a key: `__x__` reads as `x`, otherwise one
  leading `_` is dropped (LAYOUT-19); candidates are the `.py` files and the subfolders that hold a `.py` (LAYOUT-20)
  (in `scripts/` any file and subfolder (LAYOUT-21), in `.github/workflows/` the `.yml` and `.yaml` stems with `-`
  read as `_` (LAYOUT-22)), and in the top folder of a package also its own name (LAYOUT-23). The test's name,
  `test_<name>.py`, splits at `_` into words and a word prefix of it, ending at a word boundary, must equal a key
  exactly (LAYOUT-24); the rest is the aspect, and a name whose first word is empty (`test_.py`, `test__x.py`) has no
  prefix (LAYOUT-25). A `test_*.py` in a `support/` folder is red (LAYOUT-26). A member's tests map only into the
  member's own `src/` and `scripts/` (LAYOUT-27). Each case has its own fact and fix (LAYOUT-28), a file that breaks
  the folder and the name condition together gets one finding with both facts (LAYOUT-29), and every fix ends with
  declaring the path. A test module that mirrors nothing says `misplaced` (LAYOUT-30), its fact joining the facts by
  `; ` (LAYOUT-31).

  Ruled by the operator: `tests/` mirrors `src/<package>/` by path, unit and integration tests are told apart by a
  pytest marker, and only `e2e` has its own folder.

  Frontend tests. A frontend project is the directory of any tracked `package.json`, the root or a member at any depth
  (LAYOUT-32); its content (`workspaces` included) is never read, a `package.json` inside the `tests/` of a project of
  either kind makes no project, and a test file belongs to its nearest enclosing frontend project (LAYOUT-33). A
  repository without one is not judged by these rules (LAYOUT-34). Judged files are tracked TypeScript and JavaScript
  files (the suffixes `size` counts, `.mjs`, `.js` and `.tsx` included) (LAYOUT-35) with a word between the first and
  the last `.` that is `test`, `spec`, `test-d` or `spec-d` (LAYOUT-36); only the name decides, no `tests/` or
  `__tests__/` folder does, a link is judged by its name and a gitlink is a folder and never judged (LAYOUT-37). A path
  under the `tests/` of a Python project with a package is judged by the Python cases only (LAYOUT-38). Sibling: a
  judged file with no word `spec` is a test, and its base, its name up to the first `.` (LAYOUT-39), or a non-empty
  prefix of the base that ends before a `-` or `_` or between a lower-case letter or digit and an upper-case letter
  (LAYOUT-40), must equal the key of a subject in the same folder (LAYOUT-41). A subject is any tracked file there that
  is no gitlink and carries no test word, whatever its suffix (a symlink counts by its name, a folder never)
  (LAYOUT-42); its key is its base without a leading `+`, so `page.test.ts` maps to `+page.svelte` (LAYOUT-43). An
  empty base never matches, and `.test.ts` is red (LAYOUT-44). Spec: a judged file with the word `spec` lies under its
  project's top-level `e2e/` folder at any depth (LAYOUT-45); other files there are not judged as specs, and a
  `.test.*` there needs its sibling (LAYOUT-46). A declaration exempts its path from every case (LAYOUT-47). Each case
  has its own fact and fix: a test with no sibling (move it beside the file it tests and name it `<subject>[<Aspect>]`
  plus its own tail) and a spec outside `e2e/` (move it into the project's `e2e/`, or name a Vitest test
  `<subject>.test.<suffix>`); every fix ends with declaring the path; both say `misplaced` (LAYOUT-48), and a test in
  the repository root names it as the repository root, and one in a nested project's root folder names that folder
  (LAYOUT-49). `test-layout` judges no `.rs` file and reads no `Cargo.toml`, and Cargo's own layout stands (other rules
  still read tracked files for a shebang); a `package.json` or `pyproject.toml` inside a Cargo tree makes a project
  like any other. Named gaps, waiting for usage evidence: a subject that is a
  folder, an acronym run read as one word (`HTMLParser.test.ts` beside `HTML.ts` is red), a name holding both `test`
  and `spec` (judged as a spec), a test-named file outside any frontend project, where helpers, setup files and
  fixtures lie, a test whose subject lies outside its project (declare it), an `e2e/` folder that is its own
  workspace package (its specs are red), a `package.json` under a `fixtures/` folder outside any `tests/` (a project)
  and a judged file in a frontend `fixtures/` folder (it needs a sibling).
- `test-import` judges every Python project, with or without a package, as `test-layout` finds projects (TESTIMPORT-1).
  The importers are the tracked `.py` files under its `tests/` (TESTIMPORT-2) that are no link (TESTIMPORT-3) and lie in
  no `fixtures/` folder (TESTIMPORT-4), `support/` modules and `e2e/` drivers included. The targets are the tracked
  `test_*.py` and `conftest.py` files there, outside `fixtures/` (TESTIMPORT-5) (a link is a target by its name
  (TESTIMPORT-6)), including a member's or the root's own (TESTIMPORT-7). Every `import` and `from ... import` statement
  is read, inside a function or under `if TYPE_CHECKING:` too (TESTIMPORT-8). An absolute name is red when its
  components equal the trailing components of a target's path without `.py`, which covers every `pythonpath` spelling
  without reading pytest's configuration (`import conftest`, `from tests.test_x import y`, `from e2e.conftest import z`)
  (TESTIMPORT-9); a `from M import a` checks `M` and `M.a`, so `from tests import conftest` is red and
  `from tests.support.x import test_helper` is not (TESTIMPORT-10). A relative name resolves against the importer's
  folder and must equal a target's path (TESTIMPORT-11). A plain `e2e/` driver may import another (TESTIMPORT-12). One
  finding per statement, at `path:line` (TESTIMPORT-13), says `test import` and names each target it imports
  (TESTIMPORT-14); the fix is a `support/` module or a fixture in `conftest.py`, or a `[[layout.allow]]` entry with the
  rule `test-import` on the importer's path, which exempts all its imports (TESTIMPORT-15) (an untracked declared path
  is a finding; a `test-layout` declaration does not cover it). Importers are decoded as Python 3.14 reads source and
  parsed (TESTIMPORT-16): a file that does not parse (`<path> is not valid Python (...)`) (TESTIMPORT-17) or cannot be
  read (TESTIMPORT-18) makes the `files` row `✗ not checked` with the reason (exit 2); no other file is read
  (TESTIMPORT-19). All findings print sorted by path and line (TESTIMPORT-20) and no configuration switches the rule
  off. Not read, and waiting for usage evidence: string imports (`importlib.import_module`, `__import__`,
  `pytest.importorskip`), `pytest_plugins`, `sys.path` edits, `runpy`, source passed to a subprocess with `-c`, and
  `*_test.py` as a target. An import whose name matches a tracked test module is reported even when it means a
  third-party module; rename the test module or declare the importer.
- `python-floor` judges every tracked `pyproject.toml` that `test-layout` finds as a project (the root or a member at
  any depth, none inside another project's `tests/`) (FLOOR-1) and that holds a `[project]` table; one with only
  `[tool.*]` tables, or with `project` as a value, is not judged (FLOOR-2), and a gitlink is never entered (FLOOR-3).
  The floor is the highest lower bound among the `requires-python` clauses `>=`, `>`, `~=`, `==` and `===`, read with
  `packaging` (FLOOR-4); a trailing `.*` is dropped (`==3.14.*` is 3.14, `==3.*` is 3) (FLOOR-5), `!=`, `<` and `<=` set
  none (FLOOR-6), and `>3.13` counts as 3.13 (FLOOR-7). The file is clean from a floor of 3.14: `>=3.14`,
  `>=3.14,<3.15`, `~=3.14`, `==3.14.*` and `>=3.15` pass (FLOOR-8); `>=3.12`, `>=3.11,<3.15`, `>3.13`, `>=3.14.0rc1`,
  `<3.15`, an empty value, `dynamic = ["requires-python"]` and a missing `requires-python` are red (FLOOR-9) (the last
  four name no floor (FLOOR-10)). One finding per file, at its path (FLOOR-11), says `below floor` (its fact names the
  value and the floor) (FLOOR-12) or `no floor` (the value, which sets no lower bound) (FLOOR-13), with the fix: raise
  `requires-python` to `>=3.14` (FLOOR-14) and move CI, `.python-version`, Dockerfiles and ruff's `target-version` with
  it, or declare the file.
  A `[[python.allow]]` entry with exactly a `path` (the exact tracked path of the `pyproject.toml`, no folder, pattern
  or `..` (FLOOR-15)) and a one-line `reason` (FLOOR-16), one per path (FLOOR-17), makes that file clean (FLOOR-18); its
  own table, because the exception is meant to end. Each allowed file stands as a `· <path>  allowed  <fact> · <reason>`
  [row](output.md#notes) under the files row, after the area's findings (FLOOR-19). An entry whose path is no judged
  file (untracked, gone, without `[project]`) or whose file is already at the floor says `unused` (FLOOR-20); its fix is
  to delete the entry (FLOOR-21). A malformed entry refuses every rule that reads the config, and a config reader older
  than the rule refuses `[python]` as an unknown key, so declare only after the bump. A candidate that is not UTF-8
  TOML, or whose `requires-python` is no string, makes the `files` row [`✗ not checked`](output.md#refusals) (exit 2)
  with its own reason (FLOOR-22). A `requires-python` that is no PEP 440 specifier set or holds a `===` clause with a
  version that is not PEP 440 (`===foobar`) makes it `✗ not checked` with
  `<path> has a requires-python quality cannot read` (exit 2) (FLOOR-23); that reason holds no part of the value
  (FLOOR-24). No configuration switches the rule off, and a declaration asks `quality verdict` for no
  `[[gate.loosened]]` entry. Only `requires-python` is read, because uv and pip refuse an interpreter outside it, so a
  leftover setter cannot install the project on old Python. Not read, and waiting for usage evidence: workflows,
  `.python-version`, Dockerfiles, ruff's `target-version`, `setup.py`, `setup.cfg` and Poetry's `[tool.poetry]`; `!=` as
  a floor (`>=3.13,!=3.13.*` is red although it admits only 3.14 and up); a job that never installs the project, which
  runs old Python unnoticed; `uv sync` behind an older setup-python, which fetches 3.14 and leaves a stale setter; and a
  Python project without a `pyproject.toml`.

  Ruled by the operator: every tracked `pyproject.toml` that `test-layout` finds as a project is judged, the root and
  each member alike.
- `python-security` judges every tracked regular `.py` file in any directory with the `S` codes of quality's own ruff
  (SECURITY-1) (an exact pin in quality's `pyproject.toml` that serves its own lint too): the ruff binary of quality's
  installation, never one on `PATH` (SECURITY-2), at the pinned version, runs `ruff check --isolated --no-cache
  --ignore-noqa --select S,T20 --target-version py314 --output-format json -- <files>`, in batches below the argument
  limit (SECURITY-3), and writes nothing into the checkout (SECURITY-4). That one run serves `python-print` too. With no
  tracked `.py` file ruff is not started (SECURITY-5). A clean `code` row counts what ruff judged
  ([area rows](output.md#area-rows)). Not judged: untracked files, a link, anything inside a gitlink, notebooks, `.pyi`
  files and suffixless scripts (SECURITY-6). The repository's own ruff `select`, `ignore`, `per-file-ignores`,
  `exclude`, `extend` and noqa have no effect (SECURITY-7). Each finding stands as a [row](output.md#rows) under the
  `code` row, `! src/a.py:12  insecure  S307 Use of possibly insecure function; ...` (SECURITY-8), and its `fix:` names
  the line its noqa must stand on, ruff's `noqa_row` (SECURITY-9). In the files `test-import` judges (every `.py` under
  a Python project's `tests/`, outside `fixtures/`) S101, S105 to S107, S603 and S607 are exempt (SECURITY-10); S108 (a
  shared path breaks test isolation), S310 and every other code stay red there (SECURITY-11), and `tests/fixtures/` and
  `src/` get no exemption (SECURITY-12). A finding is green only when the line at ruff's `noqa_row` (the last line of a
  multi-line string) carries `# noqa: <CODE>[, <CODE>...] - <reason>` naming its code (SECURITY-13); the reason
  is the text after ` - ` up to the next `#` or the end of the line, holds a letter and no comma (SECURITY-14) (Sonar's
  noqa grammar splits on commas, so a comma raises python:S7632 for every caller that runs Sonar). Every other form is
  red: no reason, a blanket `# noqa`, another directive as the reason (`# type: ignore[x]  # noqa: S607`,
  `# noqa: S607  # pragma: no cover`), `# noqa: S607: reason`, a wrong code, another line, a file-level `# ruff: noqa`
  (SECURITY-15), and a second directive behind a reasoned one, bare or reasoned (`# noqa: S603 - why  # noqa: E501`),
  since ruff reads only a line's first directive (SECURITY-16). ruff itself honours the reasoned form, so a caller's own
  ruff stays quiet (SECURITY-17). A caller whose ruff selects none of a family (S or T20) lists exactly that family in
  `lint.external`, which keeps its RUF100 from calling the gate's noqa unused; a caller whose ruff selects a family (for
  example `select = ["ALL"]`) leaves it out, because its ruff honours each noqa itself and ignores a file-level
  `# ruff: noqa` for a code listed in `external`.
  A file ruff cannot parse makes the `code` row [`✗ not checked`](output.md#refusals) with `ruff cannot parse <path>`
  (exit 2) (SECURITY-18); a tracked `.py` file the checkout does not hold (deleted, left out by a sparse checkout, or a
  link to nowhere) (SECURITY-19), a file Python cannot decode (SECURITY-20), or reads as other text than ruff reads as
  UTF-8 (a PEP 263 cookie such as utf-7) (SECURITY-21), a missing or mismatched ruff, ruff exiting other than 0 or 1
  (SECURITY-22), a `Failed to lint` warning (SECURITY-23), a report quality cannot read (SECURITY-24), a time-out
  (SECURITY-25) or a file with a finding whose comments Python's tokenizer cannot read (SECURITY-26) is `cannot judge`
  (exit 2). One such file makes the whole rule `cannot judge` or `unparsable`, so the S findings of the other files are
  not printed in that run (SECURITY-27); everything here but the tokenizer refuses the shared run, so `python-print`
  refuses with it (SECURITY-28). The other rules are judged either way. A deliberately invalid Python fixture takes
  another suffix, since a `.py` file is Python. No configuration switches the rule off, and a reasoned noqa asks
  `quality verdict` for no `[[gate.loosened]]` entry. Not judged, and waiting for usage evidence: notebooks, `.pyi`
  files and suffixless Python; judging a non-UTF-8 source (a latin-1 file with non-ASCII bytes stays `cannot judge`),
  since ruff reads UTF-8 only; a stale reasoned noqa, which ruff's RUF100 finds.

  Ruled by the operator: the rules are red from the release that ships them, with no report-only phase; `assert` (S101)
  is exempt in tests; a rule-specific noqa with a reason is green.
- `python-print` judges `T201` (`print`) and `T203` (`pprint`) (PRINT-1) from `python-security`'s run, over the same
  files (PRINT-2), with the same deafness to the repository's ruff settings and the same refusals: a run quality cannot
  trust or a file ruff cannot parse leaves both rules `cannot judge` or `unparsable`. Each finding stands as a
  [row](output.md#rows) (``! scripts/sync_agents.py:12  print call  T201 `print` found``) (PRINT-3), a print to
  `sys.stderr` included (PRINT-4), and tests get no exemption (PRINT-5). A reasoned `# noqa: T201 - <reason>` at
  `noqa_row` clears one line (PRINT-6), as for `python-security`. A program's one output owner declares itself with a
  file-level `# ruff: noqa: T201[, T203] - <reason>` on a line of its own (PRINT-7), the form ruff honours too
  (PRINT-8), clears the codes it names in its own file only (PRINT-9); its reason holds a letter and no comma
  (PRINT-10), and the comment holds no second `#` (PRINT-11). A bare `# ruff: noqa`, one without a reason, a comma in
  the reason, one naming an S code (`# ruff: noqa: S603, T201 - reason`), one behind code on its line and
  `# flake8: noqa` clear nothing (PRINT-12), and no file directive clears an S code (PRINT-13).
  The owner line is a tool directive, so it does not satisfy `script-header`: a script keeps its comment or docstring
  first and the owner below it, as `scripts/verify_release.py` does. Each honoured owner stands as a
  [row](output.md#notes) under the `code` row, after its findings,
  `· scripts/verify_release.py:14  allowed  output owner · <reason>` (PRINT-14), and a clean row counts it
  (`· 1 allowed`); an owner that names no code found in its file is an `unused` row on its line, to delete (PRINT-15).
  python-print reads the comments of each file with a print finding or the text `# ruff:` (PRINT-16), and one whose
  comments Python's tokenizer cannot read leaves python-print alone `cannot judge`.
  No configuration switches the rule off, and an owner or noqa asks `quality verdict` for no `[[gate.loosened]]`
  entry. Not judged, and waiting for usage evidence: `sys.stdout.write` and `sys.stderr.write` (ruff's TID251 would
  flag every reference to the streams, `logging.StreamHandler(sys.stderr)` included, for a bypass nobody has used;
  reopened when a landing outside `tests/` replaces a `print` with a direct stream write), and `print(...,
  file=<variable>)`, which ruff does not report as T201.

  Ruled by the operator: a program's one output owner declares itself in its code, never in a path list.
- The baseline only falls: on a pull request `quality verdict` holds the head's `quality/baseline.toml` against the
  base commit's, so growing a function and rewriting the baseline stays red. Each loosening is one line under the
  verdict's `loosenings` row, named with the baseline's own metric, path and symbol (a file entry has no symbol):
  - an entry raised: `size baseline raises function_lines src/quality/cli.py main 82 -> 90`;
  - an entry the base does not hold (a function or class under another name included; a moved file is judged as below):
    `size baseline adds ccn src/a.py f 16`;
  - a clone pair the base does not hold, when the base has a `[clones]` table:
    `clone baseline adds tests/test_a.py run <-> tests/test_b.py check`;
  - the base's `[clones]` table gone from a head that still has the file: `clone baseline removed`;
  - the base's file deleted: `quality/baseline.toml removed`.
  - a `[[size.generated]]` declaration is judged as in [gate](gate.md), "Declared loosenings".

  Ruled by the operator: a file git recognises as renamed, a directory move included, is no loosening when its numbers do
  not rise.

  The verdict reads the renames between the base and the head commit with `git diff -M50%
  -l1000` (git's default similarity, whatever the repository's git configuration says; a file git does not pair is new
  as before). The base's entries and clone sides under the old path then move to the new path, so the head is compared
  with the entries its predecessor held and a raise or an added symbol still names the head's path; an entry the base
  holds under the new path is a leftover and does not count. A rename from a path that is not UTF-8 moves nothing, since no baseline key can name it, but still clears
  its new path of leftovers; a rename to a path that is not UTF-8 is not read. The allowance moves rather than being copied: while the
  head baseline still names the old path, nothing moves and the new path's entries are adds. A pair whose files moved
  is the same pair, and a renamed function is a new entry. Renames are read only when the base and the head both hold a baseline: without a base, or without a baseline on
  either side, nothing is read. `merge` fetches the same two commits and reads the same renames. `check` names entries and pairs by their current path; `check --fix` follows the renames since the ceiling's last commit.

  A lowered, equal or removed entry or pair is green, and so is adopting a baseline (the base has none) or having none
  on either side. A `[clones]` table the base lacks is an adoption too: its pairs are not compared, while the size
  entries are still held against the base's. A malformed baseline on either side, a head config or baseline that cannot be read or links out of the
  repository, a base file that is not a regular file (`... : not a regular file`), a base git refuses to read (`... : git exited <n>`, git's own words never printed), or a base commit the clone lacks is exit `2`, naming the side (`quality/baseline.toml at the base commit
  <sha>: ...`, `quality/config.toml at the head: ...`). Only the head's malformed baseline adds `restore quality/baseline.toml from git, or repair it by hand`
  (an unknown table adds `run the quality release your gate pins` instead): a pull request cannot rewrite its base. The base must be a ref
  name or a SHA: `HEAD~1` or `-x` is exit `2` (`... is not a ref name or a SHA`) before git runs. Outside a pull
  request (push, `merge_group`, `workflow_dispatch`) `gate.yml` passes no base, so the base is never read and only
  `check` holds the tree. A lizard update that measures a function higher therefore turns the pull request that
  takes it red; a ruled raise is declared (see [Declared loosenings](gate.md#declared-loosenings)) and stays red until the
  operator's door merges it.
- The import contracts only tighten: on a pull request `quality verdict` and the door compare the contracts of the
  three config places at the head with the base's, as import-linter parses them (never the raw options, so moving a
  config between `.importlinter`, `setup.cfg` and `pyproject.toml` changes nothing), without building an import graph.
  A contract is the same on both sides when it has the same `name`; a rename is a removal plus a new contract.
  Each loosening is one line under the verdict's `loosenings` row, sorted:
  - a contract the base holds and the head lacks: `import contract "layers" removed`;
  - the base's config gone, whatever it held, or only its section gone from a file that stays, once:
    `import-linter config in .importlinter removed`;
  - an `ignore_imports` expression the base does not hold, one line each, compared as parsed (spacing around `->`
    does not count, a wildcard is a new expression):
    `import contract "domain" ignores app.domain -> app.web.*`;
  - any other field of a contract that differs, `broken_contract_guidance` excepted, unless the change only forbids
    more (below): `import contract "layers" changes allow_indirect_imports` (a changed `type`
    reads `changes type`; a module dropped from a list reads as that list's field);
  - `root_packages` (as a set of names), `include_external_packages` or `exclude_type_checking_imports` changed:
    `import-linter changes root_packages`.

  Ruled by the operator:

  - Each of these loosenings is red, can be declared with a `[[gate.loosened]]` entry whose `what` is its line exactly,
    and lands only through the operator's door (LOOSE-1).
  - The door reads the import contracts at the merge state and at the base itself and refuses an undeclared loosening,
    even when it was opened for Sonar's legacy findings (LOOSE-2).
  - Removing the whole config file can be declared as well and goes through the door (LOOSE-3).

  Ruled by the operator: a change that can only forbid more is no loosening. Per contract type: `allow_indirect_imports` turned from true to false (also by dropping the line)
  and a module added to `source_modules` or `forbidden_modules` of a `forbidden` contract, the base's expressions all
  kept; a module added to the `modules` of an `independence` contract, the base's all kept; and a module newly placed
  in a `layers` contract: the head's `layers` are compared with every module
  name the base's `layers` do not name stripped, and a layer left empty dropped. Base layers `a`, `b` against head
  `a`, `new`, `b` is green. A layer left with one module has no sibling, so its independence says nothing: base `(game)`
  against head `(game) : (agent)` is green, and base `a | b` against head `a : b : new` stays a loosening. Everything else stays a loosening: a removed, renamed or replaced expression (`shop.web`
  turned into `shop.*` included), a module dropped because it was deleted, an expression added and another removed in
  one field, `allow_indirect_imports` turned from false to true, `as_packages`,
  `unmatched_ignore_imports_alerting`, `containers`, `exhaustive`, `exhaustive_ignores` and every `protected` or
  `acyclic_siblings` field in either direction, a reorder, a merge into `a | b`, a module moved to another layer,
  `a | b` turned into `a : b`, a required `a` made optional `(a)`, and the session options. No import graph is built.

  A new contract, a removed `ignore_imports` entry, a reworded `broken_contract_guidance` and adopting import
  contracts (the base has no config) are green. Two contracts of one name on a side, two config places holding a
  section, or a config that cannot be read are exit `2`, naming the side (`import-linter config at the base commit
  <sha>: ...`, `import-linter config at the head: ...`).

The `secret` rule scans the whole history reachable from `HEAD` with gitleaks (a key removed later is still found; other branches the checkout fetched are not scanned), always with quality's own config
(`src/quality/gitleaks.toml`): the repository's `.gitleaks.toml`, `.gitleaksignore` and `gitleaks:allow`
comments have no effect. A known false positive is allowed by the value, not the place: the repository's
`quality/config.toml` names the gitleaks rule, the file and the SHA-256 of the finding's secret (UTF-8, lowercase hex)
with a reason:

    [[secrets.allow]]
    rule = "aws-access-token"
    file = "tests/fixture.py"
    secret_sha256 = "<64 hex>"
    reason = "fixture value"

A file that lists `allow = [...]` inline reads the same and takes a new entry as one more inline table, because TOML
refuses a `[[secrets.allow]]` block after an inline list.

One entry allows that value in that file at any line and in every commit that holds it. Another value in the same
place is red, and the same value in another file is red. A red finding is the row
`! <path:line>  secret  gitleaks rule <rule> · commit <sha8> · value not shown` (in the staged change without the
commit). Under it stands `fix: rotate it; if it is no secret, allow it in quality/config.toml with a reason:` and the
entry's lines, the sha256 whole on its line, so the entry is copied from the output with a reason added. An entry
without a reason, a hash that is not 64 lowercase hex, a duplicate `(rule, file, secret_sha256)` and the old
`fingerprint` form are exit `2`. The clean row counts the commits reachable from `HEAD` that gitleaks scans
(`✓ secrets  clean  412 commits`); a count git cannot give is exit `2`.

A missing `quality/config.toml` means no allowances: the scan still runs and its findings are red. Only a file that
exists and cannot be read or parsed, or links out of the repository, is exit `2`; a link that leads nowhere or loops
exists, so it is exit `2` too.

Secret handling: the secret scan runs gitleaks without `--redact` so the report carries each `Secret` to be hashed. gitleaks
writes that report to a `tempfile` scratch directory (mode 0700); quality reads it, keeps only the hash, and deletes
the directory on every exit path (success, gitleaks failure, parse error). The value reaches no stdout,
stderr, log, exception text or retained file; gitleaks itself runs without its `-v`. While the scan runs, the value exists in
that report file and in quality's process memory. The boundary is the deletion and the absence of any other sink,
not memory-only handling. The hash is safe to print: it appears only where the history already shows the content,
and a real secret must be rotated anyway. gitleaks' scan stderr may echo scanned values, so it is shown only as the vet allows ([the vet](output.md#the-vet)); the missing-report and unexpected-exit refusals keep their exit code without those diagnostics.

The scan runs git without any attributes (not the repository's `.gitattributes`, not `info/attributes`, not the
system or global file), so a `-diff` or `binary` line cannot hide a key. That needs git 2.41 or newer; an older git
is exit `2`. The system file is excluded by `GIT_ATTR_NOSYSTEM`, git's documented switch; that is pinned as an
environment contract, not by a root-only real-file test.

Every allowed finding is a row `· <path:line>  allowed  <rule> · <reason>`, and the clean row counts them
(`· 1 allowed`). An allowance that matched nothing is a row too, so a stale entry can be deleted:
`· quality/config.toml  unused  [secrets] allow entry <n> matches no secret · <rule> in <file>`. `quality check secrets --staged` prints the allowed rows and no unused row: one change cannot tell a stale entry from one the
history still needs.

The `workflow-lint` rule (actionlint) and the `workflow-security` rule (zizmor, `--offline`) judge every tracked `.yml` and
`.yaml` file directly under `.github/workflows/` that the tree holds, with quality's own configs: a repository's scanner
config and ignore comments change nothing; quality's configuration adds the hosted runner labels `ubuntu-26.04` and
`ubuntu-26.04-arm`, which actionlint does not know yet, and accepts GitHub's `uses: $/…` form of a local action or reusable
workflow, which actionlint does not know yet either. `workflow-security` also judges every tracked `action.yml` and
`action.yaml`, wherever it lies; actionlint is never handed one, and the clean row counts the workflows alone. A lint finding is
`! <path:line>  invalid  actionlint`, the message under it behind `| `, then `fix: correct the workflow as the message says`.
A security finding is `! <path:line>  insecure  zizmor <audit> · <description>` with `fix: see <url>`.

Ruled by the operator: the clean row counts those files and says in the row itself, and in no note of its own, that only offline audits ran, so a
workflow that only an online audit would flag is not read as fully audited:
`✓ workflows  clean  5 workflows · offline audits only`.

The `advisory` rule judges every tracked `uv.lock`, `pnpm-lock.yaml` and `requirements*.txt` (a name that starts
with `requirements` and ends in `.txt`; `requirements.in` and `dev-requirements.txt` are not), root
and nested, with osv-scanner against the OSV advisory database (dev dependencies count). `uv.lock` and the
requirements files are judged against the PyPI database, `pnpm-lock.yaml` against the npm database. A
`package-lock.json`, `yarn.lock` or `bun.lock` is not judged yet.
A repository with no tracked lock needs no
osv-scanner (and none is located); a database is downloaded only for an ecosystem with a tracked lock, once per run however many locks it has.
A lock that is tracked but missing on disk is skipped;
one that links out of the repository, is not a regular file or is empty (zero bytes) is exit `2`. A lock listed in `.gitignore` is still judged.
The clean row counts the lock files it judged and the packages osv-scanner lists for them: `✓ dependencies  clean  1 lockfile · 63 packages`, and `0 lockfiles · 0 packages` with none.
A package counts once per tracked file, by its ecosystem, its name as the ecosystem compares it (Python's rule for PyPI, exact for npm, so `lodash.merge` and `lodash-merge` are two) and its version, so a requirements file's pin and the wheel line for the same package are one; every tracked name counts, so a lock reached by two names counts for each.
A finding is one per advisory group, package and lock file, named by the tracked relative path:
`! uv.lock  affected  pyyaml 5.3.1 · GHSA-8q59-q68h-6hv4 · fixed in 5.4` with `fix: uv lock --upgrade-package pyyaml`.
The row names the group's first id; an allowance matches every id and alias of the group.
For a nested lock the fix names its directory (`in speech/: uv lock --upgrade-package ...`). Several fixed versions are
listed as osv lists them. An advisory with no listed fixed version, a package listed as malicious included, says
`no fixed version listed` with `fix: remove or replace <package>; see https://osv.dev/vulnerability/<id>`. Where osv's
record names no such package the row says nothing of a fix, with `fix: see <url>`.
The fix prints a command only where it works as printed: for `pnpm-lock.yaml` it says to upgrade each listed package, or its
parent when it is transitive, or set a pnpm override in `package.json`; for a `requirements.txt` it says to raise each
listed package's pin, and to compile the file again when it is generated. A package name that needs quoting prints quoted
in the command; one that no shell word can hold (a control or format character) gets `fix: see <url>`, or
`in <dir>/: see <url>` for a nested lock, so no command is printed that would not work. npm names match as written (`lodash.merge` is not
`lodash-merge`); Python's name rule applies to PyPI only. A repository's own `osv-scanner.toml` (ignored advisories,
package overrides), its `package.json` `pnpm.auditConfig`, its `.npmrc` and its `.gitignore` change nothing: the gate
passes its own empty `src/quality/osv-scanner.toml` and each tracked lock explicitly, so only a real fix in the lock
(an upgrade or an override) turns a finding green.

An advisory the repository cannot fix is allowed in its own `quality/config.toml`, one entry per advisory and lock (ALLOW-1):

```toml
[[advisories.allow]]
id = "GHSA-462x-pgg6-55fp"
lock = "speech/providers/qwen/uv.lock"
until = 2027-01-31
date = 2026-10-21
item = "overnightworks/agent-presentator#371"
reason = "gradio caps pillow<13; no image input"
```

- `id` is any id of the advisory group, GHSA, PYSEC or CVE alike (ALLOW-2). `lock` is the tracked relative path; the entry
  allows the advisory on every package it affects in that lock and in no other, so a lock reached by two tracked names
  needs an entry per name (ALLOW-7). Two entries matching one group in one lock (its GHSA and its CVE id) are a finding at
  `quality/config.toml` naming both, and the group is allowed by neither:
  `! quality/config.toml  conflict  <id> and <id> in <lock> match one advisory · none allows it`.
- `until` is the re-check date and `date` the day of the ruling, both TOML dates without a time (ALLOW-3); `item` and `reason`
  are as in a [declared loosening](gate.md#declared-loosenings). The allowance holds through `until` (in UTC) and the advisory
  is red again from the next day: its row ends in `· allowance ran out on <until> (<item>)` (ALLOW-4). quality sets no latest `until`, and an advisory with no fixed
  version is allowed like any other.
- Each allowed advisory stands as a row under the dependencies row, after the area's findings and their `fix:`, and the rule stays clean (ALLOW-6):
  `· <lock>  allowed  <package> <version> · <id> · fixed in <versions> · until <until> (<item>) · <reason>`. The fact says what the `affected` row says of a fix. A clean row counts them (`· 1 allowed`).
- An entry no reported advisory matches is a finding (ALLOW-5): `! quality/config.toml  unused  <id> in <lock> matches no
  advisory`. Deleting it is no loosening. With no tracked lock every entry matches nothing.
- A malformed entry (a missing or extra key, an empty or multi-line `id`, `lock` or `reason`, an `item` that is not
  `#<n>` or `<owner>/<repo>#<n>`, an `until` or `date` that is text or carries a time, two entries for one `id` and
  `lock`, or an unknown key in `[advisories]`) is exit `2` in the `advisory` and `secret` rules and in `verdict`,
  naming the entry by its position (`[[advisories.allow]] entry 2`); `check secrets --staged` reads only `[secrets]`.
- A new allowance, and one whose `until` moves later, is a [declared loosening](gate.md#declared-loosenings) the allowance
  makes itself, so only the operator's door merges it. The pull request writes the allowance, so its item, date and
  reason prove nothing alone: the verdict's red and the door are the gate. An `id` that is an alias covers every
  group carrying it in that lock, while the door names the `id` and the lock, not the packages.
  Why only the operator's door merges it stands in [gate.md](gate.md#declared-loosenings). The entry's keys, the alias,
  the date format, the expiry line, the unmatched allowance, the adoption (#116 E8), no latest `until`, the printed
  lines, an advisory without a fixed version and the reach of an allowance are defaults the operator never answered.

Ruled by the operator:

- An exception stands per advisory in `quality/config.toml`, with a reason, an item and a date.
- #116 R2: a new exception is a loosening: the pull request stays red until the operator's door merges it.
- From the merge on, the advisory is green until the date and red again after it (ALLOW-8).
- No agent can silence an advisory alone.
- A pull request cannot move the re-check date later without the operator: a later `until` for an existing exception is
  a loosening like a new one.
- A new exception declares itself: it needs no separate `[[gate.loosened]]` entry.

quality downloads each needed database itself on every run (PyPI about 37 MB, npm about 219 MB;
`https://osv-vulnerabilities.storage.googleapis.com/<ecosystem>/all.zip`)
into the scan's own 0700 scratch directory and runs osv-scanner `--offline` on it, so osv-scanner makes no network call
and reads no cache of its own. A database that cannot be downloaded within 300 seconds in total for all downloads together (connect, headers and body, however the
server drags them out), cannot be placed in the scratch directory, is not a valid zip, or holds
no advisory is exit `2`, never clean, naming the database; so are an osv-scanner that is missing, at another version, exits other than `0`
or `1`, runs past its time limit, or writes a report that is not the expected JSON, disagrees with its exit, or names
a lock file quality did not pass. Trust boundary: the database is an unsigned zip fetched over TLS from the same source
osv-scanner itself uses; there is deliberately no cache, because a doctored cached database would silence every pull
request. Records the pinned osv-scanner cannot read are dropped by it silently; the live-database test for
`pyyaml 5.3.1` on every quality pull request is the canary. osv-scanner judges the lock as written; the caller's own
`uv sync --locked` guards against a hand-edited lock. osv-scanner exits `127` on a `uv.lock` or `pnpm-lock.yaml` it cannot parse, which is `cannot judge`, never clean.

The check job in `gate.yml` stops at 25 minutes: the 300-second download deadline and the 600-second scan limit take
15, and setup and the other rules get the remaining 10, so quality's own `cannot judge` shows before GitHub cancels the job.
Measured with osv-scanner 2.7.0 on 12 cores (the download at load 17 to 20): the npm archive is 219,026,863 bytes (230,735 files), the PyPI archive 37,064,024 bytes
(26,612 files); downloading npm took 34 s; a whole `check` run over a repository with one `pnpm-lock.yaml` and one hashed
`requirements.txt` (one pin) under `--all-packages`, both databases included, took 63 s and 69 s wall and 640 MB resident (load 3 to 5).
A `uv.lock` is judged against the same PyPI database; that setup was not measured.

A requirements file is read by quality itself (`pip_requirements.py`), because osv-scanner skips a line it cannot parse and
still reports clean. Exactly two logical forms are accepted, after comments are dropped and `\` continuations joined:
`name[extras]==version`, with an optional `; marker` and `--hash=` tokens, and `name[extras] @ https://.../<name>-<version>-....whl`
with at least one `--hash`, where the wheel's distribution name equals `name` (PEP 503). Any other line (`-e`, `>=`, `~=`, a bare
name, `-r`, `-c`, `--index-url`, any other option, a git or sdist URL, a wheel without a hash or named for another package) is exit `2`,
naming the file and the line (`.github/requirements-build.txt: line 4 is neither ...`); a minimum version is refused because pip
installs the newest one, so judging the lowest could hide a hole. osv-scanner reads the file as written under `--all-packages`;
the wheel lines, which it skips, reach it as a scratch file of `name==version` lines judged under the same tracked name, so a
finding on a wheel is named by the tracked file and an allowance's `lock` is that file's path. The packages osv-scanner lists for
each file must equal the pins quality read: a pin it did not list is exit `2` (`osv-scanner did not read line 4 of <file>`), and so is
a package it lists that quality did not read. The grammar is an allowlist; `-r`, `-c`, `--index-url`, `-e`, git and sdist URLs and ranges
wait for a caller that needs them. An advisory database zip with an entry that is not valid JSON is exit `2` too.

Ruled by the operator:

- An `[[advisories.allow]]` entry for an npm finding is the same entry with `lock` set to the file's tracked path
  (`id = "..."`, `lock = "frontend/pnpm-lock.yaml"`); it is a declared loosening and merges only through the operator's
  door like any other (NPM-1).
- Nothing in the repository's own files silences an advisory; only a real fix in the lock, an upgrade or an override,
  counts (NPM-2).
- #145 R3: open npm advisories keep the bump pull request red.

When a repository's frontend and its lock are gone, the npm database is no longer downloaded and an
entry for it is a finding until it is deleted. A repository that falls back to an older quality leaves such an entry
unmatched and red likewise. The predecessor `pnpm audit --prod` steps are retired by each repository's own head.

Every rule is judged, even when another cannot be (CHECK-4), and gives one verdict: clean, findings, unparsable, or
cannot judge (CHECK-5). Each finding names the file, a fact and a fix. What `quality check` prints, its frame, the
masking, `-v` and the vet, is [the output contract](output.md).
