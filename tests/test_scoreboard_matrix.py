"""`quality scoreboard` release matrix (#123): cells name the item's scenario matrix, rules its numbered rules."""

import re
import subprocess

import pytest
from conftest import GIT_IDENTITY, NEWEST, OLDER, RELEASES, head_of, row

from quality.scoreboard import (
    COLUMNS,
    MATRIX_COLUMNS,
    Call,
    ReleaseTagsError,
    calls_of,
    pin_cell,
    read_release_tags,
    repository_name,
)

UNRELEASED = "c" * 40
GATE = "overnightworks/quality/.github/workflows/gate.yml"
CODEQL = "overnightworks/quality/.github/workflows/codeql.yml"


def matrix_row(output: str, name: str) -> list[str]:
    return row(output, name, table=1)


def caller(uses: str) -> dict[str, str]:
    """A repository whose one workflow calls `uses` from a job."""
    return {".github/workflows/ci.yml": f"on: push\njobs:\n  gate:\n    uses: {uses}\n"}


@pytest.mark.parametrize(
    ("ref", "cell"),
    [
        (NEWEST, "v0.10.0"),
        (OLDER, "v0.9.0 (behind)"),
        (UNRELEASED, "ccccccc (no release)"),
        ("main", "main (not a commit)"),
        ("v0.3.1", "v0.3.1 (not a commit)"),
    ],
    ids=["newest tag", "older tag", "no tag", "branch", "tag name"],
)
def test_the_gate_cell_names_the_release_the_pinned_commit_is(scoreboard, public, ref, cell):
    """#123 E3, cells 1 to 4: the tag the SHA points at, `(behind)` when not the newest; the `# v` comment is not read."""
    code, output = scoreboard(public("pinned", caller(f"{GATE}@{ref} # v0.3.1")))
    assert code == 0, output
    assert matrix_row(output, "pinned")[1:] == [cell, "none"]


def test_without_a_caller_there_is_no_gate_and_a_step_level_uses_is_no_caller(scoreboard, public):
    """#123 E3, cell 5: only a job-level `uses:` is a call."""
    steps = {".github/workflows/ci.yml": f"jobs:\n  build:\n    steps:\n      - uses: {GATE}@{NEWEST}\n"}
    _, output = scoreboard(public("bare", {}), public("stepped", steps))
    assert matrix_row(output, "bare")[1:] == matrix_row(output, "stepped")[1:] == ["no gate", "none"]


def test_the_repositorys_own_workflow_file_is_self_only_in_the_gates_repository():
    """#123 E3, cell 6: `./` and `$/` name the gate's own file only where the repository is the gate's."""
    own = (Call(None),)
    assert pin_cell(own, "no gate", True, RELEASES) == "self"
    assert pin_cell(own, "no gate", False, RELEASES) == "no gate"


@pytest.mark.parametrize("workflow_file", ["gate.yml", "codeql.yml"])
@pytest.mark.parametrize("prefix", ["./", "$/"])
def test_a_local_reference_to_the_gates_own_file_is_a_call_without_a_ref(prefix, workflow_file):
    """#123 E3, cell 6: `./` and `$/` both name the repository's own copy."""
    assert calls_of(f"{prefix}.github/workflows/{workflow_file}", workflow_file) == [Call(None)]


def test_the_gates_owner_and_repository_names_are_read_whatever_their_case():
    """GitHub reads `uses:` owner and repository case-insensitively."""
    assert calls_of("OvernightWorks/Quality/.github/workflows/gate.yml@v1", "gate.yml") == [Call("v1")]


def test_a_commit_in_upper_case_is_a_commit(scoreboard, public):
    _, output = scoreboard(public("shouting", caller(f"{GATE}@{NEWEST.upper()}")))
    assert matrix_row(output, "shouting")[1] == "v0.10.0"


def test_two_callers_with_two_pins_are_both_named_sorted_and_joined(scoreboard, public):
    """#123 E3, cell 7."""
    files = {
        ".github/workflows/a.yml": f"jobs:\n  one:\n    uses: {GATE}@{NEWEST}\n",
        ".github/workflows/b.yaml": f"jobs:\n  two:\n    uses: {GATE}@{OLDER}\n",
    }
    _, output = scoreboard(public("both", files))
    assert matrix_row(output, "both")[1] == "v0.10.0 · v0.9.0 (behind)"


@pytest.mark.parametrize(
    "content",
    [
        "jobs: [unclosed\n",
        "env:\n  BUILD_DATE: 2026-99-99\njobs: {}\n",
        "env:\n  BUILD_DATE: !!timestamp zz\njobs: {}\n",
        "env:\n  RETRIES: !!int ''\njobs: {}\n",
        "env:\n  FLAG: !!bool x\njobs: {}\n",
        "env:\n  nested: " + "[" * 100000 + "\njobs: {}\n",
        "? [a]\n: b\njobs: {}\n",
        "env:\n  RATIO: 1:" + ":".join(["1"] * 400) + ".5\njobs: {}\n",
    ],
    ids=[
        "not YAML",
        "an impossible date",
        "a timestamp tag on a non-timestamp",
        "an empty integer",
        "an unknown boolean",
        "nesting past the recursion limit",
        "an unhashable key",
        "a sexagesimal float too large for a float",
    ],
)
def test_a_workflow_that_is_not_yaml_is_named_in_its_cells_and_the_other_rows_stand(scoreboard, public, content):
    """#123 E11, cell 8: `cannot read <file>`, exit 2, and nothing about the broken file is run or guessed."""
    broken = {".github/workflows/x.yml": content}
    code, output = scoreboard(public("broken", broken), public("fine", caller(f"{GATE}@{NEWEST}")))
    assert code == 2, output
    assert matrix_row(output, "broken")[1:] == ["cannot read .github/workflows/x.yml"] * 2
    assert matrix_row(output, "fine")[1] == "v0.10.0"


def test_unreadable_release_tags_say_so_and_show_pins_as_commits(scoreboard, public):
    """#123 E4, cell 9."""

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    code, output = scoreboard(public("pinned", caller(f"{GATE}@{NEWEST}")), tags=unreadable)
    assert code == 2, output
    assert "**Repo × rule, newest release unreadable (git exited 128), pins shown as commits**" in output
    assert matrix_row(output, "pinned")[1] == "bbbbbbb"


def test_the_codeql_cell_reads_its_own_pin_or_says_none(scoreboard, public):
    """#123 E5, cell 10."""
    gate = caller(f"{GATE}@{NEWEST}")
    scan = {".github/workflows/code.yml": f"jobs:\n  scan:\n    uses: {CODEQL}@{OLDER}\n", **gate}
    _, output = scoreboard(public("scanned", scan), public("unscanned", gate))
    assert matrix_row(output, "scanned")[1:] == ["v0.10.0", "v0.9.0 (behind)"]
    assert matrix_row(output, "unscanned")[1:] == ["v0.10.0", "none"]


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/overnightworks/quality", "overnightworks/quality"),
        ("https://github.com/overnightworks/quality.git", "overnightworks/quality"),
        ("ssh://git@github.com/o/r.git", "o/r"),
        ("https://user:secret@github.com/o/r", "o/r"),
        ("file:///tmp/org/repo", "repo"),
        ("file://localhost/tmp/org/repo", "repo"),
        ("git@github.com:o/r.git", "r"),
        ("https://github.com/lonely", "lonely"),
        ("https://user:secret@example.invalid/", "example.invalid"),
    ],
)
def test_a_row_is_named_owner_slash_name_when_the_url_has_a_host(url, name):
    """#123 E2, cell 11: a file URL and a URL without a host keep their last part; a credential is never shown."""
    assert repository_name(url) == name


def test_a_pipe_in_a_label_is_escaped_and_the_row_keeps_its_columns(scoreboard):
    """#123 E2, cell 12."""
    _, output = scoreboard("file:///nowhere/a|b")
    lines = [line for line in output.splitlines() if line.startswith("| a\\|b |")]
    assert len(lines) == 2
    assert [len(re.split(r"(?<!\\)\|", line.strip("|"))) for line in lines] == [len(COLUMNS), len(MATRIX_COLUMNS)]


def test_a_private_and_a_cannot_judge_row_keep_their_label_in_the_matrix(scoreboard, public, missing):
    """#123 E10, cell 13."""
    code, output = scoreboard("ssh://git@example.invalid/org/repo.git", public("open", {}), private=[missing])
    assert code == 2, output
    assert matrix_row(output, "org/repo")[1:] == ["cannot judge: cannot clone (not an http, https or file URL)", ""]
    assert matrix_row(output, "gone")[1:] == ["not measured: private", ""]


def test_release_tags_are_the_peeled_commits_of_annotated_and_lightweight_tags(public):
    """#123 E4, cell 15: `ls-remote` lists an annotated tag twice; the commit is the peeled line. No tag is unreadable."""
    url = public("gate", {"a": "1\n"})
    with pytest.raises(ReleaseTagsError):
        read_release_tags(url)
    git = ["git", "-C", url.removeprefix("file://"), *GIT_IDENTITY]
    for tag in (["v0.1.0"], ["-a", "v0.2.0", "-m", "release"], ["not-a-release"]):
        subprocess.run([*git, "tag", *tag], check=True)
    assert read_release_tags(url) == {"v0.1.0": head_of(url), "v0.2.0": head_of(url)}


@pytest.mark.parametrize(
    ("uses", "cell"),
    [
        ("OvernightWorks/Quality/.github/workflows/gate.yml@" + NEWEST, "v0.10.0"),
        ("overnightworks/quality/.github/workflows/Gate.yml@" + NEWEST, "no gate"),
    ],
    ids=["owner and repository match in any case", "the workflow path matches exactly"],
)
def test_only_the_owner_and_repository_of_a_reusable_call_ignore_case(scoreboard, public, uses, cell):
    _, output = scoreboard(public("cased", caller(uses)))
    assert matrix_row(output, "cased")[1] == cell
