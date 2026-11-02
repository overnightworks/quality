from pathlib import PurePosixPath

import pytest

from quality import checkout, scanners
from quality.report import FindingEvent
from quality.rules import Area, Rule
from tests.support.check import area_fact, area_finding_rows, check_result
from tests.support.door import check_door
from tests.support.scanners import fake_scanner, recording

WORKFLOW = ".github/workflows/w.yml"
ACTION = ".github/actions/x/action.yml"


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
    assert check_door(build_repo({WORKFLOW: workflow_using("$/.github/actions/x"), ACTION: clean_action()})) == 0


@pytest.mark.scanner
def test_an_action_without_a_ref_stays_a_finding(build_repo, installed_scanners, capsys):
    """The ignore holds for the `$/` form alone: a remote action with no ref is still invalid, though zizmor cannot load that workflow."""
    check_door(build_repo({WORKFLOW: workflow_using("actions/checkout")}))
    assert (f"{WORKFLOW}:7", FindingEvent.INVALID) in workflow_rows(capsys)


@pytest.mark.scanner
@pytest.mark.parametrize("name", ["action.yml", "action.yaml"])
def test_an_action_file_is_judged_by_zizmor(build_repo, installed_scanners, capsys, name):
    """An unpinned `uses:` in an action file is a finding at that file and line, and the area counts the workflows alone."""
    path = f".github/actions/x/{name}"
    repo = build_repo({WORKFLOW: workflow_using("$/.github/actions/x"), path: action_using("actions/checkout@v4")})
    assert check_door(repo) == 1
    assert (f"{path}:6", FindingEvent.INSECURE) in workflow_rows(capsys)
    security = next(verdict for verdict in next(area for area in check_result(repo).areas if area.area is Area.WORKFLOWS).verdicts if verdict.rule is Rule.WORKFLOW_SECURITY)
    assert security.extent == 1


@pytest.mark.scanner
def test_an_action_file_beside_no_workflow_is_judged(build_repo, installed_scanners, capsys):
    """A repository whose only file for the scanners is an action file is still judged."""
    assert check_door(build_repo({ACTION: action_using("actions/checkout@v4")})) == 1
    assert (f"{ACTION}:6", FindingEvent.INSECURE) in workflow_rows(capsys)


@pytest.mark.scanner
def test_a_clean_action_file_changes_no_row(build_repo, installed_scanners, capsys):
    """The workflows row of a repository with a clean action file equals the same repository's without it."""
    steps = {WORKFLOW: workflow_using("$/.github/actions/x")}
    assert check_door(build_repo(steps)) == 0
    without = area_fact(capsys.readouterr().out, Area.WORKFLOWS)
    assert check_door(build_repo({**steps, ACTION: clean_action()})) == 0
    assert area_fact(capsys.readouterr().out, Area.WORKFLOWS) == without


@pytest.mark.parametrize("name", ["action.yml", "a/b/action.yaml"])
def test_an_action_file_is_an_action_wherever_it_lies(name):
    assert checkout.is_action(PurePosixPath(name))


@pytest.mark.parametrize("name", ["action.json", "my-action.yml"])
def test_a_file_that_is_not_named_action_yml_or_yaml_is_no_action(name):
    assert not checkout.is_action(PurePosixPath(name))


def test_actionlint_is_never_handed_an_action_file(build_repo, served_scanners, tmp_path):
    """The recorded call of actionlint names the workflow alone."""
    record = tmp_path / "actionlint-calls"
    fake_scanner(served_scanners, scanners.ACTIONLINT, recording(record, "echo '[]'"))
    check_door(build_repo({WORKFLOW: workflow_using("$/.github/actions/x"), ACTION: clean_action()}), locate_zizmor=lambda: "false")
    handed = record.read_text()
    assert (WORKFLOW in handed, ACTION in handed) == (True, False)
