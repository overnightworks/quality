"""The `quality report` release matrix."""

import subprocess

import pytest

from quality import baseline_file, fleet
from quality.checkout import GATE_REPOSITORY
from quality.fleet import (
    MATRIX_COLUMNS,
    ReleaseTagsError,
    read_release_tags,
)
from quality.rules import Rule
from tests.support.architecture import FORBIDDEN, adapter_contract, contracts_toml, toml
from tests.support.baselines import ceiling
from tests.support.fleet import FIRST_RELEASE_COMMIT, FIRST_RELEASES, LITERAL_TEXT, NEWEST, OLDER, UNRELEASED, row
from tests.support.git_repositories import GIT_IDENTITY, head_commit

pytestmark = pytest.mark.integration

GATE = "overnightworks/quality/.github/workflows/gate.yml"
CODEQL = "overnightworks/quality/.github/workflows/codeql.yml"
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
def test_the_gate_cell_names_the_release_the_pinned_commit_is(fleet_report, public, ref, cell):
    """FLEET-4, FLEET-5, FLEET-6, FLEET-10, FLEET-27: the tag the SHA points at, `(behind)` when not the newest;
    the `# v` comment is not read."""
    code, output = fleet_report(public("pinned", caller(f"{GATE}@{ref} # v0.3.1")))
    assert code == 0, output
    assert matrix_row(output, "o/pinned")[1:3] == [cell, "none"]


def test_without_a_caller_there_is_no_gate_and_a_step_level_uses_is_no_caller(fleet_report, public):
    """FLEET-3, FLEET-7: only a job-level `uses:` is a call."""
    steps = {".github/workflows/ci.yml": f"jobs:\n  build:\n    steps:\n      - uses: {GATE}@{NEWEST}\n"}
    _, output = fleet_report(public("bare", {}), public("stepped", steps))
    assert matrix_row(output, "o/bare")[1:3] == matrix_row(output, "o/stepped")[1:3] == ["no gate", "none"]




def test_a_commit_in_upper_case_is_a_commit(fleet_report, public):
    """FLEET-4."""
    _, output = fleet_report(public("shouting", caller(f"{GATE}@{NEWEST.upper()}")))
    assert matrix_row(output, "o/shouting")[1] == "v0.10.0"


def test_two_callers_with_two_pins_are_both_named_and_joined(fleet_report, public):
    """FLEET-9."""
    files = {
        ".github/workflows/a.yml": f"jobs:\n  one:\n    uses: {GATE}@{NEWEST}\n",
        ".github/workflows/b.yaml": f"jobs:\n  two:\n    uses: {GATE}@{OLDER}\n",
    }
    _, output = fleet_report(public("both", files))
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
def test_a_workflow_that_is_not_yaml_is_named_in_its_cells_and_the_other_rows_stand(fleet_report, public, content):
    """FLEET-23, FLEET-26: `cannot read <file>`, exit 2, and nothing about the broken file is run or guessed."""
    broken = {".github/workflows/x.yml": content}
    code, output = fleet_report(public("broken", broken), public("fine", caller(f"{GATE}@{NEWEST}")))
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
def test_a_scalar_that_is_no_valid_value_parses_as_text_and_the_workflow_shows_its_pin(fleet_report, public, scalar):
    """FLEET-3: only the `uses` strings are read, so a scalar no type accepts does not make the file unreadable."""
    content = f"env:\n  {scalar}\njobs:\n  gate:\n    uses: {GATE}@{NEWEST}\n"
    code, output = fleet_report(public("odd", {".github/workflows/x.yml": content}))
    assert code == 0, output
    assert matrix_row(output, "o/odd")[1] == "v0.10.0"


def test_unreadable_release_tags_say_so_and_show_pins_as_commits(fleet_report, public):
    """FLEET-25, FLEET-26."""

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    code, output = fleet_report(public("pinned", caller(f"{GATE}@{NEWEST}")), tags=unreadable)
    assert code == 2, output
    assert "**Repo × rule, newest release unreadable (git exited 128), pins shown as commits**" in output
    assert matrix_row(output, "o/pinned")[1] == "bbbbbbb"


def test_the_codeql_cell_reads_its_own_pin_or_says_none(fleet_report, public):
    """FLEET-2, FLEET-7."""
    gate = caller(f"{GATE}@{NEWEST}")
    scan = {".github/workflows/code.yml": f"jobs:\n  scan:\n    uses: {CODEQL}@{OLDER}\n", **gate}
    _, output = fleet_report(public("scanned", scan), public("unscanned", gate))
    assert matrix_row(output, "o/scanned")[1:3] == ["v0.10.0", "v0.9.0 (behind)"]
    assert matrix_row(output, "o/unscanned")[1:3] == ["v0.10.0", "none"]




def test_a_cannot_judge_row_keeps_its_label_in_the_matrix(fleet_report, public, missing):
    """FLEET-95: the matrix row of a repository that cannot be judged is its label and every other cell is empty."""
    code, output = fleet_report(missing, public("open", {}))
    assert code == 2, output
    label = "cannot judge: cannot clone (git exited 128)"
    assert matrix_row(output, "o/gone")[1:] == [label] + [""] * (len(MATRIX_COLUMNS) - 2)


def test_release_tags_are_the_peeled_commits_of_annotated_and_lightweight_tags(gate_clone, remotes):
    """FLEET-1: `ls-remote` lists an annotated tag twice; the commit is the peeled line. No tag is unreadable."""
    directory = remotes / gate_clone({"a": "1\n"})
    with pytest.raises(ReleaseTagsError):
        read_release_tags()
    git = ["git", "-C", str(directory), *GIT_IDENTITY]
    for tag in (["v0.1.0"], ["-a", "v0.2.0", "-m", "release"], ["not-a-release"]):
        subprocess.run([*git, "tag", *tag], check=True)
    tip = head_commit(directory)
    assert read_release_tags() == {"v0.1.0": tip, "v0.2.0": tip}


@pytest.mark.parametrize(
    "git_preamble",
    [
        pytest.param('case " $* " in *" ls-remote "*) exit 3;; esac', id="a listing git refuses"),
        pytest.param('case " $* " in *" ls-remote "*) sleep 30;; esac', id="a listing git does not finish"),
    ],
)
def test_release_tags_git_cannot_list_are_unreadable(fake_git, monkeypatch, git_preamble):
    """FLEET-1, FLEET-25: a listing that fails or does not finish in its bound is no release tags."""
    monkeypatch.setattr(fleet, "LS_REMOTE_TIMEOUT_SECONDS", 1)
    fake_git(git_preamble)
    with pytest.raises(ReleaseTagsError):
        read_release_tags()


@pytest.mark.parametrize(
    ("uses", "cell"),
    [
        ("OvernightWorks/Quality/.github/workflows/gate.yml@" + NEWEST, "v0.10.0"),
        ("overnightworks/quality/.github/workflows/Gate.yml@" + NEWEST, "no gate"),
    ],
    ids=["owner and repository match in any case", "the workflow path matches exactly"],
)
def test_only_the_owner_and_repository_of_a_reusable_call_ignore_case(fleet_report, public, uses, cell):
    """FLEET-11."""
    _, output = fleet_report(public("cased", caller(uses)))
    assert matrix_row(output, "o/cased")[1] == cell


def cell(output: str, name: str, column: str) -> str:
    return matrix_row(output, name)[MATRIX_COLUMNS.index(column)]


def first_releases(tags: dict[str, str] = FIRST_RELEASES):
    return lambda: tags


def unreadable_tags() -> dict[str, str]:
    raise ReleaseTagsError("git exited 128")


@pytest.mark.parametrize(
    ("files", "size"),
    [({baseline_file.BASELINE_PATH: baseline_file.render_baseline(ceiling({}, None))}, "enforced"), ({}, "report-only")],
    ids=["a baseline", "no baseline"],
)
def test_the_size_cell_is_enforced_with_a_baseline_and_report_only_without(fleet_report, public, files, size):
    """FLEET-15."""
    _, output = fleet_report(public("sized", {**caller(f"{GATE}@{NEWEST}"), **files}), tags=first_releases())
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
def test_a_cell_never_claims_more_than_the_oldest_pinned_release(fleet_report, public, refs, tags, code, size):
    """FLEET-18, FLEET-22, FLEET-26, FLEET-27: only an unreadable tag list makes the run red."""
    files = {f".github/workflows/{n}.yml": f"jobs:\n  gate:\n    uses: {GATE}@{ref}\n" for n, ref in enumerate(refs)}
    result, output = fleet_report(public("pinned", files), tags=tags)
    assert result == code, output
    assert cell(output, "o/pinned", "size") == size
























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
def test_the_report_cell_for_test_layout_is_read_from_the_clone(fleet_report, gate_clone, files, words):
    """FLEET-20: the clone's tracked files decide the cell, on the gate's own repository."""
    result, output = fleet_report(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert (result, cell(output, GATE_REPOSITORY, "test-layout")) == (0, words), output


def test_a_malformed_baseline_makes_the_gates_own_clone_cell_unreadable_and_the_run_red(fleet_report, gate_clone):
    """FLEET-24, FLEET-26: the cell owns the failure."""
    broken = {**caller("./.github/workflows/gate.yml"), baseline_file.BASELINE_PATH: "[file_lines\n"}
    result, output = fleet_report(gate_clone(broken), tags=first_releases())
    assert result == 2, output
    assert cell(output, GATE_REPOSITORY, "clone") == "cannot read quality/baseline.toml"






















































@pytest.mark.parametrize(
    ("files", "words"),
    [
        ({baseline_file.BASELINE_PATH: "[spec_citations]\n"}, "enforced"),
        ({baseline_file.BASELINE_PATH: "[file_lines]\n"}, "report-only"),
        ({baseline_file.BASELINE_PATH: "[spec_citations\n"}, "cannot read quality/baseline.toml"),
    ],
    ids=["a table", "a baseline without it", "a baseline that cannot be read"],
)
def test_the_gates_own_clone_reads_the_spec_citation_cell_from_its_baseline_on_main(fleet_report, gate_clone, files, words):
    """FLEET-118: the clone's tracked `quality/baseline.toml` decides the cell on main, an unreadable one is named."""
    _, output = fleet_report(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert cell(output, GATE_REPOSITORY, "spec-citation") == words, output


@pytest.mark.parametrize(
    ("pin", "words"),
    [(NEWEST, "not in v0.3.1"), (FIRST_RELEASE_COMMIT, "report-only")],
    ids=["an older tag", "the rule's first release"],
)
def test_literal_text_reads_the_pin_against_the_rules_first_release(fleet_report, public, pin, words):
    """FLEET-101, FLEET-18: a pin before the rule's first release says so, and that release only reports it."""
    tags = {**FIRST_RELEASES, LITERAL_TEXT.first_release: FIRST_RELEASE_COMMIT}
    _, output = fleet_report(public("pinned", pinned_gate(pin)), tags=first_releases(tags))
    assert cell(output, "o/pinned", "literal-text") == words


@pytest.mark.parametrize(
    ("files", "words"),
    [
        ({"pyproject.toml": '[project]\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'}, "enforced"),
        ({"pyproject.toml": '[project]\ndependencies = ["overnightworks-design>=1"]\n'}, "no catalogs declaration"),
        ({}, "no catalogs declaration"),
    ],
    ids=["a catalogs declaration", "the dependency without the extra", "no pyproject.toml"],
)
def test_the_gates_own_clone_reads_the_catalog_cell_from_its_pyproject(fleet_report, gate_clone, files, words):
    """FLEET-98, FLEET-99: the clone's tracked `pyproject.toml` decides the cell, on the gate's own repository."""
    result, output = fleet_report(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert (result, cell(output, GATE_REPOSITORY, "catalog-check")) == (0, words), output


@pytest.mark.parametrize(
    ("files", "words"),
    [
        ({"pyproject.toml": '[project]\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'}, "enforced"),
        ({"pyproject.toml": '[project]\ndependencies = ["overnightworks-design>=1"]\n'}, "no catalogs declaration"),
        ({baseline_file.BASELINE_PATH: "[literal_text]\n"}, "no catalogs declaration"),
    ],
    ids=["a catalogs declaration", "the dependency without the extra", "a table without a declaration"],
)
def test_the_gates_own_clone_reads_the_literal_text_cell_from_its_pyproject_on_main(fleet_report, gate_clone, files, words):
    """FLEET-99: on main the clone's tracked `pyproject.toml` decides the cell, as for `catalog-check`, and a held
    `[literal_text]` table does not."""
    _, output = fleet_report(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert cell(output, GATE_REPOSITORY, "literal-text") == words, output


def test_a_malformed_pyproject_makes_the_gates_own_clone_cell_unreadable_and_the_run_red(fleet_report, gate_clone):
    """FLEET-24, FLEET-26, FLEET-100: the cell owns the failure, as for the baseline."""
    broken = {**caller("./.github/workflows/gate.yml"), "pyproject.toml": "[project\n"}
    result, output = fleet_report(gate_clone(broken), tags=first_releases())
    assert result == 2, output
    assert cell(output, GATE_REPOSITORY, "catalog-check") == "cannot read pyproject.toml"


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
def test_the_import_contract_cell_follows_the_import_linter_config(fleet_report, public, files, pin, code, cell_words):
    """FLEET-16, FLEET-18, FLEET-21, FLEET-22, FLEET-24, FLEET-26, FLEET-27: two configs leave it open which one the
    gate runs, but only where the cell shows it."""
    result, output = fleet_report(public("layered", {**pinned_gate(pin), **files}), tags=first_releases())
    assert result == code, output
    assert cell(output, "o/layered", "import-contract") == cell_words


ADAPTER_CONTRACT_COLUMNS = pytest.mark.parametrize("column", ["adapter-import", "integration-marker"])


@ADAPTER_CONTRACT_COLUMNS
@pytest.mark.parametrize(
    ("files", "code", "cell_words"),
    [
        ({"pyproject.toml": toml(adapter_contract())}, 0, "enforced"),
        ({"pyproject.toml": toml(FORBIDDEN)}, 0, "no adapter contract"),
        ({}, 0, "no adapter contract"),
        (TWO_IMPORT_CONFIGS, 2, "cannot read import-linter config"),
        ({"pyproject.toml": contracts_toml({"a": adapter_contract(), "b": adapter_contract()})}, 2, "cannot read import-linter config"),
    ],
    ids=["the contract with the id", "a contract without it", "no config", "two configs", "two contracts with the id"],
)
def test_the_adapter_contract_cells_follow_the_contract_the_config_holds(fleet_report, gate_clone, files, code, cell_words, column):
    """FLEET-13, ADAPTERIMPORT-21, INTEGRATION-11: on main the cell reads enforced when the config holds the contract with the id."""
    result, output = fleet_report(gate_clone({**caller("./.github/workflows/gate.yml"), **files}), tags=first_releases())
    assert result == code, output
    assert cell(output, GATE_REPOSITORY, column) == cell_words


@ADAPTER_CONTRACT_COLUMNS
def test_the_adapter_contract_cells_say_not_in_the_tag_under_a_pin_below_their_release(fleet_report, public, column):
    """FLEET-18, ADAPTERIMPORT-21, INTEGRATION-11: a gate pinned below the release that names the rule has no such rule."""
    _, output = fleet_report(public("tagged", pinned_gate(NEWEST)), tags=first_releases())
    assert cell(output, "o/tagged", column) == "not in v0.3.1"


def test_without_a_gate_every_cell_that_needs_one_is_a_dash_and_an_unreadable_workflow_makes_each_unreadable(
    fleet_report, public
):
    """FLEET-21, FLEET-23, FLEET-26."""
    broken = {".github/workflows/x.yml": "jobs: [unclosed\n"}
    code, output = fleet_report(public("bare", {CONFIG: TWO_JOBS}), public("broken", broken), tags=first_releases())
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
def test_the_jobs_cell_counts_and_the_legacy_cell_names_the_mode(fleet_report, public, files, pin, code, jobs, legacy):
    """FLEET-12, FLEET-21, FLEET-24, FLEET-26: an unreadable config counts only where a cell shows it."""
    result, output = fleet_report(public("gated", {**pinned_gate(pin), **files}), tags=first_releases())
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
    fleet_report, public, pin, code, size
):
    """FLEET-18, FLEET-21, FLEET-22, FLEET-24, FLEET-26."""
    broken = {**pinned_gate(pin), baseline_file.BASELINE_PATH: "[file_lines\n"}
    result, output = fleet_report(public("sized", broken), tags=first_releases())
    assert result == code, output
    assert cell(output, "o/sized", "size") == size


@pytest.mark.parametrize(
    ("pin", "words"),
    [(NEWEST, "not in v0.3.1"), (FIRST_RELEASE_COMMIT, "enforced")],
    ids=["an older tag", "the rule's first release"],
)
def test_the_job_timeout_column_reads_the_pin_against_the_rules_first_release(fleet_report, public, pin, words):
    """TIMEOUT-11, FLEET-18: a pin before the rule's first release says so, and that release enforces it."""
    tags = {**FIRST_RELEASES, Rule.JOB_TIMEOUT.release.first_release: FIRST_RELEASE_COMMIT}
    _, output = fleet_report(public("pinned", pinned_gate(pin)), tags=first_releases(tags))
    assert cell(output, "o/pinned", "job-timeout") == words


def test_the_heading_sentence_names_the_rules_the_oldest_release_always_enforces(fleet_report, public):
    """FLEET-13, FLEET-14: the five rules get a sentence, not five columns."""
    _, output = fleet_report(public("any", {}), tags=first_releases())
    assert "Every gate release enforces env-file, compose-port, secret, workflow-lint and workflow-security." in output
    every_release = {Rule.ENV_FILE, Rule.COMPOSE_PORT, Rule.LEAK, Rule.WORKFLOW_LINT, Rule.WORKFLOW_SECURITY}
    assert MATRIX_COLUMNS[5:] == tuple(rule.value for rule in Rule if rule not in every_release)
