# quality

One door, `quality` (`src/quality/cli.py`); every line it prints, and the step summary, leave through `output.py`, apart from argparse's help, usage errors and `--version` and `input()`'s prompt. Rules return `Finding`s (`report.py`) and never print; a finding's event is a `report.FindingEvent` member, a note's a `report.NoteEvent` member, never defined in `output.py`. A `CannotJudge` carries a few-word fact and, where its mechanism knows them, its next steps as typed values (`report.Reason`: a command, an instruction, alternative commands, or an example to copy), never inside the fact. A `ConfigError` carries its fact and its TOML example apart; `check` turns it into a refusal. Each rule's id, its place in the run order and its facts are one member of `Rule` (`rules.py`).
Exit 0 clean, 1 findings, 2 cannot judge (`CannotJudge` and `Unparsable` in `report.py`).

Door commands:

- `quality hygiene [PATH]` (`check.py` runs the rules in order): `.env` files (`env_file.py`), compose ports (`compose.py`, where Compose files are read as YAML 1.2), the `size` ratchet (`ratchet.py`), the
  `clone` rule (`clones.py`, enforced with `[clones]` in the baseline, else report-only), the `import-contract` rule (`architecture.py`, import-linter as a library), the `claude-md` rule (`claude_md.py`: a tracked `CLAUDE.md` is
  exactly `@AGENTS.md`), the `root-layout` rule (`root_layout.py`: every root entry is on a base list or declared with `[[root.allow]]`), the `file-suffix` rule (`layout.py`: a tracked file without a suffix is executable, a link, a dotfile, tool-mandated or declared with `[[layout.allow]]`), the `script-name` and `script-header` rules (`layout.py`: a script is named `<word>_<word>` and lies under `scripts/`, and opens with a comment or docstring), the `test-layout` rule (`layout.py`: what may lie under a Python project's `tests/`, each `test_*.py` mirrors the `src/` module it tests, a Vitest test sits beside its subject and a Playwright spec lies in its project's `e2e/`), the `test-import` rule (`layout.py`: no file under a Python project's `tests/` imports a test module or `conftest`), the `python-floor` rule (`python_floor.py`: every Python project's `requires-python` floor is 3.14 or declared with `[[python.allow]]`), the `python-security` and `python-print` rules (`python_lint.py`: one run of quality's pinned ruff reports the `S` and `T20` codes in every tracked `.py` file; only a reasoned noqa clears a finding, and a file-level `# ruff: noqa: T201 - <reason>` declares a program's one output owner), and the secret and workflow
  scans and the `advisory` rule (`advisories.py`: osv-scanner over every tracked `uv.lock`, `pnpm-lock.yaml` and `requirements*.txt` (read by `pip_requirements.py`), against the PyPI and npm
  databases it downloads itself).
- `quality baseline write [PATH]` (`ratchet.py`): writes the measured size debt and the clone pairs to `quality/baseline.toml`,
  naming each entry it raises or adds.
- `quality verdict [PATH]` (`verdict.py`): judges the mandatory jobs of the running workflow run and, on a pull request, holds the head's config, baseline and import-linter configs against the base commit's (a baseline only falls, an import contract only tightens; a loosening stays red, labelled declared when the head adds its `[[gate.loosened]]` entry); the fail-closed `quality / verdict`.
- `quality scoreboard [PATH]` (`fleet.py`): the fleet's size and each repository's gate and CodeQL release and what each gate does with each rule in two
  markdown tables, from anonymous shallow clones; only anonymously readable repositories are measured. The fleet list is `quality/fleet.toml` (`repositories`, GitHub `owner/name`s).
- `quality scan-staged [PATH]` (`staged.py`): secret scan of the staged change.
- Scanners (`scanners.py`): gitleaks, actionlint and osv-scanner come through mise from `src/quality/mise/` (`mise.toml` holds the versions, `mise.lock` the sha256); `scanners.py` asks `mise which` there under `MISE_CEILING_PATHS` and runs only the exact version. ruff and zizmor are pinned dependencies of quality's package: `installation.py` finds each in quality's own installation and reads its pin.
- `quality sonar properties` and `quality sonar findings` (`sonar.py`). Their HTTP and the verdict's go through `web.py`, which follows no redirect and bounds every wait; the door injects the transport, the GitHub fetcher and the poll sleep, so tests need no network.
- `quality emergency-merge <owner/repo> <pr>` (`merge.py`): the operator's door for legacy findings and declared
  loosenings, it needs a terminal on stdin and prints only through its injected `say`.

`.github/workflows/codeql.yml` is the reusable CodeQL workflow (input `languages`); `code-scanning.yml` is quality's own caller of it.

`.github/workflows/gate.yml` is the reusable gate; its caller contract stands in `docs/gate.md` and in the file's header.

quality's own layers are an import-linter contract in `pyproject.toml` (`[tool.importlinter]`); `quality hygiene .`
enforces it, so a module imports only from the layers below its own.

`checkout.py` lists the checkout through git, decides what a workflow file is and turns a refused read into cannot judge.
Config, `check.py`, `compose.py`, `claude_md.py`, `python_floor.py`, ratchet, `sonar findings`, `sonar properties`' read of `sonar-project.properties` and the verdict read the checkout's own files only through `repository.py`, which keeps each read
inside the repository and names the file repository-relative in every error.

The operator's rulings stand dated in the reference entry of the behaviour they rule (`docs/`); decisions with rejected alternatives: `docs/decisions/`.

quality has no total line budget; its own size baseline only falls (operator ruling 18.10.2026, replacing a budget of about 3,000 lines: „wenn es mehr braucht braucht es eben mehr, aber natürlich soll der code gut sein!“).

Checks (all targeted, run from the repo root):

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_<module>.py` (the module you changed; never the whole suite locally, CI runs it)
- `mise --cd src/quality/mise install --locked` (once per scanner bump; needs mise)
- `uv run quality hygiene .` (needs mise, the three scanners and network: no scan is skipped, so each run downloads the PyPI advisory database, about 37 MB)

The version comes from git (hatch-vcs): a tag build prints the tag, any later commit `<next>.devN+g<sha>`, so a checkout
that installs quality needs its tags (`fetch-depth: 0`). Release: tag the merge commit locally, `uv sync --reinstall-package overnightworks-quality` (a checkout dirty at its last sync
otherwise keeps a `.dYYYYMMDD` version), then `uv run python scripts/verify_release.py`, and push the tag only when it exits 0; tags are immutable on the remote.
A minor release `vX.Y.0` is tagged on main. A patch `vX.Y.Z` is tagged on main or, while main takes no tag, on `release/vX.Y`, cut from the last `vX.Y` tag: its
pull requests target that branch, pass the same gate and merge with `gh pr merge --merge --match-head-commit <sha>` once `quality / verdict` is green (`aco land` lands only
into main); after the tag the branch is merged into main.

Tests build their git fixtures in `tmp_path`; never commit a `.env` or compose fail file.

Tests mirror `src/quality/` flat in `tests/`; script tests sit in `tests/scripts/`, workflow tests in `tests/workflows/`, data in
`tests/fixtures/`. Fixtures live in `tests/conftest.py`; helpers that other test modules use live in `tests/support/` and are
imported from the repository root (`from tests.support.git_repositories import make_repo`). A test module imports no other test
module and no `conftest`.

Every test finds its scanners through a fake `mise` first on `PATH` (the autouse `served_scanners` in `tests/conftest.py`); a test marked `scanner` uses the real mise and the scanners it installed (CI installs them).

A test's kind, `unit` or `integration`, is derived in `tests/conftest.py` from the definition in `pyproject.toml`'s `markers`, and a
`unit` test that starts a process or opens a socket fails. A test that starts a process or opens a socket outside the fixtures that do carries
`@pytest.mark.integration`.
