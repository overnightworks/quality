import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml

from tests.support.workflow_steps import (
    FORMAT_STEP,
    WORKFLOWS,
    Answer,
    FakeGh,
    StepRun,
    run_step,
    step,
    step_environment,
    step_names,
    step_script,
    workflow,
)

pytestmark = pytest.mark.integration

REF_STEP = "Refuse any ref but main"
HEAD_STEP = "Refuse unless the commit is still main's head"
TAG_STEP = "Refuse a tag that exists"
TAGGED_STEP = "Refuse a commit that already carries a release tag"
CI_STEP = "Refuse unless CI's newest push run on the commit is green with a green quality verdict"
MERGED_STEP = "Refuse a commit its pull request merged without a green quality verdict"


def around_the_merge(minutes: int = 0) -> str:
    """A GitHub timestamp this many minutes after the merge (before it when negative)."""
    return (datetime(2026, 11, 6, 10, tzinfo=UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Dispatch:
    repository: str
    commit: str
    version: str


@pytest.fixture
def dispatch() -> Dispatch:
    return Dispatch("acme/widgets", "a1" * 20, "1.2.3")


@pytest.fixture(scope="module")
def gate() -> dict:
    return workflow("release-gate.yml")


def gate_caller() -> tuple[str, str]:
    """The file name of the workflow that calls `gate.yml` and the name its caller job gives the check."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            if job.get("uses", "").endswith("/gate.yml"):
                return path.name, job.get("name", job_id)
    raise AssertionError("no workflow calls gate.yml")


def verdict_check_name() -> str:
    jobs = workflow("gate.yml")["jobs"]
    verdict_job = next(
        job_id for job_id, job in jobs.items() if any("quality verdict" in step.get("run", "") for step in job["steps"])
    )
    return f"{gate_caller()[1]} / {jobs[verdict_job].get('name', verdict_job)}"


def ci_runs(*id_status_conclusion: tuple[int, str, str | None]) -> dict:
    return {
        "workflow_runs": [
            {"id": run, "status": status, "conclusion": conclusion} for run, status, conclusion in id_status_conclusion
        ]
    }


def run_jobs(*name_conclusion: tuple[str, str | None]) -> dict:
    return {"jobs": [{"name": name, "conclusion": conclusion} for name, conclusion in name_conclusion]}


def merged_pull(number: int, head: str, merged_at: str | None, base: str = "main") -> dict:
    return {"number": number, "head": {"sha": head}, "merged_at": merged_at, "base": {"ref": base}}


def check_runs(*id_conclusion_completed_at: tuple[int, str | None, str | None]) -> dict:
    return {
        "check_runs": [
            {"id": run, "conclusion": conclusion, "completed_at": completed}
            for run, conclusion, completed in id_conclusion_completed_at
        ]
    }


def tags(*name_commit: tuple[str, str]) -> list[dict]:
    return [{"name": name, "commit": {"sha": commit}} for name, commit in name_commit]


def requests_of(dispatch: Dispatch) -> dict[str, tuple[str, ...]]:
    api = f"repos/{dispatch.repository}"
    return {
        "main": ("api", f"{api}/commits/main"),
        "refs": ("api", f"{api}/git/matching-refs/tags/v{dispatch.version}"),
        "tags": ("api", "--paginate", f"{api}/tags?per_page=100"),
        "runs": (
            "api",
            f"{api}/actions/workflows/{gate_caller()[0]}/runs?event=push&branch=main&head_sha={dispatch.commit}&per_page=100",
        ),
        "pulls": ("api", f"{api}/commits/{dispatch.commit}/pulls"),
        "check_runs": (
            "api",
            f"{api}/commits/{{head}}/check-runs?check_name={quote(verdict_check_name(), safe='')}&filter=all&per_page=100",
        ),
        "jobs": ("api", f"{api}/actions/runs/{{run}}/jobs?per_page=100"),
    }


def green_answers(dispatch: Dispatch, **replaced: object) -> dict[tuple[str, ...], Answer]:
    """Every read a passing gate makes. `replaced` swaps one body by name; `jobs` and `check_runs` take {id: body}."""
    head = "b2" * 20
    bodies = {
        "main": {"sha": dispatch.commit},
        "refs": [],
        "tags": tags(("v1.2.2", "c3" * 20)),
        "runs": ci_runs((7, "completed", "success")),
        "pulls": [merged_pull(9, head, around_the_merge())],
        "jobs": {7: run_jobs((verdict_check_name(), "success"))},
        "check_runs": {head: check_runs((1, "success", around_the_merge(-60)))},
    } | replaced
    requests = requests_of(dispatch)
    answers = {requests[name]: Answer(body) for name, body in bodies.items() if name not in {"jobs", "check_runs"}}
    answers |= {
        tuple(part.format(run=run) for part in requests["jobs"]): Answer(body) for run, body in bodies["jobs"].items()
    }
    answers |= {
        tuple(part.format(head=pull_head) for part in requests["check_runs"]): Answer(body)
        for pull_head, body in bodies["check_runs"].items()
    }
    return answers


def run_gate_step(
    gate: dict, name: str, dispatch: Dispatch, directory: Path, answers: dict | None = None, **scenario: str
) -> StepRun:
    defaults = {
        "GH_TOKEN": "token",
        "VERSION": dispatch.version,
        "COMMIT": dispatch.commit,
        "GITHUB_REPOSITORY": dispatch.repository,
    }
    environment = step_environment(gate, "gate", name, **(defaults | scenario))
    fake = FakeGh(directory / "tools", green_answers(dispatch) if answers is None else answers)
    work = directory / "work"
    work.mkdir()
    return run_step(step_script(gate, "gate", name), work, environment, fake.directory)


def outcome(result: StepRun) -> tuple[int, str, str]:
    """The exit code, stderr and step outputs; the gate prints nothing to stdout in any scenario."""
    assert result.stdout == ""
    return result.returncode, result.stderr, result.outputs


def test_no_gate_job_or_step_is_skippable(gate):
    assert "if" not in gate["jobs"]["gate"]
    assert all(
        "if" not in candidate and "continue-on-error" not in candidate for candidate in gate["jobs"]["gate"]["steps"]
    )


def refused(line: str) -> str:
    return f"Refused: {line}\n"


@pytest.mark.parametrize(
    ("reference", "passes"),
    [
        ("refs/heads/main", True),
        ("refs/heads/feature", False),
        ("refs/heads/main2", False),
        ("refs/heads/maintenance", False),
        ("refs/tags/v1.2.3", False),
        ("refs/pull/7/merge", False),
    ],
    ids=[
        "main",
        "another branch",
        "a longer name starting with main",
        "a branch named maintenance",
        "a tag",
        "a pull request ref",
    ],
)
def test_only_a_dispatch_from_main_passes_the_ref_gate(gate, dispatch, tmp_path, reference, passes):
    result = run_gate_step(gate, REF_STEP, dispatch, tmp_path, DISPATCH_REF=reference)
    expected = (
        (0, "", "") if passes else (1, refused(f"dispatched from {reference}, a release is dispatched from main."), "")
    )
    assert outcome(result) == expected


@pytest.mark.parametrize(
    "version",
    [
        "0.21",
        "v0.21.0",
        "0.21.0-rc1",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "0.21.0; touch planted",
        "$(touch planted)",
        "",
        "0.21.0\n0.21.0",
        "1x2.3",
        "1.2x3",
        "\u0967.2.3",
        "1.2.\u0969",
        "1.\u0967.3",
        "1\u0967.2.3",
        "1.2\u0967.3",
        "1.2.3\u0967",
    ],
    ids=[
        "two parts",
        "a v",
        "a suffix",
        "leading zero major",
        "leading zero minor",
        "leading zero patch",
        "a command after",
        "a substitution",
        "empty",
        "two lines",
        "a letter for the first dot",
        "a letter for the second dot",
        "a Devanagari digit as major",
        "a Devanagari digit as patch",
        "a Devanagari digit as minor",
        "a Devanagari digit after an ASCII digit in the major",
        "a Devanagari digit after an ASCII digit in the minor",
        "a Devanagari digit after an ASCII digit in the patch",
    ],
)
def test_a_malformed_version_is_refused(gate, dispatch, tmp_path, version):
    result = run_gate_step(gate, FORMAT_STEP, dispatch, tmp_path, VERSION=version)
    assert outcome(result) == (1, refused("the version is not X.Y.Z, each part a number without leading zeros."), "")
    assert not (tmp_path / "work" / "planted").exists()


@pytest.mark.parametrize("version", ["0.0.0", "0.21.0", "10.200.3000"])
def test_a_full_version_passes_the_format_gate(gate, dispatch, tmp_path, version):
    result = run_gate_step(gate, FORMAT_STEP, dispatch, tmp_path, VERSION=version)
    assert outcome(result) == (0, "", "")


@pytest.mark.parametrize("case", ["pass", "main moved", "another repository's main"])
def test_the_commit_must_still_be_main_s_head(gate, dispatch, tmp_path, case):
    moved_to = "d4" * 20
    answers = {
        "pass": green_answers(dispatch),
        "main moved": green_answers(dispatch, main={"sha": moved_to}),
        "another repository's main": green_answers(dispatch),
    }[case]
    foreign = {"GITHUB_REPOSITORY": "other/repo"} if case == "another repository's main" else {}
    result = run_gate_step(gate, HEAD_STEP, dispatch, tmp_path, answers, **foreign)
    expected = {
        "pass": (0, "", ""),
        "main moved": (1, refused(f"main moved from {dispatch.commit} to {moved_to} after the dispatch."), ""),
        "another repository's main": (1, "gh: Not Found (HTTP 404)\n", ""),
    }[case]
    assert outcome(result) == expected


@pytest.mark.parametrize(
    ("existing", "passes"),
    [([], True), (["refs/tags/v1.2.30"], True), (["refs/tags/v1.2.3"], False)],
    ids=["no tag", "a longer tag name", "the tag exists"],
)
def test_a_version_whose_tag_exists_is_refused(gate, dispatch, tmp_path, existing, passes):
    result = run_gate_step(
        gate, TAG_STEP, dispatch, tmp_path, green_answers(dispatch, refs=[{"ref": ref} for ref in existing])
    )
    expected = (0, "", "") if passes else (1, refused(f"the tag v{dispatch.version} exists."), "")
    assert outcome(result) == expected


@pytest.mark.parametrize(
    ("listed", "refused_tag"),
    [
        ([], None),
        ([("v1.2.2", True)], "v1.2.2"),
        ([("v2026.11.06.1", True), ("v1.2.2", True)], "v1.2.2"),
        ([("v1.2.2", True), ("v2026.11.06.1", True)], "v1.2.2"),
        ([("v1.2.2", False)], None),
        ([("v2026.11.05.1", True)], None),
        ([("1.2.2", True)], None),
        ([("v1.02.2", True)], None),
    ],
    ids=[
        "no tag at the commit",
        "a release tag at the commit",
        "a dated deploy tag then a release tag at the commit",
        "a release tag then a dated deploy tag at the commit",
        "a release tag at another commit",
        "only a dated deploy tag at the commit",
        "a tag without v",
        "a release tag with a leading zero at the commit",
    ],
)
def test_a_commit_that_already_carries_a_release_tag_is_refused(gate, dispatch, tmp_path, listed, refused_tag):
    answers = green_answers(
        dispatch, tags=tags(*((name, dispatch.commit if at_commit else "c3" * 20) for name, at_commit in listed))
    )
    result = run_gate_step(gate, TAGGED_STEP, dispatch, tmp_path, answers)
    expected = (
        (1, refused(f"{dispatch.commit} already carries the release tag {refused_tag}."), "")
        if refused_tag
        else (0, "", "")
    )
    assert outcome(result) == expected


def ci_scenarios(verdict: str) -> dict[str, tuple[dict, str | None]]:
    """Each id: what replaces the green reads, and the refusal line (None when the step passes)."""

    def scenario(runs: dict, verdict_conclusion: str | None = "success", run: int = 7) -> dict:
        jobs = run_jobs((verdict, verdict_conclusion)) if verdict_conclusion else run_jobs(("tests", "success"))
        return {"runs": runs, "jobs": {run: jobs}}

    green = ci_runs((7, "completed", "success"))
    return {
        "green": (scenario(green), None),
        "no run": (scenario(ci_runs()), "CI has no push run on {commit}."),
        "queued": (scenario(ci_runs((7, "queued", None))), "CI's push run 7 is queued."),
        "in_progress": (scenario(ci_runs((7, "in_progress", None))), "CI's push run 7 is in_progress."),
        "failure": (scenario(ci_runs((7, "completed", "failure"))), "CI's push run 7 concluded failure."),
        "cancelled": (scenario(ci_runs((7, "completed", "cancelled"))), "CI's push run 7 concluded cancelled."),
        "the verdict missing": (scenario(green, None), "the quality / verdict of CI's push run 7 is missing."),
        "the verdict failed": (scenario(green, "failure"), "the quality / verdict of CI's push run 7 is failure."),
        "the verdict skipped": (scenario(green, "skipped"), "the quality / verdict of CI's push run 7 is skipped."),
        "the newest run decides, failed run first": (
            scenario(ci_runs((8, "completed", "failure"), (7, "completed", "success"))),
            "CI's push run 8 concluded failure.",
        ),
        "the newest run decides, failed run last": (
            scenario(ci_runs((7, "completed", "success"), (8, "completed", "failure"))),
            "CI's push run 8 concluded failure.",
        ),
        "the newest run is the highest number, not the last in text order": (
            scenario(ci_runs((9, "completed", "success"), (10, "completed", "failure")), run=9),
            "CI's push run 10 concluded failure.",
        ),
        "the newest run decides, green run first": (
            scenario(ci_runs((8, "completed", "success"), (7, "completed", "failure")), run=8),
            None,
        ),
        "the newest run decides, green run last": (
            scenario(ci_runs((7, "completed", "failure"), (8, "completed", "success")), run=8),
            None,
        ),
    }


@pytest.mark.parametrize("case", list(ci_scenarios("")))
def test_the_newest_push_run_must_be_green_with_a_green_verdict(gate, dispatch, tmp_path, case):
    replaced, refusal = ci_scenarios(verdict_check_name())[case]
    result = run_gate_step(gate, CI_STEP, dispatch, tmp_path, green_answers(dispatch, **replaced))
    expected = (1, refused(refusal.format(commit=dispatch.commit)), "") if refusal else (0, "", "")
    assert outcome(result) == expected


@pytest.mark.parametrize("ignored_run", ["another commit's run", "a pull request run"])
def test_a_run_of_another_commit_or_event_is_no_push_run_on_the_commit(gate, dispatch, tmp_path, ignored_run):
    requests = requests_of(dispatch)
    other_query = {
        "another commit's run": requests["runs"][1].replace(dispatch.commit, "e5" * 20),
        "a pull request run": requests["runs"][1].replace("event=push", "event=pull_request"),
    }[ignored_run]
    answers = green_answers(dispatch, runs=ci_runs()) | {
        ("api", other_query): Answer(ci_runs((7, "completed", "success")))
    }
    result = run_gate_step(gate, CI_STEP, dispatch, tmp_path, answers)
    assert outcome(result) == (1, refused(f"CI has no push run on {dispatch.commit}."), "")


MERGED_HEAD = "b2" * 20
NO_SINGLE_PULL = "no single pull request merged {commit} into main."
RED_VERDICT = (
    "pull request #9 merged with quality / verdict {verdict}; release the next commit merged with a green verdict."
)


def merged_scenario(pulls: list[dict] | None = None, runs: tuple = ((1, "success", around_the_merge(-60)),)) -> dict:
    return {
        "pulls": [merged_pull(9, MERGED_HEAD, around_the_merge())] if pulls is None else pulls,
        "check_runs": {MERGED_HEAD: check_runs(*runs)},
    }


def newest_run_scenarios() -> dict[str, tuple[dict, str | None]]:
    return {
        "the newest check run is the highest number, not the last in text order": (
            merged_scenario(runs=((9, "success", around_the_merge(-60)), (10, "failure", around_the_merge(-30)))),
            RED_VERDICT.format(verdict="failure"),
        ),
        "the newest check run is the highest number, not the last to complete": (
            merged_scenario(runs=((1, "success", around_the_merge(-10)), (2, "failure", around_the_merge(-50)))),
            RED_VERDICT.format(verdict="failure"),
        ),
    }


def merge_scenarios() -> dict[str, tuple[dict, str | None]]:
    """Each id: what replaces the green reads, and the refusal line (None when the step passes)."""
    head = MERGED_HEAD
    scenario = merged_scenario
    no_single_pull = NO_SINGLE_PULL
    red = RED_VERDICT
    return {
        "green": (scenario(), None),
        "no pull request": (scenario([]), no_single_pull),
        "an open pull request only": (scenario([merged_pull(9, head, None)]), no_single_pull),
        "merged into another base": (
            scenario([merged_pull(9, head, around_the_merge(), base="develop")]),
            no_single_pull,
        ),
        "two merged pull requests": (
            scenario([merged_pull(9, head, around_the_merge()), merged_pull(10, head, around_the_merge())]),
            no_single_pull,
        ),
        "the verdict failed": (scenario(runs=((1, "failure", around_the_merge(-60)),)), red.format(verdict="failure")),
        "no verdict check run": (scenario(runs=()), red.format(verdict="missing")),
        "a re-run after the merge does not lift the refusal": (
            scenario(runs=((1, "failure", around_the_merge(-60)), (2, "success", around_the_merge(30)))),
            red.format(verdict="failure"),
        ),
        "a re-run before the merge decides": (
            scenario(runs=((1, "failure", around_the_merge(-60)), (2, "success", around_the_merge(-30)))),
            None,
        ),
        "a re-run before the merge decides, listed newest first": (
            scenario(runs=((2, "success", around_the_merge(-30)), (1, "failure", around_the_merge(-60)))),
            None,
        ),
        "a verdict completed at the merge counts": (scenario(runs=((1, "success", around_the_merge()),)), None),
        "a failure completed at the merge overrides an earlier success": (
            scenario(runs=((1, "success", around_the_merge(-60)), (2, "failure", around_the_merge()))),
            red.format(verdict="failure"),
        ),
        "a run unfinished at the merge does not count": (
            scenario(runs=((1, "success", around_the_merge(-60)), (2, None, None))),
            None,
        ),
        "no check run finished by the merge": (
            scenario(runs=((1, "success", around_the_merge(30)),)),
            red.format(verdict="missing"),
        ),
    }


ALL_MERGE_SCENARIOS = merge_scenarios() | newest_run_scenarios()


@pytest.mark.parametrize("case", list(ALL_MERGE_SCENARIOS))
def test_a_commit_its_pull_request_merged_red_is_never_released(gate, dispatch, tmp_path, case):
    replaced, refusal = ALL_MERGE_SCENARIOS[case]
    result = run_gate_step(gate, MERGED_STEP, dispatch, tmp_path, green_answers(dispatch, **replaced))
    if refusal:
        assert outcome(result) == (1, refused(refusal.format(commit=dispatch.commit)), "")
    else:
        assert outcome(result) == (0, "", f"commit={dispatch.commit}\n")


def test_the_gate_asks_for_the_check_the_gate_s_caller_contract_names(gate, dispatch, tmp_path):
    only_that_name = {7: run_jobs((verdict_check_name(), "success"))}
    result = run_gate_step(gate, CI_STEP, dispatch, tmp_path, green_answers(dispatch, jobs=only_that_name))
    assert outcome(result) == (0, "", "")


def test_the_gate_takes_the_version_and_hands_on_the_commit(gate):
    call = gate[True]["workflow_call"]
    assert set(call["inputs"]) == {"version"}
    assert {key: value for key, value in call["inputs"]["version"].items() if key != "description"} == {
        "required": True,
        "type": "string",
    }
    assert set(call["outputs"]) == {"commit"}
    assert call["outputs"]["commit"]["value"] == "${{ jobs.gate.outputs.commit }}"
    descriptions = [call["inputs"]["version"]["description"], call["outputs"]["commit"]["description"]]
    assert all(isinstance(text, str) and text.strip() for text in descriptions)
    assert gate["jobs"]["gate"]["outputs"] == {"commit": "${{ steps.merged.outputs.commit }}"}
    assert gate["jobs"]["gate"]["steps"][-1]["id"] == "merged"


def test_the_gate_judges_the_dispatched_commit_and_version(gate):
    environment = gate["jobs"]["gate"]["env"]
    assert set(environment) == {"GH_TOKEN", "VERSION", "COMMIT", "RELEASE_FORM"}
    assert {key: value for key, value in environment.items() if key != "RELEASE_FORM"} == {
        "GH_TOKEN": "${{ github.token }}",
        "VERSION": "${{ inputs.version }}",
        "COMMIT": "${{ github.sha }}",
    }
    assert isinstance(environment["RELEASE_FORM"], str)
    for name in (FORMAT_STEP, TAGGED_STEP):
        assert "$RELEASE_FORM" in step_script(gate, "gate", name)
    patterns = [
        pattern
        for name in step_names(gate, "gate")
        for pattern in re.findall(r"=~\s*(\S+)", step(gate, "gate", name).get("run", ""))
    ]
    assert patterns == ["$RELEASE_FORM", "$RELEASE_FORM"]


def test_the_version_grammar_holds_no_range_whose_meaning_follows_the_locale(gate):
    brackets = re.findall(r"\[[^\]]*\]", gate["jobs"]["gate"]["env"]["RELEASE_FORM"])
    assert brackets
    assert not [bracket for bracket in brackets if "-" in bracket]


def test_the_ref_gate_reads_the_dispatch_ref(gate):
    assert step(gate, "gate", REF_STEP)["env"] == {"DISPATCH_REF": "${{ github.ref }}"}


def test_the_gate_holds_only_read_permissions(gate):
    assert gate["permissions"] == {}
    assert {level for job in gate["jobs"].values() for level in job["permissions"].values()} == {"read"}
