# quality

One door, `quality` (`src/quality/cli.py`). Rules return `Finding`s (`report.py`) and never print.
Exit 0 clean, 1 findings, 2 cannot judge.

Door commands:

- `quality hygiene [PATH]` (`hygiene.py`): `.env` files, compose ports, the `size` ratchet (`ratchet.py`), the
  `clone` rule (`clones.py`, report-only), the `import-contract` rule (`architecture.py`, import-linter as a library), the `claude-md` rule (a tracked `CLAUDE.md` is
  exactly `@AGENTS.md`), and with `--engines` the secret and workflow
  scans and the `advisory` rule (`advisories.py`: osv-scanner over every tracked `uv.lock`, against a PyPI database
  it downloads itself).
- `quality baseline write [PATH]` (`ratchet.py`): writes the measured size debt and the clone pairs to `quality/baseline.toml`,
  naming each entry it raises or adds.
- `quality verdict [PATH]` (`verdict.py`): judges the mandatory jobs of the running workflow run and, on a pull request, holds the head's config and baseline against the base commit's (a baseline only falls; a loosening stays red, labelled declared when the head adds its `[[gate.loosened]]` entry); the fail-closed `quality / verdict`.
- `quality scoreboard [PATH]` (`scoreboard.py`): the fleet's size and each repository's gate and CodeQL release and what each gate does with each rule in two
  markdown tables, from anonymous shallow clones; a private repository is never measured. The fleet list is `quality/fleet.toml` (`public` and `private`).
- `quality scan-staged [PATH] --engines DIR` (`staged.py`): secret scan of the staged change.
- `quality engines install|verify DIR` (`engines.py`): the pinned engines (`engines.toml`; `format = "binary"` for a bare executable).
- `quality sonar preflight [PATH]` and `quality sonar findings` (`sonar.py`): HTTP goes through an injected
  transport, polling through an injected sleep, so tests need no network.
- `quality emergency-merge <owner/repo> <pr>` (`emergency.py`): the operator's door for legacy findings and declared
  loosenings, it needs a terminal on stdin and prints only through its injected `say`.

`.github/workflows/codeql.yml` is the reusable CodeQL workflow (input `languages`); `code-scanning.yml` is quality's own caller of it.

`.github/workflows/gate.yml` is the reusable gate; its caller contract stands in the README's `gate.yml` section and in the file's header.

quality's own layers are an import-linter contract in `pyproject.toml` (`[tool.importlinter]`); `quality hygiene .`
enforces it, so a module imports only from the layers below its own.

Config, hygiene, `sonar findings` and the verdict read the checkout's own files only through `repository.py`, which keeps each read
inside the repository and names the file repository-relative in every error.

Roadmap and the operator's rulings: `docs/roadmap.md`, `docs/rulings.md`.

Checks (all targeted, run from the repo root):

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_<module>.py` (the module you changed; never the whole suite locally, CI runs it)
- `uv run quality hygiene .`

Tests build their git fixtures in `tmp_path`; never commit a `.env` or compose fail file.
