# Rules

Reference for the engineer or operator who needs the exact behaviour of `quality hygiene` and its output contract.

`hygiene` reads git's view of the repository `PATH` (default `.`) lies in, from its top level, so a run from `src` judges
the same files and the same `quality/baseline.toml` as one at the top, and names every file relative to the top (`size
src/big.py`). A `PATH` in a submodule or a linked worktree means that work tree's own top. It applies these rules:

- `env-file`: no tracked `.env`, `.env.*` or `*.env` file (names ending in `.example` are templates and pass).
  The fix is `git --literal-pathspecs rm --cached -- <name>`, the name quoted for the shell, so a copy untracks
  exactly that file; a name that is not printable UTF-8 gets the hint to remove it by its bytes (`git ls-files -z`
  shows them) instead of a command. The fix runs exactly as printed, with no home-made encoder for the name
  (operator ruling 19.10.2026: „Immer exakt (empfohlen)“).
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
  a number is a finding. A service with `network_mode: host` publishes whatever its process binds, so it is a finding
  too, and so is a `network_mode` that holds a Compose interpolation (`${...}`), which may resolve to `host`; `none`,
  `service:x` and any other literal are clean. A service is declared in `quality/config.toml`, a table of its own
  beside `[[layout.allow]]`, with the exact tracked path of its compose file, the service name and a one-line reason:

  ```toml
  [[compose.allow]]
  file = "compose.yaml"
  service = "traefik"
  reason = "it terminates TLS on the host's own interfaces"
  ```

  A declared service passes the host check only; its ports are still judged. A declaration that matches nothing is a
  finding on its file: the file is not tracked or not a compose file, it lacks the service, or the service is neither
  host-networked nor interpolated there; the fix is to delete the entry. A duplicate `(file, service)`, a folder (a
  trailing `/`), a pattern (`*`, `?`, `[`), `..`, an absolute path, an unknown key, a missing key or a service or reason
  that is not one-line text is `hygiene: compose-port cannot judge - ...` with a `[[compose.allow]]` example, and a
  malformed other table in `quality/config.toml` is forwarded with its validation error. A declaration is no loosening,
  so `quality verdict` asks for no `[[gate.loosened]]` entry. A config reader older than the rule refuses `[compose]`
  as an unknown key. (#45 row Q1b-3g, operator ruling 19.10.2026: „Erst Doku, dann Erlaubnisliste (empfohlen)“)
- `size`: every tracked file of Python, TypeScript (`.ts` `.tsx` `.mts` `.cts`), JavaScript (`.js` `.jsx` `.mjs`
  `.cjs`), Svelte or Vue is measured by its lines, and every tracked `*.py` file also with
  [lizard](https://github.com/terryyin/lizard): a file from 800 lines, a Python function from 60 lines and a Python
  function with a CCN above 15 are debt, counted apart for production and tests (a path under `test/` or `tests/`;
  Python also `conftest.py`, `test_*.py`, `*_test.py`; TypeScript and JavaScript also `__tests__/`, `*.test.*`,
  `*.spec.*`, `*.test-d.*` and `*.spec-d.*`). A minified or generated file is counted by its lines like any other.
  Functions of the other languages are not measured: lizard misplaces their boundaries
  ([terryyin/lizard#521](https://github.com/terryyin/lizard/issues/521)). Functions are named by their qualified
  symbol (`Class.method`); a second one of the same name, such as a property setter, is `Class.method#2`. Without
  `quality/baseline.toml` the rule only prints the counts (`size: report only (no quality/baseline.toml) - production:
  1 file(s) >= 800 lines, ...; languages: Python, TypeScript (functions in Python only)`, the languages largest
  first) and the size debt leaves the exit unchanged.
  A `.py` file is read the way Python reads source (a PEP 263 coding cookie or a UTF-8 BOM decides the encoding). One
  Python 3.14 cannot decode or parse is named as not valid Python: without a baseline the count notes it (`1 file(s) not
  valid Python, functions unmeasured: bad.py`) and the size verdict is unchanged, while `python-security` judges
  the file unparsable or cannot judge it (exit `2`); with a baseline it is `hygiene: size
  unparsable - bad.py is not valid Python (...)`, and `quality baseline write` refuses with `baseline: not written -
  bad.py is not valid Python (...)`; both exit `2`. A tracked file the operating system refuses to read is `hygiene:
  size cannot judge - ...` with or without a baseline, and `quality baseline write` refuses with `baseline: not written - ...`; both exit `2`.
  The reason names the case: a file without read permission is `cannot read locked.py: [Errno 13] ...`, a file behind
  a directory without permission is `cannot reach pkg/m.py: [Errno 13] ...`. A tracked file missing from disk
  (deleted, or a dangling symlink) is skipped. A `quality/baseline.toml` that is not UTF-8 is malformed like invalid
  TOML and says to rewrite it with `quality baseline write`. A baseline with a table this quality does not know
  (`has an unknown table [future]; quality <version> knows ccn, file_lines, function_lines, clones`) is not told that:
  a newer quality may have written it, so the head is told to write the baseline with the quality release its gate pins.
  With `quality/baseline.toml`, the tree on disk must hold the baseline: a new violation, an entry that grew and an
  entry above its measured value (stale: lower it with `quality baseline write`) are findings; an entry whose file or
  function is gone is fine.
  `quality baseline write [PATH]` writes the measured debt and the clone pairs to `quality/baseline.toml` at the top of the
  repository `PATH` lies in (from `src`: `baseline: wrote 2 entries and 1 clone pair to ../quality/baseline.toml`; a stray `src/quality/baseline.toml` is
  neither read nor removed) as sorted TOML, one table
  per metric (`ccn`, `file_lines`, `function_lines`) keyed by path and qualified symbol, then a `[clones]` table that holds each pair once, under its alphabetically first
  side (`[clones."tests/test_a.py"]`, `run = [["tests/test_b.py", "hygiene"]]`), also when it is empty. Before its
  `baseline: wrote N entries and M clone pairs ...` line it names, in the verdict's wording, each entry it raises or adds
  against the previous file (`baseline: raises function_lines src/quality/cli.py main 82 -> 90`, `baseline: adds ccn
  src/a.py f 16`) and, when the previous file has a `[clones]` table, each new pair (`baseline: adds clone
  tests/test_a.py run <-> tests/test_b.py hygiene`); a lowered or removed entry or pair is not named; without a
  previous file only the count prints, and a previous file without `[clones]` names no pair but still names its size raises and adds. A previous file it cannot read is
  overwritten with `baseline: the previous quality/baseline.toml could not be read (...), so raises cannot be named`,
  but a symlink on its way is never written through: `baseline: not written - cannot write quality/baseline.toml: a
  symlink on its way would redirect the write`, exit `2`, as for any write the system refuses.
  The write exits `0` with raises too: the verdict is the gate.
- `clone`: a pair is two functions (methods and nested functions too) in two different tracked `*.py` files, each of at
  least 40 tokens without its docstring, whose structure is at least 90 % the same once every name and literal is
  renamed; tests count like code, and a copy inside one file is no pair. A pair is named by the path and qualified
  symbol of both sides, as `size` names a function; a pair counts as tests only when both files are tests. Without a
  `[clones]` table in `quality/baseline.toml` the rule only prints the counts (`clone: report only (no [clones] in
  quality/baseline.toml) - production: 1 pair(s), tests: 0 pair(s)`). With one, even an empty one, a new pair is a
  finding at its second side (`clone tests/test_b.py:31 - function hygiene is a near copy of tests/test_a.py run (new
  pair).`, fix: give the shared body one owner and call it, or make the cases one parametrized test) and so is a held
  pair the tree lost, even when a side was deleted (`clone tests/test_a.py - the baseline holds the pair tests/test_a.py
  run <-> tests/test_b.py hygiene, which is gone (stale).`, fix: `quality baseline write`); an enforced rule prints no
  report line. A Python file that cannot be parsed may hide a pair, so with a `[clones]` table it is `clone
  unparsable` (exit `2`), as for `size`. The baseline is read once for both rules: one that cannot be read leaves both
  `cannot judge` (exit `2`), also when it has no `[clones]` table, since the mode is then unknown.
  quality owns this detector instead of calling jscpd or lizard (operator ruling 21.10.2026: „aco-Prüfer übernehmen
  (empfohlen)“); the reasons stand in [decision 3](decisions/3-clone-detector.md). The threshold, what makes a pair, how
  it is named, the report-only mode and the printed lines were shown to the operator and left unanswered, so they stand
  at the proposed default and are not his word (#113, O2 and E1 to E7). E2 stays for the rule itself: a renamed file or
  function orphans the held pair and names a new one. Only the verdict's comparison with the base treats a pair whose
  files git renamed as the same pair (see the baseline paragraph below).
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
  On a pull request `quality verdict` and the door also hold the head's import contracts against the base's (see
  "The import contracts only tighten" below).
- `claude-md`: a tracked `CLAUDE.md`, in any case (`claude.md` is named as it is), holds exactly `@AGENTS.md` with or
  without a final line feed, so Claude, Codex and Grok read the same guidance, and the repository tracks an `AGENTS.md`
  beside it. No `CLAUDE.md` is clean, with or without an `AGENTS.md`. A symlink is a finding (a dangling one too), and so
  is a tracked `.claude/CLAUDE.md`, which Claude Code reads as project memory and Codex and Grok never read. Any other
  bytes are a finding, a Windows line ending included; a nested `docs/CLAUDE.md` is not judged. The reason and the fix
  are fixed prose that names the file and never shows its content; every fix is prose with no command, and for a file's
  own text it says to move the lines into `AGENTS.md`. A tracked file missing from disk is skipped; one the filesystem refuses to read
  is `hygiene: claude-md cannot judge - ...`. No configuration switches the rule off.
  (operator ruling 20.10.2026: „ich will kein auseinanderlaufen von claude.md und agents.md!“; answers of 21.10.2026:
  the finding names only the file, „Nur Dateiname (empfohlen)“, and the fix is a sentence, „Satz, kein Befehl
  (empfohlen)“)
- `root-layout`: every entry directly in the root of the tracked tree is on quality's base list or declared. An entry is
  a file or a directory (written `frontend/`); a directory with many files is one entry. Names compare in exact case, and
  the finding for a name that differs only in case names the base-list spelling. A file and a directory of one name are
  two entries, and a root symlink counts by its own name. Only tracked files count; a stray left on disk is not judged.
  The base list: `README.md AGENTS.md CLAUDE.md HEART.md LICENSE .gitignore .gitattributes .gitmodules pyproject.toml
  uv.lock .python-version package.json pnpm-lock.yaml package-lock.json Cargo.toml Cargo.lock rust-toolchain.toml
  Dockerfile .dockerignore compose.yaml compose.yml docker-compose.yaml docker-compose.yml sonar-project.properties
  .importlinter .pre-commit-config.yaml` and the directories `.github/ .aco/ quality/ src/ tests/ docs/ scripts/`. It
  belongs to quality and changes with a quality version; a repository extends it, never shortens it. Each other entry
  is declared in `quality/config.toml`, one entry and one one-line reason, no pattern and no deeper path:

  ```toml
  [[root.allow]]
  entry = "frontend/"
  reason = "the web client, built apart from the Python package"
  ```

  A declaration that matches no tracked root entry is a finding; a declaration of a base-list entry that is tracked is
  clean. A finding names the entry and both ways out, move it to its owner's directory or declare it with a reason, in
  prose with no command and no local path; findings are sorted by entry and all printed. An invalid declaration (a `/`
  inside the entry, `.`, `..`, `.git`, an empty entry, a multi-line reason, an unknown key, or one entry declared
  twice) is `hygiene: root-layout cannot judge - ...` with a `[[root.allow]]` example. Any other malformed table or
  allowance elsewhere in `quality/config.toml`, which plain `quality hygiene` reads for this rule, is `root-layout cannot
  judge` too, with that table's own message; the values of `[gate]` stay the verdict's to judge. Any pull request may add a declaration with a reason; the review judges whether the reason holds. A declaration is no loosening, so `quality verdict` asks for no
  `[[gate.loosened]]` entry (operator ruling 23.10.2026: „jeder PR mit Grund, das Review urteilt, ohne deine Tür“).
  The rule reads names, never content, and writes nothing. No configuration switches it off. A config reader older than
  the rule refuses `[root]` as an unknown key, so a declaration lands in the pull request that moves the repository to
  a quality version that knows it. Named gaps: a submodule in the root counts as a file entry and no case of it is
  tested; marketplace's refusal of every symlink in the tree stays that repository's own check.
- `file-suffix`: a tracked file whose basename has no suffix is a finding, unless it is executable (index mode
  100755), a symlink (120000) or a gitlink (160000), its basename is a dotfile, its basename is on quality's list of
  tool-mandated names (`LICENSE Dockerfile Makefile CODEOWNERS`), or it is declared. The index (`git ls-files -s`) is
  the only source of a path's mode and a link is never followed; index data that cannot be read is
  `hygiene: file-suffix cannot judge - ...`. Names compare in exact case, in any folder. The list belongs to quality and
  changes with a quality version. A file is declared in `quality/config.toml`, a table of its own beside
  `[[root.allow]]`, one exact tracked path, the rule and a one-line reason:

  ```toml
  [[layout.allow]]
  path = "deploy/EDGE_SHA"
  rule = "file-suffix"
  reason = "the deploy script reads it by this name"
  ```

  The rule is `file-suffix`, `script-name`, `script-header`, `test-layout` or `test-import`; a declaration covers only its own rule, and one
  `script-name` entry covers both the shape and the location of its path. A declared path that is not tracked is a
  finding of the declared rule; a declaration for a file that already complies is clean. A finding
  names the file and both ways out, give it a suffix or declare it, in prose with no command; findings are sorted by
  path and all printed. A duplicate `(rule, path)`, a folder (a trailing `/`), a pattern (`*`, `?`, `[`), `..`, a
  leading `./`, an empty component, an absolute path, an unknown rule, an unknown key or a reason that is not one-line
  text is `hygiene: file-suffix cannot judge - ...` with a `[[layout.allow]]` example; a malformed `[layout]` table is
  the same, and another malformed table in `quality/config.toml` is forwarded with its validation error. A
  declaration is no loosening, so `quality verdict` asks for no `[[gate.loosened]]` entry. The rule writes nothing and
  no configuration switches it off. A config reader older than the rule refuses `[layout]` as an unknown key. (#175,
  operator ruling 24.10.2026: „ja das will ich“)
- `script-name` and `script-header` judge a script: a tracked file with index mode 100755, whose content starts with
  `#!`, or that is a `.sh`, `.py` or `.mjs` file under a `scripts/` folder at any depth (`frontend/scripts/` too). A
  path with a `tests/` component is never a script, nor is a link (120000, 160000), nor data beside a script such as
  `scripts/BACKUP.md`. The mode comes from the index only; the content is read through the repository reader, and a
  file that cannot be read is `hygiene: script-header cannot judge - ...` (exit 2). Both rules compare names in exact
  case, print all findings sorted by path and have no off switch. A declaration is a `[[layout.allow]]` entry as for
  `file-suffix`.
  - `script-name`: a script's basename without its extension is snake_case with at least two words
    (`run_static_checks.sh`, `verify_release.py`; `check.sh` and `prove-x.py` are red), lower-case letters and digits
    only, and the script lies under a `scripts/` folder. Tool-mandated basenames and dotfiles are exempt from both.
    There is no verb list: whether the verb fits stays a judgement for the structure audit.
  - `script-header`: a comment or docstring stands within the first three non-blank lines after the shebang and a
    PEP 723 block; blank lines do not count. Comments are `#`, and `//` or `/*` for `.mjs`. A tool directive
    (`# shellcheck`, `# -*-`, `# noqa`, `# type:`, `# pyright:`, `# ruff:`, `// eslint`) and code such as
    `set -euo pipefail` do not count. Presence only, never content.
  (#175, #190; head decisions of 27.10.2026: the `tests/` exemption and blank lines not counting)
- `test-layout` judges what may lie under a Python project's `tests/` and where a frontend test lies. A project is the
  directory of any tracked `pyproject.toml`, the root or a member at any depth; the file's content is never read, and
  one inside the `tests/` of another project, of either kind, makes no project. A project is judged when it has a package: a directory directly under
  `<project>/src/` that holds a tracked `.py` at any depth, with or without `__init__.py`. A project without one, and
  a `tests/` folder that belongs to no project, are not judged. Under a judged `<project>/tests/` every tracked path
  is a `test_*.py` (placed and named by the mapping below), a `conftest.py` at any depth, a file in a `support/`
  or `fixtures/` folder at any depth, or a file in the top-level `tests/e2e/`. An `__init__.py` under `tests/` is
  red anywhere, `support/` and `e2e/` included, and is data only inside a `fixtures/` folder. A finding names its
  case: an `__init__.py` (delete it, or declare it) or any other file (move it to a `support/` helper folder or a
  `fixtures/` data folder, or declare it). A declaration is a `[[layout.allow]]` entry with the rule `test-layout` on
  an exact tracked path. Paths come from the index and nothing is read from disk; a link or gitlink is judged by its
  own name and never followed; an index record that cannot be read is `hygiene: test-layout cannot judge - ...` (exit
  2). Names compare in exact case, all findings print sorted by path and no configuration switches the rule off.
  Named gaps, waiting for usage evidence: a `tests/` folder elsewhere (`src/p/tests/`), a project with only
  `setup.py`, a project whose `src/` holds only modules, and a package named `support` or `fixtures`.
  Every `test_*.py` outside `fixtures/` and the top-level `e2e/` mirrors the module it tests, one direction. Its
  folder mirrors a folder of `src/<package>/` (`tests/api/` is `src/<package>/api/`; with several packages
  `tests/<package>/api/` is `src/<package>/api/`), and that folder holds a tracked `.py` at any depth. `tests/scripts/`
  mirrors `<project>/scripts/` and `tests/workflows/` mirrors the root's `.github/workflows/`, directly and only in the
  root project; both come before any package name. Each candidate of the mirrored folder has a key: `__x__` reads as
  `x`, otherwise one leading `_` is dropped; candidates are the `.py` files and the subfolders that hold a `.py` (in
  `scripts/` any file and subfolder, in `.github/workflows/` the `.yml` and `.yaml` stems with `-` read as `_`), and in
  the top folder of a package also its own name. The test's name, `test_<name>.py`, splits at `_` into words and its
  longest word prefix, ending at a word boundary, must equal a key exactly; the rest is the aspect, and a name
  whose first word is empty (`test_.py`, `test__x.py`) has no prefix. A `test_*.py` in a `support/` folder is red.
  A member's tests map only into the member's own `src/` and `scripts/`. Each case has its own reason and fix, a
  file that breaks the folder and the name condition together gets one finding with both reasons, and every fix ends
  with declaring the path. (#175, #196, #199; operator rulings 24.10.2026: „ja das will ich“, D-A „ja beides a?“,
  D-B „ja b!“; the details are head decisions of 27.10.2026)

  Frontend tests. A frontend project is the directory of any tracked `package.json`, the root or a
  member at any depth; its content (`workspaces` included) is never read, a `package.json` inside the `tests/` of a
  project of either kind makes no project, and a test file belongs to its nearest enclosing frontend project. A
  repository without one is not judged by these rules. Judged files are tracked TypeScript and JavaScript files (the
  suffixes `size` counts, `.mjs`, `.js` and `.tsx` included) with a word between the first and the last `.` that is
  `test`, `spec`, `test-d` or `spec-d`; only the name decides, no `tests/` or `__tests__/` folder does, a link is
  judged by its name and a gitlink is a folder and never judged. A path under the `tests/` of a Python project with a
  package is judged by the Python cases only. Sibling: a judged file with no word `spec` is a test, and its base,
  its name up to the first `.`, or a non-empty prefix of the base that ends before a `-` or `_` or between a lower-case
  letter or digit and an upper-case letter, must equal the key of a subject in the same folder. A subject is any
  tracked file there that is no gitlink and carries no test word, whatever its suffix (a symlink counts by its name, a
  folder never); its key is its base without a leading `+`, so `page.test.ts` maps to `+page.svelte`. An empty base
  never matches, and `.test.ts` is red. Spec: a judged file with the word `spec` lies under its project's top-level
  `e2e/` folder at any depth; other files there are not judged as specs, and a `.test.*` there needs its sibling. A
  declaration exempts its path from every case. Each case has its own reason and fix: a test with no sibling
  (move it beside the file it tests and name it `<subject>[<Aspect>]` plus its own tail) and a spec outside `e2e/`
  (move it into the project's `e2e/`, or name a Vitest test `<subject>.test.<suffix>`); every fix ends with declaring
  the path. `test-layout` judges no `.rs` file and reads no `Cargo.toml`, and Cargo's own layout stands (other rules
  still read tracked files for a shebang); a `package.json` or `pyproject.toml` inside a Cargo tree makes a project
  like any other. Named gaps, waiting for usage evidence: a subject that is a
  folder, an acronym run read as one word (`HTMLParser.test.ts` beside `HTML.ts` is red), a name holding both `test`
  and `spec` (judged as a spec), a test-named file outside any frontend project, where helpers, setup files and
  fixtures lie, a test whose subject lies outside its project (declare it), an `e2e/` folder that is its own
  workspace package (its specs are red), a `package.json` under a `fixtures/` folder outside any `tests/` (a project)
  and a judged file in a frontend `fixtures/` folder (it needs a sibling). (#175; operator rulings 24.10.2026; the
  details are head decisions of 27.10.2026)
- `test-import` judges every Python project, with or without a package, as `test-layout` finds projects. The importers
  are the tracked `.py` files under its `tests/` that are no link and lie in no `fixtures/` folder, `support/` modules
  and `e2e/` drivers included. The targets are the tracked `test_*.py` and `conftest.py` files there (a link is a target
  by its name), including a member's or the root's own. Every `import` and `from ... import` statement is read, inside
  a function or under `if TYPE_CHECKING:` too. An absolute name is red when its components equal the trailing components
  of a target's path without `.py`, which covers every `pythonpath` spelling without reading pytest's configuration
  (`import conftest`, `from tests.test_x import y`, `from e2e.conftest import z`); a `from M import a` checks `M` and
  `M.a`, so `from tests import conftest` is red and `from tests.support.x import test_helper` is not. A relative name
  resolves against the importer's folder and must equal a target's path. A plain `e2e/` driver may import another.
  One finding per statement, at `path:line`, names each target it imports; the fix is a `support/` module or a
  fixture in `conftest.py`, or a `[[layout.allow]]` entry with the rule `test-import` on the importer's path, which
  exempts all its imports (an untracked declared path is a finding; a `test-layout` declaration does not cover it).
  Importers are decoded as Python 3.14 reads source and parsed: a file that does not parse is
  `hygiene: test-import unparsable - <path> is not valid Python (...)` (exit 2) and one that cannot be read is
  `hygiene: test-import cannot judge - ...` (exit 2); no other file is read. All findings print sorted by path and
  line and no configuration switches the rule off. Not read, and waiting for usage evidence: string imports
  (`importlib.import_module`, `__import__`, `pytest.importorskip`), `pytest_plugins`, `sys.path` edits, `runpy`,
  source passed to a subprocess with `-c`, and `*_test.py` as a target. An import whose name matches a tracked test
  module is reported even when it means a third-party module; rename the test module or declare the importer.
  (#175, #201)
- `python-floor` judges every tracked `pyproject.toml` that `test-layout` finds as a project (the root or a member at
  any depth, none inside another project's `tests/`) and that holds a `[project]` table; one with only `[tool.*]`
  tables, or with `project` as a value, is not judged, and a gitlink is never entered. The floor is the highest lower
  bound among the `requires-python` clauses `>=`, `>`, `~=`, `==` and `===`, read with `packaging`; a trailing `.*` is
  dropped (`==3.14.*` is 3.14, `==3.*` is 3), `!=`, `<` and `<=` set none, and `>3.13` counts as 3.13. The file is clean
  from a floor of 3.14: `>=3.14`, `>=3.14,<3.15`, `~=3.14`, `==3.14.*` and `>=3.15` pass; `>=3.12`, `>=3.11,<3.15`,
  `>3.13`, `>=3.14.0rc1`, `<3.15`, an empty value, `dynamic = ["requires-python"]` and a missing `requires-python` are
  red (the last four name no floor). One finding per file, at its path, naming the value and the floor, with the fix:
  raise it to `>=3.14` and move CI, `.python-version`, Dockerfiles and ruff's `target-version` with it, or declare it.
  A `[[python.allow]]` entry with exactly a `path` (the exact tracked path of the `pyproject.toml`, no folder, pattern
  or `..`) and a one-line `reason`, one per path, makes that file clean; its own table, because the exception is meant
  to end. The gate shows each one before the verdicts (`python-floor: allowed speech/pyproject.toml (requires-python
  ">=3.12,<3.13") - <reason>`). An entry whose path is no judged file (untracked, gone, without `[project]`) or whose
  file is already at the floor is a finding; its fix is to delete the entry. A malformed entry is `cannot judge` in
  every rule that reads the config, and a config reader older than the rule refuses `[python]` as an unknown key, so
  declare only after the bump. A candidate that is not UTF-8 TOML, or whose `requires-python` is no string, no PEP 440
  specifier set or a `===` clause with a version that is not PEP 440 (`===foobar`), is
  `hygiene: python-floor unparsable - ...` (exit 2). No configuration switches the rule off, and a declaration asks
  `quality verdict` for no `[[gate.loosened]]` entry. Only `requires-python` is read, because uv and
  pip refuse an interpreter outside it, so a leftover setter cannot install the project on old Python. Not read, and
  waiting for usage evidence: workflows, `.python-version`, Dockerfiles, ruff's `target-version`, `setup.py`,
  `setup.cfg` and Poetry's `[tool.poetry]`; `!=` as a floor (`>=3.13,!=3.13.*` is red although it admits only 3.14 and
  up); a job that never installs the project, which runs old Python unnoticed; `uv sync` behind an older
  setup-python, which fetches 3.14 and leaves a stale setter; and a Python project without a `pyproject.toml`. (#207)
- `python-security` judges every tracked regular `.py` file in any directory with the `S` codes of quality's own ruff
  (`ruff==0.17.1`, an exact pin in quality's `pyproject.toml` that serves its own lint too): the ruff binary of
  quality's installation, never one on `PATH`, at the pinned version, runs `ruff check --isolated --no-cache
  --ignore-noqa --select S --target-version py314 --output-format json -- <files>`, in batches below the argument
  limit, and writes nothing into the checkout. With no tracked `.py` file ruff is not started. Not judged: untracked
  files, a link, anything inside a gitlink, notebooks, `.pyi` files and suffixless scripts. The repository's own ruff
  `select`, `ignore`, `per-file-ignores`, `exclude`, `extend` and noqa have no effect. Each finding is one line at its
  file and line (`python-security src/a.py:12 - S307 Use of possibly insecure function; ...`). In the files
  `test-import` judges (every `.py` under a Python project's `tests/`, outside `fixtures/`) S101, S105 to S107, S603
  and S607 are exempt; S108 (a shared path breaks test isolation), S310 and every other code stay red there, and
  `tests/fixtures/` and `src/` get no exemption. A finding is green only when the line at ruff's `noqa_row` (the last
  line of a multi-line string or call) carries `# noqa: <CODE>[, <CODE>...] - <reason>` naming its code; the reason
  is the text after ` - ` up to the next `#` or the end of the line, holds a letter and no comma (Sonar's noqa grammar
  splits on commas, so a comma raises python:S7632 for every caller that runs Sonar). Every other form is red: no
  reason, a blanket `# noqa`, another directive as the reason (`# type: ignore[x]  # noqa: S607`, `# noqa: S607  #
  pragma: no cover`), `# noqa: S607: reason`, a wrong code, another line, a file-level `# ruff: noqa`, and a bare
  second directive behind a reasoned one (`# noqa: S603 - why  # noqa: E501`). ruff itself honours the reasoned form,
  so a caller's own ruff stays quiet, and `lint.external = ["S", "T20"]` in its ruff config keeps its RUF100 from
  calling the gate's noqa unused. A file ruff cannot parse makes the rule `hygiene: python-security unparsable -
  ruff cannot parse <path>` (exit 2); a file Python cannot decode, or reads as other
  text than ruff reads as UTF-8 (a PEP 263 cookie such as utf-7), a missing or mismatched ruff, ruff exiting other
  than 0 or 1, a `Failed to lint` warning, a report quality cannot
  read, a time-out or a file whose comments Python's tokenizer cannot read is `cannot judge` (exit 2). One such file
  makes the whole rule `cannot judge` or `unparsable`, so the S findings of the other files are not printed in that run. The other
  rules are judged either way. A deliberately invalid Python fixture takes another suffix, since a `.py` file is Python. No
  configuration switches the rule off, and a reasoned noqa asks `quality verdict` for
  no `[[gate.loosened]]` entry. Not judged, and waiting for usage evidence: notebooks, `.pyi` files and suffixless
  Python; judging a non-UTF-8 source (a latin-1 file with non-ASCII bytes stays `cannot judge`), since ruff reads UTF-8 only; a stale reasoned noqa, which ruff's RUF100 finds. (#214, #219)
- The baseline only falls: on a pull request `quality verdict` holds the head's `quality/baseline.toml` against the
  base commit's, so growing a function and rewriting the baseline stays red. Each loosening is one line, named with the
  baseline's own metric, path and symbol (a file entry has no symbol):
  - an entry raised: `loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90`;
  - an entry the base does not hold (a function or class under another name included; a moved file is judged as below):
    `loosened (undeclared): size baseline adds ccn src/a.py f 16`;
  - a clone pair the base does not hold, when the base has a `[clones]` table:
    `loosened (undeclared): clone baseline adds tests/test_a.py run <-> tests/test_b.py hygiene`;
  - the base's `[clones]` table gone from a head that still has the file: `loosened (undeclared): clone baseline removed`;
  - the base's file deleted: `loosened (undeclared): quality/baseline.toml removed`.

  A file git recognises as renamed, a directory move included, is no loosening when its numbers do not rise
  (operator ruling 22.10.2026, #149: „ja ja“). The verdict reads the renames between the base and the head commit with `git diff -M50%
  -l1000` (git's default similarity, whatever the repository's git configuration says; a file git does not pair is new
  as before). The base's entries and clone sides under the old path then move to the new path, so the head is compared
  with the entries its predecessor held and a raise or an added symbol still names the head's path; an entry the base
  holds under the new path is a leftover and does not count. A rename from a path that is not UTF-8 moves nothing, since no baseline key can name it, but still clears
  its new path of leftovers; a rename to a path that is not UTF-8 is not read. The allowance moves rather than being copied: while the
  head baseline still names the old path, nothing moves and the new path's entries are adds. A pair whose files moved
  is the same pair, and a renamed function is a new entry. Renames are read only when the base and the head both hold a baseline: without a base, or without a baseline on
  either side, nothing is read. `emergency-merge` fetches the same two commits and reads the same renames. `quality baseline write` and `hygiene` name entries and pairs by their current path.

  A lowered, equal or removed entry or pair is green, and so is adopting a baseline (the base has none) or having none
  on either side. A `[clones]` table the base lacks is an adoption too: its pairs are not compared, while the size
  entries are still held against the base's. A malformed baseline on either side, a head config or baseline that cannot be read or links out of the
  repository, a base file that is not a regular file (`... : not a regular file`), a base git refuses to read (`... : git exited <n>`, git's own words never printed), or a base commit the clone lacks is exit `2`, naming the side (`quality/baseline.toml at the base commit
  <sha>: ...`, `quality/config.toml at the head: ...`). Only the head's malformed baseline adds ``rewrite it with
  `quality baseline write` `` (an unknown table adds `write the baseline with the quality release your gate pins`
  instead): a pull request cannot rewrite its base. The base must be a ref
  name or a SHA: `HEAD~1` or `-x` is exit `2` (`... is not a ref name or a SHA`) before git runs. Outside a pull
  request (push, `merge_group`, `workflow_dispatch`) `gate.yml` passes no base, so the base is never read and only
  `hygiene` holds the tree. A lizard update that measures a function higher therefore turns the pull request that
  takes it red; a ruled raise is declared (see [Declared loosenings](gate.md#declared-loosenings)) and stays red until the
  operator's door merges it.
- The import contracts only tighten: on a pull request `quality verdict` and the door compare the contracts of the
  three config places at the head with the base's, as import-linter parses them (never the raw options, so moving a
  config between `.importlinter`, `setup.cfg` and `pyproject.toml` changes nothing), without building an import graph.
  A contract is the same on both sides when it has the same `name`; a rename is a removal plus a new contract.
  Each loosening is one line, sorted (operator rulings 26.10.2026, #148: R1-R3 „ja“):
  - a contract the base holds and the head lacks: `loosened (undeclared): import contract "layers" removed`;
  - the base's config gone, whatever it held, or only its section gone from a file that stays, once:
    `loosened (undeclared): import-linter config in .importlinter removed`;
  - an `ignore_imports` expression the base does not hold, one line each, compared as parsed (spacing around `->`
    does not count, a wildcard is a new expression):
    `loosened (undeclared): import contract "domain" ignores app.domain -> app.web.*`;
  - any other field of a contract that differs, `broken_contract_guidance` excepted, a tightening included:
    `loosened (undeclared): import contract "layers" changes allow_indirect_imports` (a changed `type` reads
    `changes type`; a module dropped from a list reads as that list's field);
  - `root_packages` (as a set of names), `include_external_packages` or `exclude_type_checking_imports` changed:
    `loosened (undeclared): import-linter changes root_packages`.

  A new contract, a removed `ignore_imports` entry, a reworded `broken_contract_guidance` and adopting import
  contracts (the base has no config) are green. Two contracts of one name on a side, two config places holding a
  section, or a config that cannot be read are exit `2`, naming the side (`import-linter config at the base commit
  <sha>: ...`, `import-linter config at the head: ...`). Each line can be declared with a `[[gate.loosened]]` entry
  and then lands only through the operator's door, which reads the same files at the base and the merge commit.

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

The `advisory` rule judges every tracked `uv.lock`, `pnpm-lock.yaml` and `requirements*.txt` (a name that starts
with `requirements` and ends in `.txt`; `requirements.in` and `dev-requirements.txt` are not), root
and nested, with osv-scanner against the OSV advisory database (dev dependencies count). `uv.lock` and the
requirements files are judged against the PyPI database, `pnpm-lock.yaml` against the npm database. A
`package-lock.json`, `yarn.lock` or `bun.lock` is not judged yet. It is part of
`--engines`, so it is skipped without it (nothing is downloaded), and a repository with no tracked lock needs no
osv-scanner; a database is downloaded only for an ecosystem with a tracked lock, once per run however many locks it has.
A lock that is tracked but missing on disk is skipped;
one that links out of the repository, is not a regular file or is empty (zero bytes) is exit `2`. A lock listed in `.gitignore` is still judged.
A finding is one per advisory group, package and lock file, named by the tracked relative path:
`advisory uv.lock - osv-scanner GHSA-8q59-q68h-6hv4 (CVE-2020-14343, PYSEC-2021-142): PyPI pyyaml 5.3.1 is affected.`
with `Fix: upgrade pyyaml to >= 5.4 (uv lock --upgrade-package pyyaml)`. For a nested lock the fix names its
directory (`in speech/: uv lock --upgrade-package ...`); several fixed versions are listed; an advisory with no fixed
version, a package listed as malicious included, says `osv lists no fixed version: remove or replace <package>; see
https://osv.dev/vulnerability/<id>`. The fix prints a command only where it works as printed: for `pnpm-lock.yaml` it
says to upgrade the package, or its parent when it is transitive, or set a pnpm override in `package.json`; for a `requirements.txt` it says to raise its pin, and to compile the file again when it is generated. npm names match as written (`lodash.merge` is not
`lodash-merge`); Python's name rule applies to PyPI only. A repository's own `osv-scanner.toml` (ignored advisories,
package overrides), its `package.json` `pnpm.auditConfig`, its `.npmrc` and its `.gitignore` change nothing: the gate
passes its own empty `src/quality/osv-scanner.toml` and each tracked lock explicitly, so only a real fix in the lock
(an upgrade or an override) turns a finding green.

An advisory the repository cannot fix is allowed in its own `quality/config.toml`, one entry per advisory and lock:

```toml
[[advisories.allow]]
id = "GHSA-462x-pgg6-55fp"
lock = "speech/providers/qwen/uv.lock"
until = 2027-01-31
date = 2026-10-21
item = "overnightworks/agent-presentator#371"
reason = "gradio caps pillow<13; no image input"
```

- `id` is any id of the advisory group, GHSA, PYSEC or CVE alike. `lock` is the tracked relative path; the entry
  allows the advisory on every package it affects in that lock and in no other, so a lock reached by two tracked names
  needs an entry per name. Two entries matching one group in one lock (its GHSA and its CVE id) are a finding at
  `quality/config.toml` naming both, and the group is allowed by neither.
- `until` is the re-check date and `date` the day of the ruling, both TOML dates without a time; `item` and `reason`
  are as in a [declared loosening](gate.md#declared-loosenings). The allowance holds through `until` (in UTC) and the advisory
  is red again from the next day: `advisory <lock> - osv-scanner <id> (<aliases>): PyPI <package> <version> is
  affected; its allowance ran out on <until> (<item>).` quality sets no latest `until`, and an advisory with no fixed
  version is allowed like any other.
- Each allowed advisory prints before the findings, and the rule stays clean: `advisory: 1 allowed`, then
  `  allowed <lock> <id> (<aliases>): PyPI <package> <version> until <until> (<item>) - <reason>`.
- An entry no reported advisory matches is a finding: `advisory quality/config.toml - the allowance for <id> in
  <lock> matches no advisory.` Deleting it is no loosening. With no tracked lock every entry matches nothing; without
  `--engines` no entry is judged.
- A malformed entry (a missing or extra key, an empty or multi-line `id`, `lock` or `reason`, an `item` that is not
  `#<n>` or `<owner>/<repo>#<n>`, an `until` or `date` that is text or carries a time, two entries for one `id` and
  `lock`, or an unknown key in `[advisories]`) is exit `2` in the `advisory` and `secret` rules and in `verdict`,
  naming the entry; `scan-staged` reads only `[secrets]`.
- A new allowance, and one whose `until` moves later, is a [declared loosening](gate.md#declared-loosenings) the allowance
  makes itself, so only the operator's door merges it. The pull request writes the allowance, so its item, date and
  reason prove nothing alone: the verdict's red and the door are the gate. An `id` that is an alias covers every
  group carrying it in that lock, while the door names the `id` and the lock, not the packages.
  Why only the operator's door merges it stands in [decision 1](decisions/1-loosening-door.md); his rulings on the
  allowance follow. The rest was shown to him and left unanswered, so it stands at the
  proposed default and is not his word: the entry's keys, the alias, the date format, the expiry line, the unmatched
  allowance, the adoption, no latest `until`, the printed lines, an advisory without a fixed version and the reach of an
  allowance (#116, E1 to E5 and E8 to E12). The head decided on 21.10.2026 that the allowance carries a `date` (E1) and
  that two entries on one group are red (E2).

For advisories (#116, 21.10.2026), asked whether a repository may allow a known security hole it cannot escape, with a
reason and a re-check date, and who approves: „Ja, aber nur du gibst frei (empfohlen)“. The ruled sentences:

- R1. Eine Ausnahme steht pro Lücke in `quality/config.toml`, mit Begründung, Item und Datum.
- R2. Eine neue Ausnahme ist eine Lockerung: der PR bleibt rot, bis deine Tür ihn mergt.
- R3. Ab dem Merge ist die Lücke grün bis zum Datum, danach wieder rot.
- R4. Kein Agent kann eine Lücke allein stummschalten. (Bekanntes Restrisiko E10 von #103: Agenten teilen deinen `gh`-Login; die Tür ist der Vertrag, keine Sperre.)

Asked whether a pull request may move the re-check date back without him: „Nein, nur über deine Tür (empfohlen)“. Asked
whether each new allowance must also be entered as a declared loosening: „Ausnahme erklärt sich selbst (empfohlen)“.

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

The hygiene job in `gate.yml` stops at 25 minutes: the 300-second download deadline and the 600-second scan limit take
15, and setup and the other rules get the remaining 10, so quality's own `cannot judge` shows before GitHub cancels the job.
Measured on 24.10.2026 (osv-scanner 2.7.0, 12 cores; the download at load 17 to 20): the npm archive is 219,026,863 bytes (230,735 files), the PyPI archive 37,064,024 bytes
(26,612 files); downloading npm took 34 s; a whole `hygiene --engines` run over a repository with one `pnpm-lock.yaml` and one hashed
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

An `[[advisories.allow]]` entry for an npm finding is the same entry with `lock` set to the file's tracked
path (`id = "..."`, `lock = "frontend/pnpm-lock.yaml"`); it is a declared loosening and merges only through the operator's
door like any other. When a repository's frontend and its lock are gone, the npm database is no longer downloaded and an
entry for it is a finding until it is deleted. A repository that falls back to an older quality leaves such an entry
unmatched and red likewise. The predecessor `pnpm audit --prod` steps are retired by each repository's own head.
Operator rulings of 24.10.2026 (#145): „ja“ to R1 to R3 (the same allowance, no silencing from the repository's files,
open npm advisories keep the bump pull request red); the other lines stand at their proposed defaults.

Every rule is judged, even when another cannot be, and gives one verdict: clean, findings, unparsable, or cannot judge.
Each finding names the rule, the file, a reason and a fix. Repository text (a file name, a service, a port, a contract
or job name, a configuration entry) prints escaped. In the door's own fields (a finding's location, reason and fix, refusals, reports), invalid UTF-8 prints as `\udcNN` and every control character (line feed,
carriage return, ESC, tab, DEL, C1) as `\xNN`. A line taken from import-linter's rendering (a broken contract's chain and guidance) prints behind the marker `  | `; rich may drop a carriage return, expand a tab, or start a new marked line at a line feed there. Every line the door prints therefore starts with its own text, so a matcher anchored at the line start, such as setup-uv's python matcher, cannot match repository text. A refusal is one line; the lines the door means to print (an import chain, the verdict's problems, the scoreboard's table) stay lines. A rule that cannot judge prints its verdict and reason under its rule id (`hygiene: secret cannot
judge - ...`). A clean run names its rules in run order (`hygiene: clean (env-file, compose-port, size, clone, import-contract, claude-md, root-layout, file-suffix, script-name, script-header, test-layout, test-import, python-floor, python-security); skipped without
--engines: ...`); any other run ends with the findings and the verdicts counted (`hygiene: 1 finding(s); verdicts: 1
clean, 1 findings, 1 unparsable`). The lines come in run order, also through a pipe that merges stdout and stderr
(`2>&1 | tee log`); each stays on its own stream. That is option (c) of #86 on 19.10.2026, `cli.main` makes stdout
line-buffered; the head's confirmation that it is the cleanest drew no objection, which is no spoken ruling. The exit is the worst verdict's: `2` if any rule is unparsable or cannot judge
(never clean), otherwise `1` if any rule has findings, otherwise `0`. A path git cannot read as a work tree (a file, a missing path, a
folder without permission, a bare repository, the inside of `.git`) is `hygiene: cannot judge - <PATH as given> is not a
readable git repository: ... (`git -C '<PATH>' status` says why; a PATH with an unprintable character gets `git status` run inside it instead)` and exit `2` before any rule runs.
In GitHub Actions (`GITHUB_ACTIONS=true`) the door first prints `::stop-commands::<token>` on stderr and on stdout, a new random token per run, so no
workflow command in repository text runs (operator ruling 20.10.2026, asked whether to stop the commands, escape the
text, or both: „Beides (empfohlen)“): no `::warning::` or `##[warning]` annotation, no `add-mask`, no `add-matcher`; the stop lasts for the rest of the step, and the door never prints the line that lifts it. Problem matchers registered in the job still read the log (the stop does not reach them). The gate's jobs register only setup-uv's python matcher, which is anchored at the line start and so cannot match repository text; a matcher another step registers may still match a door line that holds repository text, and the door cannot prevent that.
