# quality

One door: `fleet-quality hygiene [PATH]` (`src/fleet_quality/cli.py`). Rules live in `hygiene.py`, return `Finding`s
(`report.py`) and never print. Exit 0 clean, 1 findings, 2 cannot judge.

Checks (all targeted, run from the repo root):

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_hygiene.py`
- `uv run fleet-quality hygiene .`

Tests build their git fixtures in `tmp_path`; never commit a `.env` or compose fail file.
