from functools import partial
from pathlib import Path

import pytest

from quality import checkout, pin
from quality.installation import VcsOrigin
from quality.pin import RunningQuality
from tests.support.door import DRY_RUN_WORDS, STAGED_WORDS, door_in
from tests.support.git_repositories import stage
from tests.support.layout import FILES_RULE_IDS

PINNED_ROW = "  · release       v0.10.0        pinned in .github/workflows/ci.yml"


def pinned_commit() -> str:
    return "5e" * 20


def other_commit() -> str:
    return "4d" * 20


def gate_call(release: str) -> dict[str, str]:
    uses = f"{checkout.GATE_REPOSITORY}/.github/workflows/gate.yml@{pinned_commit()} # {release}"
    return {".github/workflows/ci.yml": f"on: push\njobs:\n  gate:\n    uses: {uses}\n"}


def running(version: str, commit: str):
    return partial(pin.judging_release, running=RunningQuality(version, Path(pin.__file__).parent, VcsOrigin(commit)))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "version", "commit", "words", "expected"),
    [
        pytest.param(
            gate_call("v0.10.0"), "0.10.0", pinned_commit(), ("check", "files"),
            (0, [PINNED_ROW, f"  ✓ files         clean          {len(FILES_RULE_IDS)} rules · 2 files", "✓ clean · 1 area · <1s"], []),
            id="check at the pin",
        ),
        pytest.param(
            gate_call("v0.10.0"), "0.10.0", pinned_commit(), DRY_RUN_WORDS,
            (
                1,
                [
                    PINNED_ROW, "  ~ debt          to record      0 entries, 0 clone pairs · quality/baseline.toml would be new",
                    "~ dry run · ceiling would be recorded · nothing written · <1s",
                ],
                [],
            ),
            id="the dry run at the pin",
        ),
        pytest.param(
            gate_call("v0.9.2"), "0.10.0", other_commit(), ("check", "files"),
            (2, [], ["error: the gate pins quality v0.9.2, which has no quality check", "next: raise the gate pin to v0.10.0 or later"]),
            id="a pin below the floor",
        ),
        pytest.param(
            {"README.md": "no gate\n"}, "0.10.0", pinned_commit(), ("check", "files"),
            (2, [], ["error: no workflow here calls quality's gate.yml, so no release is pinned", "next: add the gate as quality's docs/gate.md describes"]),
            id="no pin",
        ),
        pytest.param(
            gate_call("v0.10.0"), "0.10.1", pinned_commit(), ("check", "files"),
            (
                2, [],
                [
                    "error: the gate pins 5e5e5e5 as v0.10.0, but that commit is quality 0.10.1",
                    "next: make the pin's comment name its commit's own tag, as docs/gate.md shows",
                ],
            ),
            id="a misnamed comment",
        ),
        pytest.param(
            gate_call("v0.10.0"), "0.10.0", pinned_commit(), STAGED_WORDS,
            (0, [PINNED_ROW, "  ✓ secrets       clean          staged change · 1 file", "✓ clean · 1 area · <1s"], []),
            id="the commit hook at the pin", marks=pytest.mark.scanner,
        ),
        pytest.param(
            {"README.md": "no gate\n"}, "0.10.0", pinned_commit(), STAGED_WORDS,
            (
                0,
                ["  · release       v0.10.0        installed · no gate pins a release here", "  ✓ secrets       clean          staged change · 1 file", "✓ clean · 1 area · <1s"],
                [],
            ),
            id="the commit hook without a gate", marks=pytest.mark.scanner,
        ),
    ],
)
def test_check_names_the_release_that_judges_first_or_refuses_with_its_pair(build_repo, capsys, files, version, commit, words, expected):
    """PIN-1, PIN-5, PIN-8, PIN-10, PIN-11, PIN-12, ROW-17: `check` names the release that judges in its first row, or
    is exit 2 with its pair and no row; the commit hook judges with the installed release where no gate pins one."""
    repo = build_repo(files)
    stage(repo, {"docs/notes.md": "nothing secret\n"})
    code = door_in(repo, *words, judging=running(version, commit), clock=lambda: 0.0)
    printed = capsys.readouterr()
    assert (code, printed.out.splitlines(), printed.err.splitlines()) == expected


@pytest.mark.integration
def test_outside_a_work_tree_the_check_prints_only_the_location_pair(elsewhere, capsys):
    """CLOSING-17, CLOSING-18, CLOSING-19: outside a work tree the check prints only the location pair, with no release
    row."""
    code = door_in(elsewhere, "check", "files", judging=running("0.10.0", pinned_commit()), clock=lambda: 0.0)
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err.startswith("error: not inside a git work tree · ")) == (2, "", True)
    assert printed.err.endswith("next: run quality check inside a repository\n")
