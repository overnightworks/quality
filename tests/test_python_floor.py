import subprocess
from pathlib import Path

import pytest

from quality.config import CONFIG_PATH
from tests.support.check import CLEAN_LINE
from tests.support.check import hygiene_output as run
from tests.support.git_repositories import head_commit

ROOT_FLOOR_FIX = (
    "raise it to >=3.14 and move CI, .python-version, Dockerfiles and ruff's target-version with it, "
    "or declare it in quality/config.toml with [[python.allow]], its path and a reason."
)


def project(requires_python: str | None = ">=3.14") -> str:
    clause = "" if requires_python is None else f"requires-python = {requires_python!r}\n"
    return f'[project]\nname = "x"\nversion = "1"\n{clause}'


def allow(path: str, reason: str = "qwen-tts has no cp314 wheel") -> str:
    return f'[[python.allow]]\npath = "{path}"\nreason = "{reason}"\n'


def floor_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("python-floor")]


@pytest.mark.parametrize("requires_python", [">=3.14", ">=3.14,<3.15", "~=3.14", "~=3.14.1", "==3.14.*", ">=3.15"])
def test_a_floor_of_python_3_14_or_higher_is_clean(build_repo, capsys, requires_python):
    """F1, sentence 2: a floor of 3.14 or higher is clean."""
    code, output = run(capsys, build_repo({"pyproject.toml": project(requires_python)}))
    assert (code, CLEAN_LINE in output) == (0, True), output


@pytest.mark.parametrize(
    ("requires_python", "floor"),
    [
        (">=3.12", "3.12"),
        (">=3.11,<3.15", "3.11"),
        (">=3.12,<3.13", "3.12"),
        (">3.13", "3.13"),
        ("==3.*", "3"),
        (">=3.14.0rc1", "3.14.0rc1"),
        (">=3.13,!=3.13.*", "3.13"),
    ],
)
def test_a_floor_below_python_3_14_names_the_value_and_the_floor(build_repo, capsys, requires_python, floor):
    """F2, sentence 3: a floor below 3.14 is red and the reason names the value and the floor."""
    code, output = run(capsys, build_repo({"pyproject.toml": project(requires_python)}))
    reason = f'requires-python "{requires_python}" admits Python {floor}, below 3.14.'
    assert (code, floor_lines(output), ROOT_FLOOR_FIX in output) == (1, [f"python-floor pyproject.toml - {reason}"], True), output


@pytest.mark.parametrize(
    "text",
    [project("<3.15"), project(""), project(None), '[project]\nname = "x"\ndynamic = ["requires-python"]\n'],
    ids=["an upper bound only", "empty", "absent", "dynamic"],
)
def test_a_project_without_a_floor_admits_every_python(build_repo, capsys, text):
    """F3, sentence 2: a project that sets no lower bound is red as naming no floor."""
    code, output = run(capsys, build_repo({"pyproject.toml": text}))
    assert code == 1, output
    assert "names no floor" in floor_lines(output)[0], output


@pytest.mark.parametrize(
    "tracked",
    [
        {"pyproject.toml": ""},
        {"pyproject.toml": "[tool.ruff]\nline-length = 120\n"},
        {"pyproject.toml": project(), "tests/fixtures/app/pyproject.toml": project(">=3.12")},
        {"pyproject.toml": 'project = "x"\n'},
        {"README.md": "no project\n"},
    ],
    ids=["empty", "tool table only", "inside tests", "project is no table", "no pyproject"],
)
def test_a_file_without_a_project_table_is_not_judged(build_repo, capsys, tracked):
    """F4, sentence 1: a file without a [project] table, or inside tests, is not judged."""
    code, output = run(capsys, build_repo(tracked))
    assert (code, floor_lines(output)) == (0, []), output


def test_a_member_below_the_floor_is_the_only_finding_beside_a_root_at_the_floor(build_repo, capsys):
    """F5, sentence 1: a member below the floor is the only finding beside a root at the floor."""
    code, output = run(capsys, build_repo({"pyproject.toml": project(), "src/speech/pyproject.toml": project(">=3.12")}))
    assert (code, [line.split(" - ")[0] for line in floor_lines(output)]) == (1, ["python-floor src/speech/pyproject.toml"]), output


def test_every_file_below_the_floor_is_a_finding_sorted_by_path(build_repo, capsys):
    """F11, sentence 3: every file below the floor is one finding with reason and fix, sorted by path."""
    tracked = {"pyproject.toml": project(">=3.12"), "src/b/pyproject.toml": project(">=3.13"), "src/a/pyproject.toml": project(None)}
    code, output = run(capsys, build_repo(tracked))
    paths = [line.split(" - ")[0] for line in floor_lines(output)]
    assert (code, paths) == (1, ["python-floor pyproject.toml", "python-floor src/a/pyproject.toml", "python-floor src/b/pyproject.toml"]), output
    assert output.count(ROOT_FLOOR_FIX) == 3, output


def test_an_allowed_file_is_clean_and_the_gate_shows_its_value_and_reason(build_repo, capsys):
    """F6, sentences 4 and 5: an allowed file is clean and the gate shows its value and reason."""
    tracked = {
        "pyproject.toml": project(),
        "src/speech/pyproject.toml": project(">=3.12,<3.13"),
        CONFIG_PATH: allow("src/speech/pyproject.toml"),
    }
    code, output = run(capsys, build_repo(tracked))
    expected = 'python-floor: allowed src/speech/pyproject.toml (requires-python ">=3.12,<3.13") - qwen-tts has no cp314 wheel'
    lines = output.splitlines()
    assert (code, lines.count(expected), CLEAN_LINE in output) == (0, 1, True), output
    assert lines.index(expected) < next(n for n, line in enumerate(lines) if CLEAN_LINE in line), output


@pytest.mark.parametrize(
    ("tracked", "why"),
    [
        ({"pyproject.toml": project()}, "no tracked pyproject.toml with a [project] table lies there"),
        ({"pyproject.toml": project(), "src/m/pyproject.toml": "[tool.ruff]\n"}, "no tracked pyproject.toml with a [project] table lies there"),
        ({"pyproject.toml": project(), "src/m/pyproject.toml": project()}, "that file is already at the floor"),
    ],
    ids=["untracked path", "no project table", "already at the floor"],
)
def test_an_allowance_for_a_file_that_needs_none_is_a_finding(build_repo, capsys, tracked, why):
    """F7, sentence 6: an allowance for a file that needs none is a finding."""
    code, output = run(capsys, build_repo({**tracked, CONFIG_PATH: allow("src/m/pyproject.toml")}))
    expected = f"python-floor src/m/pyproject.toml - declared in {CONFIG_PATH} with [[python.allow]], but {why}."
    assert (code, floor_lines(output)) == (1, [expected]), output
    assert f"delete its [[python.allow]] entry from {CONFIG_PATH}." in output


@pytest.mark.parametrize(
    "config",
    [
        allow("src/a/pyproject.toml") + allow("src/a/pyproject.toml"),
        allow("src/a/"),
        allow("src/a"),
        allow("src/a/*.toml"),
        allow("../pyproject.toml"),
        allow("src/a/pyproject.toml", ""),
        '[[python.allow]]\npath = "src/a/pyproject.toml"\nreason = "one\\ntwo"\n',
        allow("src/a/pyproject.toml") + 'extra = 1\n',
    ],
    ids=["duplicate", "folder", "folder without slash", "pattern", "dotdot", "empty reason", "multi-line reason", "unknown key"],
)
def test_a_malformed_python_allowance_cannot_be_judged_by_any_rule_that_reads_the_config(build_repo, capsys, config):
    """F8, sentence 4: a malformed allowance is cannot-judge in every rule that reads the config."""
    code, output = run(capsys, build_repo({"src/a/pyproject.toml": project(), CONFIG_PATH: config}))
    refusals = [line for line in output.splitlines() if "[[python.allow]]" in line]
    named = {line.split(" - ")[0] for line in refusals}
    assert (code, "hygiene: python-floor cannot judge" in named, "hygiene: root-layout cannot judge" in named) == (2, True, True), output
    assert all("qwen-tts has no cp314 wheel" in line for line in refusals), refusals


@pytest.mark.parametrize(
    "content",
    [
        "[project\n",
        "[project]\nrequires-python = 3.14\n",
        project(">=banana"),
        project("===foobar"),
        project("===3.14.*"),
        project(">=" + "9" * 5000),
        f'[project]\nrequires-python = ">=3.14"\nnumber = {"9" * 5000}\n',
        b"\xff\xfe[project]\n",
        f"[project]\nrequires-python = '>=3.14'\na = {'[' * 2000}{']' * 2000}\n",
    ],
    ids=["invalid toml", "not a string", "not a specifier", "identity clause not a version", "identity wildcard", "oversized version", "oversized number", "not utf-8", "nested too deep"],
)
def test_an_unreadable_pyproject_is_unparsable_and_leaves_the_other_rules_judged(build_repo, capsys, content):
    """F9, sentence 7: an unreadable pyproject.toml is unparsable and every other rule is still judged."""
    repo = build_repo({"pyproject.toml": ""})
    (repo / "pyproject.toml").write_bytes(content if isinstance(content, bytes) else content.encode())
    (repo / ".env").write_text("A=1\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", ".env", "pyproject.toml"], check=True)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert "hygiene: python-floor unparsable - pyproject.toml" in output, output
    assert "env-file .env - " in output, output


def test_a_pyproject_that_links_out_of_the_repository_cannot_be_judged(build_repo, capsys, elsewhere):
    """F10, sentence 7: a pyproject.toml linking out of the repository cannot be judged."""
    outside = Path(elsewhere) / "pyproject.toml"
    outside.write_text(project())
    repo = build_repo({"README.md": "x\n"})
    (repo / "pyproject.toml").symlink_to(outside)
    subprocess.run(["git", "-C", str(repo), "add", "--", "pyproject.toml"], check=True)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert "hygiene: python-floor cannot judge - pyproject.toml resolves outside the repository" in output, output


@pytest.mark.parametrize("gitlink_path", ["src/acestep", "src/acestep/pyproject.toml"], ids=["vendor directory", "at a pyproject.toml path"])
def test_a_gitlink_is_never_entered(build_repo, capsys, elsewhere, gitlink_path):
    """F12, sentence 1: a gitlink is never entered."""
    inner = build_repo({"pyproject.toml": project(">=3.12")})
    repo = Path(elsewhere) / "outer"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", "160000", head_commit(inner), gitlink_path], check=True
    )
    (repo / gitlink_path).mkdir(parents=True)
    (repo / gitlink_path / "pyproject.toml").write_text(project(">=3.12"))
    code, output = run(capsys, repo)
    assert (code, floor_lines(output)) == (0, []), output
