import json
import shlex
from pathlib import Path

import pytest

from quality import pytest_random
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import check_result, refusals, rule_findings
from tests.support.git_repositories import git

RULE = Rule.PYTEST_RANDOM
UNSHUFFLED, UNSEEDED, MISSING = FindingEvent.UNSHUFFLED, FindingEvent.UNSEEDED, FindingEvent.MISSING
WORKFLOW = ".github/workflows/w.yml"
MARKER = "# pytest-random: browser tests keep one stack"
SCRIPT_LINE = 10
ADDOPTS_LINE = 5

# One reading of a flag list for every source that holds one: pyproject's addopts, a pytest call, PYTEST_ADDOPTS.
FLAG_CASES = [
    pytest.param("-p no:randomly", UNSHUFFLED, id="plugin off"),
    pytest.param("-pno:randomly", UNSHUFFLED, id="plugin off glued"),
    pytest.param("--randomly-dont-reorganize", UNSHUFFLED, id="no reorganize"),
    pytest.param("--randomly-seed=1234", UNSHUFFLED, id="fixed seed"),
    pytest.param("--randomly-seed 1234", UNSHUFFLED, id="fixed seed, next word"),
    pytest.param("--randomly-seed=last", UNSHUFFLED, id="last seed"),
    pytest.param("--randomly-seed=default --randomly-seed=1234", UNSHUFFLED, id="fixed seed last"),
    pytest.param("--disable-plugin-autoload", UNSHUFFLED, id="autoload off"),
    pytest.param("-q", UNSEEDED, id="quiet"),
    pytest.param("-qq", UNSEEDED, id="quieter"),
    pytest.param("--quiet", UNSEEDED, id="quiet long"),
    pytest.param("--no-header", UNSEEDED, id="no header"),
    pytest.param("-qn4", UNSEEDED, id="quiet before workers"),
    pytest.param("-qk quick", UNSEEDED, id="quiet before a value letter"),
    pytest.param("--verbosity=-1", UNSEEDED, id="negative verbosity"),
    pytest.param("--verbosity -1", UNSEEDED, id="negative verbosity, next word"),
    pytest.param("-q --verbosity=-1", UNSEEDED, id="quiet and negative verbosity"),
    pytest.param("--verbosity=0 -q", UNSEEDED, id="quiet after a verbosity"),
    pytest.param("-p randomly", None, id="plugin loaded"),
    pytest.param("-p no:cacheprovider", None, id="another plugin off"),
    pytest.param("--randomly-seed=default", None, id="fresh seed"),
    pytest.param("-n auto", None, id="workers"),
    pytest.param("--randomly-seed=1234 --randomly-seed=default", None, id="fresh seed last"),
    pytest.param("-q -v", None, id="quiet, verbose"),
    pytest.param("-q --verbose", None, id="quiet, verbose long"),
    pytest.param("-qv", None, id="quiet, verbose glued"),
    pytest.param("-vq", None, id="verbose, quiet glued"),
    pytest.param("-v", None, id="verbose"),
    pytest.param("-q --verbosity=0", None, id="quiet before a verbosity"),
    pytest.param("--verbosity=0 -v", None, id="verbose after a verbosity"),
    pytest.param("-q -n auto -v", None, id="quiet and verbose around workers"),
    pytest.param("-kquick", None, id="quiet as a keyword"),
    pytest.param("-mquick", None, id="quiet as a mark"),
    pytest.param("-rfq", None, id="quiet as report chars"),
    pytest.param("-n4 -k q", None, id="quiet as a separate value"),
    pytest.param("--disable-plugin-autoload -p randomly", None, id="autoload off, plugin named"),
    pytest.param("--disable-plugin-autoload -p pytest_randomly", None, id="autoload off, module named"),
    pytest.param("-vpno:randomly", None, id="a -p inside a cluster is no plugin word"),
    pytest.param("--disable-plugin-autoload -qp randomly", UNSHUFFLED, id="a -p inside a cluster names no plugin"),
    pytest.param("--randomly-seed=${{ inputs.seed }}", None, id="seed chosen at run time"),
    pytest.param("-q -- -x", UNSEEDED, id="before the first --"),
    pytest.param("-n auto -- -q", None, id="after the first --"),
    pytest.param("--verbosity", None, id="verbosity without a value"),
]
FLAGGED = [case for case in FLAG_CASES if case.values[1] is not None]


def lock(*names: str) -> str:
    packages = "".join(f'\n[[package]]\nname = "{name}"\nversion = "9.2.0"\n' for name in names)
    return f"version = 1\n{packages}"


PYTEST_TABLES = ["tool.pytest.ini_options", "tool.pytest"]


def pyproject(addopts: str, table: str = PYTEST_TABLES[0]) -> str:
    """A pyproject whose `addopts` key is line 5."""
    return f"[project]\nname = \"x\"\n\n[{table}]\naddopts = {addopts}\n"


def head() -> str:
    """A workflow up to its `steps:` key (line 7); the next line is line 8."""
    return "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


def script(*lines: str) -> str:
    """One step whose `run: |` is line 9 and whose script starts on line 10."""
    return head() + "      - name: t\n        run: |\n" + "".join(f"          {line}\n" for line in lines)


def judged(build_repo, files: dict[str, str]):
    """The findings of pytest-random over the given files, not one workflow text."""
    return rule_findings(build_repo(files), RULE)


def workflow_judged(build_repo, text: str):
    return judged(build_repo, {WORKFLOW: text})


def shape(findings):
    """A finding as (event, path, line): this rule is judged by place, not by fact."""
    return [(finding.event, finding.path, finding.line) for finding in findings]


def expected(event: FindingEvent | None, path: str, line: int):
    return [] if event is None else [(event, path, line)]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("packages", "events"),
    [
        (("pytest",), [MISSING]),
        (("pytest", "pytest-randomly"), []),
        (("Pytest", "Pytest_Randomly"), []),
        (("pytest-randomly",), []),
        (("pytest-cov", "pyyaml"), []),
    ],
    ids=["pytest alone", "both", "both, spelled otherwise", "plugin alone", "neither"],
)
def test_a_lock_that_holds_pytest_holds_pytest_randomly_too(build_repo, packages, events):
    """RANDOM-1: a tracked uv.lock that locks pytest without pytest-randomly is `missing`, once, at the lock."""
    findings = judged(build_repo, {"uv.lock": lock(*packages)})
    assert [finding.event for finding in findings] == events
    assert all((finding.path, finding.line) == ("uv.lock", None) for finding in findings)


@pytest.mark.integration
def test_the_missing_plugin_names_the_version_and_the_dev_group_fix(build_repo):
    """RANDOM-1: the fact names the locked pytest version, the fix the dev dependency group and `uv lock`."""
    (finding,) = judged(build_repo, {"uv.lock": lock("pytest")})
    assert finding.fact == "pytest 9.2.0 is locked without pytest-randomly"
    assert finding.fix == "add pytest-randomly to the dev dependency group, then uv lock"


@pytest.mark.integration
def test_a_nested_lock_is_named_by_its_path_and_the_extent_counts_the_locks(build_repo):
    """RANDOM-2: each tracked lock is judged; the finding names the nested one and the extent is 2."""
    repo = build_repo({"uv.lock": lock("pytest", "pytest-randomly"), "svc/uv.lock": lock("pytest")})
    (finding,) = rule_findings(repo, RULE)
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is RULE)
    assert (finding.path, verdict.extent) == ("svc/uv.lock", 2)


@pytest.mark.integration
def test_a_lock_tracked_under_two_names_is_judged_once_under_the_first(build_repo):
    """RANDOM-2: `speech/uv.lock -> ../uv.lock` and `uv.lock` are one file, one finding named by the first."""
    repo = build_repo({"uv.lock": lock("pytest")})
    (repo / "speech").mkdir()
    (repo / "speech/uv.lock").symlink_to("../uv.lock")
    git(repo, "add", "speech/uv.lock")
    git(repo, "commit", "-q", "-m", "link")
    assert [finding.path for finding in rule_findings(repo, RULE)] == ["speech/uv.lock"]


@pytest.mark.integration
def test_a_repository_without_a_lock_a_pyproject_or_a_workflow_is_clean_at_extent_zero(build_repo):
    """RANDOM-2: nothing to read is clean, and the extent is 0."""
    repo = build_repo({"README.md": "x\n"})
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is RULE)
    assert (verdict.findings, verdict.extent) == ((), 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"uv.lock": "version = [\n"}, "uv.lock is not UTF-8 TOML"),
        ({"uv.lock": "version = 1\npackage = [1]\n"}, "uv.lock is not a uv lock"),
        ({"pyproject.toml": "x = [\n"}, "pyproject.toml is not UTF-8 TOML"),
        ({"pyproject.toml": pyproject("1")}, "pyproject.toml: addopts is not a string or a list of strings"),
        ({"pyproject.toml": pyproject('["-q", 1]')}, "pyproject.toml: addopts is not a string or a list of strings"),
        ({"pyproject.toml": pyproject("\"-k 'a\"")}, "pyproject.toml: addopts is not shell words"),
    ],
    ids=["lock no TOML", "packages no tables", "pyproject no TOML", "addopts number", "addopts list of mixed", "addopts quote"],
)
def test_a_file_the_rule_cannot_read_refuses_it_naming_the_file(build_repo, files, reason):
    """RANDOM-3: a lock or pyproject that cannot be read is exit 2 naming the file, never clean."""
    assert reason in refusals(build_repo(files))[RULE]


@pytest.mark.integration
def test_a_finding_is_exit_one_and_an_unreadable_file_exit_two(build_repo, capsys):
    """RANDOM-3: the exit codes of a finding and of a file the rule cannot read."""
    assert run(capsys, build_repo({"uv.lock": lock("pytest")}))[0] == 1
    assert run(capsys, build_repo({"uv.lock": "version = [\n"}))[0] == 2


@pytest.mark.integration
@pytest.mark.parametrize("table", PYTEST_TABLES)
@pytest.mark.parametrize("quoting", [json.dumps, lambda flags: json.dumps(shlex.split(flags))], ids=["string", "list"])
@pytest.mark.parametrize(("flags", "event"), FLAG_CASES)
def test_addopts_are_judged_at_the_key_that_holds_them(build_repo, table, quoting, flags, event):
    """RANDOM-4, RANDOM-5, RANDOM-6, RANDOM-7: the switches and the quiet flags of `addopts`, a list or a string, in either table pytest reads."""
    findings = judged(build_repo, {"pyproject.toml": pyproject(quoting(flags), table)})
    assert shape(findings) == expected(event, "pyproject.toml", ADDOPTS_LINE)


@pytest.mark.integration
@pytest.mark.parametrize("flags", ["-p no:randomly", "-q"])
def test_no_marker_clears_addopts(build_repo, flags):
    """RANDOM-8: `addopts` holds for every layer, so a marker beside it clears nothing."""
    text = f"[project]\nname = \"x\"\n\n[tool.pytest.ini_options]\n{MARKER}\naddopts = {json.dumps(flags)}\n"
    assert len(judged(build_repo, {"pyproject.toml": text})) == 1


@pytest.mark.integration
def test_addopts_set_by_a_dotted_key_are_judged_without_a_line(build_repo):
    """RANDOM-4: the finding stands at the pyproject when the key's line cannot be told."""
    (finding,) = judged(build_repo, {"pyproject.toml": '[tool.pytest]\nini_options.addopts = "-q"\n'})
    assert (finding.path, finding.line) == ("pyproject.toml", None)


@pytest.mark.integration
def test_a_nested_project_is_judged_and_a_fixture_project_below_tests_is_not(build_repo):
    """RANDOM-4: the pyproject set is the python-floor rule's, so a fixture project under `tests/` stays out."""
    files = {
        "svc/pyproject.toml": pyproject('"-q"'),
        "tests/fixture/pyproject.toml": pyproject('"-q"'),
        "pyproject.toml": "[project]\nname = \"root\"\n",
    }
    assert [finding.path for finding in judged(build_repo, files)] == ["svc/pyproject.toml"]


@pytest.mark.integration
@pytest.mark.parametrize("command", ["pytest {}", "uv run pytest {}", "python -m pytest {}", "uv run --locked python3.14 -m pytest {}"])
def test_a_workflow_call_is_judged_at_its_line_through_any_runner(build_repo, command):
    """RANDOM-9: the call of the pytest-workers reader, through `uv run` or `python -m`, is judged at its line."""
    findings = workflow_judged(build_repo, script(command.format("-n auto -p no:randomly")))
    assert shape(findings) == [(UNSHUFFLED, WORKFLOW, SCRIPT_LINE)]


@pytest.mark.integration
@pytest.mark.parametrize(("flags", "event"), FLAG_CASES)
def test_a_workflow_call_is_judged_by_its_flags(build_repo, flags, event):
    """RANDOM-4, RANDOM-5, RANDOM-6, RANDOM-9: the same reading of a flag list holds for a pytest call."""
    findings = workflow_judged(build_repo, script("echo start", f"pytest {flags}"))
    assert shape(findings) == expected(event, WORKFLOW, SCRIPT_LINE + 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "line",
    ["pip install pytest-randomly", "pip install -q pytest", "echo pytest -q", "# pytest -q", "scripts/run_tests.sh tests -q"],
)
def test_a_line_that_is_no_pytest_call_is_clean(build_repo, line):
    """RANDOM-9: a pip install, an echo, a comment and a script that starts pytest are no call the rule reads."""
    assert workflow_judged(build_repo, script(line)) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "lines",
    [
        ("pytest -q  " + MARKER,),
        (MARKER, "pytest -q"),
        ("pytest -p no:randomly \\", "  tests  " + MARKER),
        (MARKER, "# a plain comment", "pytest -q"),
    ],
    ids=["on the line", "directly above", "on a continuation line", "above a plain comment"],
)
def test_a_marker_with_a_reason_clears_a_workflow_call(build_repo, lines):
    """RANDOM-8: `# pytest-random: <reason>` on the call's line or in the unbroken comment run directly above it clears it."""
    assert workflow_judged(build_repo, script(*lines)) == []


@pytest.mark.integration
def test_a_marker_trailing_another_commands_line_clears_that_command_only(build_repo):
    """RANDOM-8: the marker clears the line it trails, not the call on the next line."""
    assert [finding.line for finding in workflow_judged(build_repo, script(f"pytest -q {MARKER}", "pytest -q"))] == [11]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("line", "event"),
    [("pytest --collect-only -q", None), ("pytest --collectonly -q", None), ("pytest --co -q", None), ("pytest -q", UNSEEDED), ("pytest -q -- --collect-only", UNSEEDED)],
    ids=["collect-only", "collectonly", "co", "a run", "after the separator"],
)
def test_a_listing_only_call_is_not_judged(build_repo, line, event):
    """RANDOM-9: a call that only lists tests runs none, so its quiet flag hides no seed."""
    assert shape(workflow_judged(build_repo, script(line))) == expected(event, WORKFLOW, 10)


@pytest.mark.integration
@pytest.mark.parametrize(
    "lines",
    [
        ("# pytest-random:", "pytest -q"),
        ("# pytest-random: 1", "pytest -q"),
        ("# pytest-workers: reason", "pytest -q"),
        (MARKER, "echo x", "pytest -q"),
        (MARKER, "echo x", "# a plain comment", "pytest -q"),
    ],
    ids=["no reason", "reason of no letter", "another rule's marker", "two lines above", "above a command and a comment"],
)
def test_a_marker_without_a_reason_or_too_far_clears_nothing(build_repo, lines):
    """RANDOM-8: the marker needs its own id, a reason with a letter and a place on the call's line or in the unbroken comment run above it."""
    assert [finding.event for finding in workflow_judged(build_repo, script(*lines))] == [UNSEEDED]


@pytest.mark.integration
def test_a_call_with_both_a_switch_and_a_quiet_flag_is_one_finding_of_the_switch(build_repo):
    """RANDOM-9: a call is one finding, its no-shuffle problem first; the fix names the switch's fix."""
    (finding,) = workflow_judged(build_repo, script("pytest -q -p no:randomly"))
    assert finding.event is UNSHUFFLED
    assert "# pytest-random: <reason>" in str(finding.fix)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("flags", "words"),
    [
        ("-p no:randomly", ("-p no:randomly", "--randomly-dont-reorganize", "--randomly-seed", "--disable-plugin-autoload")),
        ("-q", ("-q", "--verbosity=-1", "--no-header", "Using --randomly-seed=N")),
    ],
    ids=["unshuffled", "unseeded"],
)
def test_the_fix_names_what_to_drop_in_each_source(build_repo, flags, words):
    """RANDOM-10: addopts and a workflow call share the fix of an event; the workflow adds the marker."""
    repo = build_repo({"pyproject.toml": pyproject(json.dumps(flags)), WORKFLOW: script(f"pytest {flags}")})
    by_path = {finding.path: finding for finding in rule_findings(repo, RULE)}
    in_addopts, in_call = by_path["pyproject.toml"], by_path[WORKFLOW]
    assert all(word in in_addopts.fix for word in words)
    assert in_call.fix == f"{in_addopts.fix}, or explain on the line above: # pytest-random: <reason>"


def env_workflow(level: str, name: str, value: str) -> tuple[str, int]:
    """A workflow with `name: value` in the `env:` of its workflow, job or step, and the line of that value."""
    if level == "workflow":
        return f"env:\n  {name}: {value}\n" + head() + "      - run: echo\n", 2
    if level == "job":
        job = head().replace("    steps:\n", f"    env:\n      {name}: {value}\n    steps:\n")
        return job + "      - run: echo\n", 8
    return head() + f"      - run: echo\n        env:\n          {name}: {value}\n", 10


LEVELS = ["workflow", "job", "step"]


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize(("flags", "event"), FLAG_CASES)
def test_pytest_addopts_in_an_env_is_judged_at_its_value(build_repo, level, flags, event):
    """RANDOM-4, RANDOM-5, RANDOM-6, RANDOM-11: PYTEST_ADDOPTS in a workflow, job or step `env:` is judged like addopts."""
    text, line = env_workflow(level, "PYTEST_ADDOPTS", json.dumps(flags))
    assert shape(workflow_judged(build_repo, text)) == expected(event, WORKFLOW, line)


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize(("value", "event"), [('"1"', UNSHUFFLED), ("1", UNSHUFFLED), ('"0"', UNSHUFFLED), ('" "', UNSHUFFLED), ('""', None)])
def test_disabled_plugin_autoload_in_an_env_is_no_random_order_unless_empty(build_repo, level, value, event):
    """RANDOM-6, RANDOM-11: PYTEST_DISABLE_PLUGIN_AUTOLOAD with any value pytest reads as set is `unshuffled`."""
    text, line = env_workflow(level, "PYTEST_DISABLE_PLUGIN_AUTOLOAD", value)
    assert shape(workflow_judged(build_repo, text)) == expected(event, WORKFLOW, line)


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", ["PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"])
def test_a_marker_clears_an_env_value_on_its_line_or_directly_above(build_repo, level, name):
    """RANDOM-8, RANDOM-11: the marker clears a workflow value that is a switch or a quiet flag."""
    value = json.dumps("-q" if name == "PYTEST_ADDOPTS" else "1")
    text, line = env_workflow(level, name, value)
    lines = text.split("\n")
    above = "\n".join([*lines[: line - 1], MARKER, *lines[line - 1 :]])
    beside = "\n".join([*lines[: line - 1], f"{lines[line - 1]}  {MARKER}", *lines[line:]])
    assert workflow_judged(build_repo, above) == [] == workflow_judged(build_repo, beside)


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVELS)
def test_two_comment_lines_above_an_env_value_clear_it(build_repo, level):
    """RANDOM-8: the marker is read anywhere in the unbroken comment run above the value."""
    text, line = env_workflow(level, "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    lines = text.split("\n")
    above = "\n".join([*lines[: line - 1], MARKER, "# a plain comment", *lines[line - 1 :]])
    assert workflow_judged(build_repo, above) == []


@pytest.mark.integration
@pytest.mark.parametrize("markers", [("# pytest-workers: one worker", MARKER), (MARKER, "# pytest-workers: one worker")], ids=["workers first", "random first"])
def test_two_markers_on_two_lines_above_a_plain_run_clear_the_call_for_both_rules(build_repo, markers):
    """RANDOM-8, PYTEST-7: a call that both rules judge carries each marker on a line of its own."""
    text = head() + "".join(f"      {marker}\n" for marker in markers) + "      - run: pytest -n 0 -q\n"
    repo = build_repo({WORKFLOW: text})
    assert [rule_findings(repo, rule) for rule in (RULE, Rule.PYTEST_WORKERS)] == [[], []]


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVELS)
def test_a_marker_trailing_the_previous_env_value_does_not_clear_the_next(build_repo, level):
    """RANDOM-8: a marker trailing another line's env value clears that line only."""
    text, line = env_workflow(level, "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    lines = text.split("\n")
    indent = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
    marked = "\n".join([*lines[: line - 1], f"{indent}OTHER: x  {MARKER}", *lines[line - 1 :]])
    assert shape(workflow_judged(build_repo, marked)) == expected(UNSHUFFLED, WORKFLOW, line + 1)


@pytest.mark.integration
def test_an_env_value_built_at_run_time_is_not_read(build_repo):
    """RANDOM-11: a `${{ }}` value is chosen at run time and judged clean."""
    text, _ = env_workflow("step", "PYTEST_ADDOPTS", "${{ matrix.opts }}")
    assert workflow_judged(build_repo, text) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "build",
    [
        lambda: "on: push\njobs:\n  j:\n    steps:\n      - run: echo\n        env: [\n",
        lambda: "on: push\nenv:\n  <<: {PYTEST_ADDOPTS: -q}\njobs: {}\n",
        lambda: "on: push\nx: &o -q\njobs:\n  j:\n    env:\n      PYTEST_ADDOPTS: *o\n    steps: []\n",
        lambda: "on: push\nx: &e {PYTEST_ADDOPTS: -q}\njobs:\n  j:\n    steps:\n      - run: echo\n        env: *e\n",
        lambda: "on: push\nenv:\n  PYTEST_ADDOPTS: \"-k 'a\"\njobs: {}\n",
        lambda: "on: push\njobs: [a]\n",
        lambda: "on: push\njobs:\n  j: x\n",
    ],
    ids=["no YAML", "merge key in env", "alias as a value", "alias as env", "value no shell words", "jobs is a list", "a job is a scalar"],
)
def test_a_workflow_the_env_reader_cannot_read_refuses_the_rule_naming_the_file(build_repo, build):
    """RANDOM-12: YAML that cannot be read, an alias or a merge key where it hides `env`, a `jobs` or a job that is no mapping, or a value that is no shell words is exit 2."""
    assert WORKFLOW in refusals(build_repo({WORKFLOW: build()}))[RULE]


@pytest.mark.integration
@pytest.mark.parametrize(("flags", "event"), FLAGGED)
@pytest.mark.parametrize("form", ['PYTEST_ADDOPTS={} pytest tests', 'export PYTEST_ADDOPTS={}', 'env PYTEST_ADDOPTS={} uv run pytest'])
def test_a_script_assignment_of_pytest_addopts_is_judged_at_its_line(build_repo, form, flags, event):
    """RANDOM-11: PYTEST_ADDOPTS as an assignment word, with a command or exported, is judged like the env."""
    findings = workflow_judged(build_repo, script(form.format(shlex.quote(flags))))
    assert shape(findings) == [(event, WORKFLOW, SCRIPT_LINE)]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("line", "event"),
    [
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -n auto", UNSHUFFLED),
        ("export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", UNSHUFFLED),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD= pytest -n auto", None),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD=' ' pytest -n auto", UNSHUFFLED),
        ("""PYTEST_ADDOPTS='-p "no:randomly"' pytest -n auto""", UNSHUFFLED),
        ("echo PYTEST_ADDOPTS=-q", None),
        ("PYTEST_ADDOPTS=${{ matrix.opts }} pytest -n auto", None),
    ],
    ids=["with a call", "exported", "empty", "blank", "quoted option value", "echoed", "built at run time"],
)
def test_a_script_assignment_is_read_only_as_an_assignment(build_repo, line, event):
    """RANDOM-11: only an assignment word is read, and an empty autoload value is not set."""
    assert shape(workflow_judged(build_repo, script(line))) == expected(event, WORKFLOW, SCRIPT_LINE)


@pytest.mark.integration
def test_a_script_assignment_of_pytest_addopts_that_is_no_shell_words_refuses_the_rule(build_repo):
    """RANDOM-12: pytest splits `PYTEST_ADDOPTS` as shell words, so a value it cannot split is exit 2."""
    reason = refusals(build_repo({WORKFLOW: script("""PYTEST_ADDOPTS='-k "a' pytest""")}))[RULE]
    assert "PYTEST_ADDOPTS in a run script is not shell words" in reason
    assert WORKFLOW in reason


@pytest.mark.integration
@pytest.mark.parametrize("flags", ["-p no:randomly", "--randomly-seed=1234", "--randomly-seed=last", "-q"])
def test_the_marker_clears_a_workflow_source_that_a_flow_layer_needs(build_repo, flags):
    """RANDOM-8: a marker clears a call and a PYTEST_ADDOPTS assignment, never the same flags in addopts."""
    assert workflow_judged(build_repo, script(MARKER, f"pytest {flags}")) == []
    assert workflow_judged(build_repo, script(MARKER, f"PYTEST_ADDOPTS={shlex.quote(flags)} pytest")) == []
    assert len(judged(build_repo, {"pyproject.toml": pyproject(json.dumps(flags))})) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "lines",
    [
        ('pytest -q "unclosed',),
        ("pytest -n auto", "$TOOL -q"),
        ("pytest -n auto", "eval pytest -q"),
    ],
    ids=["unreadable", "built at run time", "eval"],
)
def test_a_line_the_pytest_workers_rule_cannot_read_is_reported_once_by_that_rule(build_repo, lines):
    """RANDOM-13: pytest-workers reports the line as `invalid`; pytest-random reports nothing for it."""
    repo = build_repo({WORKFLOW: script(*lines)})
    assert rule_findings(repo, RULE) == []
    assert [finding.event for finding in rule_findings(repo, Rule.PYTEST_WORKERS)] == [FindingEvent.INVALID]


def test_the_checkout_of_quality_holds_no_finding_of_the_rule():
    """RANDOM-14: quality locks pytest-randomly and keeps its header, its checkout holds no finding of the rule."""
    root = Path(__file__).parents[1]
    tracked = ["uv.lock", *(str(path.relative_to(root)) for path in (root / ".github/workflows").glob("*.yml"))]
    result = pytest_random.check_pytest_random(root, tracked, {"pyproject.toml": (root / "pyproject.toml").read_bytes()})
    assert (result.findings, result.extent) == ([], 1)
