# quality

## Owners

- One door, `quality` (`src/quality/cli.py`). Every line it prints, the step summary and the step output leave
  through `output.py`.
- Rules return `Finding`s (`report.py`) and never print. A finding's event is a `report.FindingEvent` member, a note's
  a `report.NoteEvent` member, never defined in `output.py`.
- A `CannotJudge` carries a few-word fact and, apart from it, the next steps its mechanism knows as typed values
  (`report.Reason`). A `ConfigError` carries its fact and its TOML example apart; `report.config_refusal` turns it
  into a refusal.
- Each rule's id, area, place among its area's rules and facts are one member of `Rule` (`rules.py`). Areas run and
  print in `Area`'s order, and an area's help sentence is its member of `Area`.
- `checkout.py` lists the checkout through git, decides what a workflow file is, which release the gate calls of the
  workflows pin, and whether the checkout is quality's own (by its git remote), owns the full-commit form and turns a
  refused read into cannot judge. quality reads the checkout's own files through `repository.py`, which keeps each read inside the repository
  and names the file repository-relative in every error; only `architecture.py` opens a source itself, for grimp.
- `pin.py` decides which quality judges a `check` from the checkout's gate pin and the commit the running
  installation is (its installer's record, or an editable directory's clean HEAD at its release tag); a run it refuses
  prints only the pair.
- A scanner's stderr reaches a fact or `-v` only through `scanners`' vet (`gitleaks stdin` under `gitleaks.toml`) and
  is withheld whole when gitleaks does not clear it. `check.py` keeps `report.ScannerRun`s only under `-v`,
  `output.py` prints them, and a scanner's report never enters.
- HTTP goes through `web.py`, which follows no redirect and bounds every wait; the door injects the transport, the
  GitHub fetcher and the poll sleep, so tests need no network.
- The line a merge commit ends with is `landing.py`'s.

The references in `docs/` own what each command does; `docs/ARCHITECTURE.md` owns the parts and their reasons.

quality has no total line budget; its size baseline only falls.

## Checks

Run from the repository root:

- `uv sync --locked`
- `uv run ruff check`
- `uv run pytest tests/test_<module>.py`, the module you changed
- `mise --cd src/quality/mise install --locked`, once per scanner bump
- `uv run quality check` (needs mise, its three scanners and network: every run downloads the advisory databases)

## Tests

- Helpers several test modules use live in `tests/support/`, imported from the root
  (`from tests.support.git_repositories import make_repo`).
- A test that starts a process or opens a socket outside the fixtures that do carries `@pytest.mark.integration`.

## Release

The version comes from git tags (hatch-vcs), so a checkout that installs quality needs its tags (`fetch-depth: 0`).
A release is dispatched as [docs/release.md](docs/release.md#qualitys-own-release) says. A patch `vX.Y.Z` while main
takes no tag is tagged by hand on `release/vX.Y`, cut from the last `vX.Y` tag: tag its merge commit locally, run
`uv sync --reinstall-package overnightworks-quality` (a checkout dirty at its last sync otherwise keeps a
`.dYYYYMMDD` version) and `uv run python scripts/verify_release.py`, and push the tag only when it exits 0; tags are
immutable on the remote. Its pull requests target that branch, pass the same gate and merge with
`gh pr merge --merge --match-head-commit <sha>` once `quality / verdict` is green (`aco land` lands only into main);
after the tag the branch is merged into main and deleted.
