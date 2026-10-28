# quality

One door, `quality` (`src/quality/cli.py`). Rules return `Finding`s (`report.py`) and never print.
Exit 0 clean, 1 findings, 2 cannot judge.

Door commands:

- `quality hygiene [PATH]` (`hygiene.py`): `.env` files, compose ports, the `size` ratchet (`ratchet.py`), the
  `clone` rule (`clones.py`, enforced with `[clones]` in the baseline, else report-only), the `import-contract` rule (`architecture.py`, import-linter as a library), the `claude-md` rule (a tracked `CLAUDE.md` is
  exactly `@AGENTS.md`), the `root-layout` rule (every root entry is on a base list or declared with `[[root.allow]]`), the `file-suffix` rule (`layout.py`: a tracked file without a suffix is executable, a link, a dotfile, tool-mandated or declared with `[[layout.allow]]`), the `script-name` and `script-header` rules (`layout.py`: a script is named `<word>_<word>` and lies under `scripts/`, and opens with a comment or docstring), the `test-layout` rule (`layout.py`: what may lie under a Python project's `tests/`, each `test_*.py` mirrors the `src/` module it tests, a Vitest test sits beside its subject and a Playwright spec lies in its project's `e2e/`), the `test-import` rule (`layout.py`: no file under a Python project's `tests/` imports a test module or `conftest`), the `python-floor` rule (`hygiene.py`: every Python project's `requires-python` floor is 3.14 or declared with `[[python.allow]]`), the `python-security` rule (`python_lint.py`: quality's pinned ruff reports the `S` codes in every tracked `.py` file, and only a reasoned noqa clears one), and with `--engines` the secret and workflow
  scans and the `advisory` rule (`advisories.py`: osv-scanner over every tracked `uv.lock`, `pnpm-lock.yaml` and `requirements*.txt` (read by `pip_requirements.py`), against the PyPI and npm
  databases it downloads itself).
- `quality baseline write [PATH]` (`ratchet.py`): writes the measured size debt and the clone pairs to `quality/baseline.toml`,
  naming each entry it raises or adds.
- `quality verdict [PATH]` (`verdict.py`): judges the mandatory jobs of the running workflow run and, on a pull request, holds the head's config, baseline and import-linter configs against the base commit's (a baseline only falls, an import contract only tightens; a loosening stays red, labelled declared when the head adds its `[[gate.loosened]]` entry); the fail-closed `quality / verdict`.
- `quality scoreboard [PATH]` (`scoreboard.py`): the fleet's size and each repository's gate and CodeQL release and what each gate does with each rule in two
  markdown tables, from anonymous shallow clones; a private repository is never measured. The fleet list is `quality/fleet.toml` (`public` and `private`).
- `quality scan-staged [PATH] --engines DIR` (`staged.py`): secret scan of the staged change.
- `quality engines install|verify DIR` (`engines.py`): the pinned engines (`engines.toml`; `format = "binary"` for a bare executable).
- `quality sonar preflight [PATH]` and `quality sonar findings` (`sonar.py`): HTTP goes through an injected
  transport, polling through an injected sleep, so tests need no network.
- `quality emergency-merge <owner/repo> <pr>` (`emergency.py`): the operator's door for legacy findings and declared
  loosenings, it needs a terminal on stdin and prints only through its injected `say`.

`.github/workflows/codeql.yml` is the reusable CodeQL workflow (input `languages`); `code-scanning.yml` is quality's own caller of it.

`.github/workflows/gate.yml` is the reusable gate; its caller contract stands in `docs/gate.md` and in the file's header.

quality's own layers are an import-linter contract in `pyproject.toml` (`[tool.importlinter]`); `quality hygiene .`
enforces it, so a module imports only from the layers below its own.

Config, hygiene, `sonar findings` and the verdict read the checkout's own files only through `repository.py`, which keeps each read
inside the repository and names the file repository-relative in every error.

The operator's rulings stand dated in the reference entry of the behaviour they rule (`docs/`); decisions with rejected alternatives: `docs/decisions/`.

quality has no total line budget; its own size baseline only falls (operator ruling 18.10.2026, replacing a budget of about 3,000 lines: „wenn es mehr braucht braucht es eben mehr, aber natürlich soll der code gut sein!“).

Checks (all targeted, run from the repo root):

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_<module>.py` (the module you changed; never the whole suite locally, CI runs it)
- `uv run quality hygiene .`

The version comes from git (hatch-vcs): a tag build prints the tag, any later commit `<next>.devN+g<sha>`, so a checkout
that installs quality needs its tags (`fetch-depth: 0`). Release: tag the merge commit locally, `uv sync --reinstall-package overnightworks-quality` (a checkout dirty at its last sync
otherwise keeps a `.dYYYYMMDD` version), then `uv run python scripts/verify_release.py`, and push the tag only when it exits 0; tags are immutable on the remote.

Tests build their git fixtures in `tmp_path`; never commit a `.env` or compose fail file.

Tests mirror `src/quality/` flat in `tests/`; script tests sit in `tests/scripts/`, workflow tests in `tests/workflows/`, data in
`tests/fixtures/`. Fixtures live in `tests/conftest.py`; helpers that other test modules use live in `tests/support/` and are
imported from the repository root (`from tests.support.git_repositories import make_repo`). A test module imports no other test
module and no `conftest`.

A test's kind, `unit` or `integration`, is derived in `tests/conftest.py` from the definition in `pyproject.toml`'s `markers`, and a
`unit` test that starts a process or opens a socket fails. A test that starts a process or opens a socket outside the fixtures that do carries
`@pytest.mark.integration`.
