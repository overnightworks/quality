"""`quality report`'s streams: the fleet line, a row per repository and the closing line or the pair on stderr, the
tables alone on stdout and in the step summary."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quality import __version__, cli
from quality.fleet import ReleaseTagsError, TagReader
from tests.support.fleet import NEWEST, RELEASES, row, write_fleet
from tests.support.git_repositories import head_commit


@dataclass(frozen=True)
class Printed:
    code: int
    out: str
    err: str


def ticks(*seconds: float) -> Callable[[], float]:
    """A clock that reads each of `seconds` once; one read too many is a StopIteration."""
    return iter(seconds).__next__


def report_in(
    folder: Path,
    capsys,
    monkeypatch,
    clock: Callable[[], float],
    words: tuple[str, ...] = (),
    tags: TagReader = lambda: RELEASES,
    environment: Mapping[str, str] | None = None,
) -> Printed:
    monkeypatch.chdir(folder)
    code = cli.main(
        ["report", *words],
        release_tags=tags,
        clock=clock,
        now=lambda: datetime(2026, 11, 6, 12, tzinfo=UTC),
        environment=environment or {},
    )
    printed = capsys.readouterr()
    return Printed(code, printed.out, printed.err)


def gate_caller(ref: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": f"jobs:\n  gate:\n    uses: overnightworks/quality/.github/workflows/gate.yml@{ref}\n"}


def test_a_measured_fleet_prints_its_rows_on_stderr_and_only_the_tables_on_stdout(capsys, monkeypatch, tmp_path, public, remotes):
    alpha = public("alpha", {"a.py": "a = 1\n"})
    beta = public("beta-repository", {"b.py": "b = 1\n"})
    folder = write_fleet(tmp_path / "fleet", [beta, alpha])
    printed = report_in(folder, capsys, monkeypatch, ticks(5, 6, 9, 9, 14, 77))
    alpha_commit, beta_commit = head_commit(remotes / alpha)[:7], head_commit(remotes / beta)[:7]
    assert (printed.code, printed.err) == (
        0,
        ("  · fleet              2 listed       quality/fleet.toml\n"
        f"  ✓ o/alpha            measured       {alpha_commit} · 3s\n"
        f"  ✓ o/beta-repository  measured       {beta_commit} · 5s\n"
        "✓ 2 of 2 repositories measured · 1m12s\n"),
    )
    lines = printed.out.splitlines()
    assert lines[:6] == [
        f"**Fleet report, 2026-11-06, quality {__version__} — functions and CCN: Python only**",
        "",
        ("| Repo | Commit | Languages | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
        "| CCN \\> 15 prod / test | Clone pairs prod / test | Note |"),
        "|---" * 10 + "|",
        f"| o/alpha | {alpha_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
        f"| o/beta-repository | {beta_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
    ]
    assert lines[6:9] == ["", "**Repo × rule, newest release v0.10.0**", ""]
    assert [cells[:3] for cells in (row(printed.out, alpha, 1), row(printed.out, beta, 1))] == [
        ["o/alpha", "no gate", "none"],
        ["o/beta-repository", "no gate", "none"],
    ]
    assert (len(lines), [line.split(" |")[0] for line in lines[-2:]]) == (15, ["| o/alpha", "| o/beta-repository"])


def test_a_repository_that_cannot_be_cloned_is_its_row_and_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public, missing, remotes):
    alpha = public("alpha", {"a.py": "a = 1\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, missing]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         2 listed       quality/fleet.toml\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "  ✗ o/gone        not measured   cannot clone (git exited 128)\n"
        "error: 1 of 2 repositories not measured · the tables say so\n"
        "next: quality report\n"),
    )
    assert row(printed.out, missing)[2] == "cannot judge: cannot clone (git exited 128)"


def test_unread_release_tags_stand_under_the_fleet_line_and_in_the_pair(capsys, monkeypatch, tmp_path, public, remotes):
    alpha = public("alpha", {"a.py": "a = 1\n"})

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), tags=unreadable)
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 listed       quality/fleet.toml\n"
        "  ✗ release tags  not read       git exited 128\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "error: the release tags not read · the tables say so\n"
        "next: quality report\n"),
    )


def test_a_cell_that_cannot_read_a_file_makes_the_row_partly_read(capsys, monkeypatch, tmp_path, public):
    alpha = public("alpha", {**gate_caller(NEWEST), "quality/config.toml": "[gate\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 listed       quality/fleet.toml\n"
        "  ✗ o/alpha       partly read    cannot read quality/config.toml\n"
        "error: 1 partly read · the tables say so\n"
        "next: quality report\n"),
    )


def test_an_unwritable_step_summary_is_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public):
    alpha = public("alpha", {"a.py": "a = 1\n"})
    summary = tmp_path / "a-directory"
    summary.mkdir()
    printed = report_in(
        write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (printed.code, printed.out.splitlines()[0].startswith("**Fleet report, "), "| o/alpha |" in printed.out) == (2, True, True)
    assert printed.err.endswith(
        f"error: GITHUB_STEP_SUMMARY is not writable · [Errno 21] Is a directory: '{summary}'\n"
        "next: fix what the error names, then run quality report again\n"
    )


@pytest.mark.parametrize(
    ("ref", "cell"),
    [
        ("<!--x", "\\<!--x (not a commit)"),
        ("[a](b)", "\\[a\\](b) (not a commit)"),
        ("a\\b", "a\\\\b (not a commit)"),
        ("a|b", "a\\|b (not a commit)"),
        ("*a*_b_", "\\*a\\*\\_b\\_ (not a commit)"),
    ],
    ids=["comment", "link", "backslash", "cell border", "emphasis"],
)
def test_markdown_in_repository_text_stays_text_in_the_tables(capsys, monkeypatch, tmp_path, public, ref, cell):
    hostile = public("hostile", gate_caller(ref))
    summary = tmp_path / "summary.md"
    printed = report_in(
        write_fleet(tmp_path / "fleet", [hostile]), capsys, monkeypatch, ticks(0, 1, 2, 3), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (row(printed.out, hostile, 1)[1], row(summary.read_text(), hostile, 1)[1]) == (cell, cell)


def test_report_takes_v_and_prints_the_same(capsys, monkeypatch, tmp_path, public):
    folder = write_fleet(tmp_path / "fleet", [public("alpha", {"a.py": "a = 1\n"})])
    plain = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3))
    verbose = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3), words=("-v",))
    assert verbose == plain
