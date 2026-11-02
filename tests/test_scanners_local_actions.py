from pathlib import PurePosixPath

import pytest

from quality import checkout, scanners
from quality.report import FindingEvent
from quality.rules import Area
from tests.support.check import area_fact, area_finding_rows
from tests.support.door import check_door
from tests.support.scanners import fake_scanner, fake_zizmor, recording


def workflow_using(uses: str) -> str:
    """A workflow that holds no finding of its own but its one step, which is on line 7."""
    return f"on: push\npermissions: {{}}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: {uses}\n"


def action_using(uses: str) -> str:
    return f"name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n    - uses: {uses}\n"


def clean_action() -> str:
    return "name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n    - run: 'true'\n      shell: bash\n"


def workflow_rows(capsys) -> list[tuple[str, FindingEvent]]:
    """The location and event of each finding row under the workflows row of what the door printed."""
    return [(location, row.event) for location, row in area_finding_rows(capsys.readouterr().out, Area.WORKFLOWS)]


@pytest.mark.scanner
def test_a_local_action_in_the_dollar_slash_form_passes_both_scanners(build_repo, installed_scanners):
    """A step in GitHub's `uses: $/…` form of a local action passes, the action file clean."""
    workflow = ".github/workflows/w.yml"
    action = ".github/actions/x/action.yml"
    assert check_door(build_repo({workflow: workflow_using("$/.github/actions/x"), action: clean_action()})) == 0


@pytest.mark.scanner
def test_an_action_without_a_ref_stays_a_finding(build_repo, installed_scanners, capsys):
    """The ignore holds for the `$/` form alone: a remote action with no ref is still invalid, though zizmor cannot load that workflow."""
    workflow = ".github/workflows/w.yml"
    check_door(build_repo({workflow: workflow_using("actions/checkout")}))
    assert (f"{workflow}:7", FindingEvent.INVALID) in workflow_rows(capsys)


@pytest.mark.scanner
@pytest.mark.parametrize("name", ["action.yml", "action.yaml"])
def test_an_action_file_is_judged_by_zizmor(build_repo, installed_scanners, capsys, name):
    """An unpinned `uses:` in an action file is a finding at that file and line."""
    workflow = ".github/workflows/w.yml"
    path = f".github/actions/x/{name}"
    repo = build_repo({workflow: workflow_using("$/.github/actions/x"), path: action_using("actions/checkout@v4")})
    assert check_door(repo) == 1
    assert (f"{path}:6", FindingEvent.INSECURE) in workflow_rows(capsys)


@pytest.mark.scanner
def test_an_action_file_beside_no_workflow_is_judged(build_repo, installed_scanners, capsys):
    """A repository whose only file for the scanners is an action file is still judged."""
    action = ".github/actions/x/action.yml"
    assert check_door(build_repo({action: action_using("actions/checkout@v4")})) == 1
    assert (f"{action}:6", FindingEvent.INSECURE) in workflow_rows(capsys)


@pytest.mark.scanner
def test_a_clean_action_file_changes_no_row(build_repo, installed_scanners, capsys):
    """The workflows row of a repository with a clean action file equals the same repository's without it."""
    workflow = ".github/workflows/w.yml"
    action = ".github/actions/x/action.yml"
    steps = {workflow: workflow_using("$/.github/actions/x")}
    assert check_door(build_repo(steps)) == 0
    without = area_fact(capsys.readouterr().out, Area.WORKFLOWS)
    assert check_door(build_repo({**steps, action: clean_action()})) == 0
    assert area_fact(capsys.readouterr().out, Area.WORKFLOWS) == without


@pytest.mark.parametrize(
    ("name", "is_action"),
    [("action.yml", True), ("a/b/action.yaml", True), ("action.json", False), ("my-action.yml", False)],
)
def test_only_an_action_file_counts_as_an_action(name, is_action):
    assert checkout.is_action(PurePosixPath(name)) is is_action


def test_actionlint_is_never_handed_an_action_file(build_repo, served_scanners, tmp_path):
    """The recorded call of actionlint names the workflow alone."""
    workflow = ".github/workflows/w.yml"
    action = ".github/actions/x/action.yml"
    record = tmp_path / "actionlint-calls"
    fake_scanner(served_scanners, scanners.ACTIONLINT, recording(record, "echo '[]'"))
    repo = build_repo({workflow: workflow_using("$/.github/actions/x"), action: clean_action()})
    assert check_door(repo, locate_zizmor=fake_zizmor(tmp_path / "zizmor", "echo '[]'")) == 0
    handed = record.read_text()
    assert (workflow in handed, action in handed) == (True, False)
