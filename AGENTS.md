# quality

One door, `quality` (`src/quality/cli.py`). Rules return `Finding`s (`report.py`) and never print.
Exit 0 clean, 1 findings, 2 cannot judge.

Door commands:

- `quality hygiene [PATH]` (`hygiene.py`): `.env` files, compose ports, the `size` ratchet (`ratchet.py`), and with
  `--engines` the secret and workflow scans.
- `quality baseline write [PATH]` (`ratchet.py`): writes the measured size debt to `quality/baseline.toml`.
- `quality verdict [PATH]` (`verdict.py`): judges the mandatory jobs of the running workflow run; the fail-closed `quality / verdict`.
- `quality scan-staged [PATH] --engines DIR` (`staged.py`): secret scan of the staged change.
- `quality engines install|verify DIR` (`engines.py`): the pinned engines.
- `quality sonar preflight [PATH]` and `quality sonar findings` (`sonar.py`): HTTP goes through an injected
  transport, polling through an injected sleep, so tests need no network.
- `quality emergency-merge <owner/repo> <pr>` (`emergency.py`): the operator's door, it needs a terminal on stdin.

Roadmap and the operator's rulings: `docs/roadmap.md`, `docs/rulings.md`.

Checks (all targeted, run from the repo root):

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_<module>.py` (the module you changed; never the whole suite locally, CI runs it)
- `uv run quality hygiene .`

Tests build their git fixtures in `tmp_path`; never commit a `.env` or compose fail file.
