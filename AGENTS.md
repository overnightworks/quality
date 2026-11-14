# quality

## Commands

- `uv sync --locked`: install the locked dependencies; needs uv.
- `uv run ruff check`: lint; run after `uv sync --locked`.
- `uv run pytest tests/test_<module>.py`: the tests of the module you changed; run after `uv sync --locked`.
- `mise --cd src/quality/mise install --locked`: install the scanners `src/quality/mise/mise.toml` pins; needs mise; run on a fresh machine and after each change of that file.
- `uv run quality check`: quality's own gate on this checkout; needs those scanners and network, since every run asks
  the server about the advisory databases.
- `quality merge <owner/repo>#<pr>`: the operator's door past a red gate; an agent never runs it.
- `gh workflow run release.yml -R overnightworks/quality -f version=X.Y.Z`: dispatch quality's release; an agent runs it once the release pull request's review and CI are green.

## Rules

- A finding's event is a `report.FindingEvent` member and a note's a `report.NoteEvent` member, never defined in `output.py`.
- A checkout that installs quality needs its git tags (CI uses `fetch-depth: 0`), because the version comes from them.
- quality has no total line budget; its size baseline only falls.

## Owners

- The operator decides every merge past a red gate.

## Documents

- `docs/ARCHITECTURE.md`: read when you change a module's job, a boundary or a trust decision.
- `docs/VISION.md`: read when you judge whether a feature belongs in quality.
- `docs/rules.md`: read when you change a `check` rule, the secret scan or the advisory scan.
- `docs/commands.md`: read when you change `check secrets --staged`, the scanner handling, `report` or `merge`.
- `docs/output.md`: read when you change a printed line, an event word or an exit code.
- `docs/gate.md`: read when you change `gate.yml`, the verdict or a declared loosening.
- `docs/codeql.md`: read when you change `codeql.yml` or its caller contract.
- `docs/release.md`: read when you change a release workflow, the image build or how quality releases itself.
- `docs/sonar.md`: read when you change `sonar findings`, coverage on new code or the Sonar gate.
- `docs/runbooks/`: read when the operator's door is the question, a pull request red that no change can turn green; the files are named by their task.
