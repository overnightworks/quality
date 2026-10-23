# Rules

Reference for the engineer or operator who needs the exact behaviour of `quality hygiene` and its output contract.

`hygiene` reads git's view of the repository `PATH` (default `.`) lies in, from its top level, so a run from `src` judges
the same files and the same `quality/baseline.toml` as one at the top, and names every file relative to the top (`size
src/big.py`). A `PATH` in a submodule or a linked worktree means that work tree's own top. It applies five rules:

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
  a number is a finding. A service with `network_mode: host` publishes whatever its process binds, and this rule does
  not check that; the limit is documented first, and the allowlist the same ruling asks for is
  [#45](https://github.com/overnightworks/quality/issues/45) row Q1b-3g (operator ruling 19.10.2026: „Erst Doku, dann
  Erlaubnisliste (empfohlen)“).
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
  twice) is `hygiene: root-layout cannot judge - ...` with an example, and so is any other malformed table or allowance
  elsewhere in `quality/config.toml`, which plain `quality hygiene` reads for this rule; the values of `[gate]` stay the
  verdict's to judge. Any pull request may add a declaration with a reason; the review judges whether the reason holds. A declaration is no loosening, so `quality verdict` asks for no
  `[[gate.loosened]]` entry (operator ruling 23.10.2026: „jeder PR mit Grund, das Review urteilt, ohne deine Tür“).
  The rule reads names, never content, and writes nothing. No configuration switches it off. A config reader older than
  the rule refuses `[root]` as an unknown key, so a declaration lands in the pull request that moves the repository to
  a quality version that knows it. Named gaps: a submodule in the root counts as a file entry and no case of it is
  tested; marketplace's refusal of every symlink in the tree stays that repository's own check.
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
passes its own empty `src/quality/osv-scanner.toml` and each tracked lock explicitly.

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
judge - ...`). A clean run names its rules in run order (`hygiene: clean (env-file, compose-port, size, clone, import-contract, claude-md, root-layout); skipped without
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
