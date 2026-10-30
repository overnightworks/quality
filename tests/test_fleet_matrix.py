"""`quality scoreboard` release matrix (#123): cells name the item's scenario matrix, rules its numbered rules."""

import os
import re
import subprocess

import pytest

from quality import ratchet
from quality.fleet import (
    COLUMNS,
    GATE_REPOSITORY,
    MATRIX_COLUMNS,
    Call,
    Declared,
    ReleaseTagsError,
    calls_of,
    gate_release,
    pin_cell,
    read_release_tags,
    rule_cell,
)
from quality.rules import Rule, RuleRelease, Switch, SwitchChange
from tests.support.architecture import FORBIDDEN, toml
from tests.support.fleet import NEWEST, OLDER, RELEASES, row
from tests.support.git_repositories import GIT_IDENTITY, head_commit

UNRELEASED = "c" * 40
GATE = "overnightworks/quality/.github/workflows/gate.yml"
CODEQL = "overnightworks/quality/.github/workflows/codeql.yml"
FIRST_RELEASES = {"v0.2.1": OLDER, "v0.3.1": NEWEST}
CONFIG = "quality/config.toml"
TWO_IMPORT_CONFIGS = {"pyproject.toml": toml(FORBIDDEN), ".importlinter": "[importlinter]\nroot_package = shop\n"}
TWO_JOBS = '[gate]\nmandatory_jobs = ["checks", "tests"]\n'


def matrix_row(output: str, name: str) -> list[str]:
    return row(output, name, table=1)


def pinned_gate(ref: str | None) -> dict[str, str]:
    """A repository calling the gate at `ref`, or none at all."""
    return {} if ref is None else caller(f"{GATE}@{ref}")


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
    assert matrix_row(output, "o/pinned")[1:3] == [cell, "none"]


def test_without_a_caller_there_is_no_gate_and_a_step_level_uses_is_no_caller(scoreboard, public):
    """#123 E3, cell 5: only a job-level `uses:` is a call."""
    steps = {".github/workflows/ci.yml": f"jobs:\n  build:\n    steps:\n      - uses: {GATE}@{NEWEST}\n"}
    _, output = scoreboard(public("bare", {}), public("stepped", steps))
    assert matrix_row(output, "o/bare")[1:3] == matrix_row(output, "o/stepped")[1:3] == ["no gate", "none"]


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
    assert matrix_row(output, "o/shouting")[1] == "v0.10.0"


def test_two_callers_with_two_pins_are_both_named_sorted_and_joined(scoreboard, public):
    """#123 E3, cell 7."""
    files = {
        ".github/workflows/a.yml": f"jobs:\n  one:\n    uses: {GATE}@{NEWEST}\n",
        ".github/workflows/b.yaml": f"jobs:\n  two:\n    uses: {GATE}@{OLDER}\n",
    }
    _, output = scoreboard(public("both", files))
    assert matrix_row(output, "o/both")[1] == "v0.10.0 · v0.9.0 (behind)"


@pytest.mark.parametrize(
    "content",
    [
        "jobs: [unclosed\n",
        "env:\n  nested: " + "[" * 100000 + "\njobs: {}\n",
        "? [a]\n: b\njobs: {}\n",
        'env:\n  escape: "\\UFFFFFFFF"\njobs: {}\n',
    ],
    ids=["not YAML", "nesting past the recursion limit", "an unhashable key", "a unicode escape past the last code point"],
)
def test_a_workflow_that_is_not_yaml_is_named_in_its_cells_and_the_other_rows_stand(scoreboard, public, content):
    """#123 E11, cell 8: `cannot read <file>`, exit 2, and nothing about the broken file is run or guessed."""
    broken = {".github/workflows/x.yml": content}
    code, output = scoreboard(public("broken", broken), public("fine", caller(f"{GATE}@{NEWEST}")))
    assert code == 2, output
    assert matrix_row(output, "o/broken")[1:] == ["cannot read .github/workflows/x.yml"] * (len(MATRIX_COLUMNS) - 1)
    assert matrix_row(output, "o/fine")[1] == "v0.10.0"


@pytest.mark.parametrize(
    "scalar",
    [
        "BUILD_DATE: 2026-99-99",
        "BUILD_DATE: !!timestamp zz",
        "RETRIES: !!int ''",
        "FLAG: !!bool x",
        "RATIO: 1:" + ":".join(["1"] * 400) + ".5",
    ],
    ids=["an impossible date", "a timestamp tag on a non-timestamp", "an empty integer", "an unknown boolean", "a sexagesimal float too large for a float"],
)
def test_a_scalar_that_is_no_valid_value_parses_as_text_and_the_workflow_shows_its_pin(scoreboard, public, scalar):
    """#123 E11, cell 8: only the `uses` strings are read, so a scalar no type accepts does not make the file unreadable."""
    content = f"env:\n  {scalar}\njobs:\n  gate:\n    uses: {GATE}@{NEWEST}\n"
    code, output = scoreboard(public("odd", {".github/workflows/x.yml": content}))
    assert code == 0, output
    assert matrix_row(output, "o/odd")[1] == "v0.10.0"


def test_unreadable_release_tags_say_so_and_show_pins_as_commits(scoreboard, public):
    """#123 E4, cell 9."""

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    code, output = scoreboard(public("pinned", caller(f"{GATE}@{NEWEST}")), tags=unreadable)
    assert code == 2, output
    assert "**Repo × rule, newest release unreadable (git exited 128), pins shown as commits**" in output
    assert matrix_row(output, "o/pinned")[1] == "bbbbbbb"


def test_the_codeql_cell_reads_its_own_pin_or_says_none(scoreboard, public):
    """#123 E5, cell 10."""
    gate = caller(f"{GATE}@{NEWEST}")
    scan = {".github/workflows/code.yml": f"jobs:\n  scan:\n    uses: {CODEQL}@{OLDER}\n", **gate}
    _, output = scoreboard(public("scanned", scan), public("unscanned", gate))
    assert matrix_row(output, "o/scanned")[1:3] == ["v0.10.0", "v0.9.0 (behind)"]
    assert matrix_row(output, "o/unscanned")[1:3] == ["v0.10.0", "none"]


@pytest.mark.integration
def test_a_pipe_in_a_label_is_escaped_and_the_row_keeps_its_columns(scoreboard, public, remotes):
    """#123 E2, cell 12: a tracked file named `a|b.py` that cannot be read puts a `|` into a reason."""
    name = public("pipe", {"docs/a.md": "text\n"})
    os.symlink("docs", remotes / name / "a|b.py")
    subprocess.run(["git", "-C", str(remotes / name), "add", "a|b.py"], check=True)
    subprocess.run(["git", "-C", str(remotes / name), *GIT_IDENTITY, "commit", "-q", "-m", "link"], check=True)
    _, output = scoreboard(name)
    lines = [line for line in output.splitlines() if line.startswith("| o/pipe |")]
    assert [r"a\|b.py" in line for line in lines] == [True, True]
    assert [len(re.split(r"(?<!\\)\|", line.strip("|"))) for line in lines] == [len(COLUMNS), len(MATRIX_COLUMNS)]


def test_a_cannot_judge_row_keeps_its_label_in_the_matrix(scoreboard, public, missing):
    """#123 E10, cell 13."""
    code, output = scoreboard(missing, public("open", {}))
    assert code == 2, output
    label = "cannot judge: cannot clone (git exited 128)"
    assert matrix_row(output, "o/gone")[1:] == [label] + [""] * (len(MATRIX_COLUMNS) - 2)


def test_release_tags_are_the_peeled_commits_of_annotated_and_lightweight_tags(gate_clone, remotes):
    """#123 E4, cell 15: `ls-remote` lists an annotated tag twice; the commit is the peeled line. No tag is unreadable."""
    directory = remotes / gate_clone({"a": "1\n"})
    with pytest.raises(ReleaseTagsError):
        read_release_tags()
    git = ["git", "-C", str(directory), *GIT_IDENTITY]
    for tag in (["v0.1.0"], ["-a", "v0.2.0", "-m", "release"], ["not-a-release"]):
        subprocess.run([*git, "tag", *tag], check=True)
    tip = head_commit(directory)
    assert read_release_tags() == {"v0.1.0": tip, "v0.2.0": tip}


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
    assert matrix_row(output, "o/cased")[1] == cell


def cell(output: str, name: str, column: str) -> str:
    return matrix_row(output, name)[MATRIX_COLUMNS.index(column)]


def first_releases(tags: dict[str, str] = FIRST_RELEASES):
    return lambda: tags


def unreadable_tags() -> dict[str, str]:
    raise ReleaseTagsError("git exited 128")


@pytest.mark.parametrize(
    ("files", "size"),
    [({ratchet.BASELINE_PATH: ratchet.render_baseline(ratchet.Baseline({}, None))}, "enforced"), ({}, "report-only")],
    ids=["a baseline", "no baseline"],
)
def test_the_size_cell_is_enforced_with_a_baseline_and_report_only_without(scoreboard, public, files, size):
    """#125 cell 16, E7."""
    _, output = scoreboard(public("sized", {**caller(f"{GATE}@{NEWEST}"), **files}), tags=first_releases())
    assert cell(output, "o/sized", "size") == size


@pytest.mark.parametrize(
    ("refs", "tags", "code", "size"),
    [
        ([OLDER], first_releases(), 0, "not in v0.2.1"),
        ([NEWEST, OLDER], first_releases(), 0, "not in v0.2.1"),
        ([UNRELEASED], first_releases(), 0, "release unknown"),
        (["main"], first_releases(), 0, "release unknown"),
        (["v0.3.1"], first_releases(), 0, "release unknown"),
        ([NEWEST], unreadable_tags, 2, "release unknown"),
    ],
    ids=["older than the rule", "two pins, the oldest decides", "a commit without a tag", "a branch", "a tag name",
         "unreadable tags"],
)
def test_a_cell_never_claims_more_than_the_oldest_pinned_release(scoreboard, public, refs, tags, code, size):
    """#125 cell 17, E16, E17, E12: only an unreadable tag list makes the run red."""
    files = {f".github/workflows/{n}.yml": f"jobs:\n  gate:\n    uses: {GATE}@{ref}\n" for n, ref in enumerate(refs)}
    result, output = scoreboard(public("pinned", files), tags=tags)
    assert result == code, output
    assert cell(output, "o/pinned", "size") == size


CLONE_RELEASE = "d" * 40
CLONE_TABLE_PIN = "e" * 40
CLONE_RELEASES = {**FIRST_RELEASES, "v0.4.0": CLONE_RELEASE, "v0.5.0": CLONE_TABLE_PIN}


def declared(**fields) -> Declared:
    """A clone that declares nothing, with the given fields set."""
    return Declared(**{"settings": None, "has_baseline": False, "has_clone_table": False, "has_import_config": False, "has_judged_project": False,
                       "unreadable": frozenset(), **fields})


CLONE = Rule.CLONE.release


@pytest.mark.parametrize("has_clone_table", [False, True], ids=["no clone table", "a clone table"])
@pytest.mark.parametrize(
    ("pin", "words"),
    [
        (CLONE_RELEASE, "report-only"),
        (NEWEST, "not in v0.3.1"),
    ],
    ids=["a pin at the clone release", "an older tag"],
)
def test_a_pinned_gate_reports_clones_whatever_the_clone_table_says(pin, words, has_clone_table):
    """#137 tests 1 and 4: no tag before the clone-table release enforces clone."""
    release = gate_release((Call(pin),), False, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


@pytest.mark.parametrize(
    ("has_clone_table", "words"), [(True, "enforced"), (False, "report-only")], ids=["a clone table", "no clone table"]
)
def test_a_gate_pinned_at_the_clone_table_release_enforces_clones_with_a_clone_table(has_clone_table, words):
    """#162: v0.5.0 is the first tag that enforces the clone table."""
    release = gate_release((Call(CLONE_TABLE_PIN),), False, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


@pytest.mark.parametrize(
    ("has_clone_table", "words"), [(True, "enforced"), (False, "report-only")], ids=["a clone table", "no clone table"]
)
def test_the_gates_own_repository_enforces_clones_with_a_clone_table(has_clone_table, words):
    """#137 test 2."""
    release = gate_release((Call(None),), True, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


TEST_LAYOUT = Rule.TEST_LAYOUT.release


@pytest.mark.parametrize(
    ("has_judged_project", "words"), [(True, "enforced"), (False, "no judged project")], ids=["a judged project", "none"]
)
def test_main_enforces_test_layout_only_where_a_project_is_judged(has_judged_project, words):
    """#196 sentence 9: where the gate knows the rule, the cell says whether any project in the clone is judged."""
    release = gate_release((Call(None),), True, CLONE_RELEASES)
    assert rule_cell(TEST_LAYOUT, release, declared(has_judged_project=has_judged_project)) == words


@pytest.mark.parametrize(
    ("pin", "has_judged_project", "words"),
    [("v0.6.1", True, "not in v0.6.1"), ("v0.7.0", True, "enforced"), ("v0.7.0", False, "no judged project")],
    ids=["before the first release", "a judged project", "none"],
)
def test_a_pinned_gate_judges_test_layout_from_the_tag_that_first_ships_it(pin, has_judged_project, words):
    """docs/commands.md rule cells, #249: v0.7.0 is the first tag that wires test-layout, for a judged project."""
    tags = {"v0.6.1": "e" * 40, "v0.7.0": "f" * 40}
    release = gate_release((Call(tags[pin]),), False, tags)
    assert rule_cell(TEST_LAYOUT, release, declared(has_judged_project=has_judged_project)) == words


@pytest.mark.parametrize("rule_id", ["test-import", "python-floor", "python-security", "python-print"])
def test_a_rule_judging_every_python_project_needs_no_applicability_cell(rule_id):
    """docs/commands.md rule cells: the rule judges every Python project, so main enforces it with no applicability cell."""
    release = gate_release((Call(None),), True, CLONE_RELEASES)
    assert rule_cell(Rule(rule_id).release, release, declared()) == "enforced"


@pytest.mark.parametrize(
    ("rule_id", "before", "first"),
    [
        ("root-layout", "v0.5.1", "v0.6.0"),
        ("file-suffix", "v0.6.1", "v0.7.0"),
        ("script-name", "v0.6.1", "v0.7.0"),
        ("script-header", "v0.6.1", "v0.7.0"),
        ("test-import", "v0.6.1", "v0.7.0"),
        ("python-floor", "v0.7.0", "v0.8.0"),
        ("python-security", "v0.8.0", "v0.9.0"),
        ("python-print", "v0.8.0", "v0.9.0"),
    ],
)
def test_a_pinned_gate_has_a_rule_from_the_tag_that_first_ships_it(rule_id, before, first):
    """#249: the tags' hygiene wires each rule from its first tag on, unconditionally."""
    tags = {before: "a" * 40, first: "b" * 40}
    rule = Rule(rule_id).release
    assert rule_cell(rule, gate_release((Call("a" * 40),), False, tags), declared()) == f"not in {before}"
    assert rule_cell(rule, gate_release((Call("b" * 40),), False, tags), declared()) == "enforced"


@pytest.mark.parametrize(
    ("files", "words"),
    [
        ({"pyproject.toml": "", "src/p/x.py": ""}, "enforced"),
        ({"frontend/package.json": ""}, "enforced"),
        ({"pyproject.toml": "", "tests/fixtures/package.json": ""}, "no judged project"),
        ({"pyproject.toml": "", "tests/helpers.py": ""}, "no judged project"),
        ({"src/p/x.py": ""}, "no judged project"),
        ({"pyproject.toml": "", "tests/pyproject.toml": "", "tests/src/a/x.py": ""}, "no judged project"),
    ],
    ids=["a project with a package", "a frontend project", "a package.json only in tests", "a project without src",
         "a package without a project", "a package only in tests"],
)
def test_the_scoreboard_cell_for_test_layout_is_read_from_the_clone(scoreboard, gate_clone, files, words):
    """#196 sentence 9, E3: the clone's tracked files decide the cell, on the gate's own repository."""
    result, output = scoreboard(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert (result, cell(output, GATE_REPOSITORY, "test-layout")) == (0, words), output


def test_a_malformed_baseline_makes_the_gates_own_clone_cell_unreadable_and_the_run_red(scoreboard, gate_clone):
    """#137 test 3: the cell owns the failure, as #125 cell 22 rules."""
    broken = {**caller("./.github/workflows/gate.yml"), ratchet.BASELINE_PATH: "[file_lines\n"}
    result, output = scoreboard(gate_clone(broken), tags=first_releases())
    assert result == 2, output
    assert cell(output, GATE_REPOSITORY, "clone") == "cannot read quality/baseline.toml"


@pytest.mark.parametrize(("pin", "words"), [("v0.5.0", "enforced"), ("v0.4.0", "report-only")], ids=["the switch", "older"])
def test_a_later_switch_applies_from_the_tag_that_names_it(pin, words):
    """#137 test 5."""
    rule = RuleRelease("future", "v0.1.0", Switch.REPORT_ONLY, SwitchChange("v0.5.0", Switch.CLONE_TABLE))
    release = gate_release((Call("e" * 40),), False, {**FIRST_RELEASES, pin: "e" * 40})
    assert rule_cell(rule, release, declared(has_clone_table=True)) == words


def test_a_rule_without_a_release_is_not_released_on_a_pinned_gate():
    """#125 cell 18, E7."""
    unreleased = RuleRelease("future", None, Switch.REPORT_ONLY)
    release = gate_release((Call(NEWEST),), False, FIRST_RELEASES)
    assert rule_cell(unreleased, release, declared()) == "not released"


@pytest.mark.parametrize(
    ("files", "pin", "code", "cell_words"),
    [
        ({"pyproject.toml": toml(FORBIDDEN)}, NEWEST, 0, "enforced"),
        ({}, NEWEST, 0, "report-only"),
        (TWO_IMPORT_CONFIGS, NEWEST, 2, "cannot read import-linter config"),
        (TWO_IMPORT_CONFIGS, None, 0, "—"),
        (TWO_IMPORT_CONFIGS, OLDER, 0, "not in v0.2.1"),
        (TWO_IMPORT_CONFIGS, UNRELEASED, 0, "release unknown"),
    ],
    ids=["a config", "no config", "two configs", "two configs, no gate", "two configs, older pin",
         "two configs, no release"],
)
def test_the_import_contract_cell_follows_the_import_linter_config(scoreboard, public, files, pin, code, cell_words):
    """#125 cell 19, E7, E19: two configs leave it open which one the gate runs, but only where the cell shows it."""
    result, output = scoreboard(public("layered", {**pinned_gate(pin), **files}), tags=first_releases())
    assert result == code, output
    assert cell(output, "o/layered", "import-contract") == cell_words


def test_without_a_gate_every_cell_that_needs_one_is_a_dash_and_an_unreadable_workflow_makes_each_unreadable(
    scoreboard, public
):
    """#125 cell 20, E18."""
    broken = {".github/workflows/x.yml": "jobs: [unclosed\n"}
    code, output = scoreboard(public("bare", {CONFIG: TWO_JOBS}), public("broken", broken), tags=first_releases())
    assert code == 2, output
    assert matrix_row(output, "o/bare")[3:] == ["—"] * (len(MATRIX_COLUMNS) - 3)
    assert matrix_row(output, "o/broken")[1:] == ["cannot read .github/workflows/x.yml"] * (len(MATRIX_COLUMNS) - 1)


@pytest.mark.parametrize(
    ("files", "pin", "code", "jobs", "legacy"),
    [
        ({CONFIG: TWO_JOBS}, NEWEST, 0, "2", "report"),
        ({CONFIG: TWO_JOBS + 'legacy = "fail"\n'}, NEWEST, 0, "2", "fail"),
        ({}, NEWEST, 0, "no config", "no config"),
        ({CONFIG: "[gate\n"}, NEWEST, 2, "cannot read quality/config.toml", "cannot read quality/config.toml"),
        ({CONFIG: "[gate\n"}, None, 0, "—", "—"),
    ],
    ids=["jobs counted", "legacy fail", "no config", "malformed config", "malformed config, no gate"],
)
def test_the_jobs_cell_counts_and_the_legacy_cell_names_the_mode(scoreboard, public, files, pin, code, jobs, legacy):
    """#125 cell 21, E6, E9, E11: an unreadable config counts only where a cell shows it."""
    result, output = scoreboard(public("gated", {**pinned_gate(pin), **files}), tags=first_releases())
    assert result == code, output
    assert (cell(output, "o/gated", "Mandatory jobs"), cell(output, "o/gated", "Sonar legacy")) == (jobs, legacy)


@pytest.mark.parametrize(
    ("pin", "code", "size"),
    [
        (NEWEST, 2, "cannot read quality/baseline.toml"),
        (None, 0, "—"),
        (OLDER, 0, "not in v0.2.1"),
        (UNRELEASED, 0, "release unknown"),
    ],
    ids=["newest pin", "no gate", "older pin", "no release"],
)
def test_a_malformed_size_baseline_is_named_in_the_size_cell_only_where_the_cell_shows_it(
    scoreboard, public, pin, code, size
):
    """#125 cell 22, E11."""
    broken = {**pinned_gate(pin), ratchet.BASELINE_PATH: "[file_lines\n"}
    result, output = scoreboard(public("sized", broken), tags=first_releases())
    assert result == code, output
    assert cell(output, "o/sized", "size") == size


def test_the_heading_sentence_names_the_rules_the_oldest_release_always_enforces(scoreboard, public):
    """#125 cell 24, E8: the five rules get a sentence, not five columns."""
    _, output = scoreboard(public("any", {}), tags=first_releases())
    assert "Every gate release enforces env-file, compose-port, secret, workflow-lint and workflow-security." in output
    assert MATRIX_COLUMNS[5:] == ("size", "clone", "import-contract", "claude-md", "root-layout", "file-suffix", "script-name", "script-header", "test-layout", "test-import", "python-floor", "python-security", "python-print", "advisory")
